from __future__ import annotations

"""Translate Claude Code hook events into generic display-state transitions.

Claude Code fires one-shot hook commands (configured in ``settings.json``) and
delivers the event payload as JSON on stdin. Unlike xi, there is no persistent
IPC connection — every event is an independent process invocation. ``claude_hook.py``
reads that stdin payload, asks this translator for a state, and writes the
resulting ``{"state": ...}`` line to the daemon FIFO.

The mapping below is the single source of truth for "Claude Code event -> face".
Keep the state names in sync with states.json.
"""

from dataclasses import dataclass
from typing import Any

from states_model import load_states


# Claude Code settings.json file the installer targets by default.
DEFAULT_SETTINGS_PATH = "~/.claude/settings.json"

# Hook events we wire up in settings.json. Each is delivered to claude_hook.py
# on stdin with a "hook_event_name" field; this translator dispatches on it.
HOOK_EVENTS = (
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Notification",
    "Stop",
    "StopFailure",
    "SubagentStart",
    "SubagentStop",
    "PreCompact",
    "PostCompact",
    "SessionStart",
    "SessionEnd",
    "FileChanged",
)

# Built-in Claude Code tool name -> display state. Tools not listed here fall
# back to the generic "tool_running" state.
TOOL_STATE_MAP = {
    "Bash": "tool_bash",
    "Read": "tool_read_file",
    "Edit": "tool_edit_file",
    "MultiEdit": "tool_edit_file",
    "NotebookEdit": "tool_edit_file",
    "Write": "tool_write_file",
    "Glob": "tool_find_files",
    "Grep": "tool_find_files",
    "WebFetch": "tool_find_files",
    "WebSearch": "tool_find_files",
    "AskUserQuestion": "tool_ask_user",
    "ExitPlanMode": "tool_ask_user",
}

# Events that map directly to a single state with no extra inspection.
EVENT_STATE_MAP = {
    "UserPromptSubmit": "waiting",
    "PostToolUse": "thinking",
    "PostToolUseFailure": "error",
    "PostCompact": "thinking",
    "SubagentStart": "tool_running",
    "SubagentStop": "thinking",
    "Stop": "done",
    "PreCompact": "compacting",
    "SessionStart": "idle",
    "SessionEnd": "idle",
    "FileChanged": "external_change",
}


@dataclass
class ClaudeHookEventTranslator:
    available_state_names: set[str]

    @classmethod
    def from_states_file(cls, states_json: str) -> "ClaudeHookEventTranslator":
        states, _render, _debounce_ms, _jpeg_quality = load_states(states_json)
        return cls(available_state_names={state.name for state in states})

    def translate_event(self, event: dict[str, Any]) -> dict[str, str] | None:
        point = event.get("hook_event_name")
        if not isinstance(point, str):
            return None

        if point == "PreToolUse":
            return self._translate_tool(event)
        if point == "Notification":
            return self._translate_notification(event)
        if point == "StopFailure":
            return self._translate_stop_failure(event)

        state_name = EVENT_STATE_MAP.get(point)
        if state_name is None:
            return None
        return self._state_payload(state_name)

    def _translate_tool(self, event: dict[str, Any]) -> dict[str, str] | None:
        tool_name = event.get("tool_name")
        specific = TOOL_STATE_MAP.get(tool_name) if isinstance(tool_name, str) else None
        if specific and specific in self.available_state_names:
            return {"state": specific}
        return self._state_payload("tool_running")

    def _translate_notification(self, event: dict[str, Any]) -> dict[str, str] | None:
        raw = event.get("notification_type")
        kind = raw.lower() if isinstance(raw, str) else ""

        if "permission" in kind or "elicitation" in kind:
            return self._state_payload("tool_ask_user")
        if "auth" in kind:
            return self._state_payload("login")
        return None

    def _translate_stop_failure(self, event: dict[str, Any]) -> dict[str, str] | None:
        # The failure reason may arrive under a few different keys depending on
        # the Claude Code version; scan the obvious ones for a rate-limit signal.
        haystacks = [
            event.get("notification_type"),
            event.get("error_type"),
            event.get("reason"),
            event.get("error"),
        ]
        text = " ".join(h for h in haystacks if isinstance(h, str)).lower()
        if "rate" in text and "limit" in text:
            return self._state_payload("rate_limited")
        return self._state_payload("error")

    def _state_payload(self, state_name: str) -> dict[str, str] | None:
        if state_name in self.available_state_names:
            return {"state": state_name}
        return None
