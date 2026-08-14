#!/bin/sh
# One-shot Fly deploy. Assumes you are authenticated (fly auth login OR FLY_API_TOKEN set).
# Reads the Anthropic key from .env and pipes it straight into `fly secrets` — the value
# is never printed, never committed, never baked into the image.
set -eu
cd "$(dirname "$0")/.."

APP="holly-demo-kg-0717"

echo "==> whoami"
fly auth whoami

echo "==> create app (idempotent)"
fly apps create "$APP" 2>/dev/null || echo "    app $APP already exists"

echo "==> persistent volume for the ledger (idempotent)"
fly volumes list -a "$APP" 2>/dev/null | grep -q holly_data \
  || fly volumes create holly_data --size 3 --region sjc -a "$APP" --yes

echo "==> secrets (values read from .env / openssl; not echoed)"
KEY="$(grep '^ANTHROPIC_API_KEY=' .env | cut -d= -f2- | tr -d '\"'"'"' \r')"
[ -n "$KEY" ] || { echo "no ANTHROPIC_API_KEY in .env" >&2; exit 1; }
VIEWER="$(openssl rand -base64 18)"
ADMIN="$(openssl rand -base64 18)"
# Ledger HMAC key: lives ONLY in Fly's secret store, never on the data volume — that
# separation is what makes the audit chain unforgeable by anyone who can read the disk.
LEDGER_KEY="$(openssl rand -base64 32)"
fly secrets set -a "$APP" \
  ANTHROPIC_API_KEY="$KEY" \
  HOLLY_VIEWER_PASSWORD="$VIEWER" \
  HOLLY_ADMIN_PASSWORD="$ADMIN" \
  HOLLY_LEDGER_KEY="$LEDGER_KEY" \
  HOLLY_REQUIRE_AUTH=1

echo "==> deploy (prepare_deploy runs in the build; refuses if any known answer fails)"
fly deploy -a "$APP" --ha=false

echo
echo "============================================================"
echo " Live:    https://$APP.fly.dev"
# The two passwords are printed DELIBERATELY — this is how the operator learns them to
# share/use; they land in terminal scrollback, so rotate after the demo (fly secrets
# set). The API key and the ledger HMAC key are never printed.
echo " Viewer:  $VIEWER"
echo " Admin:   $ADMIN"
echo "============================================================"
echo " Share the URL + the VIEWER password (chat + documents only)."
echo " Keep ADMIN to yourself — it alone opens /admin (ratify, upload, ledger)."
echo " Rotate both after the demo: fly secrets set HOLLY_VIEWER_PASSWORD=..."
