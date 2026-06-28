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
No hook for done→idle — daemon auto-transitions 5s after done.

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
| 4 | **thinking** | `on_first_thinking_token` | 🤔 | — | Model is producing chain-of-thought / reasoning. |
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

## Hook-to-event mapping

| Hook point | Event type | Image |
|---|---|---|
| `pre_turn` | `waiting` | `waiting_N.png` (random 0–4) |
| `on_first_thinking_token` | `thinking` | `thinking.png` |
| `on_first_text_token` | `responding` | `responding.png` |
| `pre_tool` | `tool` | `{tool}.png` |
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
  │                                         │ (5s timeout)
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

- All events received via named FIFO (`/tmp/xi_display_fifo`).
- Events debounced by event type: same event type cannot show twice consecutively.
- 400ms global debounce between any two uploads.
- Sleep auto-triggers 30s after `idle` event if no other events arrive.
- Done auto-transitions to idle 5s after the `done` event if no other events arrive.
- Any active state (not done, idle, sleep, suspicious, worried, disappointed) for 15s → suspicious (🤨).
- Suspicious for 15s → worried (😟), 15s more → disappointed (😞), 15s more → sleep (😴).
  Any FIFO event during the chain resets the timer.
- Any non-idle/non-sleep event during sleep wakes the display.
- Sleep shows a randomly-chosen face from the pool (🥱 😮‍💨 😑 😌 😪 😴 😌);
  cycles to a different image every 10s. Wakes immediately on any event.
- Responding cycles randomly through the pool (😮 😯 😲 😦 😧 + 💬) every 1s;
  never repeats consecutively. Any new FIFO event stops the cycle.

## Files

| File | Role |
|---|---|
| `~/prj/emoji/states/*.png` | 480×480 emoji images |
| `~/prj/emoji/display_daemon.py` | Daemon: FIFO reader, debounce, serial upload |
| `~/prj/emoji/hook_display.sh` | Hook dispatcher: writes JSON to FIFO |
| `~/.config/xi/config.toml` | `[[hooks.*]]` entries |
| `~/.config/systemd/user/xi-display.service` | systemd user service |
