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

FULL_SIZE = (DISPLAY_WIDTH, DISPLAY_HEIGHT)


def _center_for(size: tuple[int, int]) -> tuple[float, float]:
    return (size[0] / 2, size[1] / 2)


def _point(
    angle_deg: float,
    radius: float,
    *,
    center: tuple[float, float],
) -> tuple[float, float]:
    angle = math.radians(angle_deg - 90)
    return (
        center[0] + math.cos(angle) * radius,
        center[1] + math.sin(angle) * radius,
    )


def _draw_hand(
    draw: ImageDraw.ImageDraw,
    angle_deg: float,
    *,
    center: tuple[float, float],
    tail_radius: float,
    tip_radius: float,
    fill: tuple[int, int, int] | tuple[int, int, int, int],
    width: int,
) -> None:
    draw.line(
        [
            _point(angle_deg + 180, tail_radius, center=center),
            _point(angle_deg, tip_radius, center=center),
        ],
        fill=fill,
        width=width,
    )


def render_clock_overlay(
    now: datetime,
    *,
    size: tuple[int, int] = FULL_SIZE,
    background: tuple[int, int, int] | None = None,
) -> Image.Image:
    """Render an analog clock.

    If ``background`` is None, returns an RGBA image with a transparent
    background suitable for compositing on top of another frame.
    """
    scale = min(size) / min(FULL_SIZE)
    mode = "RGBA" if background is None else "RGB"
    fill = (0, 0, 0, 0) if background is None else background
    image = Image.new(mode, size, fill)
    draw = ImageDraw.Draw(image)

    center = _center_for(size)
    cx, cy = center
    radius = min(size) / 2 - max(8, round(24 * scale))

    face_outline = (200, 200, 200, 255) if mode == "RGBA" else (200, 200, 200)
    major_tick = (255, 255, 255, 255) if mode == "RGBA" else (255, 255, 255)
    minor_tick = (110, 110, 110, 255) if mode == "RGBA" else (110, 110, 110)
    hour_fill = (255, 255, 255, 255) if mode == "RGBA" else (255, 255, 255)
    minute_fill = (80, 180, 255, 255) if mode == "RGBA" else (80, 180, 255)
    center_fill = (255, 80, 80, 255) if mode == "RGBA" else (255, 80, 80)

    draw.ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius),
        outline=face_outline,
        width=max(2, round(6 * scale)),
    )

    for minute in range(60):
        outer = radius - max(3, round(10 * scale))
        if minute % 5 == 0:
            inner = radius - max(10, round(42 * scale))
            width = max(2, round(8 * scale))
            color = major_tick
        else:
            inner = radius - max(6, round(24 * scale))
            width = max(1, round(3 * scale))
            color = minor_tick
        draw.line(
            [
                _point(minute * 6, inner, center=center),
                _point(minute * 6, outer, center=center),
            ],
            fill=color,
            width=width,
        )

    minute_value = now.minute
    hour_value = (now.hour % 12) + (minute_value / 60)

    _draw_hand(
        draw,
        hour_value * 30,
        center=center,
        tail_radius=max(4, round(18 * scale)),
        tip_radius=radius * 0.50,
        fill=hour_fill,
        width=max(3, round(12 * scale)),
    )
    _draw_hand(
        draw,
        minute_value * 6,
        center=center,
        tail_radius=max(5, round(24 * scale)),
        tip_radius=radius * 0.78,
        fill=minute_fill,
        width=max(2, round(8 * scale)),
    )

    cap_radius = max(3, round(10 * scale))
    draw.ellipse(
        (cx - cap_radius, cy - cap_radius, cx + cap_radius, cy + cap_radius),
        fill=center_fill,
    )

    return image


def render_clock_image(now: datetime) -> Image.Image:
    """Render a full-screen clock with the original black background."""
    return render_clock_overlay(now, size=FULL_SIZE, background=(0, 0, 0))


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
