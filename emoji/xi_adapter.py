from __future__ import annotations

"""Translate xi hook IPC events into generic display-state transitions."""

from dataclasses import dataclass
from typing import Any

from states_model import load_states


DEFAULT_HOOK_ENDPOINT_WINDOWS = r"\\.\pipe\xi-hook-events"
DEFAULT_HOOK_ENDPOINT_UNIX = "/tmp/xi-hook-events.sock"


@dataclass
class XiHookEventTranslator:
    available_state_names: set[str]

    @classmethod
    def from_states_file(cls, states_json: str) -> "XiHookEventTranslator":
        states, _render, _debounce_ms, _jpeg_quality = load_states(states_json)
        return cls(available_state_names={state.name for state in states})

    def translate_event(self, event: dict[str, Any]) -> dict[str, str] | None:
        point = event.get("point")
        if not isinstance(point, str):
            return None

        if point in {"on_tool_intent", "pre_tool"}:
            tool_name = self._tool_name(event)
            if tool_name is None:
                return self._state_payload("tool_running")
            specific = f"tool_{tool_name}"
            if specific in self.available_state_names:
                return {"state": specific}
            return self._state_payload("tool_running")

        if point == "pre_turn":
            return self._state_payload("waiting")
        if point == "post_turn":
            return self._state_payload("turn_end")
        if point == "on_error":
            return self._state_payload("error")
        if point == "on_done":
            return self._state_payload("done")
        if point == "on_first_thinking_token":
            return self._state_payload("thinking")
        if point == "on_first_text_token":
            return self._state_payload("responding")
        if point == "on_idle":
            return self._state_payload("idle")
        if point == "on_compacting":
            return self._state_payload("compacting")
        if point == "on_external_change":
            return self._state_payload("external_change")
        if point == "on_status_update":
            return self._translate_status_update(event)

        return None

    def _translate_status_update(self, event: dict[str, Any]) -> dict[str, str] | None:
        payload = event.get("payload")
        status = None
        if isinstance(payload, dict):
            raw = payload.get("status")
            if isinstance(raw, str):
                status = raw.lower()

        if status and "rate" in status and "limit" in status:
            return self._state_payload("rate_limited")
        return self._state_payload("status_update")

    def _tool_name(self, event: dict[str, Any]) -> str | None:
        tool = event.get("tool")
        if isinstance(tool, str) and tool:
            return tool

        payload = event.get("payload")
        if isinstance(payload, dict):
            payload_tool = payload.get("tool")
            if isinstance(payload_tool, str) and payload_tool:
                return payload_tool

        return None

    def _state_payload(self, state_name: str) -> dict[str, str] | None:
        if state_name in self.available_state_names:
            return {"state": state_name}
        return None
