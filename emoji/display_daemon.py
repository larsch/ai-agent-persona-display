#!/usr/bin/env python3
"""Config-driven display daemon.

Supports one or both generic input sources in a single process:

- FIFO state commands
- xi hook IPC translated to generic state commands

Generic command surface:

    {"state": "thinking"}
    {"transition": "thinking"}
    {"event": "thinking"}              # legacy alias
    {"state": "tool_read_file"}
    {"set_brightness": 64}

The daemon itself is agent-agnostic. Agent-specific integrations should map
native events into configured state names from states.json.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import stat
import sys
import time
from datetime import datetime
from typing import Any, TextIO

from PIL import Image

from analog_clock import FULL_SIZE, render_clock_overlay
from display_controller import DisplayController
from renderers import DryRunRenderer, SerialRenderer
from states_model import load_states
from upload_image import JPEG_QUALITY
from xi_adapter import (
    DEFAULT_HOOK_ENDPOINT_UNIX,
    DEFAULT_HOOK_ENDPOINT_WINDOWS,
    XiHookEventTranslator,
)
from xi_ipc_source import run_xi_ipc_source

FIFO_PATH = "/tmp/xi_display_fifo"
CLOCK_SLEEP_FULLSCREEN_AFTER_SECONDS = 120
CLOCK_ACTIVE_SIZE = (101, 101)
CLOCK_SLEEP_AGENT_SIZE = (152, 152)
CLOCK_PADDING = 20


class TimestampedStream:
    def __init__(self, stream: TextIO):
        self.stream = stream
        self._at_line_start = True

    def write(self, data: str) -> int:
        if not data:
            return 0
        written = 0
        for chunk in data.splitlines(keepends=True):
            if self._at_line_start:
                self.stream.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ")
            self.stream.write(chunk)
            written += len(chunk)
            self._at_line_start = chunk.endswith(("\n", "\r"))
        return written

    def flush(self) -> None:
        self.stream.flush()

    def isatty(self) -> bool:
        return False


class TeeStream:
    def __init__(self, *streams: TextIO):
        self.streams = streams

    def write(self, data: str) -> int:
        for stream in self.streams:
            stream.write(data)
        return len(data)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()

    def isatty(self) -> bool:
        return False


class RedirectStdStreams:
    def __init__(self, log_path: str):
        self.log_path = log_path
        self._handle: TextIO | None = None
        self._stdout: TextIO | None = None
        self._stderr: TextIO | None = None

    def __enter__(self) -> TextIO:
        os.makedirs(os.path.dirname(os.path.abspath(self.log_path)), exist_ok=True)
        self._handle = open(self.log_path, "a", encoding="utf-8", buffering=1)
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        timestamped_handle = TimestampedStream(self._handle)
        sys.stdout = TeeStream(self._stdout, timestamped_handle)
        sys.stderr = TeeStream(self._stderr, timestamped_handle)
        print(f"Logging to {self.log_path}", file=sys.stderr)
        return self._handle

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._stdout is not None:
            sys.stdout = self._stdout
        if self._stderr is not None:
            sys.stderr = self._stderr
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def debug_log(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[debug] {message}", file=sys.stderr)


class ClockOverlayPolicy:
    def __init__(self, *, jpeg_quality: int, debug: bool = False):
        self.jpeg_quality = jpeg_quality
        self.debug = debug

    def overlay_signature(
        self,
        *,
        state_name: str,
        image_name: str,
        entered_at: float,
        now_ts: float | None = None,
    ) -> tuple[str, str, int, int]:
        if now_ts is None:
            now_ts = time.time()

        fullscreen = 0
        if state_name == "sleep":
            asleep_seconds = int(time.monotonic() - entered_at)
            fullscreen = int(asleep_seconds >= CLOCK_SLEEP_FULLSCREEN_AFTER_SECONDS)

        minute_key = int(now_ts // 60)
        return (state_name, image_name, minute_key, fullscreen)

    def compose(
        self,
        base_jpeg: bytes,
        *,
        now: datetime,
        state_name: str,
        entered_at: float,
    ) -> bytes:
        base = Image.open(io.BytesIO(base_jpeg)).convert("RGB")
        canvas = Image.new("RGB", FULL_SIZE, "black")

        if state_name == "sleep":
            asleep_seconds = time.monotonic() - entered_at
            if asleep_seconds >= CLOCK_SLEEP_FULLSCREEN_AFTER_SECONDS:
                agent = base.resize(CLOCK_SLEEP_AGENT_SIZE, Image.LANCZOS)
                x = (FULL_SIZE[0] - agent.width) // 2
                y = (FULL_SIZE[1] // 2) + 10
                canvas.paste(agent, (x, y))
                clock = render_clock_overlay(now, size=FULL_SIZE, background=None)
                canvas_rgba = canvas.convert("RGBA")
                canvas_rgba.alpha_composite(clock, (0, 0))
                return self._encode(canvas_rgba.convert("RGB"))

        canvas.paste(base, (0, 0))
        clock = render_clock_overlay(now, size=CLOCK_ACTIVE_SIZE)
        canvas_rgba = canvas.convert("RGBA")
        canvas_rgba.alpha_composite(clock, (CLOCK_PADDING, CLOCK_PADDING))
        canvas = canvas_rgba.convert("RGB")
        return self._encode(canvas)

    def _encode(self, image: Image.Image) -> bytes:
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=self.jpeg_quality)
        return buf.getvalue()


class ClockOverlayRenderer:
    def __init__(
        self,
        base_renderer,
        controller: DisplayController,
        overlay_policy: ClockOverlayPolicy,
    ):
        self.base_renderer = base_renderer
        self.controller = controller
        self.overlay_policy = overlay_policy

    async def render(self, state_name: str, image_name: str, jpeg_data: bytes) -> None:
        _state_snapshot, _image_snapshot, entered_at = self.controller.state_snapshot()
        if entered_at is None:
            await self.base_renderer.render(state_name, image_name, jpeg_data)
            return

        signature = self.overlay_policy.overlay_signature(
            state_name=state_name,
            image_name=image_name,
            entered_at=entered_at,
        )
        if signature is None:
            await self.base_renderer.render(state_name, image_name, jpeg_data)
            return

        now = datetime.now().replace(second=0, microsecond=0)
        compose_started = time.monotonic()
        composite_jpeg = await asyncio.to_thread(
            self.overlay_policy.compose,
            jpeg_data,
            now=now,
            state_name=state_name,
            entered_at=entered_at,
        )
        debug_log(
            self.overlay_policy.debug,
            f"overlay compose state={state_name} image={image_name} bytes={len(composite_jpeg)} "
            f"elapsed_ms={(time.monotonic() - compose_started) * 1000:.0f}",
        )
        await self.base_renderer.render(state_name, f"{image_name}+clock", composite_jpeg)

    async def set_brightness(self, level: int) -> None:
        await self.base_renderer.set_brightness(level)

    async def close(self) -> None:
        await self.base_renderer.close()


class ClockRefreshLoop:
    def __init__(
        self,
        controller: DisplayController,
        overlay_policy: ClockOverlayPolicy,
        *,
        tick_seconds: float = 0.25,
    ):
        self.controller = controller
        self.overlay_policy = overlay_policy
        self.tick_seconds = tick_seconds
        self._task: asyncio.Task | None = None
        self._last_signature: tuple[str, str, int, int] | None = None

    async def start(self) -> None:
        if self._task is None:
            debug_log(self.overlay_policy.debug, "clock refresh loop start")
            self._task = asyncio.create_task(self._run(), name="clock-refresh")

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        try:
            while True:
                await self._tick()
                await asyncio.sleep(self.tick_seconds)
        except asyncio.CancelledError:
            raise

    async def _tick(self) -> None:
        state_name, image_name, entered_at = self.controller.state_snapshot()
        if state_name is None or image_name is None or entered_at is None:
            self._last_signature = None
            return

        signature = self.overlay_policy.overlay_signature(
            state_name=state_name,
            image_name=image_name,
            entered_at=entered_at,
        )
        if signature is None:
            self._last_signature = None
            return

        if signature == self._last_signature:
            return

        debug_log(
            self.overlay_policy.debug,
            f"clock refresh trigger state={state_name} image={image_name} signature={signature}",
        )
        refreshed = await self.controller.refresh(reason="clock-refresh")
        debug_log(
            self.overlay_policy.debug,
            f"clock refresh result state={state_name} image={image_name} refreshed={refreshed}",
        )
        state_after, image_after, entered_after = self.controller.state_snapshot()
        if state_after == state_name and image_after == image_name and entered_after == entered_at:
            self._last_signature = signature
        else:
            self._last_signature = None


def ensure_fifo(path: str) -> None:
    if os.path.exists(path):
        if not stat.S_ISFIFO(os.stat(path).st_mode):
            os.remove(path)
        elif not os.access(path, os.W_OK):
            os.remove(path)
    if not os.path.exists(path):
        os.mkfifo(path)


async def open_fifo_reader(path: str):
    return await asyncio.to_thread(open, path, "r", encoding="utf-8")


def extract_state_name(message: dict[str, Any]) -> str | None:
    if isinstance(message.get("state"), str):
        return message["state"]
    if isinstance(message.get("transition"), str):
        return message["transition"]

    legacy_event = message.get("event")
    if not isinstance(legacy_event, str):
        return None

    if legacy_event == "tool":
        tool_name = message.get("tool")
        if isinstance(tool_name, str) and tool_name:
            return f"tool_{tool_name}"
        return "tool_running"

    return legacy_event


async def handle_message(controller: DisplayController, message: dict[str, Any], *, debug: bool = False) -> None:
    debug_log(debug, f"message {message}")
    if "set_brightness" in message:
        await controller.set_brightness(int(message["set_brightness"]))
        return
    if message.get("event") == "set_brightness":
        await controller.set_brightness(int(message.get("level", 255)))
        return

    state_name = extract_state_name(message)
    if state_name is None:
        print(f"WARN: ignoring message without state: {message}", file=sys.stderr)
        return

    try:
        shown = await controller.transition(state_name)
    except ValueError as exc:
        print(f"WARN: {exc}", file=sys.stderr)
        return

    if shown:
        print(f"state -> {state_name}", file=sys.stderr)


async def fifo_source_loop(queue: asyncio.Queue[dict[str, Any]], fifo_path: str) -> None:
    ensure_fifo(fifo_path)
    print(f"FIFO listening on {fifo_path}", file=sys.stderr)

    while True:
        reader = await open_fifo_reader(fifo_path)
        try:
            while True:
                line = await asyncio.to_thread(reader.readline)
                if line == "":
                    await asyncio.sleep(0.1)
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    print(f"WARN: invalid JSON: {line}", file=sys.stderr)
                    continue
                if not isinstance(message, dict):
                    print(f"WARN: expected object, got: {message!r}", file=sys.stderr)
                    continue
                await queue.put(message)
        finally:
            reader.close()


async def dispatcher_loop(
    controller: DisplayController,
    queue: asyncio.Queue[dict[str, Any]],
    *,
    debug: bool = False,
) -> None:
    while True:
        message = await queue.get()
        try:
            debug_log(debug, f"dispatch queue_size={queue.qsize()} message={message}")
            await handle_message(controller, message, debug=debug)
        finally:
            queue.task_done()


async def main_async(args) -> None:
    base_renderer = DryRunRenderer(debug=args.debug) if args.dry_run else SerialRenderer(args.port, args.baud, debug=args.debug)
    if isinstance(base_renderer, SerialRenderer):
        await base_renderer.connect()

    translator = XiHookEventTranslator.from_states_file(args.states_json)
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    refresh_loop: ClockRefreshLoop | None = None
    _states, _render, _debounce_ms, jpeg_quality = load_states(args.states_json)

    async with DisplayController.from_file(
        args.states_json,
        renderer=base_renderer,
        states_dir=args.states_dir,
    ) as controller:
        if args.clock:
            overlay_policy = ClockOverlayPolicy(jpeg_quality=jpeg_quality, debug=args.debug)
            controller.renderer = ClockOverlayRenderer(base_renderer, controller, overlay_policy)
            refresh_loop = ClockRefreshLoop(controller, overlay_policy)
            await refresh_loop.start()

        if args.brightness is not None:
            await controller.set_brightness(args.brightness)

        tasks = [asyncio.create_task(dispatcher_loop(controller, queue, debug=args.debug), name="dispatcher")]

        if args.source in {"fifo", "both"}:
            tasks.append(asyncio.create_task(fifo_source_loop(queue, args.fifo), name="fifo-source"))
        if args.source in {"xi-ipc", "both"}:
            tasks.append(
                asyncio.create_task(
                    run_xi_ipc_source(
                        queue,
                        endpoint=args.xi_ipc_endpoint,
                        translator=translator,
                    ),
                    name="xi-ipc-source",
                )
            )

        print(f"Daemon running. sources={args.source} clock={args.clock} debug={args.debug}", file=sys.stderr)
        try:
            await asyncio.gather(*tasks)
        finally:
            if refresh_loop is not None:
                await refresh_loop.close()


def parse_args():
    parser = argparse.ArgumentParser(description="display state daemon")
    parser.add_argument(
        "--source",
        choices=["fifo", "xi-ipc", "both"],
        default="fifo",
        help="Input source(s) to enable",
    )
    parser.add_argument("--fifo", default=FIFO_PATH, help="Path to command FIFO")
    parser.add_argument(
        "--xi-ipc-endpoint",
        default=DEFAULT_HOOK_ENDPOINT_WINDOWS if os.name == "nt" else DEFAULT_HOOK_ENDPOINT_UNIX,
        help="Xi hook IPC endpoint to host/listen on",
    )
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=3000000)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--clock", action="store_true", help="Overlay an analog clock on top of agent frames")
    parser.add_argument("--debug", action="store_true", help="Enable diagnostic logging")
    parser.add_argument("--log-file", help="Append stdout/stderr logs to a file")
    parser.add_argument("--brightness", type=int, metavar="0-255")
    parser.add_argument(
        "--states-json",
        default=os.path.join(os.path.dirname(__file__), "states.json"),
        help="Path to states.json",
    )
    parser.add_argument(
        "--states-dir",
        default=os.path.join(os.path.dirname(__file__), "states"),
        help="Directory containing rendered state JPEGs",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.log_file:
        with RedirectStdStreams(args.log_file):
            asyncio.run(main_async(args))
    else:
        asyncio.run(main_async(args))
