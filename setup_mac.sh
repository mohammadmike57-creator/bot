#!/usr/bin/env bash
# One-time development setup on macOS (Apple Silicon, e.g. M2).
# Verifies tools, creates the Python virtualenv, installs backend + dashboard deps,
# creates .env from the template with freshly generated secrets, and runs the tests.
set -euo pipefail
cd "$(dirname "$0")"

ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$1"; }
fail() { printf "  \033[31m✗\033[0m %s\n" "$1"; exit 1; }

echo "== Checking tools"
[[ "$(uname -s)" == "Darwin" ]] || warn "not macOS - continuing anyway"
[[ "$(uname -m)" == "arm64" ]] && ok "Apple Silicon (arm64)" || warn "architecture $(uname -m)"

PY=""
for cand in python3.12 python3.11 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then PY="$cand"; break; fi
  fi
done
[[ -n "$PY" ]] && ok "Python $($PY -V 2>&1 | cut -d' ' -f2) ($PY)" || fail "Python 3.11+ required: brew install python@3.11"
$PY -m pip --version >/dev/null 2>&1 && ok "pip" || fail "pip missing: $PY -m ensurepip --upgrade"
command -v git  >/dev/null && ok "git $(git --version | cut -d' ' -f3)" || fail "git missing: xcode-select --install"
if command -v node >/dev/null; then
  NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]')
  [[ "$NODE_MAJOR" -ge 18 ]] && ok "node $(node -v)" || fail "Node 18+ required: brew install node"
else
  fail "node missing: brew install node"
fi
command -v npm >/dev/null && ok "npm $(npm -v)" || fail "npm missing"
command -v docker >/dev/null && ok "docker (optional) $(docker --version | cut -d' ' -f3 | tr -d ,)" \
  || warn "docker not found (optional - only needed for docker compose / local Postgres)"

echo "== Python virtualenv (.venv)"
[[ -d .venv ]] || $PY -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
pip install -r server/requirements-dev.txt
ok "backend dependencies installed"

echo "== Dashboard dependencies"
(cd dashboard && npm install --no-audit --no-fund)
[[ -f dashboard/.env ]] || cp dashboard/.env.example dashboard/.env
ok "dashboard dependencies installed"

echo "== Environment file"
if [[ ! -f .env ]]; then
  cp .env.example .env
  gen() { python -c 'import secrets; print(secrets.token_urlsafe(48))'; }
  # Fill the four secrets with random values (macOS sed needs the '' after -i).
  sed -i '' "s|^EA_API_KEY_ID=.*|EA_API_KEY_ID=ea-$(python -c 'import secrets; print(secrets.token_hex(6))')|" .env
  sed -i '' "s|^EA_HMAC_SECRET=.*|EA_HMAC_SECRET=$(gen)|" .env
  sed -i '' "s|^API_KEY=.*|API_KEY=$(gen)|" .env
  sed -i '' "s|^JWT_SECRET=.*|JWT_SECRET=$(gen)|" .env
  chmod 600 .env
  ok ".env created with generated secrets (chmod 600) - set your broker symbol names in it"
else
  ok ".env already exists (left untouched)"
fi

echo "== Database migrations (SQLite by default, or DATABASE_URL from .env)"
(cd server && alembic upgrade head)

echo "== Tests"
(cd server && python -m pytest -q)

cat <<'MSG'

Setup complete. Next:
  source .venv/bin/activate
  cd server && uvicorn app.main:app --reload --port 8000      # backend  -> http://localhost:8000/health
  cd dashboard && npm run dev                                  # dashboard -> http://localhost:5173
  Sign in to the dashboard with API_KEY from .env.
  Then follow docs/mt5-setup.md to install MT5 + the EA in Windows (Parallels).
MSG
