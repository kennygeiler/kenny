# Bringing Holly to a New Government — Deployment Strategy
### Interview reference · how an engagement actually pans out

> The one-line thesis: **we don't install software, we establish trust — and we do it
> against the jurisdiction's own payroll.** Everything below is sequenced around that.
> Every claim here is grounded in what the prototype actually did against a real,
> scanned municipal corpus (Central Fire District of Santa Cruz County).

---

## 0 · Before the engagement: what we ask for

Three artifacts and three people. That's the whole intake.

| Artifact | Why | Who has it |
|---|---|---|
| **The document inventory** — every MOU, side letter, amendment, salary schedule, and the dates + units each binds | Governance (who is covered by what, when) is the one thing no machine can extract — it's declared by the person who knows | Labor relations |
| **A payroll export** (1–2 pay periods, per-line-code) | The payroll's pay codes *enumerate every pay branch that actually costs money* — a finite list of dozens, not a 200-page reading assignment. Each real paid amount becomes a verification scenario | Payroll/Finance |
| **The position/classification table** (class × step × schedule × certs × rate) | The costing unit. No employee names ever enter the system — classifications only, which HR already maintains | HR |

People: a **labor-relations lead** (declares governance, adjudicates interpretation), a
**payroll analyst** (supplies known answers, reviews rules), an **IT contact** (data
export, access). Total customer time in month one: **roughly a day each.**

## 1 · Phase 0 — Land (week 1): searchable before trusted

Day-one deliverable: **every contract ingested, cited, and queryable.** Policy Q&A and
rate lookup need zero rules — "what does the MOU say about overtime," "what's a
captain's top-step rate" work the first afternoon, every answer boxed on the source PDF.

What we know from doing this for real:
- **Expect scans.** All four of the real district's MOUs arrived as scanned images with
  no text layer. OCR is a solved, one-time step (an afternoon), and the pipeline's
  citations survive it — but it's why we *never* promise "just upload and it works."
- **Heavy parsing is a workstation/build step, not a server step.** Real OCR'd contracts
  peak ~4GB memory to parse. We bake the corpus once and ship it; the cloud instance
  never re-parses.
- The extraction is reviewed like any import: parse coverage per document is asserted,
  not assumed, and a document that extracts nothing is a loud error, not a silent gap.

**What the customer sees in week 1:** their own contracts, searchable, with a
click-to-the-clause answer for any policy question. Immediate utility, zero trust asked.

## 2 · Phase 1 — Trust (weeks 2–4): payroll-driven verification

This is the phase that *is* the product. We do not extract the whole rulebook — we
proved that fails (our first attempt produced 33 competing rules on one MOU, an
unreviewable queue, and totals nobody could defend). Instead:

1. **Walk the payroll's pay codes** (OT, HOL, differential, premium…). Each code that
   pays real money becomes a **verification scenario**: real classifications, real
   inputs, the real amount actually paid.
2. Per scenario, the system **retrieves only the clauses that branch needs** and drafts
   the few rules they require — typically 3–5, not 33.
3. The analyst **reviews each rule beside its highlighted clause** and approves.
   Approval is *blocked* unless the rule set reproduces the known amount — a misread
   longevity step cannot go live, it goes red with the wrong number named.
4. Repeat per pay code. **Coverage is measured, not claimed**: "we reproduce N of your
   real pay lines to the cent" — and that sentence is the sales artifact.

Two postures we hold and say out loud:
- **Uncovered = refusal, not a guess.** A costing question with no ratified rule is
  refused with a reason. Refusals are the roadmap: each one names the next branch to
  author. Safe while incomplete, and incompleteness is visible.
- **The forgotten side letter is found by money, not memory.** If an old side letter
  pays anything, it shows up in payroll reconciliation even when nobody remembers the
  clause. (The prototype demonstrably handles supersession: the same question on two
  dates returns two correct totals because the amendment replaces the base clause
  mid-term.)

**Exit criterion for Phase 1:** the pay branches the customer actually asks about
reproduce their payroll to the cent, each rule human-approved and clause-cited.

## 3 · Phase 2 — Operate (months 2–3): from answers to negotiation support

With a verified rule layer, speed is free — every scenario computed on trusted rules is
instant *and* defensible. This is where the negotiation-table features land:

- **Proposal costing** ("cost a 3% raise for the unit"): baseline vs modified rates
  through the *same* rules, diffed. Flagged honestly as extrapolation — no paycheck has
  paid the raised rate yet.
- **Burden roll-up** (pension/Medicare/workers' comp as configurable % rules): the
  compounding a finance director demands — a raise costs ~3% × (1 + burden rate), which
  for safety units is a third to half more than wages alone. We either include burdens
  or label the number *wages-only*; we never ship the understated figure silently.
- **Baseline anchoring against public data** where it exists (e.g. publicpay.ca.gov
  filings) — verify the annual baseline against externally reported wages, then deltas
  are arithmetic on a verified number.
- Cadence: new side letter or amendment → declared, ingested, its branch re-verified.
  Rule changes live in version control — "who changed the differential, when, against
  which clause" is the history itself.

## 4 · Phase 3 — Institutionalize (quarter 2+)

The demo-to-production gaps, named up front because a government buyer will ask:

| Gap | Why it matters | Path |
|---|---|---|
| **Identity** | Basic auth can't answer "*which* analyst ratified this rule" — the first question a union asks | SSO + per-user attribution on the approval gate |
| **Ledger durability** | The audit trail is the one artifact that can't be rebuilt | Move from file to append-only DB; backup/replication |
| **508/WCAG** | Procurement gate, not polish | Built to AA patterns; formal audit + VPAT is a deliverable |
| **Data posture** | Contracts are public; payroll extracts are sensitive | Classifications-only design (no names/PII in the costing path); single-tenant deploy per jurisdiction; keys never in images |

## 5 · Risks and the honest answers (interview ammunition)

- *"Import tools always get our PDFs wrong."* — Agreed; that's why we don't sell
  hands-free import. We sell extraction **plus a review loop that catches what
  extraction gets wrong before it can touch a number**, gated on reproducing your own
  payroll. The review burden is small because drafting is scoped per scenario.
- *"What if it misreads one longevity step?"* — Then the known-answer check goes red
  with the wrong number named, and the rule can't be approved. That gate caught real
  drafting errors during our build (a weekend-bonus rule that dropped straight time; a
  court-time rule that would have hijacked holiday shifts).
- *"Can we trust AI with pay?"* — The AI never computes. It reads language and proposes
  rules; every dollar is produced by a deterministic engine from human-approved,
  clause-cited rules. The system runs (and reproduces its known answers) with the model
  switched off — and every answer displays whether AI was involved and where.
- *"Our contracts are scanned messes / 200 pages."* — So were the real ones we onboarded
  (four scanned MOUs, 1,800+ clauses). Most of a contract is never costable — it's
  quotable. The pay branches that matter are enumerated by your payroll codes: a finite
  list we cover one verified scenario at a time.

## 6 · The soundbites

- **"Searchable on day one, trusted by week four, at the table by month two."**
- **"We verify against your payroll, not our promises."**
- **"Coverage is a number: we reproduce N of your pay lines to the cent."**
- **"When we don't know, we refuse and tell you why — a refusal is a roadmap item, a
  wrong number is a lawsuit."**
- **"The AI reads; it never does the math."**

---

*Basis: everything above was exercised for real against the Central Fire District of
Santa Cruz County's published MOUs — scanned-document OCR, governance declaration,
scenario-scoped rule authoring, the approval gate catching drafting errors, supersession
by effective date, and a cloud deploy with the corpus baked and the audit ledger on a
persistent volume.*
