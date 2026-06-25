#!/usr/bin/env python3
"""
Upload a PNG/JPEG image to the ESP32-S3 display over USB serial.

Usage:
    python3 upload_image.py <image_file> [--port PORT] [--baud BAUD]

The script converts the image to JPEG, sends it to the device,
and waits for an "OK" acknowledgment.

Use --dry-run to process the image without sending.
"""

import argparse
import io
import os
import struct
import sys
import time

import serial
from PIL import Image

MAGIC = b"IMG!"
DISPLAY_WIDTH = 480
DISPLAY_HEIGHT = 480
DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD = 3000000
JPEG_QUALITY = 85


def main():
    t0 = time.time()

    parser = argparse.ArgumentParser(description="Upload image to ESP32-S3 display")
    parser.add_argument("image", help="Path to PNG or JPEG image file")
    parser.add_argument("--port", default=DEFAULT_PORT, help=f"Serial port (default: {DEFAULT_PORT})")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help=f"Baud rate (default: {DEFAULT_BAUD})")
    parser.add_argument("--quality", type=int, default=JPEG_QUALITY, help=f"JPEG quality 1-100 (default: {JPEG_QUALITY})")
    parser.add_argument("--dry-run", action="store_true", help="Process image but don't send over serial")
    parser.add_argument("--save-jpeg", metavar="FILE", help="Save converted JPEG to file for inspection")
    args = parser.parse_args()

    if not os.path.exists(args.image):
        print(f"ERROR: File not found: {args.image}", file=sys.stderr)
        sys.exit(1)

    # ── Load ──
    t1 = time.time()
    src = Image.open(args.image)
    orig_w, orig_h = src.size

    if src.mode not in ("RGBA", "RGBa", "LA", "PA"):
        src = src.convert("RGBA")

    if orig_w > DISPLAY_WIDTH or orig_h > DISPLAY_HEIGHT:
        scale = min(DISPLAY_WIDTH / orig_w, DISPLAY_HEIGHT / orig_h)
        new_w = int(orig_w * scale)
        new_h = int(orig_h * scale)
        src = src.resize((new_w, new_h), Image.LANCZOS)
    t2 = time.time()

    # ── Composite ──
    img = Image.new("RGB", src.size, (0, 0, 0))
    if src.mode in ("RGBA", "RGBa", "LA", "PA"):
        img.paste(src, mask=src.split()[-1])
    else:
        img.paste(src)
    t3 = time.time()

    # ── JPEG encode ──
    jpeg_buf = io.BytesIO()
    img.save(jpeg_buf, format="JPEG", quality=args.quality)
    jpeg_data = jpeg_buf.getvalue()
    jpeg_size = len(jpeg_data)
    t4 = time.time()

    if args.save_jpeg:
        with open(args.save_jpeg, "wb") as f:
            f.write(jpeg_data)

    if args.dry_run:
        print(f"  load+resize: {(t2-t1)*1000:.0f}ms  composite: {(t3-t2)*1000:.0f}ms  encode: {(t4-t3)*1000:.0f}ms")
        print("Dry run complete. Image NOT sent to device.")
        return

    header = MAGIC + struct.pack("<I", jpeg_size)

    # ── Serial open ──
    try:
        ser = serial.Serial(args.port, baudrate=args.baud, timeout=30)
    except serial.SerialException as e:
        print(f"ERROR: Could not open {args.port}: {e}", file=sys.stderr)
        sys.exit(1)
    t5 = time.time()

    # ── Send ──
    ser.write(header)
    ser.write(jpeg_data)
    ser.flush()
    t6 = time.time()

    # ── Wait for OK ──
    ok_received = False
    decode_us = draw_us = 0
    deadline = time.time() + 35
    while time.time() < deadline:
        line = ser.readline().decode("ascii", errors="replace").strip()
        if line:
            if line.startswith("OK"):
                parts = line.split()
                if len(parts) >= 3:
                    decode_us = int(parts[1])
                    draw_us = int(parts[2])
                ok_received = True
                break
    t7 = time.time()
    ser.close()

    # ── Report ──
    print(f"  load+resize: {(t2-t1)*1000:5.0f}ms")
    print(f"  composite:   {(t3-t2)*1000:5.0f}ms")
    print(f"  jpeg encode: {(t4-t3)*1000:5.0f}ms  ({jpeg_size/1024:.1f} KB)")
    print(f"  serial open: {(t5-t4)*1000:5.0f}ms")
    wire_time = jpeg_size * 10 / args.baud  # 10 bits per byte (8N1)
    print(f"  serial send: {(t6-t5)*1000:5.0f}ms  ({jpeg_size} B @ {args.baud} baud, wire min {wire_time*1000:.0f}ms)")
    device_ms = (t7 - t6) * 1000
    print(f"  device:     {device_ms:5.0f}ms  (decode {decode_us/1000:.0f}ms + draw {draw_us/1000:.0f}ms + transfer tail {device_ms - (decode_us+draw_us)/1000:.0f}ms)")
    print(f"  ─────────────────────")
    print(f"  total:       {(t7-t0)*1000:5.0f}ms")

    if ok_received:
        print("SUCCESS")
    else:
        print("WARNING: Did not receive OK")


if __name__ == "__main__":
    main()
