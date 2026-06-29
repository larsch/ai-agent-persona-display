from __future__ import annotations

"""Async controller for config-driven display state transitions.

The public abstraction is intentionally small:

- load a ``states.json``
- call ``transition(state_name)``
- let the controller enforce debounce, min-display, timeout, and image cycling

Agent-specific adapters are expected to translate their own native events into
configured state names.
"""

import asyncio
import os
import random
import sys
import time
from typing import Protocol

from states_model import State, load_states

DEFAULT_STATES_DIR = os.path.join(os.path.dirname(__file__), "states")


class Renderer(Protocol):
    async def render(self, state_name: str, image_name: str, jpeg_data: bytes) -> None:
        """Render one pre-encoded image for the given state."""

    async def set_brightness(self, level: int) -> None:
        """Set display brightness, 0-255."""

    async def close(self) -> None:
        """Release renderer resources."""


def _preload_images(states_dir: str, images: set[str]) -> dict[str, bytes]:
    cache: dict[str, bytes] = {}
    for image_name in sorted(images):
        path = os.path.join(states_dir, image_name)
        if not os.path.exists(path):
            continue
        with open(path, "rb") as handle:
            cache[image_name] = handle.read()
    return cache


class DisplayController:
    def __init__(
        self,
        *,
        states: list[State],
        global_debounce_ms: int,
        renderer: Renderer,
        image_cache: dict[str, bytes],
        tick_seconds: float = 0.05,
    ):
        self.states = states
        self.states_by_name = {state.name: state for state in states}
        self.global_debounce_ms = global_debounce_ms
        self.renderer = renderer
        self.image_cache = image_cache
        self.tick_seconds = tick_seconds

        self._validate_states()

        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

        self._current_state_name: str | None = None
        self._current_image_name: str | None = None
        self._state_entered_at = 0.0
        self._last_cycle_at = 0.0
        self._last_render_at = 0.0

        self._pending_state_name: str | None = None
        self._pending_reason = "transition"
        self._pending_deadline = 0.0

    @classmethod
    def from_file(
        cls,
        states_json: str,
        *,
        renderer: Renderer,
        states_dir: str = DEFAULT_STATES_DIR,
        tick_seconds: float = 0.05,
    ) -> "DisplayController":
        states, _render, global_debounce_ms, _jpeg_quality = load_states(states_json)

        all_images: set[str] = set()
        for state in states:
            all_images.update(state.images)

        image_cache = _preload_images(states_dir, all_images)
        missing = sorted(all_images - set(image_cache))
        if missing:
            print(
                f"WARN: {len(missing)} images missing: {missing}",
                file=sys.stderr,
            )

        return cls(
            states=states,
            global_debounce_ms=global_debounce_ms,
            renderer=renderer,
            image_cache=image_cache,
            tick_seconds=tick_seconds,
        )

    async def __aenter__(self) -> "DisplayController":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="display-controller")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.renderer.close()

    def available_states(self) -> list[str]:
        return [state.name for state in self.states]

    def current_state(self) -> str | None:
        return self._current_state_name

    async def set_brightness(self, level: int) -> None:
        level = max(0, min(255, int(level)))
        await self.renderer.set_brightness(level)

    async def transition(
        self,
        state_name: str,
        *,
        immediate: bool = False,
        reason: str = "transition",
    ) -> bool:
        async with self._lock:
            state = self.states_by_name.get(state_name)
            if state is None:
                raise ValueError(f"Unknown state: {state_name}")
            if not self._state_images(state):
                raise ValueError(f"State has no available images on disk: {state_name}")

            if state_name == self._current_state_name:
                return False

            now = time.monotonic()

            if not immediate and self._current_state_name is not None:
                current_state = self.states_by_name[self._current_state_name]
                if current_state.min_display_ms > 0:
                    elapsed_ms = (now - self._state_entered_at) * 1000
                    if elapsed_ms < current_state.min_display_ms:
                        self._queue_pending(
                            state_name,
                            deadline=self._state_entered_at + current_state.min_display_ms / 1000,
                            reason=reason,
                        )
                        return False

            if not immediate and self._current_state_name is not None:
                current_state = self.states_by_name[self._current_state_name]
                debounce_ms = current_state.debounce_ms
                if debounce_ms is None:
                    debounce_ms = self.global_debounce_ms
                if (now - self._last_render_at) * 1000 < debounce_ms:
                    self._queue_pending(
                        state_name,
                        deadline=self._last_render_at + debounce_ms / 1000,
                        reason=reason,
                    )
                    return False

            self._clear_pending()
            await self._show_state_locked(state_name, reason=reason)
            return True

    def _validate_states(self) -> None:
        for state in self.states:
            if not state.images:
                raise ValueError(f"State has no images configured: {state.name}")
            if state.timeout_state and state.timeout_state not in self.states_by_name:
                raise ValueError(
                    f"State {state.name!r} references unknown timeout_state "
                    f"{state.timeout_state!r}"
                )

    def _state_images(self, state: State) -> list[str]:
        return [image_name for image_name in state.images if image_name in self.image_cache]

    def _queue_pending(self, state_name: str, *, deadline: float, reason: str) -> None:
        self._pending_state_name = state_name
        self._pending_deadline = deadline
        self._pending_reason = reason

    def _clear_pending(self) -> None:
        self._pending_state_name = None
        self._pending_deadline = 0.0
        self._pending_reason = "transition"

    async def _show_state_locked(self, state_name: str, *, reason: str) -> None:
        state = self.states_by_name[state_name]
        image_name = random.choice(self._state_images(state))
        await self.renderer.render(state_name, image_name, self.image_cache[image_name])

        now = time.monotonic()
        self._current_state_name = state_name
        self._current_image_name = image_name
        self._state_entered_at = now
        self._last_cycle_at = now
        self._last_render_at = now

    async def _cycle_current_state(self) -> bool:
        async with self._lock:
            if self._current_state_name is None:
                return False

            state = self.states_by_name[self._current_state_name]
            images = self._state_images(state)
            if not images:
                return False

            pool = [image_name for image_name in images if image_name != self._current_image_name]
            if not pool:
                pool = images

            image_name = random.choice(pool)
            await self.renderer.render(
                self._current_state_name,
                image_name,
                self.image_cache[image_name],
            )

            now = time.monotonic()
            self._current_image_name = image_name
            self._last_cycle_at = now
            self._last_render_at = now
            return True

    async def _service_timers(self) -> None:
        now = time.monotonic()

        pending_state_name = None
        pending_reason = "pending"
        async with self._lock:
            if self._pending_state_name is not None and now >= self._pending_deadline:
                pending_state_name = self._pending_state_name
                pending_reason = self._pending_reason

        if pending_state_name is not None:
            await self.transition(
                pending_state_name,
                immediate=True,
                reason=f"pending:{pending_reason}",
            )
            return

        current_state_name = self._current_state_name
        if current_state_name is None:
            return

        state = self.states_by_name[current_state_name]
        elapsed_ms = (now - self._state_entered_at) * 1000

        if state.timeout_ms > 0 and state.timeout_state and elapsed_ms >= state.timeout_ms:
            await self.transition(state.timeout_state, reason=f"timeout:{current_state_name}")
            return

        if state.cycle_interval_ms > 0 and (now - self._last_cycle_at) * 1000 >= state.cycle_interval_ms:
            await self._cycle_current_state()

    async def _run(self) -> None:
        try:
            while True:
                await self._service_timers()
                await asyncio.sleep(self.tick_seconds)
        except asyncio.CancelledError:
            raise
