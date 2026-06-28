#!/usr/bin/env python3
"""Render emoji state images using ImageMagick pango + Noto Color Emoji font.

Reads states.json for the list of images to render, plus JPEG quality.
Images without a "render" entry are skipped (sourced externally).

Uses a temporary fontconfig to load the font from a file path rather than
relying on system-wide font installation. The font family name is detected
automatically from the .ttf file.

Requires: ImageMagick, Pillow, Noto Color Emoji .ttf file
"""

import argparse
import os
import subprocess
import sys
import tempfile
from PIL import Image

from states_model import load_states

DEFAULT_FONT = os.path.expanduser("~/.local/share/fonts/Noto-COLRv1.ttf")
STATES_DIR = os.path.join(os.path.dirname(__file__), "states")
CANVAS_SIZE = 480
FACE_SIZE = 320   # max dimension after trim+resize
AUX_SIZE = 150    # max dimension after trim+resize
BLACK = (0, 0, 0, 255)

# Face emoji pango size (points * PANGO_SCALE / 1024)
FACE_PANGO = 280000
AUX_PANGO = 135000

# Cached font family name and temporary fontconfig path (keyed by font path)
_font_cache: dict[str, tuple[str, str]] = {}


def _detect_family(font_path: str) -> str:
    """Return the font family name from the .ttf file using fc-scan."""
    result = subprocess.run(
        ["fc-scan", "--format=%{family}", font_path],
        check=True, capture_output=True, text=True,
    )
    # fc-scan may return comma-separated families; take the first.
    return result.stdout.strip().split(",")[0]


def _setup_fontconfig(font_path: str) -> str:
    """Create a temporary fontconfig XML that points to the font's directory.

    Returns the path to the temp config file.
    """
    font_dir = os.path.dirname(os.path.abspath(font_path))
    xml = f'<?xml version="1.0"?>\n' \
          f'<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n' \
          f'<fontconfig><dir>{font_dir}</dir></fontconfig>\n'

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".conf", prefix="fc_", delete=False,
    )
    tmp.write(xml)
    tmp.close()
    return tmp.name


def _get_font_info(font_path: str) -> tuple[str, str]:
    """Get (family_name, fontconfig_path) for a font, caching results."""
    if font_path not in _font_cache:
        family = _detect_family(font_path)
        fc_config = _setup_fontconfig(font_path)
        _font_cache[font_path] = (family, fc_config)
    return _font_cache[font_path]


def render_pango(emoji: str, size: int, font_path: str) -> Image.Image:
    """Render a single emoji via ImageMagick pango, return PIL Image (RGBA).

    Loads the font from *font_path* by setting FONTCONFIG_FILE to a temporary
    config that includes only the font's directory.
    """
    family, fc_config = _get_font_info(font_path)
    markup = f'<span font="{family}" size="{size}">{emoji}</span>'
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmpname = tmp.name
    try:
        subprocess.run(
            ["magick", "-background", "none", f"pango:{markup}", tmpname],
            check=True, capture_output=True,
            env={**os.environ, "FONTCONFIG_FILE": fc_config},
        )
        return Image.open(tmpname).convert("RGBA")
    finally:
        os.unlink(tmpname)


def trim_resize(img: Image.Image, max_size: int) -> Image.Image:
    """Trim transparent borders, then fit into max_size×max_size preserving aspect ratio."""
    bbox = img.getbbox()
    if bbox is None:
        return img
    img = img.crop(bbox)
    img.thumbnail((max_size, max_size), Image.LANCZOS)
    return img


def render_face(emoji: str, font_path: str) -> Image.Image:
    """Render face emoji, trim, and resize."""
    raw = render_pango(emoji, FACE_PANGO, font_path)
    return trim_resize(raw, FACE_SIZE)


def render_aux(emoji: str, font_path: str) -> Image.Image:
    """Render auxiliary emoji, trim, and resize."""
    raw = render_pango(emoji, AUX_PANGO, font_path)
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


def main():
    parser = argparse.ArgumentParser(description="Render emoji state images")
    parser.add_argument("--states-json", default=os.path.join(
        os.path.dirname(__file__), "states.json"),
        help="Path to states.json")
    parser.add_argument("--out-dir", default=STATES_DIR,
        help="Output directory")
    parser.add_argument("--quality", type=int, default=None,
        help="Override JPEG quality from states.json")
    parser.add_argument("--font", default=DEFAULT_FONT,
        help=f"Path to Noto COLRv1 .ttf font (default: {DEFAULT_FONT})")
    args = parser.parse_args()

    if not os.path.exists(args.font):
        print(f"Font not found: {args.font}", file=sys.stderr)
        sys.exit(1)

    states, render, _debounce, jpeg_quality = load_states(args.states_json)
    quality = args.quality if args.quality is not None else jpeg_quality

    if not render:
        print("No 'render' section in states.json — nothing to render.")
        sys.exit(0)

    os.makedirs(args.out_dir, exist_ok=True)

    for fname, params in render.items():
        face = params["face"]
        aux = params.get("aux")
        aux_pos = params.get("aux_pos", "bottom-left")
        face_scale = params.get("face_scale", 1.0)
        face_offset_x = params.get("face_offset_x", 0)

        outpath = os.path.join(args.out_dir, fname)
        print(f"  {fname:24s}  face={face}  aux={aux or '-':4s}", end="")

        face_img = render_face(face, args.font)
        aux_img = render_aux(aux, args.font) if aux else None
        result = composite(face_img, aux_img, aux_pos,
                           face_scale=face_scale,
                           face_offset_x=face_offset_x)

        # Save as JPEG
        result.save(outpath, "JPEG", quality=quality)
        print(f"  → {result.size} q={quality} ✓")


if __name__ == "__main__":
    main()
