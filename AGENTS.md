# xi-agent Emoji Display

Animated emoji on an ESP32-S3 480×480 display that shows xi-agent's current
agent-loop state. Built with Noto Color Emoji, managed by a systemd daemon.

## Layout

```
~/prj/emoji/
├── AGENTS.md               ← this file
├── DISPLAY-SPEC.md          ← state-to-image mapping, transitions, daemon logic
├── display_daemon.py        ← FIFO reader, debounce, JPEG pre-encode, serial upload
├── render_states.py         ← renders all states via ImageMagick pango (see below)
├── hook_display.sh          ← xi-agent hook → writes JSON line to FIFO
├── states/                  ← 480×480 emoji PNGs (face ± aux icon)
│   ├── idle.png                🙂
│   ├── sleep.png               😴
│   ├── waiting_0.png           😒
│   ├── waiting_1.png           🙄
│   ├── waiting_2.png           😑
│   ├── waiting_3.png           😮‍💨
│   ├── waiting_4.png           😵‍💫
│   ├── thinking.png            🤔
│   ├── responding.png          😊 + 💬 (top-right)
│   ├── done.png                🥳
│   ├── error.png               😱 + ❌
│   ├── tool_running.png        😖 + ⚙️
│   ├── bash.png                😖 + 💻
│   ├── python.png              😖 + 🐍
│   ├── exec.png                😖 + ▶️
│   ├── read_file.png           🧐 + 📖
│   ├── write_file.png          🧐 + ✍️
│   ├── edit_file.png           🧐 + ✂️
│   ├── find_files.png          🧐 + 🔍
│   ├── ask_user.png            🤷 + ❓
│   ├── compacting.png          😫 + 🗜️
│   ├── external_change.png     😲 + 👀
│   ├── status_update.png       😫 + 🚦 (rate-limited)
│   ├── step_back.png           🧐 + ⏪
│   ├── login.png               😐 + 🔑
│   └── shell_mode.png          😎 + 🐚
└── emoji.png                ← original test render (grinning face)

~/prj/xi-agent/
└── src/
    ├── hooks.rs             ← HookPoint enum (OnIdle, etc.)
    └── agent/
        └── mod.rs           ← hook firing (OnIdle after OnDone)

~/.config/xi/config.toml     ← [[hooks.*]] entries
~/.config/systemd/user/xi-display.service  ← systemd user service

~/prj/esp32s3_4848s040_bootstrap/
└── upload_image.py          ← serial image uploader (used by daemon)
```

## How it runs

**Daemon** (starts automatically with your graphical session):

```
systemctl --user status xi-display
```

It listens on `/tmp/xi_display_fifo` for JSON events, pre-encodes all images
to JPEG at startup, debounces duplicate event types, and uploads via serial.

**Hooks** (wired into xi-agent's agent loop):

Each hook calls `hook_display.sh`, which writes one JSON line to the FIFO.
Example: `{"event":"waiting"}` or `{"event":"tool","tool":"bash"}`.

Hook config is in `~/.config/xi/config.toml`.

## How to render new images

**Font requirement:** Uses Noto Color Emoji COLRv1 (true vector), not the bitmap
CBDT/CBLC version shipped by some distros. Install it once:

```
curl -sL "https://github.com/googlefonts/noto-emoji/raw/main/fonts/Noto-COLRv1.ttf" \
  -o ~/.local/share/fonts/Noto-COLRv1.ttf
fc-cache -f ~/.local/share/fonts/
```

Then render all states:

```
cd ~/prj/emoji && python3 render_states.py
```

How each image is made:

1. ImageMagick `pango:` renders the emoji glyph at `size='280000'` (face,
   trimmed → resized to 320×320 max) or `size='135000'` (aux → 264×264 max).
2. Face is centered; aux is composited at `bottom-left` or `top-right`
   (20 px padding).
3. Final composite: 480×480 RGB (black background) → saved as PNG.

To add or change a state, edit the `STATES` list in `render_states.py`,
re-run the script, then restart the daemon so it re-pre-encodes:

```
systemctl --user restart xi-display
```

## How to add a hook point

1. Add variant to `HookPoint` enum in `~/prj/xi-agent/src/hooks.rs`
2. Add to the `Display` impl (snake_case name)
3. Call `maybe_run_hook(...)` at the right spot in `src/agent/mod.rs`
4. Add to the case statement in `hook_display.sh`
5. Add `[[hooks.<point>]]` to `~/.config/xi/config.toml`
6. `cargo build --release` in xi-agent
7. Restart xi-agent
