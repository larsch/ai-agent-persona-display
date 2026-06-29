from __future__ import annotations

"""In-process xi hook IPC source for the display daemon."""

import asyncio
import json
import os
import socket
import sys
import time
from typing import Any

from xi_adapter import XiHookEventTranslator


async def run_xi_ipc_source(
    queue: asyncio.Queue[dict[str, Any]],
    *,
    endpoint: str,
    translator: XiHookEventTranslator,
) -> None:
    if os.name == "nt":
        loop = asyncio.get_running_loop()
        await asyncio.to_thread(_run_windows_server, queue, loop, endpoint, translator)
    else:
        await _run_unix_server(queue, endpoint, translator)


def _translate_line(
    line: bytes,
    translator: XiHookEventTranslator,
) -> dict[str, Any] | None:
    text = line.decode("utf-8", errors="replace").strip()
    if not text:
        return None

    try:
        message = json.loads(text)
    except json.JSONDecodeError:
        print(f"WARN: invalid xi IPC JSON: {text}", file=sys.stderr)
        return None

    if not isinstance(message, dict):
        print(f"WARN: xi IPC message is not an object: {message!r}", file=sys.stderr)
        return None

    payload = translator.translate_event(message)
    if payload is None:
        point = message.get("point", "?")
        print(f"skip xi point={point}", file=sys.stderr)
        return None

    point = message.get("point", "?")
    print(f"xi {point} -> {payload['state']}", file=sys.stderr)
    return payload


async def _run_unix_server(
    queue: asyncio.Queue[dict[str, Any]],
    endpoint: str,
    translator: XiHookEventTranslator,
) -> None:
    if os.path.exists(endpoint):
        os.unlink(endpoint)

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        print("xi client connected", file=sys.stderr)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                payload = _translate_line(line, translator)
                if payload is not None:
                    await queue.put(payload)
        finally:
            writer.close()
            await writer.wait_closed()
            print("xi client disconnected", file=sys.stderr)

    server = await asyncio.start_unix_server(handle_client, path=endpoint)
    print(f"xi IPC listening on {endpoint}", file=sys.stderr)
    try:
        async with server:
            await server.serve_forever()
    finally:
        try:
            os.unlink(endpoint)
        except FileNotFoundError:
            pass


def _run_windows_server(
    queue: asyncio.Queue[dict[str, Any]],
    loop: asyncio.AbstractEventLoop,
    endpoint: str,
    translator: XiHookEventTranslator,
) -> None:
    try:
        import pywintypes  # type: ignore
        import win32file  # type: ignore
        import win32pipe  # type: ignore
    except ImportError:
        print("pywin32 is required on Windows: pip install pywin32", file=sys.stderr)
        return

    print(f"xi IPC listening on {endpoint}", file=sys.stderr)

    while True:
        try:
            pipe = win32pipe.CreateNamedPipe(
                endpoint,
                win32pipe.PIPE_ACCESS_INBOUND,
                win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
                win32pipe.PIPE_UNLIMITED_INSTANCES,
                65536,
                65536,
                0,
                None,
            )
        except pywintypes.error as exc:
            if exc.winerror == 231:
                print(
                    "xi IPC pipe busy; another listener may already own "
                    f"{endpoint}. retrying",
                    file=sys.stderr,
                )
                time.sleep(0.2)
                continue
            raise

        try:
            try:
                win32pipe.ConnectNamedPipe(pipe, None)
            except pywintypes.error as exc:
                if exc.winerror != 535:
                    raise
            print("xi client connected", file=sys.stderr)
            buf = b""
            while True:
                try:
                    _hr, chunk = win32file.ReadFile(pipe, 4096)
                except pywintypes.error:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    payload = _translate_line(line, translator)
                    if payload is not None:
                        loop.call_soon_threadsafe(queue.put_nowait, payload)
        finally:
            try:
                win32file.CloseHandle(pipe)
            except Exception:
                pass
            print("xi client disconnected", file=sys.stderr)
