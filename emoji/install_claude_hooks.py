#!/usr/bin/env python3
"""Install Claude Code hooks that drive the emoji display daemon.

Merges the `hooks` block from claude_settings.json.example into a Claude Code
settings.json (default: ~/.claude/settings.json), wiring every relevant event to
claude_hook.py. Idempotent — running it again won't add duplicate entries, and
it leaves any unrelated hooks you already have in place.

  python3 install_claude_hooks.py                       # ~/.claude/settings.json
  python3 install_claude_hooks.py --settings .claude/settings.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(HERE, "claude_settings.json.example")
HOOK_SCRIPT = os.path.join(HERE, "claude_hook.py")
PLACEHOLDER = "__CLAUDE_HOOK__"


def load_template_hooks(command: str) -> dict:
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        template = json.load(f)
    hooks = template["hooks"]
    # Substitute the real command into every entry.
    for entries in hooks.values():
        for entry in entries:
            for hook in entry.get("hooks", []):
                if hook.get("command") == PLACEHOLDER:
                    hook["command"] = command
    return hooks


def entry_already_present(existing_entries: list, marker: str) -> bool:
    for entry in existing_entries:
        for hook in entry.get("hooks", []):
            if marker in str(hook.get("command", "")):
                return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Claude Code display hooks")
    parser.add_argument(
        "--settings",
        default=os.path.expanduser("~/.claude/settings.json"),
        help="Path to the Claude Code settings.json to update",
    )
    parser.add_argument(
        "--command",
        default=f"python3 {HOOK_SCRIPT}",
        help="Command Claude Code runs for each hook event",
    )
    args = parser.parse_args()

    if not os.path.exists(TEMPLATE_PATH):
        print(f"ERROR: {TEMPLATE_PATH} not found", file=sys.stderr)
        return 1
    if not os.path.exists(HOOK_SCRIPT):
        print(f"ERROR: {HOOK_SCRIPT} not found", file=sys.stderr)
        return 1

    settings_path = os.path.expanduser(args.settings)
    settings: dict = {}
    if os.path.exists(settings_path):
        with open(settings_path, encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            try:
                settings = json.loads(text)
            except json.JSONDecodeError as exc:
                print(f"ERROR: {settings_path} is not valid JSON: {exc}", file=sys.stderr)
                return 1
        if not isinstance(settings, dict):
            print(f"ERROR: {settings_path} top level is not an object", file=sys.stderr)
            return 1

    template_hooks = load_template_hooks(args.command)
    existing_hooks = settings.setdefault("hooks", {})
    if not isinstance(existing_hooks, dict):
        print(f"ERROR: existing 'hooks' in {settings_path} is not an object", file=sys.stderr)
        return 1

    added = 0
    skipped = 0
    for event, new_entries in template_hooks.items():
        current = existing_hooks.setdefault(event, [])
        if not isinstance(current, list):
            print(f"WARN: hooks.{event} is not a list — skipping", file=sys.stderr)
            continue
        for entry in new_entries:
            if entry_already_present(current, HOOK_SCRIPT):
                skipped += 1
                continue
            current.append(entry)
            added += 1

    if added == 0:
        print("Display hooks already installed — nothing to do.")
        return 0

    os.makedirs(os.path.dirname(settings_path) or ".", exist_ok=True)
    if os.path.exists(settings_path):
        shutil.copyfile(settings_path, settings_path + ".bak")
        print(f"Backed up existing settings to {settings_path}.bak")

    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")

    print(f"Installed {added} hook event(s) into {settings_path} "
          f"({skipped} already present).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
