# xi-agent Display Spec

## Overview

Each state of the agent loop is represented by an emoji on the ESP32 display.
The primary expression is a face emoji (centered, ~2/3 canvas). When the face
alone cannot convey what the agent is doing, a smaller auxiliary icon is added
bottom-left (264×264). The speech bubble (responding) is top-right.

## States

### Idle chain

| # | State | Hook | Face | Aux |
|---|-------|------|------|-----|
| 1 | **idle** | `on_idle` | 🙂 | — |
| 2 | **sleep** | (30s after idle) | 🥱😮‍💨😑😌😪😴 | — | Cycles randomly every 10s (never repeats consecutively). |

No hook for sleep — daemon auto-transitions 30s after idle.
No hook for done→idle — daemon auto-transitions 10s after done.

Sleep displays a randomly-selected face from the pool (🥱 😮‍💨 😑 😌 😪 😴 😌),
changing to a different image every 10 seconds.

### Stuck detection

| # | State | Trigger | Face | Meaning |
|---|-------|---------|------|---------|
| — | **suspicious** | (15s in any active state) | 🤨 | Agent hasn't changed state in 15s. |
| — | **worried** | (another 15s after suspicious) | 😟 | Still stuck after 30s total. |
| — | **disappointed** | (another 15s after worried) | 😞 | Still stuck after 45s total. |
| — | (→ sleep) | (another 15s after disappointed) | 😴 | Gave up after 60s. |

Active states = any state except idle, sleep, done, suspicious, worried, or disappointed.
Any state change (FIFO event) resets the timer.

### Turn lifecycle

| # | State | Hook | Face | Aux | Meaning |
|---|-------|------|------|-----|---------|
| 3 | **waiting** | `pre_turn` | 😒🙄😑😮‍💨😵‍💫 | — | Request sent, awaiting first token. Face chosen randomly each turn. |
| 4 | **thinking** | `on_first_thinking_token` | 🤔😖🤫 | — | Model is producing chain-of-thought / reasoning. Face and aux cycle randomly (cross-product of 3 faces × 4 aux). |
| 5 | **responding** | `on_first_text_token` | 😮😯😲😦😧 | 💬 | Model is streaming visible output. Face cycles randomly every 1s (never repeats consecutively). |

### Tool execution

| # | State | Hook | Face | Aux |
|---|-------|------|------|-----|
| 6 | **tool (generic)** | `pre_tool` (unrecognised) | 😖 | ⚙️ |
| 7 | **bash** | `pre_tool` (`bash`) | 😖 | 💻 |
| 8 | **python** | `pre_tool` (`python`) | 😖 | 🐍 |
| 9 | **exec** | `pre_tool` (`exec`) | 😖 | ▶️ |
|10 | **read_file** | `pre_tool` (`read_file`) | 🧐 | 📖 |
|11 | **write_file** | `pre_tool` (`write_file`) | 🧐 | ✍️ |
|12 | **edit_file** | `pre_tool` (`edit_file`) | 🧐 | ✂️ |
|13 | **find_files** | `pre_tool` (`find_files`) | 🧐 | 🔍 |
|14 | **ask_user** | `pre_tool` (`ask_user`) | 🤷 | ❓ |

### Meta / lifecycle

| # | State | Hook | Face | Aux | Meaning |
|---|-------|------|------|-----|---------|
|15 | **compacting** | `on_compacting` | 😫 | 🗜️ | Session compaction in progress. |
|16 | **external_change** | `on_external_change` | 😲 | 👀 | Files modified outside the agent. |
|17 | **status_update** | `on_status_update` | 😫 | 🚦 | Provider status (rate-limit, retry). |

### Terminal

| # | State | Hook | Face | Aux |
|---|-------|------|------|-----|
|18 | **done** | `on_done` | 🥳 | — |
|19 | **error** | `on_error` | 😱 | ❌ |

## Hook-to-state mapping

| Hook point | State name | Image |
|---|---|---|
| `pre_turn` | `waiting` | `waiting_N.png` (random 0–4) |
| `on_first_thinking_token` | `thinking` | `thinking.png` |
| `on_first_text_token` | `responding` | `responding.png` |
| `pre_tool` | `tool_<name>` | `{tool}.png` |
| `on_compacting` | `compacting` | `compacting.png` |
| `on_external_change` | `external_change` | `external_change.png` |
| `on_status_update` | `status_update` | `status_update.png` |
| `on_done` | `done` | `done.png` |
| `on_idle` | `idle` | `idle.png` |
| `on_error` | `error` | `error.png` |

## Transition diagram

```
 IDLE ──pre_turn──▶ WAITING ──on_first_thinking_token──▶ THINKING
  ▲                                                    │
  │                                          on_first_text_token
  │                                                    ▼
  │                                               RESPONDING
  │                                                    │
  │                                          ┌─pre_tool─┴───┐
  │                                          ▼               ▼
  │                                        TOOL ────────── TOOL
  │                                          │               │
  │                               (more tools? loop to TOOL)
  │                                          │
  │                                    on_done ▼
  │                                        DONE
  │                                         │ (10s timeout)
  │                                   on_idle ▼
  └───────────────────────────────────── IDLE
                                           │ (30s no events)
                                           ▼
                                         SLEEP
```

On `on_error`, any state → ERROR (terminal, no transition out).

Any active state ──(15s stuck)──▶ SUSPICIOUS ──(15s)──▶ WORRIED ──(15s)──▶ DISAPPOINTED ──(15s)──▶ SLEEP
Either resets on any FIFO event.

## Daemon logic

- The daemon accepts generic state transition commands via named FIFO (`/tmp/xi_display_fifo`).
- Preferred input shape is `{"state":"thinking"}`; compatibility aliases may also be accepted.
- 400ms global debounce between uploads unless overridden per state in `states.json`.
- Debounce is defined on the target state: a state is not shown until its `debounce_ms` has elapsed since the last actual display change, and if another transition arrives first the pending state is replaced. If a state does not set `debounce_ms`, the 400ms global debounce applies.
- `min_display_ms`, `timeout_ms`, `timeout_state`, and `cycle_interval_ms` are enforced from `states.json`.
- Sleep, done, suspicious-chain, and other timing behavior are therefore config-driven rather than hardcoded.
- Agent-specific adapters are responsible for translating native events into configured state names.
- For xi, `xi_adapter.py` maps hook IPC points like `pre_turn` or `pre_tool` into those configured state names.

## Files

| File | Role |
|---|---|
| `~/prj/emoji/states/*.png` | 480×480 emoji images |
| `~/prj/emoji/display_controller.py` | Async state machine: debounce, timeout, cycling |
| `~/prj/emoji/display_daemon.py` | Single-process daemon: FIFO and/or xi IPC input, state transitions, serial upload |
| `~/prj/emoji/hooks.toml.example` | Xi hook template: writes generic state JSON to FIFO |
| `~/prj/emoji/xi_ipc_source.py` | In-process xi hook IPC listener |
| `~/.config/xi/config.toml` | `[[hooks.*]]` entries |
| `~/.config/systemd/user/xi-display.service` | systemd user service |
