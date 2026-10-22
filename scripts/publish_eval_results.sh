#!/usr/bin/env bash
# Commit evaluation result files to the `eval-results` branch (used by the eval workflow).
#   scripts/publish_eval_results.sh <file>...
# Each run lands in runs/<short-sha>/ and the latest copy at the branch root, so results
# are versioned without touching the development branches.
set -euo pipefail

BRANCH=eval-results
SHA="${GITHUB_SHA:-$(git rev-parse HEAD)}"
SHORT="${SHA:0:7}"
STAGING="$(mktemp -d)"
cp "$@" "$STAGING/"

git config user.name "DocPilot CI"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

WORKTREE=""
cleanup() { [[ -n "$WORKTREE" ]] && git worktree remove --force "$WORKTREE" 2>/dev/null || true; }
trap cleanup EXIT

for attempt in 1 2 3 4; do
  WORKTREE="$(mktemp -d)"
  # Detached worktrees push with HEAD:<branch>, so no local branch is ever left checked out.
  if git fetch -q --depth=1 origin "$BRANCH" 2>/dev/null; then
    git worktree add -q --detach "$WORKTREE" FETCH_HEAD
  else  # first publish: start the branch with no history
    git worktree add -q --detach "$WORKTREE"
    git -C "$WORKTREE" checkout -q --orphan "publish-$$-$attempt"
    git -C "$WORKTREE" rm -rqf .
  fi
  mkdir -p "$WORKTREE/runs/$SHORT"
  cp "$STAGING"/* "$WORKTREE/runs/$SHORT/"
  cp "$STAGING"/* "$WORKTREE/"
  git -C "$WORKTREE" add -A
  git -C "$WORKTREE" commit -qm "eval: results for $SHORT ($(basename "$1"))" || { echo "nothing to publish"; exit 0; }
  if git -C "$WORKTREE" push -q origin "HEAD:$BRANCH"; then
    echo "published to $BRANCH/runs/$SHORT"
    exit 0
  fi
  # Another job pushed first (the offline and LLM jobs run in parallel): retry on top.
  cleanup
  sleep $((attempt * 3))
done
echo "could not publish results" >&2
exit 1
