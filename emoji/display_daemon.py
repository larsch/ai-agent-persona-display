#!/usr/bin/env python3
"""xi-agent display daemon — all events show, idle/sleep based on inactivity."""

import argparse, json, os, random, select, stat, sys, time

sys.path.insert(0, os.path.expanduser("~/prj/esp32s3_4848s040_bootstrap"))
from upload_image import process_image, send_over_serial  # noqa: E402
import serial

FIFO_PATH = "/tmp/xi_display_fifo"
STATES_DIR = os.path.expanduser("~/prj/emoji/states")
JPEG_QUALITY = 95

DEBOUNCE_MS = 400
DONE_MIN_DISPLAY_S = 2.0   # keep 🥳 visible at least 2s before idle
DONE_TO_IDLE_S = 5.0        # fallback if on_idle hook never arrives
IDLE_TO_SLEEP_S = 30.0
SUSPICIOUS_S = 15.0
WORRIED_S = 30.0

WAITING_POOL = ["waiting_0.png","waiting_1.png","waiting_2.png","waiting_3.png","waiting_4.png"]
SLEEP_POOL = ["sleep_0.png","sleep_1.png","sleep_2.png","sleep_3.png","sleep_4.png","sleep_5.png","sleep_6.png"]
SLEEP_CYCLE_S = 10.0
RESPONDING_POOL = ["responding_0.png","responding_1.png","responding_2.png","responding_3.png","responding_4.png"]
RESPONDING_CYCLE_S = 1.0
DONE_POOL = ["done_0.png","done_1.png","done_2.png"]
THINKING_POOL = ["thinking_0.png","thinking_1.png","thinking_2.png","thinking_3.png","thinking_4.png"]
THINKING_CYCLE_S = 5.0
ASK_USER_POOL = ["ask_user_0.png","ask_user_1.png","ask_user_2.png","ask_user_3.png"]
ASK_USER_CYCLE_S = 5.0

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
}


def preencode():
    cache = {}
    all_imgs = (set(EVENT_IMAGE.values()) | set(TOOL_IMAGE.values()) |
                set(WAITING_POOL) | set(SLEEP_POOL) | set(RESPONDING_POOL) |
                set(DONE_POOL) | set(THINKING_POOL) | set(ASK_USER_POOL) |
                {"idle.png", "sleep.png"})
    for fname in sorted(all_imgs):
        path = os.path.join(STATES_DIR, fname)
        if not os.path.exists(path): continue
        jpeg_data, _ = process_image(path, JPEG_QUALITY)
        cache[fname] = jpeg_data
    return cache


def image_for(event):
    ev = event.get("event","")
    if ev == "idle": return "idle.png"
    if ev == "sleep": return random.choice(SLEEP_POOL)
    if ev == "responding": return random.choice(RESPONDING_POOL)
    if ev == "waiting": return random.choice(WAITING_POOL)
    if ev == "done": return random.choice(DONE_POOL)
    if ev == "thinking": return random.choice(THINKING_POOL)
    if ev == "tool":
        tool = event.get("tool","")
        if tool == "ask_user": return random.choice(ASK_USER_POOL)
        return TOOL_IMAGE.get(tool, "tool_running.png")
    return EVENT_IMAGE.get(ev, "")


def open_serial(port, baud):
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            ser = serial.Serial(port, baudrate=baud, timeout=5)
            print(f"Serial: {ser.name}", file=sys.stderr); return ser
        except serial.SerialException: time.sleep(0.5)
    print("WARN: no serial", file=sys.stderr); return None


