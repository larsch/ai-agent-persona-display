#!/usr/bin/env python3
"""
Fast image loop — sends JPEG images to the ESP32-S3 display as fast as possible.

Pre-encodes all images, opens the serial port once, and cycles through them.
Press Ctrl+C to stop; displays FPS and timing stats.
"""

import argparse
import glob
import io
import os
import struct
import sys
import time
from collections import deque

import serial
from PIL import Image

MAGIC = b"IMG!"
DISPLAY_WIDTH = 480
DISPLAY_HEIGHT = 480
DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD = 3000000
JPEG_QUALITY = 85


def pre_encode(image_paths, quality):
    """Load, resize, JPEG-encode all images once. Returns list of (name, data)."""
    encoded = []
    for path in image_paths:
        try:
            src = Image.open(path)
            orig_w, orig_h = src.size

            if src.mode not in ("RGBA", "RGBa", "LA", "PA"):
                src = src.convert("RGBA")

            if orig_w > DISPLAY_WIDTH or orig_h > DISPLAY_HEIGHT:
                scale = min(DISPLAY_WIDTH / orig_w, DISPLAY_HEIGHT / orig_h)
                new_w = int(orig_w * scale)
                new_h = int(orig_h * scale)
                src = src.resize((new_w, new_h), Image.LANCZOS)

            img = Image.new("RGB", src.size, (0, 0, 0))
            if src.mode in ("RGBA", "RGBa", "LA", "PA"):
                img.paste(src, mask=src.split()[-1])
            else:
                img.paste(src)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            encoded.append((os.path.basename(path), buf.getvalue()))
        except Exception as e:
            print(f"  SKIP {os.path.basename(path)}: {e}", file=sys.stderr)

    return encoded


def main():
    parser = argparse.ArgumentParser(description="Fast image loop to ESP32-S3 display")
    parser.add_argument("images", nargs="*", help="Image files or directories (default: ~/prj/emoji/states/*.png)")
    parser.add_argument("--port", default=DEFAULT_PORT, help=f"Serial port (default: {DEFAULT_PORT})")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help=f"Baud rate (default: {DEFAULT_BAUD})")
    parser.add_argument("--quality", type=int, default=JPEG_QUALITY, help=f"JPEG quality 1-100 (default: {JPEG_QUALITY})")
    parser.add_argument("--count", type=int, default=0, help="Stop after N frames (0 = loop forever)")
    parser.add_argument("--delay", type=float, default=0, help="Extra delay between sends in seconds")
    args = parser.parse_args()

    # Resolve image paths
    if args.images:
        paths = []
        for item in args.images:
            if os.path.isdir(item):
                paths.extend(sorted(glob.glob(os.path.join(item, "*.png"))))
            else:
                paths.append(item)
    else:
        default_dir = os.path.expanduser("~/prj/emoji/states")
        paths = sorted(glob.glob(os.path.join(default_dir, "*.png")))

    if not paths:
        print("ERROR: No images found", file=sys.stderr)
        sys.exit(1)

    print(f"Pre-encoding {len(paths)} images at quality {args.quality}...")
    t0 = time.time()
    encoded = pre_encode(paths, args.quality)
    pre_ms = (time.time() - t0) * 1000
    total_kb = sum(len(data) for _, data in encoded) / 1024
    print(f"  {len(encoded)} images, {total_kb:.0f} KB total, {pre_ms:.0f}ms pre-encode")
    if not encoded:
        print("ERROR: No images could be encoded", file=sys.stderr)
        sys.exit(1)

    # Open serial
    print(f"Opening {args.port} @ {args.baud}...")
    try:
        ser = serial.Serial(args.port, baudrate=args.baud, timeout=3)
        ser.reset_input_buffer()
        ser.reset_output_buffer()
    except serial.SerialException as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Timing ring buffers
    timings = {
        "decode_ms": deque(maxlen=100),
        "draw_ms": deque(maxlen=100),
        "send_ms": deque(maxlen=100),
    }
    ok_count = 0
    fail_count = 0
    t_start = time.time()
    frame = 0

    header_template = MAGIC + b"xxxx"  # placeholder for size

    def send_one(name, data):
        nonlocal ok_count, fail_count, frame

        # Build header with size
        header = MAGIC + struct.pack("<I", len(data))

        # Drain any pending input
        ser.reset_input_buffer()

        t_send = time.time()
        ser.write(header)
        ser.write(data)
        ser.flush()
        send_ms = (time.time() - t_send) * 1000

        # Wait for OK
        deadline = time.time() + 3
        while time.time() < deadline:
            line = ser.readline().decode("ascii", errors="replace").strip()
            if line.startswith("OK"):
                parts = line.split()
                if len(parts) >= 3:
                    timings["decode_ms"].append(int(parts[1]) / 1000)
                    timings["draw_ms"].append(int(parts[2]) / 1000)
                ok_count += 1
                break
            elif line:
                # Unexpected output — likely a stale OK from previous send
                pass
        else:
            fail_count += 1

        timings["send_ms"].append(send_ms)
        frame += 1

    def avg(deq):
        return sum(deq) / len(deq) if deq else 0

    def stats_line():
        elapsed = time.time() - t_start
        fps = frame / elapsed if elapsed > 0 else 0
        return (f"\r  FPS {fps:5.1f}  "
                f"decode {avg(timings['decode_ms']):5.1f}ms  "
                f"draw {avg(timings['draw_ms']):4.1f}ms  "
                f"send {avg(timings['send_ms']):4.1f}ms  "
                f"OK {ok_count}/{frame}  "
                f"elapsed {elapsed:.0f}s   ")

    print("Looping — press Ctrl+C to stop\n")
    try:
        while True:
            for name, data in encoded:
                send_one(name, data)
                if args.delay > 0:
                    time.sleep(args.delay)
                if frame % 10 == 0 and frame > 0:
                    print(stats_line(), end="", flush=True)
                if args.count > 0 and frame >= args.count:
                    raise KeyboardInterrupt
    except KeyboardInterrupt:
        pass

    ser.close()
    elapsed = time.time() - t_start
    print(f"\n\n{'='*50}")
    print(f"Stopped after {frame} frames in {elapsed:.1f}s")
    fps = frame / elapsed if elapsed > 0 else 0
    print(f"  Avg FPS:     {fps:.1f}")
    print(f"  Avg decode:  {avg(timings['decode_ms']):.1f}ms")
    print(f"  Avg draw:    {avg(timings['draw_ms']):.1f}ms")
    print(f"  Avg send:    {avg(timings['send_ms']):.1f}ms")
    print(f"  OK: {ok_count}  FAIL: {fail_count}")


if __name__ == "__main__":
    main()
