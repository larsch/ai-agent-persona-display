#!/usr/bin/env python3
"""xi-agent display daemon — all events show, idle/sleep based on inactivity.

Reads states.json for all state definitions, image pools, cycle intervals,
timeouts, and transitions.  Pre-loads JPEG images from disk (no PNG→JPEG
conversion needed — render_states.py handles that).

Event → state name mapping is a small lookup table in this file.
"""

import argparse
import json
import os
import random
import select
import stat
import sys
import time

sys.path.insert(0, os.path.expanduser("~/prj/esp32s3_4848s040_bootstrap"))
from upload_image import send_over_serial  # noqa: E402
import serial

from states_model import load_states, State

FIFO_PATH = "/tmp/xi_display_fifo"
STATES_DIR = os.path.join(os.path.dirname(__file__), "states")

# ── Global constants (not derived from model) ────────────────────────────

THINKING_MIND_BLOWN_S = 15.0   # 🤯 gate: thinking_4.jpg only after 15s


def preencode(images: set[str]) -> dict[str, bytes]:
    """Load JPEG images from disk into memory. Skips missing files."""
    cache: dict[str, bytes] = {}
    for fname in sorted(images):
        path = os.path.join(STATES_DIR, fname)
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            cache[fname] = f.read()
    return cache


def build_event_map(states: list[State]) -> dict[str, str]:
    """Build event-name → state-name lookup.

    Most events map 1:1 (e.g. "thinking" → "thinking").
    Tool events map to "tool_<toolname>" (e.g. "tool_bash").
    """
    em: dict[str, str] = {}

    # Direct-mapped event names (must match state names in states.json)
    direct = [
        "idle", "sleep", "waiting", "thinking", "responding", "done",
        "error", "compacting", "external_change", "status_update",
        "suspicious", "worried", "disappointed",
        "step_back", "shell_mode", "login", "rate_limited", "turn_end",
    ]
    for name in direct:
        em[name] = name

    # Tool events: event="tool" with tool=<name>
    tool_names = [
        "bash", "python", "exec", "read_file", "write_file",
        "edit_file", "find_files", "ask_user",
    ]
    for tool in tool_names:
        em[f"tool:{tool}"] = f"tool_{tool}"

    return em


def image_for(event: dict, states_by_name: dict[str, State],
              event_map: dict[str, str]) -> tuple[str | None, str | None]:
    """Pick an image for the incoming event.

    Returns (image_filename, state_name) or (None, None) if unknown.
    """
    ev = event.get("event", "")
    if ev == "tool":
        tool = event.get("tool", "")
        key = f"tool:{tool}"
        state_name = event_map.get(key, "tool_running")
    else:
        key = ev
        state_name = event_map.get(key)
        if state_name is None:
            return None, None

    state = states_by_name.get(state_name)
    if state is None or not state.images:
        return None, None

    return random.choice(state.images), state_name


def open_serial(port, baud):
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            ser = serial.Serial(port, baudrate=baud, timeout=5)
            print(f"Serial: {ser.name}", file=sys.stderr)
            return ser
        except serial.SerialException:
            time.sleep(0.5)
    print("WARN: no serial", file=sys.stderr)
    return None


def upload(ser, jpeg_data, baud):
    if ser is None:
        return False
    for _ in range(2):
        try:
            ok, _, _, _, _ = send_over_serial(ser, jpeg_data, baud)
            if ok:
                return True
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


