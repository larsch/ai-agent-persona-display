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
import json
import os
import stat
import sys
from typing import Any

from display_controller import DisplayController
from renderers import DryRunRenderer, SerialRenderer
from xi_adapter import (
    DEFAULT_HOOK_ENDPOINT_UNIX,
    DEFAULT_HOOK_ENDPOINT_WINDOWS,
    XiHookEventTranslator,
)
from xi_ipc_source import run_xi_ipc_source

FIFO_PATH = "/tmp/xi_display_fifo"


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


async def handle_message(controller: DisplayController, message: dict[str, Any]) -> None:
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
) -> None:
    while True:
        message = await queue.get()
        try:
            await handle_message(controller, message)
        finally:
            queue.task_done()


async def main_async(args) -> None:
    renderer = DryRunRenderer() if args.dry_run else SerialRenderer(args.port, args.baud)
    if isinstance(renderer, SerialRenderer):
        await renderer.connect()

    translator = XiHookEventTranslator.from_states_file(args.states_json)
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async with DisplayController.from_file(
        args.states_json,
        renderer=renderer,
        states_dir=args.states_dir,
    ) as controller:
        if args.brightness is not None:
            await controller.set_brightness(args.brightness)

        tasks = [asyncio.create_task(dispatcher_loop(controller, queue), name="dispatcher")]

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

        print(f"Daemon running. sources={args.source}", file=sys.stderr)
        await asyncio.gather(*tasks)


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
    asyncio.run(main_async(parse_args()))
