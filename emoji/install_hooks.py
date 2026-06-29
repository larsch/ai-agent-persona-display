#!/usr/bin/env python3
"""Install xi hooks that emit generic display-state transitions.

Idempotent — running it again won't add duplicates.
Does not change any existing config entries.
"""

import os
import sys

CONFIG_PATH = os.path.expanduser("~/.config/xi/config.toml")
HOOKS_TEMPLATE = os.path.join(os.path.dirname(__file__), "hooks.toml.example")
MARKER = "/tmp/xi_display_fifo"
SECTION_HEADER = "[hooks]"


def main():
    if not os.path.exists(CONFIG_PATH):
        print(f"ERROR: {CONFIG_PATH} not found", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(HOOKS_TEMPLATE):
        print(f"ERROR: {HOOKS_TEMPLATE} not found", file=sys.stderr)
        sys.exit(1)

    with open(CONFIG_PATH) as f:
        config_text = f.read()

    if MARKER in config_text:
        print("Display hooks already installed — nothing to do.")
        return

    with open(HOOKS_TEMPLATE) as f:
        template_text = f.read()

    # Strip leading/trailing blank lines from template
    template_text = template_text.strip("\n") + "\n"

    # Remove the comment header and the leading blank line after it
    # (comments are lines starting with #, plus blank lines between them)
    lines = template_text.split("\n")
    while lines and (lines[0].startswith("#") or lines[0].strip() == ""):
        lines.pop(0)
    template_text = "\n".join(lines) + "\n"

    lines = config_text.split("\n")

    # Find insertion point: after [hooks] section, or at EOF
    hook_section_idx = None
    for i, line in enumerate(lines):
        if line.strip() == SECTION_HEADER:
            hook_section_idx = i
            break

    if hook_section_idx is not None:
        # Find the end of the hooks section (next section header or EOF)
        insert_idx = len(lines)
        for i in range(hook_section_idx + 1, len(lines)):
            stripped = lines[i].strip()
            if stripped.startswith("[") and not stripped.startswith("[[hooks."):
                insert_idx = i
                break

        # Insert after the hooks section, with a blank line separator
        if insert_idx == len(lines):
            lines.append("")
        else:
            lines.insert(insert_idx, "")
            insert_idx += 1

        # Split template into lines and insert
        template_lines = template_text.split("\n")
        for j, tl in enumerate(template_lines):
            lines.insert(insert_idx + j, tl)

        # If original file didn't end with newline, we added one — trim trailing blank
        if lines and lines[-1] == "" and not config_text.endswith("\n"):
            pass  # keep it
    else:
        # No [hooks] section — append at end
        if config_text and not config_text.endswith("\n"):
            lines.append("")
        lines.append("")
        lines.append(SECTION_HEADER)
        lines.extend(template_text.split("\n"))

    new_text = "\n".join(lines)
    if not new_text.endswith("\n"):
        new_text += "\n"

    # Write back
    with open(CONFIG_PATH, "w") as f:
        f.write(new_text)

    print(f"Installed {len([l for l in template_text.split('\n') if l.startswith('[[')])} hook entries into {CONFIG_PATH}")


if __name__ == "__main__":
    main()
