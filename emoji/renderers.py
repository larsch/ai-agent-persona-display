from __future__ import annotations

"""Renderer implementations for the display controller."""

import asyncio
import os
import sys
import time

import serial

sys.path.insert(0, os.path.expanduser("~/prj/esp32s3_4848s040_bootstrap"))
from upload_image import send_brightness, send_over_serial  # noqa: E402


class DryRunRenderer:
    async def render(self, state_name: str, image_name: str, jpeg_data: bytes) -> None:
        print(f"[dry] render {state_name:16s} -> {image_name}", file=sys.stderr)

    async def set_brightness(self, level: int) -> None:
        print(f"[dry] brightness {level}/255", file=sys.stderr)

    async def close(self) -> None:
        return None


class SerialRenderer:
    def __init__(self, port: str, baud: int):
        self.port = port
        self.baud = baud
        self.ser: serial.Serial | None = None

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
        if self.ser is None:
            print(f"[skip] render {state_name:16s} -> {image_name} (no serial)", file=sys.stderr)
            return

        ok = False
        for _ in range(2):
            try:
                ok, _, _, _, _ = send_over_serial(self.ser, jpeg_data, self.baud)
                if ok:
                    break
            except serial.SerialException:
                await asyncio.sleep(0.5)
        tag = "OK" if ok else "FAIL"
        print(f"[{tag}] render {state_name:16s} -> {image_name}", file=sys.stderr)

    async def set_brightness(self, level: int) -> None:
        if self.ser is None:
            print(f"[skip] brightness {level}/255 (no serial)", file=sys.stderr)
            return
        try:
            send_brightness(self.ser, level)
            print(f"brightness {level}/255", file=sys.stderr)
        except serial.SerialException:
            print("brightness FAIL (serial error)", file=sys.stderr)

    async def close(self) -> None:
        if self.ser is not None:
            try:
                self.ser.close()
            finally:
                self.ser = None
