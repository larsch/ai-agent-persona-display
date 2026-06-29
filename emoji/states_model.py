"""State machine data model for the emoji display.

Shared between render_states.py, display_daemon.py, and the ESP32 firmware.
"""

from dataclasses import dataclass, asdict
import json


@dataclass
class State:
    name: str
    images: list[str]
    cycle_interval_ms: int = 0
    debounce_ms: int | None = None       # null = use global default
    min_display_ms: int = 0
    timeout_ms: int = 0                  # 0 = no timeout
    timeout_state: str | None = None     # null = no transition


def load_states(path: str) -> tuple[list[State], dict | None, int, int]:
    """Load states.json.

    Returns (states, render_dict, global_debounce_ms, jpeg_quality).
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    states = [State(**s) for s in data["states"]]
    render = data.get("render")
    debounce_ms = data.get("debounce_ms", 400)
    jpeg_quality = data.get("jpeg_quality", 50)

    return states, render, debounce_ms, jpeg_quality


def save_states(path: str, states: list[State], render: dict | None,
                debounce_ms: int, jpeg_quality: int) -> None:
    """Save states.json, round-tripping all fields."""
    data: dict = {
        "debounce_ms": debounce_ms,
        "jpeg_quality": jpeg_quality,
        "states": [asdict(s) for s in states],
    }
    if render is not None:
        data["render"] = render
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
