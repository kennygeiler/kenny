# Holly — Document-Grounded Costing & Policy Engine

Holly answers HR costing and policy questions directly from contract PDFs — with
**deterministic math**, **clause-level bounding-box citations**, a **human approval
gate**, and a **tamper-evident audit ledger**. The LLM only reads language and routes
questions; it never computes the number.

> **The job it does:** *"Hand me a number I can defend, while the moment is still alive."*
> Trust is verified once, up front, against ground truth. Every scenario computed on
> those verified rules is then instant *and* defensible.

**Go deeper:** [`PRD.md`](PRD.md) ([PDF](PRD.pdf)) — the product, users, scope, and trust
model, for a product audience. · [`ARCHITECTURE.md`](ARCHITECTURE.md)
([PDF](ARCHITECTURE.pdf)) — component map, data contracts, flows, security, and the
onboarding playbook. · [`DEPLOY.md`](DEPLOY.md) — putting it behind a shared URL. ·

The PRD owns *intent*; ARCHITECTURE owns *behaviour*. Where they overlap, ARCHITECTURE is
the tiebreak.

Use santa cruz case for Holly Case. Other cases are tests.

---

## The trust model — why this is different

Four rules, enforced in the architecture rather than promised in prose:

1. **The AI reads; it never computes.** Intent parsing, rule drafting, tagging, and
   retrieval ranking are LLM touchpoints. Every dollar figure is produced by a
   deterministic engine from human-approved rules.
2. **Nothing goes live without a human.** Drafted rules sit in a review queue until a
   person approves each one against its highlighted source clause.
3. **A rule can't ship unless it reproduces a known-correct answer.** Approval is blocked
   until the rule set reproduces the verification scenario's known amount. (This gate
   caught real drafting errors during development.)
4. **Every answer is traceable.** Each figure clicks through to the exact clause, boxed on
   the rendered PDF page, and every state change lands in a hash-chained audit ledger.

---

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # docling is heavy; see "offline" below
python scripts/make_reference_pdfs.py    # generate reference PDFs + clause sidecars
pytest                                    # proves the reference case 
uvicorn core.app:app --reload            # http://127.0.0.1:8000 (chat) · /admin (ops)
```

**Activate Claude (optional):** put your key in `.env` (`ANTHROPIC_API_KEY=sk-...`) and
restart. The chat badge then shows `LLM: Claude`. Without a key it still runs end-to-end —
deterministic fallbacks cover every LLM touchpoint. **Money math is deterministic either
way.**

**Offline / lightweight:** docling pulls torch + models on first parse. Ingestion falls
back to the committed `sources/*.clauses.json` sidecars (exact bboxes), so you can skip
installing docling and everything still works.

---

## Demo walkthrough

The demo has two surfaces: **Chat** (`/`) is where you ask and cost; **Admin** (`/admin`)
is where documents become trusted rules. The intended path is Admin left-to-right once to
establish trust, then Chat forever.

### Part 1 — Admin: turn documents into trusted rules

Open `/admin`. The tabs are numbered in the order you use them.

| Tab | What it does | What to expect |
|---|---|---|
| **1 · Documents** | Ingest the contract PDFs. Each is parsed by docling for its text, its tables, and the **exact position of every clause**, then tagged and summarized into a searchable catalog. | Both source PDFs appear in the contract library, searchable immediately. No rules exist yet. |
| **2 · Verification** | The trust anchor. Pick a known-correct scenario and press **"Draft the rules for this scenario."** The system retrieves only the clauses that answer needs and drafts the few rules they require — then checks them against the known amount. | A short set of candidate rules (≈4, not the ~33 you'd get extracting a whole MOU) with a pass/fail against the known number. Scoping extraction to one verified scenario is deliberate — it keeps review humanly reviewable. |
| **3 · Review queue** | The human gate. Each drafted rule is shown beside its highlighted source clause, waiting for approval. Nothing here affects an answer yet. | You approve each rule against the clause it came from. Approval is **blocked** until the approved set reproduces the verification amount — a misdrafted rule cannot go live. |
| **Rule library** | The ratified rules currently in force, plus gaps (scenarios the classification table can't yet answer, and which data dimension would unlock them). | The approved rules become the live library the engine computes from. |
| **Audit** | The tamper-evident event log: recent questions and the full hash-chained event chain with a `verify()` status and export. | Every ingest, draft, approval, and query is a chained event. `verify()` reports the chain intact. |

**Approve → ratify** at the end of the flow promotes the proposed rules into the ratified
library. That single human action is the line between "the AI suggested this" and "this is
what the engine will compute."

### Part 2 — Chat: ask and cost, with receipts

Open `/`. Ask in plain language. The badge (top-right) shows whether Claude is active.
Chat handles four kinds of question:

- **Costing** — *"Calculate the total cost of mandating an 8-hour shift for the road tech
  classifications on July 4th (a Saturday)."*
- **Entitlement** — *"How much bereavement leave does a firefighter get?"*
- **Rate lookup** — the hourly/loaded rate for a classification and step.
- **Policy Q&A** — *"What does the MOU say about overtime?"*

**What to expect from a costing answer:**

1. A **single deterministic figure** 
2. The answer is **routed to the right document**
3. **Click any dollar amount** → the audit drawer opens, showing the full decision trace
   and the **source clause boxed on the rendered PDF page**. This is the "number I can
   defend" made literal.


---

## Onboard a new policy domain (no code)

```bash
python scripts/new_case.py my_domain
# drop a PDF in cases/my_domain/sources, fill case.yaml + data + extraction.yaml,
# then draft/approve rules via the admin panel
CASE=cases/my_domain uvicorn core.app:app
```

Nothing in `core/` changes. A new domain is a new data bundle plus a trip through the
Admin flow above. See ARCHITECTURE §12 (onboarding playbook) before pointing this at a
real corpus.

---

## Layout

- **`core/`** — case-agnostic engine: ingest, tagging, retrieval, the DSL rule engine,
  the ledger, and the chat/admin surfaces.
- **`cases/<name>/`** — a swappable bundle: PDFs, data, rules, taxonomy, prompts.

```
core/            engine, surfaces, ledger (never changes per case)
cases/<name>/    sources/ · data/ · rules/ · taxonomy.yaml · prompt/ · case.yaml
scripts/         PDF generation, new-case scaffold, deploy helpers
tests/           per-case proofs (overtime = $1,660, sheriff = $1,940.56, …)
```

Read ARCHITECTURE §2 (the law), §11 (pitfalls), and §12 (playbook) before running against
real contracts.
