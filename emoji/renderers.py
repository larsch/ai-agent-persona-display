from __future__ import annotations

"""Renderer implementations for the display controller."""

import asyncio
import os
import sys
import time

import serial

sys.path.insert(0, os.path.expanduser("~/prj/esp32s3_4848s040_bootstrap"))
from upload_image import send_brightness, send_over_serial  # noqa: E402


def _debug_log(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[debug] {message}", file=sys.stderr)


class DryRunRenderer:
    def __init__(self, debug: bool = False):
        self.debug = debug

    async def render(self, state_name: str, image_name: str, jpeg_data: bytes) -> None:
        _debug_log(self.debug, f"dry render bytes={len(jpeg_data)} state={state_name} image={image_name}")
        print(f"[dry] render {state_name:16s} -> {image_name}", file=sys.stderr)

    async def set_brightness(self, level: int) -> None:
        print(f"[dry] brightness {level}/255", file=sys.stderr)

    async def close(self) -> None:
        return None


class SerialRenderer:
    def __init__(self, port: str, baud: int, debug: bool = False):
        self.port = port
        self.baud = baud
        self.debug = debug
        self.ser: serial.Serial | None = None
        self._io_lock = asyncio.Lock()

    async def connect(self) -> None:
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                self.ser = serial.Serial(self.port, baudrate=self.baud, timeout=5)
                print(f"Serial: {self.ser.name}", file=sys.stderr)
                await asyncio.sleep(2.5)
                return
            except serial.SerialException:
                await asyncio.sleep(0.5)
        print("WARN: no serial", file=sys.stderr)
        self.ser = None

    async def render(self, state_name: str, image_name: str, jpeg_data: bytes) -> None:
        _debug_log(self.debug, f"render request state={state_name} image={image_name} bytes={len(jpeg_data)}")
        async with self._io_lock:
            if self.ser is None:
                print(f"[skip] render {state_name:16s} -> {image_name} (no serial)", file=sys.stderr)
                return

            ok = False
            decode_us = draw_us = 0
            device_ms = send_ms = 0.0
            for attempt in range(1, 3):
                try:
                    _debug_log(self.debug, f"render start attempt={attempt} state={state_name} image={image_name}")
                    started = time.monotonic()
                    ok, decode_us, draw_us, device_ms, send_ms = send_over_serial(
                        self.ser,
                        jpeg_data,
                        self.baud,
                        self.debug,
                        f"state={state_name} image={image_name} attempt={attempt}",
                    )
                    elapsed_ms = (time.monotonic() - started) * 1000
                    _debug_log(
                        self.debug,
                        "render done "
                        f"attempt={attempt} ok={ok} elapsed_ms={elapsed_ms:.0f} "
                        f"send_ms={send_ms:.0f} device_ms={device_ms:.0f} "
                        f"decode_ms={decode_us/1000:.0f} draw_ms={draw_us/1000:.0f} "
                        f"state={state_name} image={image_name}",
                    )
                    if ok:
                        break
                except serial.SerialException as exc:
                    _debug_log(self.debug, f"render serial exception attempt={attempt} state={state_name} image={image_name}: {exc}")
                    await asyncio.sleep(0.5)
            tag = "OK" if ok else "FAIL"
            print(f"[{tag}] render {state_name:16s} -> {image_name}", file=sys.stderr)

    async def set_brightness(self, level: int) -> None:
        async with self._io_lock:
            if self.ser is None:
                print(f"[skip] brightness {level}/255 (no serial)", file=sys.stderr)
                return
            try:
                _debug_log(self.debug, f"brightness start level={level}")
                send_brightness(self.ser, level)
                _debug_log(self.debug, f"brightness done level={level}")
                print(f"brightness {level}/255", file=sys.stderr)
            except serial.SerialException as exc:
                _debug_log(self.debug, f"brightness serial exception level={level}: {exc}")
                print("brightness FAIL (serial error)", file=sys.stderr)

    async def close(self) -> None:
        async with self._io_lock:
            if self.ser is not None:
                try:
                    self.ser.close()
                finally:
                    self.ser = None
