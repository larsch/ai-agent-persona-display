#!/bin/bash
# xi-agent hook: write display event to daemon FIFO (fire-and-forget, ~0ms).
# The display_daemon.py handles debounce, priority, idle timeout, and JPEG upload.

set -euo pipefail

FIFO="/tmp/xi_display_fifo"

# ── Map hook point → event type ───────────────────────────────────────────────
t=unknown
case "${XI_HOOK_POINT:-}" in
    pre_turn)                   t=waiting ;;
    on_first_thinking_token)    t=thinking ;;
    on_first_text_token)        t=responding ;;
    post_turn)                  t=waiting ;;  # treated as low-priority, debounced
    on_done)                    t=done ;;
    on_idle)                    t=idle ;;
    on_error)                   t=error ;;
    on_compacting)              t=compacting ;;
    on_external_change)         t=external_change ;;
    on_status_update)           t=status_update ;;
    pre_tool)                   ;;
    *)                          exit 0 ;;
esac

# ── pre_tool: extract tool name from stdin JSON ───────────────────────────────
if [ "${XI_HOOK_POINT:-}" = "pre_tool" ]; then
    TOOL=$(jq -r '.tool // empty' 2>/dev/null || echo "")
    if [ -n "$TOOL" ]; then
        # Non-blocking write: times out if daemon isn't running
        timeout 0.5 bash -c "printf '{\"event\":\"tool\",\"tool\":\"%s\"}\n' \"\$1\" >> \"\$2\"" _ "$TOOL" "$FIFO" 2>/dev/null || true
    fi
    exit 0
fi

# ── Write event to FIFO ───────────────────────────────────────────────────────
if [ "$t" != "unknown" ]; then
    timeout 0.5 bash -c "printf '{\"event\":\"%s\"}\n' \"\$1\" >> \"\$2\"" _ "$t" "$FIFO" 2>/dev/null || true
fi
