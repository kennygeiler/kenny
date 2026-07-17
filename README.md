# Holly — Document-Grounded Costing & Policy Engine

Answers HR costing questions from policy PDFs with **deterministic math** and
**clause-level, bounding-box citations**, under a **human approval gate**, with a
**tamper-evident audit ledger**. The LLM only reads language and routes; it never
computes the number.

Two documents, read in this order:

- **`PRD.md`** — the product: problem, users, scope, principles, the trust model, honest status.
  Written for a product audience; no code.
- **`ARCHITECTURE.md`** — the engineering companion: component map, data contracts, flows, error
  handling, security, scaling, the incident log, and the **onboarding playbook**. Read §2 (the law),
  §11 (pitfalls) and §12 (playbook) before pointing this at a real corpus.
- **`DEPLOY.md`** — putting it behind a shared URL.

The PRD owns *intent*; ARCHITECTURE owns *behaviour*. Where they overlap, that is the tiebreak.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # docling is heavy; see "offline" below
python scripts/make_reference_pdfs.py    # generate the reference PDFs + clause sidecars
pytest                                    # proves the reference case = $1,660.00
uvicorn core.app:app --reload            # http://127.0.0.1:8000  (chat)  /admin (ops)
```

**Activate Claude:** put your key in `.env` (`ANTHROPIC_API_KEY=sk-...`) and restart.
The LLM touchpoints (intent parse, rule drafting, tagging, retrieval ranking) then call
Claude; the chat badge shows `LLM: Claude`. Without a key it still runs — deterministic
fallbacks cover every touchpoint. Money math is deterministic either way.

**Upload your own PDFs:** Admin → *Choose File* → *Upload & ingest PDF*. The file is
parsed by docling (real bboxes), tagged/summarized into the catalog, and its rules are
drafted for the review gate — no code or config changes.

**Offline / lightweight:** docling pulls torch + models (first parse downloads them).
Ingestion falls back to the committed `sources/*.clauses.json` sidecars (exact bboxes),
so you can skip installing docling and everything still works.

## Try it

1. **Admin → Ingest sources** — parses both PDFs, tags/summarizes them into the catalog,
   drafts candidate rules.
2. **Admin → Approve all → ratify** — the human gate; proposed rules become the ratified
   library.
3. **Chat** — ask: *"Calculate the total cost of mandating an 8-hour shift for the road tech
   classifications on July 4th (a Saturday)."* → **$1,660.00**, routed to the MOU (not
   the salary schedule), with the weekend-schedule §9.2 ambiguity flagged.
4. **Click any amount** — the audit drawer shows the decision trace and the source clause
   boxed on the rendered PDF page.
5. **Admin → Ledger** — the full hash-chained event chain, with a `verify()` status and
   export.

## Onboard a new policy domain (no code)

```bash
python scripts/new_case.py my_domain
# drop a PDF in cases/my_domain/sources, fill case.yaml + data + extraction.yaml,
# write/approve rules via the admin panel
CASE=cases/my_domain uvicorn core.app:app
```

## Layout

`core/` is case-agnostic (ingest, tagging, retrieval, DSL engine, ledger, surfaces).
`cases/<name>/` is a swappable bundle (PDFs, data, rules, taxonomy, prompts).

Two cases ship, proving the engine is config-not-code (same `core/`, different data):
- **`cases/overtime`** — SEIU MOU Article 9. Prompt → **$1,660.00**. Preview: `holly` (port 8400).
- **`cases/sheriff`** — Sheriff MOU Article 12 holdover: different multiplier (2.0×), bonus
  ($200), pre-multiplier bump (hazmat +8%), and an equality-based exception
  (`shift == "Graveyard"`). Prompt *"…6-hour mandatory holdover for the detention and patrol classifications on a High-Security Day."* → **$1,940.56**. Preview: `holly-sheriff` (port 8401).

Generate the sheriff PDFs with `python scripts/make_sheriff_pdfs.py`.
