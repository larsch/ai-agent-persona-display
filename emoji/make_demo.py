#!/usr/bin/env python3
"""Build a demo GIF showing a typical state cycle.

Extra dwell time on sleep, thinking, and responding states.
"""

import argparse
import os
from PIL import Image

from states_model import load_states

STATES_DIR = os.path.join(os.path.dirname(__file__), "states")
DEFAULT_OUT = os.path.join(os.path.dirname(__file__), "demo.gif")

# Base frame duration in milliseconds for non-highlighted states
BASE_DWELL = 250
# Multipliers for states that get extra dwell
DWELL_MULT = {
    "sleep": 4,
    "thinking": 3,
    "responding": 3,
}


def frame_sequence(states_by_name: dict) -> list[tuple[str, int]]:
    """Build a (filename, duration_ms) sequence for a typical agent cycle."""
    frames: list[tuple[str, int]] = []

    def add(state_name: str, count: int = 1, dwell_ms: int | None = None):
        state = states_by_name.get(state_name)
        if state is None or not state.images:
            return
        if dwell_ms is not None:
            duration = dwell_ms
        else:
            mult = DWELL_MULT.get(state_name, 1)
            duration = BASE_DWELL * mult
        pool = state.images
        for i in range(count):
            fname = pool[i % len(pool)]
            frames.append((fname, duration))

    # idle → thinking → working + thinking interleaved → responding → done → idle → sleep
    add("idle")
    add("thinking", count=2, dwell_ms=1500)
    add("tool_bash", dwell_ms=750)
    add("tool_read_file", dwell_ms=1000)
    add("tool_edit_file", dwell_ms=1000)
    add("thinking", count=1, dwell_ms=1500)
    add("tool_write_file", dwell_ms=750)
    add("tool_find_files", dwell_ms=1000)
    add("tool_python", dwell_ms=750)
    add("thinking", count=2, dwell_ms=1500)
    add("responding", count=4)
    add("done", dwell_ms=1000)
    add("idle", dwell_ms=2500)
    add("sleep", count=6)

    return frames


def make_gif(
    frames: list[tuple[str, int]],
    out_path: str,
    loop: int = 0,
) -> None:
    """Assemble frames into an animated GIF."""
    images = []
    durations = []
    black = Image.new("RGB", (480, 480), (0, 0, 0))
    for fname, duration in frames:
        path = os.path.join(STATES_DIR, fname)
        if not os.path.exists(path):
            print(f"  SKIP missing: {fname}")
            continue
        frame = Image.open(path)
        if frame.mode == "RGBA":
            bg = black.copy()
            bg.paste(frame, mask=frame.split()[-1])
            frame = bg
        else:
            frame = frame.convert("RGB")
        frame = frame.resize((240, 240), Image.LANCZOS)
        images.append(frame)
        durations.append(duration)
        print(f"  {duration:4d}ms  {fname}")

    if not images:
        print("No images found — run render_states.py first.", flush=True)
        return

    # Save as animated GIF
    images[0].save(
        out_path,
        save_all=True,
        append_images=images[1:],
        duration=durations,
        loop=loop,
        optimize=False,
    )
    size_kb = os.path.getsize(out_path) / 1024
    print(f"\n  → {out_path}  ({len(images)} frames, {size_kb:.0f} KB)")


def main():
    parser = argparse.ArgumentParser(
        description="Build a demo GIF from rendered state images",
    )
    parser.add_argument(
        "--states-json",
        default=os.path.join(os.path.dirname(__file__), "states.json"),
        help="Path to states.json",
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT,
        help=f"Output GIF path (default: {DEFAULT_OUT})",
    )
    args = parser.parse_args()

    states, _render, _debounce, _jpeg_quality = load_states(args.states_json)
    states_by_name = {s.name: s for s in states}

    frames = frame_sequence(states_by_name)
    make_gif(frames, args.out)


if __name__ == "__main__":
    main()
