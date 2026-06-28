#!/usr/bin/env python3
"""Render emoji state images using ImageMagick pango + Noto Color Emoji COLRv1.

Requires: ImageMagick, Pillow, Noto-COLRv1.ttf installed to ~/.local/share/fonts/
"""

import subprocess
import tempfile
import os
from PIL import Image

STATES_DIR = os.path.expanduser("~/prj/emoji/states")
CANVAS_SIZE = 480
FACE_SIZE = 320   # max dimension after trim+resize
AUX_SIZE = 150    # max dimension after trim+resize
BLACK = (0, 0, 0, 255)

# Face emoji pango size (points * PANGO_SCALE / 1024)
FACE_PANGO = 280000
AUX_PANGO = 135000


def render_pango(emoji: str, size: int) -> Image.Image:
    """Render a single emoji via ImageMagick pango, return PIL Image (RGBA)."""
    markup = f'<span font="Noto Color Emoji" size="{size}">{emoji}</span>'
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmpname = tmp.name
    try:
        subprocess.run(
            ["magick", "-background", "none", f"pango:{markup}", tmpname],
            check=True, capture_output=True,
        )
        return Image.open(tmpname).convert("RGBA")
    finally:
        os.unlink(tmpname)


def trim_resize(img: Image.Image, max_size: int) -> Image.Image:
    """Trim transparent borders, then fit into max_size×max_size preserving aspect ratio."""
    # Crop to bounding box of non-transparent pixels
    bbox = img.getbbox()
    if bbox is None:
        return img
    img = img.crop(bbox)
    # Resize to fit max_size, maintaining aspect ratio
    img.thumbnail((max_size, max_size), Image.LANCZOS)
    return img


def render_face(emoji: str) -> Image.Image:
    """Render face emoji, trim, and resize."""
    raw = render_pango(emoji, FACE_PANGO)
    return trim_resize(raw, FACE_SIZE)


def render_aux(emoji: str) -> Image.Image:
    """Render auxiliary emoji, trim, and resize."""
    raw = render_pango(emoji, AUX_PANGO)
    return trim_resize(raw, AUX_SIZE)


def composite(face: Image.Image, aux: Image.Image | None,
              aux_pos: str = "bottom-left",
              face_scale: float = 1.0, face_offset_x: int = 0) -> Image.Image:
    """Composite face (centered) and optional aux onto 480×480 black canvas.

    face_scale: optional multiplier for face size (e.g. 1.2 = 20% bigger).
    face_offset_x: optional horizontal pixel shift (positive = right).
    """
    canvas = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), BLACK)

    # Remember original height for bottom-anchored scaling
    orig_h = face.height

    # Apply scale to face if needed
    if face_scale != 1.0:
        new_w = int(face.width * face_scale)
        new_h = int(face.height * face_scale)
        face = face.resize((new_w, new_h), Image.LANCZOS)

    # Center the face (with optional horizontal offset)
    fx = (CANVAS_SIZE - face.width) // 2 + face_offset_x
    # Bottom-anchored vertical placement: the original bottom edge stays fixed,
    # so scaling only expands upward (and left/right).
    orig_fy = (CANVAS_SIZE - orig_h) // 2
    orig_bottom = orig_fy + orig_h
    fy = orig_bottom - face.height
    canvas.paste(face, (fx, fy), face)

    if aux is not None:
        if aux_pos == "top-right":
            ax = CANVAS_SIZE - aux.width - 20
            ay = 20
        elif aux_pos == "bottom-left":
            ax = 20
            ay = CANVAS_SIZE - aux.height - 20
        else:
            ax = 20
            ay = CANVAS_SIZE - aux.height - 20
        canvas.paste(aux, (ax, ay), aux)

    # Flatten to RGB (black background, no transparency needed for JPEG)
    rgb = Image.new("RGB", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0))
    rgb.paste(canvas, mask=canvas.split()[-1])
    return rgb


# ── State definitions ────────────────────────────────────────────────────

