#!/usr/bin/env python3
"""Tiny example adapter that writes generic state transitions to the FIFO."""

from __future__ import annotations

import argparse
import json
import os
import time

DEFAULT_FIFO = "/tmp/xi_display_fifo"


def send(payload: dict, fifo_path: str) -> None:
    with open(fifo_path, "w", encoding="utf-8") as fifo:
        fifo.write(json.dumps(payload) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Example display-state adapter")
    parser.add_argument("--fifo", default=DEFAULT_FIFO)
    args = parser.parse_args()

    sequence = [
        {"state": "idle"},
        {"state": "waiting"},
        {"state": "thinking"},
        {"state": "tool_read_file"},
        {"state": "tool_edit_file"},
        {"state": "responding"},
        {"state": "done"},
        {"state": "idle"},
    ]

    if not os.path.exists(args.fifo):
        raise SystemExit(f"FIFO not found: {args.fifo}")

    for payload in sequence:
        print(f"sending {payload}")
        send(payload, args.fifo)
        time.sleep(1.0)


if __name__ == "__main__":
    main()
