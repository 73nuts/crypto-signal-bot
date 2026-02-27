#!/usr/bin/env bash
# export_clean.sh
#
# Purpose: Produce a clean copy of the Ignis codebase ready for open-source publishing.
# Usage:   ./scripts/export_clean.sh [TARGET_DIR]
# Default: TARGET_DIR = ../crypto-signal-bot-clean
#
# The script:
#   1. Uses rsync to copy the repo, excluding private/internal files
#   2. Runs a grep scan on the copy for potential secrets/PII
#   3. Prints a summary with any warnings

set -euo pipefail

TARGET="${1:-../crypto-signal-bot-clean}"
SOURCE="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== export_clean.sh ==="
echo "Source : $SOURCE"
echo "Target : $TARGET"
echo ""

# ---------------------------------------------------------------------------
# 1. rsync copy with exclusions
# ---------------------------------------------------------------------------
echo "[1/3] Copying codebase via rsync..."

rsync -a --delete \
  --exclude='.git/' \
  --exclude='.env' \
  --exclude='.env.local' \
  --exclude='.env.telegram' \
  --exclude='CLAUDE.md' \
  --exclude='.claude/' \
  --exclude='@LisaLyu77' \
  --exclude='clear_sl_order' \
  --exclude='long_range' \
  --exclude='字符' \
  --exclude='log.txt' \
  --exclude='record.txt' \
  --exclude='/data/' \
  --exclude='/logs/' \
  --exclude='.DS_Store' \
  --exclude='__pycache__/' \
  --exclude='.mypy_cache/' \
  --exclude='.pytest_cache/' \
  --exclude='.ruff_cache/' \
  --exclude='redis_data/' \
  --exclude='.venv/' \
  --exclude='venv/' \
  --exclude='uv.lock' \
  --exclude='docs/OPEN_SOURCE_CHECKLIST.md' \
  --exclude='docs/adr/' \
  --exclude='docs/brand/' \
  --exclude='docs/operations/SERVER_MIGRATION.md' \
  --exclude='docs/operations/PRE_LAUNCH_CHECKLIST.md' \
  --exclude='scripts/migrations/' \
  --exclude='scripts/telegram/*.sql' \
  --exclude='scripts/analysis/' \
  --exclude='scripts/data/' \
  --exclude='scripts/deployment/' \
  --exclude='scripts/testing/verify_feishu_config.py' \
  --exclude='scripts/testing/run_server_tests.sh' \
  --exclude='tests/verify_throttle_fix.sh' \
  --exclude='tests/system/test_webhook_security.sh' \
  --exclude='scripts/testing/run_local_tests.sh' \
  --exclude='scripts/utils/run_all_tests.sh' \
  --exclude='scripts/test_ai_v5.py' \
  --exclude='scripts/diagnose_margin.sh' \
  --exclude='scripts/.DS_Store' \
  --exclude='cloudflare-workers/' \
  "$SOURCE/" "$TARGET/"

echo "    Done."
echo ""

# ---------------------------------------------------------------------------
# 2. Security scan on the copied directory
# ---------------------------------------------------------------------------
echo "[2/3] Running security scan on $TARGET ..."

WARNINGS=0

run_scan() {
  local label="$1"
  local pattern="$2"
  local results
  results=$(grep -rn --include='*.py' --include='*.yml' --include='*.yaml' \
    --include='*.json' --include='*.toml' --include='*.cfg' \
    --include='*.sh' --include='*.md' --include='*.txt' --include='*.sql' \
    -E "$pattern" "$TARGET" 2>/dev/null \
    | grep -v 'export_clean\.sh' || true)
  if [[ -n "$results" ]]; then
    echo "  [WARN] $label"
    echo "$results" | sed 's/^/    /'
    WARNINGS=$((WARNINGS + 1))
  fi
}

# API key patterns
run_scan "Possible API keys (sk- prefix)"           '\bsk-[A-Za-z0-9]{20,}'
run_scan "Possible key fragment (gOPgRSc)"          'gOPgRSc'
run_scan "Possible key fragment (GCUR2HJQ)"         'GCUR2HJQ'
run_scan "Generic API key assignment"               '(api_key|API_KEY|apikey)\s*[=:]\s*["\x27][A-Za-z0-9_\-]{16,}'

# IP addresses
run_scan "Private IP (192.168.x.x)"                '192\.168\.[0-9]+\.[0-9]+'
run_scan "Private IP (10.x.x.x)"                   '\b10\.[0-9]+\.[0-9]+\.[0-9]+'

# Internal codenames / server references
# Note: 'ignis' is the project name (intentional, not a leak) — only scan for old deploy paths
run_scan "Old codename 'orion'"                     '\borion\b'

# Email addresses
run_scan "Gmail address"                            '[A-Za-z0-9._%+-]+@gmail\.com'
run_scan "Handle 'bojackem'"                        'bojackem'

# Telegram user IDs
run_scan "Telegram ID (1828015132)"                 '1828015132'

# Wallet addresses
run_scan "Wallet address fragment (0xf65c)"         '0xf65c'

# File paths
run_scan "Server deploy path (/opt/crypto_signal_ignis)" '/opt/crypto_signal_ignis'

echo ""

# ---------------------------------------------------------------------------
# 3. Summary
# ---------------------------------------------------------------------------
echo "[3/3] Summary"
echo "  Excluded (files/dirs not copied):"
echo "    .git/, .env*, CLAUDE.md, .claude/, stray files (log.txt record.txt"
echo "    @LisaLyu77 clear_sl_order long_range 字符), data/, logs/, .DS_Store,"
echo "    __pycache__/, cache dirs, redis_data/, docs/OPEN_SOURCE_CHECKLIST.md,"
echo "    docs/adr/, docs/brand/, docs/operations/SERVER_MIGRATION.md,"
echo "    docs/operations/PRE_LAUNCH_CHECKLIST.md, scripts/migrations/,"
echo "    scripts/telegram/*.sql, scripts/analysis/, scripts/data/,"
echo "    scripts/deployment/, scripts/testing/verify_feishu_config.py,"
echo "    scripts/testing/run_server_tests.sh, scripts/testing/run_local_tests.sh,"
echo "    tests/verify_throttle_fix.sh, tests/system/test_webhook_security.sh,"
echo "    scripts/utils/run_all_tests.sh,"
echo "    scripts/test_ai_v5.py, scripts/diagnose_margin.sh,"
echo "    scripts/.DS_Store, cloudflare-workers/"
echo ""

if [[ "$WARNINGS" -gt 0 ]]; then
  echo "  WARNING: $WARNINGS secret/PII pattern(s) found — review before publishing!"
  exit 1
else
  echo "  No secrets or PII detected. Clean copy is at: $TARGET"
fi