STATES = [
    # (filename, face_emoji, aux_emoji, aux_position)
    ("idle.png",           "🙂",   None,    None),
    ("sleep.png",          "😴",   None,    None),  # legacy, kept for compat
    # Sleep cycle pool (cycled randomly every 10s while sleeping)
    ("sleep_0.png",        "🥱",   None,    None),  # yawning face
    ("sleep_1.png",        "😮‍💨", None,    None),  # face exhaling
    ("sleep_2.png",        "😑",   None,    None),  # expressionless
    ("sleep_3.png",        "😌",   None,    None),  # relieved
    ("sleep_4.png",        "😪",   None,    None),  # sleepy face
    ("sleep_5.png",        "😴",   None,    None),  # sleeping face
    ("sleep_6.png",        "😌",   None,    None),  # relieved (duplicate)
    ("waiting_0.png",      "😒",   None,    None),
    ("waiting_1.png",      "🙄",   None,    None),
    ("waiting_2.png",      "😑",   None,    None),
    ("waiting_3.png",      "😮‍💨", None,    None),
    ("waiting_4.png",      "😵‍💫", None,    None),
    ("thinking.png",       "🤔",   "💭",    "top-right"),  # legacy
    # Thinking cycle pool (cycled every 5s while reasoning)
    ("thinking_0.png",     "🤔",   "💭",    "top-right"),
    ("thinking_1.png",     "🤔",   "🧠",    "top-right"),
    ("thinking_2.png",     "🤔",   "💡",    "top-right"),
    ("thinking_3.png",     "😕",   "💭",    "top-right"),
    ("thinking_4.png",     "🤯",   None,    None),
    ("responding.png",     "😊",   "💬",    "top-right"),  # legacy, kept for compat
    # Responding cycle pool (cycled every 1s while speaking)
    ("responding_0.png",   "😮",   "💬",    "top-right"),
    ("responding_1.png",   "😯",   "💬",    "top-right"),
    ("responding_2.png",   "😲",   "💬",    "top-right"),
    ("responding_3.png",   "😦",   "💬",    "top-right"),
    ("responding_4.png",   "😧",   "💬",    "top-right"),
    ("done.png",           "🥳",   None,    None,     {"face_scale": 1.2, "face_offset_x": 16}),  # legacy
    # Done celebration pool (cycled every 1s while celebrating)
    ("done_0.png",         "🥳",   None,    None,     {"face_scale": 1.2, "face_offset_x": 16}),
    ("done_1.png",         "🤩",   None,    None),
    ("done_2.png",         "😎",   None,    None),
    ("error.png",          "😱",   "❌",    "bottom-left"),
    ("tool_running.png",   "😖",   "⚙️",    "bottom-left"),
    ("bash.png",           "😖",   "💻",    "bottom-left"),
    ("python.png",         "😖",   "🐍",    "bottom-left"),
    ("exec.png",           "😖",   "▶️",    "bottom-left"),
    ("read_file.png",      "🧐",   "📖",    "bottom-left"),
    ("write_file.png",     "🧐",   "✍️",    "bottom-left"),
    ("edit_file.png",      "🧐",   "✂️",    "bottom-left"),
    ("find_files.png",     "🧐",   "🔍",    "bottom-left"),
    ("ask_user.png",       "🤷",   "❓",    "bottom-left"),  # legacy
    # Ask-user pool (cycled every 5s while waiting for user input)
    ("ask_user_0.png",     "🫣",   "❓",    "top-right"),
    ("ask_user_1.png",     "😕",   "❓",    "top-right"),
    ("ask_user_2.png",     "🥺",   "❓",    "top-right"),
    ("ask_user_3.png",     "😐",   "❓",    "top-right"),
    ("compacting.png",     "😫",   "🗜️",    "bottom-left"),
    ("external_change.png","😲",   "👀",    "bottom-left"),
    ("status_update.png",  "😫",   "🚦",    "bottom-left"),
    ("suspicious.png",     "🤨",   None,    None),
    ("worried.png",        "😟",   None,    None),
    ("disappointed.png",   "😞",   None,    None),
    # Legacy / extra states with no hook yet (kept for compatibility)
    ("step_back.png",      "🧐",   "⏪",    "bottom-left"),
    ("shell_mode.png",     "😎",   "🐚",    "bottom-left"),
    ("login.png",          "😐",   "🔑",    "bottom-left"),
    ("rate_limited.png",   "😫",   "🚦",    "bottom-left"),
    ("turn_end.png",       "😊",   None,    None),
    ("waiting.png",        "😒",   None,    None),  # generic waiting
]


def main():
    os.makedirs(STATES_DIR, exist_ok=True)
    for entry in STATES:
        fname, face, aux = entry[0], entry[1], entry[2]
        aux_pos = entry[3] if len(entry) > 3 else None
        overrides = entry[4] if len(entry) > 4 else {}
        outpath = os.path.join(STATES_DIR, fname)
        print(f"  {fname:24s}  face={face}  aux={aux or '-':4s}", end="")
        face_img = render_face(face)
        aux_img = render_aux(aux) if aux else None
        result = composite(face_img, aux_img, aux_pos or "bottom-left",
                           face_scale=overrides.get("face_scale", 1.0),
                           face_offset_x=overrides.get("face_offset_x", 0))
        result.save(outpath, "PNG")
        print(f"  → {result.size} ✓")


if __name__ == "__main__":
    main()
