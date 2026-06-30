#!/usr/bin/env python3
"""Claude Code hook entrypoint for the emoji display daemon.

Wired into Claude Code via settings.json (see claude_settings.json.example).
Claude Code runs this once per hook event and pipes the event payload as JSON
on stdin. We translate it to a generic display state and write that state to
the daemon FIFO — fire-and-forget, never blocking the agent.

If the daemon isn't running (no FIFO reader) or anything goes wrong, we exit 0
silently so the agent is never held up or interrupted.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from claude_adapter import ClaudeHookEventTranslator

# Same FIFO the daemon listens on. The daemon is agent-agnostic, so xi and
# Claude Code can both feed it. Override with --fifo or $EMOJI_DISPLAY_FIFO.
DEFAULT_FIFO = os.environ.get("EMOJI_DISPLAY_FIFO", "/tmp/xi_display_fifo")
DEFAULT_STATES_JSON = os.path.join(os.path.dirname(__file__), "states.json")


def write_state(fifo_path: str, payload: dict) -> None:
    """Write one state line to the FIFO without blocking.

    Opening a FIFO for writing normally blocks until a reader is present; we
    open non-blocking so a missing/stopped daemon just drops the event.
    """
    line = (json.dumps(payload) + "\n").encode("utf-8")
    try:
        fd = os.open(fifo_path, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return  # no reader, FIFO missing, or buffer full — drop the event
    try:
        os.write(fd, line)
    except OSError:
        pass
    finally:
        os.close(fd)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Claude Code -> display FIFO hook")
    parser.add_argument("--fifo", default=DEFAULT_FIFO, help="Path to daemon FIFO")
    parser.add_argument("--states-json", default=DEFAULT_STATES_JSON, help="Path to states.json")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    if not isinstance(event, dict):
        return 0

    try:
        translator = ClaudeHookEventTranslator.from_states_file(args.states_json)
    except (OSError, ValueError, KeyError):
        return 0

    payload = translator.translate_event(event)
    if payload is None:
        return 0

    write_state(args.fifo, payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
