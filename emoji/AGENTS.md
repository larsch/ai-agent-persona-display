# xi-agent Emoji Display

Animated emoji on an ESP32-S3 480×480 display that shows xi-agent's current
agent-loop state. Built with Noto Color Emoji, managed by a systemd daemon.

## Layout

```
~/prj/emoji/
├── AGENTS.md               ← this file
├── DISPLAY-SPEC.md          ← state-to-image mapping, transitions, daemon logic
├── display_controller.py    ← async state-machine controller
├── display_daemon.py        ← FIFO reader, generic state commands, serial upload
├── render_states.py         ← renders all states via ImageMagick pango (see below)
├── hooks.toml.example       ← xi hook template → writes generic state JSON to FIFO
├── xi_adapter.py            ← xi hook IPC event → generic state translator
├── xi_ipc_source.py         ← in-process xi hook IPC listener
├── claude_hook.py           ← Claude Code hook entrypoint → writes state JSON to FIFO
├── claude_adapter.py        ← Claude Code hook event → generic state translator
├── claude_settings.json.example ← Claude Code settings.json hooks template
├── install_claude_hooks.py  ← merge hooks into ~/.claude/settings.json (idempotent)
├── claude-display.service   ← systemd user unit (daemon with --source claude)
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
└── upload_image.py          ← serial image uploader (used by renderer)
```

## How it runs

**Daemon** (starts automatically with your graphical session):

```
systemctl --user status xi-display
```

It listens on `/tmp/xi_display_fifo` for generic display-state transition
messages, preloads JPEGs at startup, enforces debounce / min-display / timeout
behavior from `states.json`, and uploads via serial.

**Hooks / IPC adapters** (wired into xi-agent's agent loop):

Xi can drive the display either by writing direct state JSON such as
`{"state":"waiting"}` / `{"state":"tool_bash"}` to the FIFO, or through
hook IPC handled directly inside `display_daemon.py`, which translates xi IPC
events into those same generic state commands.

Hook config is in `~/.config/xi/config.toml`.

**Claude Code** (wired via `settings.json` hooks):

Claude Code has no persistent IPC — it fires one-shot hook commands and pipes
the event payload as JSON on stdin. So it uses the FIFO path directly: every
hook event runs `claude_hook.py`, which reads the payload, asks
`claude_adapter.py` for a state, and writes `{"state":"…"}` to the same daemon
FIFO. Run the daemon with `--source claude` (an alias for `--source fifo`,
self-documenting which agent is driving it).

Install (merges into `~/.claude/settings.json`, idempotent, backs up first):

```
python3 install_claude_hooks.py
```

Pass `--settings .claude/settings.json` to scope it to a single project instead.
The full mapping (which event/tool → which state) lives in `claude_adapter.py`;
`test_claude_adapter.py` checks it (`python3 test_claude_adapter.py`).

Run the daemon under systemd with the bundled unit:

```
cp claude-display.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now claude-display
```

(Adjust `--port`/`--baud` in the unit to match your board. Don't run
`claude-display` and `xi-display` at once — they'd both drive the display.)

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
3. Final composite: 480×480 RGB (black background) → saved as JPEG.

To add or change a state, edit `states.json`, re-run the renderer, then
restart the daemon so it reloads the state machine and image cache:

```
systemctl --user restart xi-display
```

## How to add a hook point

1. Add variant to `HookPoint` enum in `~/prj/xi-agent/src/hooks.rs`
2. Add to the `Display` impl (snake_case name)
3. Call `maybe_run_hook(...)` at the right spot in `src/agent/mod.rs`
4. Add a matching hook entry that writes `{"state":"..."}` to the FIFO
5. `cargo build --release` in xi-agent
6. Restart xi-agent
