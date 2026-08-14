# Deploying Holly to a shared link

**What this is for.** Putting the prototype behind a URL so a handful of named people can
try it. It is **not** a production posture — see "What this deploy is not" below, and say
so out loud when you share the link.

## What the deploy adds over `uvicorn core.app:app`

| Blocker | Fix |
|---|---|
| `/admin` open to anyone with the URL | HTTP Basic auth, **two passwords, enforced per-path** — viewer opens chat + documents; only admin opens `/admin/*` (ratify gate, uploads, ledger). `core/auth.py` |
| Every visitor spends your Anthropic budget | Per-client rate limit on `POST /chat` (default 20/min; real client IP via `Fly-Client-IP` on Fly) |
| Online password guessing | Exponential backoff after repeated failed logins, attempts ledgered |
| Cross-site request forgery | State-changing requests from a foreign `Origin` are refused |
| Ledger rewritable by anyone with disk access | Events HMAC-signed with `HOLLY_LEDGER_KEY` (kept in the secret store, never on the volume); head hash anchored to platform logs |
| Ledger corrupts under concurrent writes | Serialised append (in-process lock + `flock`) — `core/ledger.py` |
| Ledger dies on redeploy | Persistent volume at `/data`, seeded from the image on first boot |
| 4-minute cold-start ingest | Corpus + models baked at **build** time — `scripts/prepare_deploy.py` |
| Multi-worker forks the audit trail | `--workers 1`, `min_machines_running = 1` |
| A demo link reads as a product | Standing `HOLLY_BANNER` on both surfaces |

## Deploy (Fly.io)

```sh
fly launch --no-deploy --copy-config      # creates the app from fly.toml; keep the name
fly volumes create holly_data --size 3 --region sjc

# Secrets are set OUT of band and never enter the image (.dockerignore excludes .env).
fly secrets set ANTHROPIC_API_KEY=...        # paste it yourself; it is never echoed
fly secrets set HOLLY_VIEWER_PASSWORD="$(openssl rand -base64 18)"
fly secrets set HOLLY_ADMIN_PASSWORD="$(openssl rand -base64 18)"
fly secrets set HOLLY_LEDGER_KEY="$(openssl rand -base64 32)"   # audit-chain HMAC key
fly secrets list                             # names only; read the values from your manager

fly deploy --remote-only                  # no local Docker needed; ~15 min first build
fly open
```

`HOLLY_REQUIRE_AUTH=1` is baked into the image: **if either password or the ledger HMAC
key is missing the app refuses to boot** rather than quietly serving the admin panel to
the internet (or an audit trail anyone with the disk could rewrite). Verified by
`tests/test_auth.py::test_deploy_refuses_to_start_without_passwords` and
`::test_deploy_refuses_to_start_without_ledger_key`.

**Render / Railway** work the same way: same Dockerfile, mount a disk at `/data`, set the
same secrets, force one instance.

## Checks after deploy

```sh
curl -sf https://<app>/healthz                      # {"status":"ok"}
curl -so /dev/null -w '%{http_code}\n' https://<app>/           # 401
curl -so /dev/null -w '%{http_code}\n' -u u:$VIEWER https://<app>/admin   # 403 (viewer ≠ admin)
```
`/healthz` runs `verify()`, so a broken chain shows up as an unhealthy instance instead
of as a wrong answer discovered months later. (Pass/fail only — the tamper detail is on
`/admin/ledger`, behind the admin credential.)

## Operating it

- **Ledger backup:** `fly ssh console -C "cat /data/cases/santacruz/ledger.jsonl" > ledger.bak`.
  The volume is a single disk with no replication — treat it as losable.
- **Rule changes are on the volume, not in git.** Ratifying through the hosted admin panel
  writes `/data/.../rules_ratified.json`, which the repo never sees. Pull it back if you
  want to keep it.
- **Cost:** one always-on `shared-cpu-2x`/2GB machine, plus Anthropic usage per chat.
  `auto_stop_machines = "suspend"` idles it between sessions.
- **Rotate the passwords** after a demo; they are shared secrets with no revocation.

## What this deploy is **not**

Do not describe it as production or as 508-conformant.

- **Basic auth, not identity.** No accounts, no per-user attribution, no revocation. The
  ledger records `actor: "admin"` — *which* admin is unknowable. Real use needs SSO + RBAC,
  because "who ratified this rule" is the question a union will ask.
- **One machine, no failover.** A single volume, no replication, no backup schedule.
- **Rate limit is per-process.** Caps API spend and stops a runaway loop; not an attacker.
- **Synthetic corpus.** Everything on the link is illustrative. Hence the banner.
- **Unaudited accessibility** (PRD §5.1a): built to WCAG 2.1 AA patterns, no VPAT.
