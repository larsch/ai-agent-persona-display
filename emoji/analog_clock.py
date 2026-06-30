#!/usr/bin/env python3
from __future__ import annotations

"""Render an analog clock image and upload it to the display every minute.

Uses the serial upload helpers from upload_image.py directly, keeping the serial
port open between updates.
"""

import argparse
import io
import math
import sys
import time
from datetime import datetime, timezone
from typing import Iterable

from PIL import Image, ImageDraw
import serial

from upload_image import (
    DEFAULT_BAUD,
    DEFAULT_PORT,
    DISPLAY_HEIGHT,
    DISPLAY_WIDTH,
    JPEG_QUALITY,
    open_serial,
    send_brightness,
    send_over_serial,
)

CENTER = (DISPLAY_WIDTH // 2, DISPLAY_HEIGHT // 2)


def _point(angle_deg: float, radius: float) -> tuple[float, float]:
    angle = math.radians(angle_deg - 90)
    return (
        CENTER[0] + math.cos(angle) * radius,
        CENTER[1] + math.sin(angle) * radius,
    )


def _draw_hand(
    draw: ImageDraw.ImageDraw,
    angle_deg: float,
    *,
    tail_radius: float,
    tip_radius: float,
    fill: tuple[int, int, int],
    width: int,
) -> None:
    draw.line(
        [_point(angle_deg + 180, tail_radius), _point(angle_deg, tip_radius)],
        fill=fill,
        width=width,
    )


def render_clock_image(now: datetime) -> Image.Image:
    """Render a 480x480 analog clock for the given time."""
    image = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), "black")
    draw = ImageDraw.Draw(image)

    cx, cy = CENTER
    radius = min(DISPLAY_WIDTH, DISPLAY_HEIGHT) // 2 - 24

    # Face outline.
    draw.ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius),
        outline=(200, 200, 200),
        width=6,
    )

    # Minute/hour tick marks.
    for minute in range(60):
        outer = radius - 10
        if minute % 5 == 0:
            inner = radius - 42
            width = 8
            color = (255, 255, 255)
        else:
            inner = radius - 24
            width = 3
            color = (110, 110, 110)
        draw.line([_point(minute * 6, inner), _point(minute * 6, outer)], fill=color, width=width)

    # Hands.
    minute_value = now.minute
    hour_value = (now.hour % 12) + (minute_value / 60)

    hour_angle = hour_value * 30
    minute_angle = minute_value * 6

    _draw_hand(
        draw,
        hour_angle,
        tail_radius=18,
        tip_radius=radius * 0.50,
        fill=(255, 255, 255),
        width=12,
    )
    _draw_hand(
        draw,
        minute_angle,
        tail_radius=24,
        tip_radius=radius * 0.78,
        fill=(80, 180, 255),
        width=8,
    )

    # Center cap.
    draw.ellipse((cx - 10, cy - 10, cx + 10, cy + 10), fill=(255, 80, 80))

    return image


def encode_clock_jpeg(now: datetime, quality: int = JPEG_QUALITY) -> bytes:
    image = render_clock_image(now)
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def upload_clock(ser: serial.Serial, now: datetime, quality: int = JPEG_QUALITY) -> bool:
    jpeg_data = encode_clock_jpeg(now, quality=quality)
    ok, decode_us, draw_us, device_ms, send_ms = send_over_serial(ser, jpeg_data)
    stamp = now.strftime("%Y-%m-%d %H:%M")
    status = "OK" if ok else "NO_ACK"
    print(
        f"[{stamp}] {status} upload  jpeg={len(jpeg_data)}B  "
        f"send={send_ms:.0f}ms  device={device_ms:.0f}ms  "
        f"decode={decode_us/1000:.0f}ms  draw={draw_us/1000:.0f}ms",
        flush=True,
    )
    return ok


def seconds_until_next_minute(now_ts: float | None = None) -> float:
    if now_ts is None:
        now_ts = time.time()
    return max(0.0, 60 - (now_ts % 60))


def minute_ticks(run_once: bool) -> Iterable[datetime]:
    first = True
    while True:
        now = datetime.now()
        if first:
            yield now.replace(second=0, microsecond=0)
            first = False
            if run_once:
                return
        sleep_for = seconds_until_next_minute()
        if sleep_for > 0:
            time.sleep(sleep_for + 0.05)
        yield datetime.now().replace(second=0, microsecond=0)
        if run_once:
            return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render and upload an analog clock every minute")
    parser.add_argument("--port", default=DEFAULT_PORT, help=f"Serial port (default: {DEFAULT_PORT})")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help=f"Baud rate (default: {DEFAULT_BAUD})")
    parser.add_argument("--quality", type=int, default=JPEG_QUALITY, help=f"JPEG quality 1-100 (default: {JPEG_QUALITY})")
    parser.add_argument("--brightness", type=int, metavar="0-255", help="Set brightness once before updates")
    parser.add_argument("--once", action="store_true", help="Render and upload once, then exit")
    parser.add_argument("--dry-run", action="store_true", help="Render without uploading")
    parser.add_argument("--save", metavar="FILE", help="Save the latest rendered image to a file")
    parser.add_argument("--utc", action="store_true", help="Render UTC time instead of local time")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    ser: serial.Serial | None = None
    if not args.dry_run:
        try:
            ser = open_serial(args.port, args.baud)
        except serial.SerialException as exc:
            print(f"ERROR: Could not open {args.port}: {exc}", file=sys.stderr)
            return 1

    try:
        if ser is not None and args.brightness is not None:
            level, _send_ms = send_brightness(ser, args.brightness)
            print(f"brightness {level}/255", flush=True)

        for tick in minute_ticks(run_once=args.once):
            now = tick.astimezone(timezone.utc) if args.utc else tick
            image = render_clock_image(now)

            if args.save:
                image.save(args.save)

            if args.dry_run:
                stamp = now.strftime("%Y-%m-%d %H:%M")
                target = f" -> {args.save}" if args.save else ""
                print(f"[{stamp}] rendered{target}", flush=True)
                continue

            jpeg_buf = io.BytesIO()
            image.save(jpeg_buf, format="JPEG", quality=args.quality)
            ok, decode_us, draw_us, device_ms, send_ms = send_over_serial(ser, jpeg_buf.getvalue(), args.baud)
            stamp = now.strftime("%Y-%m-%d %H:%M")
            status = "OK" if ok else "NO_ACK"
            print(
                f"[{stamp}] {status} upload  jpeg={jpeg_buf.tell()}B  "
                f"send={send_ms:.0f}ms  device={device_ms:.0f}ms  "
                f"decode={decode_us/1000:.0f}ms  draw={draw_us/1000:.0f}ms",
                flush=True,
            )
    except KeyboardInterrupt:
        print("stopped", flush=True)
    finally:
        if ser is not None:
            ser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