def upload(ser, jpeg_data, baud):
    if ser is None: return False
    for attempt in range(2):
        try:
            ok, _, _, _, _ = send_over_serial(ser, jpeg_data, baud)
            if ok: return True
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
    sleep_image = None            # current sleep cycle image
    last_sleep_cycle = 0.0        # last time we changed sleep image
    responding_image = None       # current responding cycle image
    last_responding_cycle = 0.0   # last time we changed responding image
    thinking_image = None         # current thinking cycle image
    last_thinking_cycle = 0.0     # last time we changed thinking image
    thinking_started = 0.0        # monotonic time thinking first entered
    ask_user_image = None         # current ask_user cycle image
    last_ask_user_cycle = 0.0     # last time we changed ask_user image
    pending_event = None          # event dict awaiting debounce flush
    pending_deadline = 0.0        # monotonic time when it's safe to flush

    fifo_fd = os.open(FIFO_PATH, os.O_RDONLY | os.O_NONBLOCK)
    poller = select.poll()
    poller.register(fifo_fd, select.POLLIN)
    buf = b""
    print("Daemon running.", file=sys.stderr)

    def do_upload(fname, label):
        nonlocal last_upload, current_label, idle_active, sleep_active
        nonlocal sleep_image, last_sleep_cycle
        nonlocal responding_image, last_responding_cycle
        nonlocal thinking_image, last_thinking_cycle, thinking_started
        nonlocal ask_user_image, last_ask_user_cycle
        if fname not in cache: return
        if not dry_run:
            ok = upload(ser, cache[fname], baud)
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
            sleep_image = fname
            last_sleep_cycle = time.monotonic()
        elif label == "responding":
            responding_image = fname
            last_responding_cycle = time.monotonic()
        elif label == "thinking":
            thinking_image = fname
            last_thinking_cycle = time.monotonic()
        elif label == "ask_user":
            ask_user_image = fname
            last_ask_user_cycle = time.monotonic()
        else:
            # Other events show, but don't reset the done→idle→sleep timer
            pass

    def show(event, immediate=False):
        """Show an event on the display.

        If *immediate* is True the debounce is bypassed (used when
        flushing a previously-pended event whose deadline has passed).
        """
        nonlocal idle_active, sleep_active, pending_event, pending_deadline
        nonlocal thinking_started
        evtype = event.get("event", "")
        # Capture thinking start time for 🤯 gate
        if evtype == "thinking" and current_label != "thinking":
            thinking_started = time.monotonic()
        fname = image_for(event)
        if not fname or fname not in cache:
            return
        # Type-specific dedup: already showing this exact state.
        if evtype == current_label:
            return
        # Don't show 🤯 until 15s of thinking have elapsed
        if evtype == "thinking" and fname == "thinking_4.png":
            if time.monotonic() - thinking_started < 15:
                pool = [img for img in THINKING_POOL if img != "thinking_4.png"]
                fname = random.choice(pool)
        now = time.monotonic()
        if not immediate:
            # Normal debounce: wait at least DEBOUNCE_MS after last upload.
            # Celebration protection: while 🥳 is showing, block all events
            # (except errors) for DONE_MIN_DISPLAY_S so the celebration
            # isn't cut short by a fast follow-up message triggering waiting.
            min_delay = DEBOUNCE_MS / 1000
            if current_label == "done" and evtype != "error":
                min_delay = DONE_MIN_DISPLAY_S
            if now - last_upload < min_delay:
                pending_event = event
                pending_deadline = last_upload + min_delay
                return
        # We're about to show this event → clear any stale pending.
        pending_event = None
        idle_active = (evtype == "idle")
        sleep_active = (evtype == "sleep")
        label = "ask_user" if evtype == "tool" and event.get("tool") == "ask_user" else evtype
        do_upload(fname, label)

    while True:
        now = time.monotonic()

        # ── Flush pending event once debounce window expires ──────────────
        if pending_event is not None and now >= pending_deadline:
            show(pending_event, immediate=True)

        # ── Done timeout: 5s → idle ──────────────────────────────────────
        if current_label == "done" and now - last_upload > DONE_TO_IDLE_S:
            show({"event": "idle"})

        # ── Suspicion timeout: 15s → 🤨 → 😟 → 😞 → 💤 ──────────────────
        ACTIVE_LABELS = {"done", "idle", "sleep", "suspicious", "worried",
                         "disappointed", "thinking", "responding", "waiting",
                         "tool", "ask_user", None}
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

        # ── Sleep cycle: random different image every 10s ─────────────────
        if sleep_active and now - last_sleep_cycle > SLEEP_CYCLE_S:
            pool = [img for img in SLEEP_POOL if img != sleep_image]
            new_img = random.choice(pool)
            do_upload(new_img, "sleep")

        # ── Responding cycle: random different image every 1s ─────────────
        if current_label == "responding" and now - last_responding_cycle > RESPONDING_CYCLE_S:
            pool = [img for img in RESPONDING_POOL if img != responding_image]
            new_img = random.choice(pool)
            do_upload(new_img, "responding")

        # ── Thinking cycle: random different thinking emoji every 5s ──────
        if current_label == "thinking" and now - last_thinking_cycle > THINKING_CYCLE_S:
            pool = [img for img in THINKING_POOL if img != thinking_image]
            # 🤯 only after 15s of thinking
            if now - thinking_started < 15:
                pool = [img for img in pool if img != "thinking_4.png"]
            if pool:
                new_img = random.choice(pool)
                do_upload(new_img, "thinking")

        # ── Ask-user cycle: random different question emoji every 5s ──────
        if current_label == "ask_user" and now - last_ask_user_cycle > ASK_USER_CYCLE_S:
            pool = [img for img in ASK_USER_POOL if img != ask_user_image]
            if pool:
                new_img = random.choice(pool)
                do_upload(new_img, "ask_user")

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
