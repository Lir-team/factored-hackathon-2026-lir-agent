#!/usr/bin/env sh
# PreToolUse hook: denies any Bash command that pushes to main.
cmd=$(jq -r '.tool_input.command // ""')
case "$cmd" in
  *"git push"*|*"rtk git push"*) ;;
  *) exit 0 ;;
esac
branch=$(git symbolic-ref --short HEAD 2>/dev/null)
if printf '%s' "$cmd" | rg -q '\bmain\b' || [ "$branch" = "main" ]; then
  jq -n '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:"Pushing to main is not allowed. Create a feature branch and open a PR."}}'
  exit 0
fi
exit 0
