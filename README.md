# AI Agent Persona Display

Physical emoji display for an AI agent — a tiny screen that shows what the
agent is doing through expressive emoji faces.  Runs on an ESP32-S3 with a
480×480 LCD.

## How it works

```
agent  ──FIFO──▶  display_daemon.py  ──serial──▶  ESP32  ──▶  LCD
                       │
               emoji/states/*.jpg
                       ▲
             render_states.py
                       │
               emoji/states.json
```

- **`emoji/render_states.py`** — renders 480×480 JPEG images from emoji
  definitions in `states.json`.  Uses ImageMagick pango + a Noto Color Emoji
  `.ttf` font file (loaded via a temporary fontconfig, no system installation
  needed).

- **`emoji/display_daemon.py`** — reads JSON events from a named FIFO
  (`/tmp/xi_display_fifo`), picks the corresponding state image, and sends it
  over serial to the ESP32.  Handles debouncing, min-display time, timeouts,
  transitions, and image cycling.

- **`emoji/install_hooks.py`** — installs event-forwarding hooks into the
  agent's config file so it writes events to the FIFO.

- **`emoji/states.json`** — the single source of truth: state machine
  definitions, image pools, cycle intervals, timeouts, transitions, and
  JPEG quality.

## States

Each state has a face emoji (centered) and an optional auxiliary emoji
(positioned in a corner).  Supported states include:

| State            | Face | Aux  | Meaning                                      |
|------------------|------|------|----------------------------------------------|
| `idle`           | 🙂   | —    | Waiting for input                            |
| `sleep`          | 😴🥱 | —    | Long inactivity (cycles through sleep poses) |
| `waiting`        | 😒🙄 | —    | Waiting on the agent to respond              |
| `thinking`       | 🤔   | 💭🧠💡 | Processing a request                     |
| `responding`     | 😊😮 | 💬    | Writing a response                           |
| `done`           | 🥳🤩 | —    | Task completed                               |
| `error`          | 😱   | ❌    | Something went wrong                         |
| `tool_bash`      | 😖   | 💻    | Running a shell command                      |
| `tool_python`    | 😖   | 🐍    | Running Python code                          |
| `tool_read_file` | 🧐   | 📖    | Reading a file                               |
| `tool_write_file`| 🧐   | ✍️   | Writing a file                               |
| `tool_edit_file` | 🧐   | ✂️   | Editing a file                               |
| `tool_find_files`| 🧐   | 🔍    | Searching for files                          |
| `tool_ask_user`  | 🤷🫣 | ❓    | Asking the user a question                   |
| `suspicious`     | 🤨   | —    | Something looks off                          |
| `worried`        | 😟   | —    | Getting concerned                            |
| `disappointed`   | 😞   | —    | Not great                                    |

## Getting started

### Prerequisites

- Python 3.13+, [uv](https://docs.astral.sh/uv/)
- ImageMagick
- [Noto Color Emoji](https://fonts.google.com/noto/specimen/Noto+Color+Emoji) `.ttf` file (COLRv1)
- ESP-IDF (for the ESP32 firmware)

### Install dependencies

```sh
cd emoji
uv sync
```

### Render images

```sh
cd emoji
python render_states.py --font /path/to/NotoColorEmoji-Regular.ttf
```

Images are written to `emoji/states/`.

### Start the daemon

```sh
cd emoji
python display_daemon.py --port /dev/ttyUSB0
```

Use `--dry-run` to test without a connected device.

### Configure event hooks

The daemon reads JSON events from `/tmp/xi_display_fifo`.  Your agent must
write one JSON object per line to this FIFO.  Example events:

```json
{"event": "thinking"}
{"event": "tool", "tool": "bash"}
{"event": "responding"}
{"event": "done"}
{"event": "error"}
```

Use `install_hooks.py` to automatically configure hooks for supported agents,
or set up the FIFO writer manually in your agent's event pipeline.

## ESP32 firmware

The `esp32s3_4848s040_bootstrap/` directory contains the firmware that receives
JPEG images over serial and displays them on the LCD.

```sh
. /opt/esp-idf/export.sh
idf.py build && idf.py -p /dev/ttyUSB0 flash monitor
```

See the original bootstrap project at
[larsch/esp32s3_4848s040_bootstrap](https://gitea.belunktum.dk/larsch/esp32s3_4848s040_bootstrap).
