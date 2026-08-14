# Deploying Kenny to a shared link (Railway)

**What this is for.** Putting the prototype behind a URL so people can try it. It is
**not** a production posture — see "What this deploy is not" below, and say so out loud
when you share the link.

**Default posture: OPEN.** No sign-in anywhere — chat, documents, and `/admin` all serve
without credentials, exactly like a local run. Two protections stay on regardless:

- **Chat rate limit** (default 20/min per client) — an open `/chat` is the operator's
  Anthropic budget, so removing auth never removes the spend cap.
- **No API key by default.** The app runs end-to-end on deterministic fallbacks. Add
  `ANTHROPIC_API_KEY` only once you accept that *anyone with the URL* can spend it
  (rate-limited, but public).

Be aware of what open means here: anyone with the URL can ratify rules, upload/replace
corpus PDFs, and read the ledger. Fine for a demo corpus; not fine the day real
decisions live on the volume. To lock it back down, set `KENNY_REQUIRE_AUTH=1` plus
`KENNY_VIEWER_PASSWORD`, `KENNY_ADMIN_PASSWORD`, and `KENNY_LEDGER_KEY` — the app
refuses to boot half-configured, the viewer/admin split is enforced per-path, and the
audit ledger becomes HMAC-signed.

## Deploy (Railway)

```bash
railway init -n kenny          # once: create the project (or `railway link`)
railway volume add -m /data    # persistent case tree: ledger, catalog, ratified rules
railway up --detach            # builds the Dockerfile (~15 min first time: torch + models)
railway domain                 # mint the public URL
```

The image bakes the parsed Santa Cruz corpus and model weights at build time
(`scripts/prepare_deploy.py` refuses to ship an image whose known-answer scenarios
fail). First boot seeds `/data` from the image; after that the volume wins, so
ratifications and the ledger survive redeploys. Railway mounts volumes root-owned —
the entrypoint chowns `/data` and immediately drops to the unprivileged `kenny` user.

Optional variables (`railway variables set KEY=value`):

| Variable | Effect |
|---|---|
| `ANTHROPIC_API_KEY` | Turns on live Claude for routing/drafting (public spend — see above) |
| `KENNY_CHAT_RATE_LIMIT` | Chat requests per client per minute (default 20) |
| `KENNY_BANNER` | Standing banner text on both surfaces |
| `KENNY_REQUIRE_AUTH=1` + 3 secrets | Re-enables the locked posture described above |

## Checks after deploy

```bash
curl -sf https://<app>.up.railway.app/healthz     # {"status":"ok"}
```

`/healthz` runs the ledger `verify()`, so a broken chain shows up as an unhealthy
instance instead of as a wrong answer discovered months later.

## Operating it

- **Ledger backup:** `railway ssh "cat /data/cases/santacruz/ledger.jsonl" > ledger.bak`.
  Single disk, no replication — treat it as losable.
- **Rule changes live on the volume, not in git.** Ratifying through the hosted admin
  writes `/data/.../rules_ratified.json`, which the repo never sees. Pull it back if you
  want to keep it.
- **Reseed** (replace the volume's case with the image's): set `KENNY_SEED_FORCE=1` and
  `KENNY_SEED_FORCE_CONFIRM=yes`, redeploy, then unset both. The old case is archived
  beside it on the volume, never deleted.

## What this deploy is **not**

Do not describe it as production or as 508-conformant.

- **Open by default.** No identity, no attribution: the ledger records `actor: "admin"`
  for whoever clicked. Real use needs the locked posture at minimum, then SSO + RBAC.
- **One instance, one volume.** No failover, no replication, no backup schedule.
- **Rate limit is per-process.** Caps API spend and stops a runaway loop; not an attacker.
- **Synthetic-ish corpus.** Everything on the link is illustrative. Hence the banner.
- **Unaudited accessibility** (PRD §5.1a): built to WCAG 2.1 AA patterns, no VPAT.

## Fly.io (archived)

The original take-home deploy targeted Fly; those assets live in `archive/fly/` with
notes. The Fly app that exists in the account was never successfully deployed and holds
no data.
