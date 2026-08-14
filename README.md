# Kenny — Document-Grounded Costing & Policy Engine

Kenny answers HR costing and policy questions directly from contract PDFs — with
**deterministic math**, **clause-level bounding-box citations**, a **human approval
gate**, and a **tamper-evident audit ledger**. The LLM only reads language and routes
questions; it never computes the number.

> **The job it does:** *"Hand me a number I can defend, while the moment is still alive."*
> Trust is verified once, up front, against ground truth. Every scenario computed on
> those verified rules is then instant *and* defensible.

This build ships a single corpus — the **Central Fire District of Santa Cruz County**:
four bargaining-unit MOUs (with side letters) plus the master salary schedule, all
published by the district.

**Go deeper:** [`PRD.md`](PRD.md) ([PDF](PRD.pdf)) — the product, users, scope, and trust
model, for a product audience. · [`ARCHITECTURE.md`](ARCHITECTURE.md)
([PDF](ARCHITECTURE.pdf)) — component map, data contracts, flows, security, and the
onboarding playbook. · [`DEPLOY.md`](DEPLOY.md) — putting it behind a shared URL.

The PRD owns *intent*; ARCHITECTURE owns *behaviour*. Where they overlap, ARCHITECTURE is
the tiebreak.

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
pytest                                    # proves the Santa Cruz case = $640.80
uvicorn core.app:app --reload            # http://127.0.0.1:8000 (chat) · /admin (ops)
```

**Activate Claude (optional):** put your key in `.env` (`ANTHROPIC_API_KEY=sk-...`) and
restart. The chat badge then shows `LLM: Claude`. Without a key it still runs end-to-end —
deterministic fallbacks cover every LLM touchpoint. **Money math is deterministic either
way.**

**Offline / lightweight:** docling pulls torch + models on first parse — but the Santa
Cruz case ships with its catalog and search index already baked, so the app answers
without ever parsing. Skipping docling only matters if you RE-ingest: parsing then
degrades to raw page text (page-level citations), unless a `sources/*.clauses.json`
sidecar is present — an optional generated artifact that is only trusted when it embeds
the SHA-256 of the exact PDF it was extracted from.

---

## Demo walkthrough

The demo has two surfaces: **Chat** (`/`) is where you ask and cost; **Admin** (`/admin`)
is where documents become trusted rules. The intended path is Admin left-to-right once to
establish trust, then Chat forever.

### Part 1 — Admin: turn documents into trusted rules

Open `/admin`. The tabs are numbered in the order you use them.

| Tab | What it does | What to expect |
|---|---|---|
| **1 · Documents** | Ingest the contract PDFs. Each is parsed by docling for its text, its tables, and the **exact position of every clause**, then tagged and summarized into a searchable catalog. | The five source documents (four MOUs + the master salary schedule) appear in the contract library, searchable immediately. No rules exist yet. |
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

- **Costing** — *"Cost an 8-hour overtime shift for a Firefighter/Paramedic (56 hr, top
  step)."*
- **Entitlement** — *"How much bereavement leave does a firefighter get?"*
- **Rate lookup** — the hourly/loaded rate for a classification and step.
- **Policy Q&A** — *"What does the Firefighters Local 3535 MOU say about overtime?"*

**What to expect from a costing answer:**

1. A **single deterministic figure** — e.g. **$640.80** for the overtime shift above
   (1.5× the $53.40/hr top-step rate × 8 hours).
2. The answer is **routed to the right document** — the multiplier comes from the Local
   3535 MOU, the rate from the Master Salary Schedule — and any ambiguity is flagged.
3. **Click any dollar amount** → the audit drawer opens, showing the full decision trace
   and the **source clause boxed on the rendered PDF page**. This is the "number I can
   defend" made literal.

### Expected results

The engine is **config-not-code** — `core/` is case-agnostic; the corpus is a swappable
data bundle. The Santa Cruz goldens ship as verified acceptance tests (`pytest`):

| Scenario | Prompt | Expected result | Source |
|---|---|---|---|
| Overtime (money) | 8-hour overtime shift, Firefighter/Paramedic (56 hr, top step) | **$640.80** | Local 3535 MOU p.8 (1.5×) × Master Salary Schedule ($53.40/hr). |
| Bereavement (entitlement) | Bereavement leave for a Firefighter/Paramedic | **3 shifts** | Local 3535 MOU Article XIV (p.21). |

Other bargaining units (Admin Group, Management, Chief Officers) are declared in the
corpus with their own known-answer scenarios; their rules are drafted through the Admin
flow above and read as *pending* until then.

---

## Layout

- **`core/`** — case-agnostic engine: ingest, tagging, retrieval, the DSL rule engine,
  the ledger, and the chat/admin surfaces.
- **`cases/<name>/`** — a swappable bundle: PDFs, data, rules, taxonomy, prompts.

```
core/            engine, surfaces, ledger (case-agnostic)
cases/santacruz/ sources/ · data/ · rules/ · taxonomy.yaml · prompt/ · case.yaml
scripts/         deploy helpers, corpus reset, doc/PDF generation, trace
tests/           acceptance proofs (Santa Cruz overtime = $640.80, bereavement = 3 shifts)
```

Read ARCHITECTURE §2 (the law), §11 (pitfalls), and §12 (playbook) before running against
real contracts.
