#!/usr/bin/env python3
"""Standalone checks for the Claude Code hook translation.

Run directly: `python3 test_claude_adapter.py` (no test framework required).
"""

from __future__ import annotations

import os

from claude_adapter import ClaudeHookEventTranslator

STATES_JSON = os.path.join(os.path.dirname(__file__), "states.json")


def state_of(translator, event):
    payload = translator.translate_event(event)
    return payload["state"] if payload else None


def main() -> int:
    t = ClaudeHookEventTranslator.from_states_file(STATES_JSON)

    cases = [
        # event payload                                              -> expected state
        ({"hook_event_name": "UserPromptSubmit"}, "waiting"),
        ({"hook_event_name": "PreToolUse", "tool_name": "Bash"}, "tool_bash"),
        ({"hook_event_name": "PreToolUse", "tool_name": "Read"}, "tool_read_file"),
        ({"hook_event_name": "PreToolUse", "tool_name": "Edit"}, "tool_edit_file"),
        ({"hook_event_name": "PreToolUse", "tool_name": "MultiEdit"}, "tool_edit_file"),
        ({"hook_event_name": "PreToolUse", "tool_name": "Write"}, "tool_write_file"),
        ({"hook_event_name": "PreToolUse", "tool_name": "Grep"}, "tool_find_files"),
        ({"hook_event_name": "PreToolUse", "tool_name": "WebSearch"}, "tool_find_files"),
        ({"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion"}, "tool_ask_user"),
        ({"hook_event_name": "PreToolUse", "tool_name": "Task"}, "tool_running"),
        ({"hook_event_name": "PreToolUse", "tool_name": "SomethingNew"}, "tool_running"),
        ({"hook_event_name": "PostToolUse", "tool_name": "Bash"}, "thinking"),
        ({"hook_event_name": "PostToolUseFailure"}, "error"),
        ({"hook_event_name": "Notification", "notification_type": "permission_prompt"}, "tool_ask_user"),
        ({"hook_event_name": "Notification", "notification_type": "elicitation_dialog"}, "tool_ask_user"),
        ({"hook_event_name": "Notification", "notification_type": "auth_success"}, "login"),
        ({"hook_event_name": "Notification", "notification_type": "something_else"}, None),
        ({"hook_event_name": "Stop"}, "done"),
        ({"hook_event_name": "StopFailure", "error_type": "rate_limit"}, "rate_limited"),
        ({"hook_event_name": "StopFailure", "error_type": "server_error"}, "error"),
        ({"hook_event_name": "SubagentStart"}, "tool_running"),
        ({"hook_event_name": "SubagentStop"}, "thinking"),
        ({"hook_event_name": "PreCompact", "trigger_source": "auto"}, "compacting"),
        ({"hook_event_name": "PostCompact"}, "thinking"),
        ({"hook_event_name": "SessionStart", "session_source": "startup"}, "idle"),
        ({"hook_event_name": "SessionEnd"}, "idle"),
        ({"hook_event_name": "FileChanged"}, "external_change"),
        # Unmapped / malformed -> no transition
        ({"hook_event_name": "MessageDisplay"}, None),
        ({"hook_event_name": "WorktreeCreate"}, None),
        ({}, None),
        ({"hook_event_name": 123}, None),
    ]

    failures = 0
    for event, expected in cases:
        got = state_of(t, event)
        ok = got == expected
        if not ok:
            failures += 1
            print(f"FAIL: {event} -> {got!r} (expected {expected!r})")

    # Every state the translator can emit must exist in states.json.
    from claude_adapter import EVENT_STATE_MAP, TOOL_STATE_MAP
    emitted = set(EVENT_STATE_MAP.values()) | set(TOOL_STATE_MAP.values()) | {
        "tool_running", "rate_limited", "login", "tool_ask_user",
    }
    missing = emitted - t.available_state_names
    if missing:
        failures += 1
        print(f"FAIL: states.json is missing emitted states: {sorted(missing)}")

    total = len(cases)
    print(f"{total - failures if failures <= total else 0}/{total} mapping cases passed"
          f"{' + state-coverage check' if not missing else ''}")
    if failures:
        print(f"{failures} failure(s)")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