def run(port, baud, dry_run, states_json):
    # ── Load model ───────────────────────────────────────────────────────
    states, _render, global_debounce_ms, _jpeg_quality = load_states(states_json)
    states_by_name: dict[str, State] = {s.name: s for s in states}
    event_map = build_event_map(states)

    # Collect all image filenames from all states
    all_images: set[str] = set()
    for s in states:
        all_images.update(s.images)

    cache = preencode(all_images)
    missing = all_images - set(cache)
    if missing:
        print(f"WARN: {len(missing)} images missing: {sorted(missing)}",
              file=sys.stderr)

    ensure_fifo()
    ser = None if dry_run else open_serial(port, baud)
    if ser is not None:
        time.sleep(2.5)  # let ESP32 boot after DTR reset

    # ── Runtime state ────────────────────────────────────────────────────
    last_upload = 0.0
    current_label: str | None = None       # state name currently showing
    current_image: str | None = None       # specific image filename showing
    state_entry_time = 0.0                 # when current_label was entered
    last_cycle_time = 0.0                  # last image cycle change
    thinking_started = 0.0                 # monotonic time thinking entered
    pending_event: dict | None = None      # event awaiting debounce flush
    pending_deadline = 0.0

    fifo_fd = os.open(FIFO_PATH, os.O_RDONLY | os.O_NONBLOCK)
    poller = select.poll()
    poller.register(fifo_fd, select.POLLIN)
    buf = b""
    print("Daemon running.", file=sys.stderr)

    def do_upload(fname, label):
        nonlocal last_upload, current_label, current_image
        nonlocal state_entry_time, last_cycle_time, thinking_started

        if fname not in cache:
            return

        if not dry_run:
            ok = upload(ser, cache[fname], baud)
            tag = "OK" if ok else "FAIL"
            print(f"  [{tag}] {label:16s} → {fname}", file=sys.stderr)
        else:
            print(f"  [dry] {label:16s} → {fname}", file=sys.stderr)

        now = time.monotonic()

        # Detect state transition (new label or re-entry after interruption)
        if label != current_label:
            current_label = label
            state_entry_time = now
            last_cycle_time = now
            if label == "thinking":
                thinking_started = now

        current_image = fname
        last_upload = now

    def show(event, immediate=False):
        """Show an event on the display.

        If *immediate* is True the debounce is bypassed (used when
        flushing a previously-pended event whose deadline has passed).
        """
        nonlocal pending_event, pending_deadline

        fname, state_name = image_for(event, states_by_name, event_map)
        if fname is None or fname not in cache:
            return

        evtype = event.get("event", "")

        # 🤯 gate: thinking_4.jpg only after THINKING_MIND_BLOWN_S
        if state_name == "thinking" and fname == "thinking_4.jpg":
            if time.monotonic() - thinking_started < THINKING_MIND_BLOWN_S:
                pool = [img for img in states_by_name["thinking"].images
                        if img != "thinking_4.jpg"]
                if pool:
                    fname = random.choice(pool)

        # Dedup: already showing this exact state
        if state_name == current_label:
            return

        now = time.monotonic()

        # min_display protection: current state blocks transitions
        if not immediate and current_label is not None:
            cur_state = states_by_name.get(current_label)
            if cur_state and cur_state.min_display_ms > 0:
                elapsed = (now - state_entry_time) * 1000
                if elapsed < cur_state.min_display_ms:
                    if evtype != "error":
                        pending_event = event
                        pending_deadline = (
                            state_entry_time
                            + cur_state.min_display_ms / 1000
                        )
                        return

        # Debounce (global or per-state)
        if not immediate and current_label is not None:
            cur_state = states_by_name.get(current_label)
            debounce = global_debounce_ms
            if cur_state and cur_state.debounce_ms is not None:
                debounce = cur_state.debounce_ms
            if (now - last_upload) * 1000 < debounce:
                pending_event = event
                pending_deadline = last_upload + debounce / 1000
                return

        # Flush
        pending_event = None
        do_upload(fname, state_name)

    while True:
        now = time.monotonic()

        # ── Flush pending event once debounce/min_display expires ────────
        if pending_event is not None and now >= pending_deadline:
            show(pending_event, immediate=True)

        # ── Per-state timeouts ───────────────────────────────────────────
        if current_label is not None:
            cur_state = states_by_name.get(current_label)
            if cur_state and cur_state.timeout_ms > 0 and cur_state.timeout_state:
                elapsed = (now - state_entry_time) * 1000
                if elapsed >= cur_state.timeout_ms:
                    show({"event": cur_state.timeout_state})

        # ── Image cycling ────────────────────────────────────────────────
        if current_label is not None:
            cur_state = states_by_name.get(current_label)
            if cur_state and cur_state.cycle_interval_ms > 0:
                interval_s = cur_state.cycle_interval_ms / 1000
                if now - last_cycle_time >= interval_s:
                    pool = [img for img in cur_state.images
                            if img != current_image]
                    if not pool:
                        pool = cur_state.images
                    new_img = random.choice(pool)
                    do_upload(new_img, current_label)

        # ── Poll FIFO ────────────────────────────────────────────────────
        events = poller.poll(100)  # shorter poll for responsive cycling
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
            if not line:
                continue
            try:
                event = json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            show(event)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="xi-agent display daemon")
    p.add_argument("--port", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=3000000)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--states-json",
                   default=os.path.join(os.path.dirname(__file__), "states.json"),
                   help="Path to states.json")
    args = p.parse_args()
    run(args.port, args.baud, args.dry_run, args.states_json)
