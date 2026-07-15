#!/usr/bin/env bash
set -euo pipefail

WITH_TRADINGAGENTS=0
if [[ "${1:-}" == "--with-tradingagents" ]]; then
  WITH_TRADINGAGENTS=1
fi

ROOT="${DSA_AI_UPSTREAM_ROOT:-$HOME/.dsa-ai/upstreams}"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
VIBE_SHA="ac6424ed4640d009880ec5a97c13f71ef050e4b2"
TRADINGAGENTS_SHA="01477f9afb7a47b849ed4c9259d3a9a4738d9fda"

if ! command -v git >/dev/null 2>&1; then
  echo "git is required" >&2
  exit 1
fi
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN=python3
  else
    echo "Python 3.11+ is required" >&2
    exit 1
  fi
fi

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Python 3.11+ is required; resolved interpreter: $PYTHON_BIN" >&2
  exit 1
fi

mkdir -p "$ROOT/src" "$ROOT/venvs"

clone_pin() {
  local name="$1"
  local repository="$2"
  local sha="$3"
  local target="$ROOT/src/$name"
  if [[ ! -d "$target/.git" ]]; then
    git clone --filter=blob:none --no-checkout "$repository" "$target"
  fi
  git -C "$target" fetch --depth 1 origin "$sha"
  git -C "$target" checkout --detach "$sha"
  test "$(git -C "$target" rev-parse HEAD)" = "$sha"
}

create_venv() {
  local target="$1"
  if [[ ! -x "$target/bin/python" ]]; then
    "$PYTHON_BIN" -m venv "$target"
  fi
  "$target/bin/python" -m pip install --upgrade pip setuptools wheel
}

echo "[1/2] Installing Vibe-Trading read-only research sidecar"
clone_pin "vibe-trading" "https://github.com/HKUDS/Vibe-Trading.git" "$VIBE_SHA"
create_venv "$ROOT/venvs/vibe"
"$ROOT/venvs/vibe/bin/python" -m pip install -e "$ROOT/src/vibe-trading[ashare]"
"$ROOT/venvs/vibe/bin/python" - <<'PY'
from src.tools.market_screener_tool import MarketScreenerTool
assert MarketScreenerTool.name == "screen_market"
print("Vibe-Trading import verification passed")
PY

if [[ "$WITH_TRADINGAGENTS" == "1" ]]; then
  echo "[2/2] Installing TradingAgents research lab (not in the production order path)"
  clone_pin "tradingagents" "https://github.com/TauricResearch/TradingAgents.git" "$TRADINGAGENTS_SHA"
  create_venv "$ROOT/venvs/tradingagents"
  "$ROOT/venvs/tradingagents/bin/python" -m pip install -e "$ROOT/src/tradingagents"
else
  echo "[2/2] TradingAgents skipped. The repository's native multi-agent path is used in production."
fi

cat > "$ROOT/upstreams.env" <<EOF
DSA_AI_UPSTREAM_ROOT=$ROOT
VIBE_PYTHON=$ROOT/venvs/vibe/bin/python
VIBE_COMMIT=$VIBE_SHA
TRADINGAGENTS_COMMIT=$TRADINGAGENTS_SHA
EOF

echo
printf 'Installed. Run:\n  %q ai_select.py --source vibe --notify\n' "$PYTHON_BIN"
echo "Vibe interpreter: $ROOT/venvs/vibe/bin/python"
