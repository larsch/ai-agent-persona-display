#!/usr/bin/env python3
"""xi-agent display daemon — all events show, idle/sleep based on inactivity."""

import argparse, io, json, os, random, stat, struct, sys, time, select

try: import serial
except ImportError: serial = None

FIFO_PATH = "/tmp/xi_display_fifo"
STATES_DIR = os.path.expanduser("~/prj/emoji/states")
JPEG_QUALITY = 95
MAGIC = b"IMG!"

DEBOUNCE_MS = 400
DONE_TO_IDLE_S = 5.0
IDLE_TO_SLEEP_S = 30.0
SUSPICIOUS_S = 15.0
WORRIED_S = 30.0

WAITING_POOL = ["waiting_0.png","waiting_1.png","waiting_2.png","waiting_3.png","waiting_4.png"]

EVENT_IMAGE = {
    "thinking": "thinking.png", "responding": "responding.png",
    "compacting": "compacting.png", "external_change": "external_change.png",
    "done": "done.png", "error": "error.png",
    "suspicious": "suspicious.png", "worried": "worried.png",
    "disappointed": "disappointed.png",
}

TOOL_IMAGE = {
    "bash": "bash.png", "python": "python.png", "exec": "exec.png",
    "read_file": "read_file.png", "write_file": "write_file.png",
    "edit_file": "edit_file.png", "find_files": "find_files.png",
    "ask_user": "ask_user.png",
}


def preencode():
    from PIL import Image
    cache = {}
    all_imgs = (set(EVENT_IMAGE.values()) | set(TOOL_IMAGE.values()) |
                set(WAITING_POOL) | {"idle.png", "sleep.png"})
    for fname in sorted(all_imgs):
        path = os.path.join(STATES_DIR, fname)
        if not os.path.exists(path): continue
        src = Image.open(path)
        if src.mode not in ("RGBA","RGBa","LA","PA"): src = src.convert("RGBA")
        canvas = Image.new("RGB", src.size, (0,0,0))
        canvas.paste(src, mask=src.split()[-1]) if src.mode in ("RGBA","RGBa","LA","PA") else canvas.paste(src)
        buf = io.BytesIO(); canvas.save(buf, format="JPEG", quality=JPEG_QUALITY)
        cache[fname] = buf.getvalue()
    return cache


def packet(jpeg_bytes):
    return MAGIC + struct.pack("<I", len(jpeg_bytes)) + jpeg_bytes


def image_for(event):
    ev = event.get("event","")
    if ev == "idle": return "idle.png"
    if ev == "sleep": return "sleep.png"
    if ev == "waiting": return random.choice(WAITING_POOL)
    if ev == "tool": return TOOL_IMAGE.get(event.get("tool",""), "tool_running.png")
    return EVENT_IMAGE.get(ev, "")


def open_serial(port, baud):
    if serial is None: return None
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            ser = serial.Serial(port, baudrate=baud, timeout=5)
            print(f"Serial: {ser.name}", file=sys.stderr); return ser
        except serial.SerialException: time.sleep(0.5)
    print("WARN: no serial", file=sys.stderr); return None


def upload(ser, pkt):
    if ser is None: return False
    for attempt in range(2):
        try:
            ser.write(pkt); ser.flush()
            deadline = time.time() + 5
            while time.time() < deadline:
                line = ser.readline().decode("ascii", errors="replace").strip()
                if line.startswith("OK"): return True
        except serial.SerialException:
            time.sleep(0.5)
    return False


def ensure_fifo():
    if os.path.exists(FIFO_PATH):
        if not stat.S_ISFIFO(os.stat(FIFO_PATH).st_mode):
            os.remove(FIFO_PATH)
        elif not os.access(FIFO_PATH, os.W_OK):
            os.remove(FIFO_PATH)
    if not os.path.exists(FIFO_PATH):
        os.mkfifo(FIFO_PATH)


def run(port, baud, dry_run):
    cache = preencode()
    ensure_fifo()
    ser = None if dry_run else open_serial(port, baud)
    if ser is not None:
        time.sleep(2.5)  # let ESP32 boot after DTR reset

    last_upload = 0.0
    last_event_time = 0.0
    current_label = None          # debounce by event type, not filename
    idle_active = False
    sleep_active = False

    fifo_fd = os.open(FIFO_PATH, os.O_RDONLY | os.O_NONBLOCK)
    poller = select.poll()
    poller.register(fifo_fd, select.POLLIN)
    buf = b""
    print("Daemon running.", file=sys.stderr)

    def do_upload(fname, label):
        nonlocal last_upload, current_label, idle_active, sleep_active
        if fname not in cache: return
        if not dry_run:
            ok = upload(ser, packet(cache[fname]))
            tag = "OK" if ok else "FAIL"
            print(f"  [{tag}] {label:16s} → {fname}", file=sys.stderr)
        else:
            print(f"  [dry] {label:16s} → {fname}", file=sys.stderr)
        last_upload = time.monotonic()
        current_label = label
        if label == "done":
            idle_active = False
            sleep_active = False
        elif label == "idle":
            idle_active = True
            sleep_active = False
        elif label == "sleep":
            sleep_active = True
        else:
            # Other events show, but don't reset the done→idle→sleep timer
            pass

    def show(event):
        nonlocal idle_active, sleep_active
        evtype = event.get("event", "")
        fname = image_for(event)
        if not fname or fname not in cache: return
        if evtype == current_label: return
        now = time.monotonic()
        if now - last_upload < (DEBOUNCE_MS / 1000): return
        idle_active = (evtype == "idle")
        sleep_active = (evtype == "sleep")
        do_upload(fname, evtype)

    while True:
        now = time.monotonic()

        # ── Done timeout: 5s → idle ──────────────────────────────────────
        if current_label == "done" and now - last_upload > DONE_TO_IDLE_S:
            show({"event": "idle"})

        # ── Suspicion timeout: 15s → 🤨 → 😟 → 😞 → 💤 ──────────────────
        ACTIVE_LABELS = {"done", "idle", "sleep", "suspicious", "worried", "disappointed", None}
        if current_label not in ACTIVE_LABELS and now - last_upload > SUSPICIOUS_S:
            show({"event": "suspicious"})
        elif current_label == "suspicious" and now - last_upload > SUSPICIOUS_S:
            show({"event": "worried"})
        elif current_label == "worried" and now - last_upload > SUSPICIOUS_S:
            show({"event": "disappointed"})
        elif current_label == "disappointed" and now - last_upload > SUSPICIOUS_S:
            show({"event": "sleep"})

        # ── Sleep timeout: 30s after idle ─────────────────────────────────
        if idle_active and not sleep_active:
            if now - last_event_time > IDLE_TO_SLEEP_S:
                show({"event": "sleep"})

        # ── Poll FIFO ─────────────────────────────────────────────────────
        events = poller.poll(250)
        if not events:
            continue

        try:
            chunk = os.read(fifo_fd, 4096)
        except BlockingIOError:
            continue

        if not chunk:
            os.close(fifo_fd)
            time.sleep(0.5)
            fifo_fd = os.open(FIFO_PATH, os.O_RDONLY | os.O_NONBLOCK)
            poller = select.poll()
            poller.register(fifo_fd, select.POLLIN)
            buf = b""
            continue

        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if not line: continue
            try:
                event = json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            last_event_time = time.monotonic()
            show(event)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="xi-agent display daemon")
    p.add_argument("--port", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=3000000)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(args.port, args.baud, args.dry_run)
