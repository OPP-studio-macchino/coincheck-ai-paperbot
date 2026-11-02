#!/usr/bin/env bash
# Public GitHub publishing; refuses secrets, existing remote or unexpected owner.
set -euo pipefail
cd "$(dirname "$0")/.."
expected='OPP-studio-macchino'
repo='coincheck-ai-paperbot'
command -v git >/dev/null
command -v gh >/dev/null
account="$(gh api user --jq .login)"
if [[ "$account" != "$expected" ]]; then
  echo "STOP: wrong GitHub login: $account" >&2; exit 1
fi
if gh repo view "$expected/$repo" --json name >/dev/null 2>&1; then
  echo "STOP: GitHub repo already exists; refusing to overwrite" >&2; exit 2
fi
if [[ -d .git ]]; then
  echo 'STOP: existing git repo; refusing to replace its history' >&2; exit 3
fi
for p in .env runtime; do
  if [[ -f "$p" ]] && [[ "$p" != .env ]]; then
    echo "STOP: unexpected data path $p" >&2; exit 4
  fi
done
git init -b main
# Deliberate allowlist: never `git add .` with a live runtime nearby.
git add -- README.md Dockerfile compose.yaml requirements.txt schema.sql nginx.conf \
  .dockerignore .gitignore .env.example app frontend docs tests scripts .github
if git diff --cached --name-only | grep -E '(^|/)(\.env$|runtime/|data/|results/|.*\.sqlite|.*\.pem|.*\.key)' ; then
  echo 'STOP: sensitive path staged' >&2; exit 5
fi
if git diff --cached --name-only | grep -E '(^|/)\.DS_Store$' ; then
  echo 'STOP: unwanted hidden files staged' >&2; exit 6
fi
git diff --cached --check
git commit -m 'feat: paper-only Coincheck AI bot Docker foundation'
gh repo create "$expected/$repo" --public --source . --remote origin --push --description 'Coincheck BTC/JPY paper-only Python strategy, news risk gate, Postgres and frontend (no live orders)'
gh repo view "$expected/$repo" --json url,visibility,defaultBranchRef
