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
AUX_SIZE = 264    # max dimension after trim+resize
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
              aux_pos: str = "bottom-left") -> Image.Image:
    """Composite face (centered) and optional aux onto 480×480 black canvas."""
    canvas = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), BLACK)

    # Center the face
    fx = (CANVAS_SIZE - face.width) // 2
    fy = (CANVAS_SIZE - face.height) // 2
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
    ("sleep.png",          "😴",   None,    None),
    ("waiting_0.png",      "😒",   None,    None),
    ("waiting_1.png",      "🙄",   None,    None),
    ("waiting_2.png",      "😑",   None,    None),
    ("waiting_3.png",      "😮‍💨", None,    None),
    ("waiting_4.png",      "😵‍💫", None,    None),
    ("thinking.png",       "🤔",   None,    None),
    ("responding.png",     "😊",   "💬",    "top-right"),
    ("done.png",           "🥳",   None,    None),
    ("error.png",          "😱",   "❌",    "bottom-left"),
    ("tool_running.png",   "😖",   "⚙️",    "bottom-left"),
    ("bash.png",           "😖",   "💻",    "bottom-left"),
    ("python.png",         "😖",   "🐍",    "bottom-left"),
    ("exec.png",           "😖",   "▶️",    "bottom-left"),
    ("read_file.png",      "🧐",   "📖",    "bottom-left"),
    ("write_file.png",     "🧐",   "✍️",    "bottom-left"),
    ("edit_file.png",      "🧐",   "✂️",    "bottom-left"),
    ("find_files.png",     "🧐",   "🔍",    "bottom-left"),
    ("ask_user.png",       "🤷",   "❓",    "bottom-left"),
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
    for fname, face, aux, aux_pos in STATES:
        outpath = os.path.join(STATES_DIR, fname)
        print(f"  {fname:24s}  face={face}  aux={aux or '-':4s}", end="")
        face_img = render_face(face)
        aux_img = render_aux(aux) if aux else None
        result = composite(face_img, aux_img, aux_pos or "bottom-left")
        result.save(outpath, "PNG")
        print(f"  → {result.size} ✓")


if __name__ == "__main__":
    main()
