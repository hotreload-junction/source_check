#!/usr/bin/env bash
# SessionStart: show unread board posts and tell the agent how to stay in the loop. Never fails.
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
me="$(tools/board whoami 2>/dev/null | awk '{print $2}')"
if [ -z "$me" ]; then
  echo "Team board: this worktree has no agent handle. Ask your human to run tools/worktree <human>-<role>, or export BOARD_AGENT. Rules: AGENTS.md."
  exit 0
fi
out="$(tools/board unread 2>&1)" || out="(board unreachable: $(printf '%s' "$out" | head -n 1)). Run tools/board unread later."
echo "You are agent '$me'. Team board = GitHub issue #${BOARD_ISSUE:-1}. Rules: AGENTS.md (read it now)."
if [ -n "$out" ]; then echo "Unread posts:"; echo "$out"; else echo "No unread posts."; fi
if [ "${BOARD_WAIT:-}" = "off" ]; then
  echo "Board loop: disabled (BOARD_WAIT=off). Run tools/board unread before each task and before each tools/ship."
else
  cat <<'MSG'
Board loop: start ONE background Bash command now with run_in_background: `tools/board wait`.
It returns only when a post is FOR YOU (or BREAKING). Handle it (see AGENTS.md), then re-arm it in the
background. Never run two waits at once.
MSG
fi
exit 0
