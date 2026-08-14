#!/bin/sh
# Container start. Two jobs: put the case on the persistent volume, then serve.
#
# The image ships /app/cases/<case> already ingested (scripts/prepare_deploy.py). But a
# container filesystem is ephemeral — a redeploy or a crash would take the LEDGER with
# it, and an audit trail that disappears on deploy is not an audit trail. So the case
# lives on a mounted volume at /data, seeded from the image on first boot only. After
# that the volume wins, and ratifications and ledger entries survive redeploys.
set -eu

CASE_NAME="${CASE_NAME:-santacruz}"
VOLUME_CASE="/data/cases/${CASE_NAME}"

# Platform volumes (Railway) mount root-owned with no pre-mount chown hook, so the
# container starts as root, takes ownership of /data, and IMMEDIATELY drops to the
# unprivileged app user for everything else — including the server itself.
if [ "$(id -u)" = "0" ]; then
  chown -R holly:holly /data
  exec su -p holly -s /bin/sh -c "exec /app/scripts/entrypoint.sh"
fi

# HOLLY_SEED_FORCE=1 replaces the volume's copy of the case with the image's — for
# shipping a corrected corpus/rule library to a demo. The old case (ledger included) is
# ARCHIVED beside it, never deleted: an audit trail one env var away from destruction
# is not an audit trail. A second env var must agree, so a single stray setting cannot
# trigger the reseed on its own.
if [ "${HOLLY_SEED_FORCE:-0}" = "1" ] && [ -d "${VOLUME_CASE}" ]; then
  if [ "${HOLLY_SEED_FORCE_CONFIRM:-}" != "yes" ]; then
    echo "[entrypoint] HOLLY_SEED_FORCE=1 set but HOLLY_SEED_FORCE_CONFIRM!=yes — refusing to reseed"
  else
    STAMP="$(date +%Y%m%d-%H%M%S)"
    echo "[entrypoint] HOLLY_SEED_FORCE — archiving ${VOLUME_CASE} to ${VOLUME_CASE}.pre-reseed.${STAMP}"
    mv "${VOLUME_CASE}" "${VOLUME_CASE}.pre-reseed.${STAMP}"
  fi
fi

if [ ! -f "${VOLUME_CASE}/case.yaml" ]; then
  echo "[entrypoint] seeding ${VOLUME_CASE} from the image (first boot)"
  mkdir -p "/data/cases"
  cp -R "/app/cases/${CASE_NAME}" "${VOLUME_CASE}"
else
  echo "[entrypoint] using existing ${VOLUME_CASE} (ledger preserved)"
fi

export CASE="${VOLUME_CASE}"

# --workers 1 is REQUIRED, not a resource choice. The ingest job registry is an
# in-process dict, and the ledger's cross-process file lock has never been exercised
# under real load. A second worker would silently poll a job it cannot see. Concurrency
# comes from the threadpool; scaling past that needs a shared queue + DB (PRD §8).
exec uvicorn core.app:app --host 0.0.0.0 --port "${PORT:-8080}" --workers 1
