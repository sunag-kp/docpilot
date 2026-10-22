#!/usr/bin/env bash
# One-time repository settings that need admin rights (run once from your machine):
#   gh auth login && ./scripts/github_setup.sh
# Sets description, homepage, topics, branch protection on main and GitHub Pages.
set -euo pipefail

REPO="${REPO:-sunag-kp/docpilot}"

gh repo edit "$REPO" \
  --description "Production-style RAG platform: FastAPI, Kafka ingestion, pgvector hybrid search, streaming cited answers, React UI" \
  --homepage "https://sunag-kp.github.io/docpilot/" \
  --enable-issues --enable-wiki=false --delete-branch-on-merge

gh api -X PUT "repos/$REPO/topics" -H "Accept: application/vnd.github+json" --input - <<'JSON'
{"names": ["python", "fastapi", "system-design", "rag", "retrieval-augmented-generation", "llm",
           "kafka", "redpanda", "pgvector", "postgresql", "redis", "hybrid-search",
           "opentelemetry", "prometheus", "react", "typescript", "docker", "sse"]}
JSON

# Branch protection: CI must pass, no force-pushes or deletion of main.
gh api -X PUT "repos/$REPO/branches/main/protection" -H "Accept: application/vnd.github+json" --input - <<'JSON'
{
  "required_status_checks": {
    "strict": false,
    "contexts": ["Lint & type-check", "Tests (unit + integration)", "Docker build & compose smoke test", "UI (lint, typecheck, test, build)"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_linear_history": false
}
JSON

# GitHub Pages served by the `pages.yml` workflow (static demo of the UI).
gh api -X POST "repos/$REPO/pages" -f build_type=workflow >/dev/null 2>&1 \
  || gh api -X PUT "repos/$REPO/pages" -f build_type=workflow >/dev/null

echo "Done: https://github.com/$REPO"
