# Labor Costing Module — User Research Read & Next-Step Proposal
### Founding PM case response

---

## Q1 — The core problem: the job they're hiring this product to do

Read the eight quotes as one system and they split into two camps that appear to want
opposite things:

- **The speed camp.** Ron needs the union's number costed across 27 funds *today* — "by
  then the moment's gone." Donna needs the ridiculous 12% ask costed fast, "fast
  scenarios matter more than perfect ones."
- **The trust camp.** Chris won't hand council "a number I can't defend" without
  wages, pension, Medicare, and workers' comp line by line. Garry says one misread
  longevity step and "my credibility's gone." Ann has watched every import tool
  mangle an 80-page PDF. Ben got burned by a side letter nobody remembered.

The contradiction is intentional, and it's the signal. Donna says *"fast scenarios
matter more than perfect ones"*; Garry says one misread step and *"my credibility's
gone"* — those are directly opposed instructions. Ann says don't trust import; the
product's positioning promises minutes. You cannot satisfy both camps by averaging
them — you have to decide *why* the tension exists.

It exists because **costing is slow today precisely because trust is manual** — the
analyst's two days (Ron's quote) are spent *being* the trust layer: re-deriving rates,
checking side letters, reconciling the two systems Leslie describes. Speed and trust
aren't competing requirements; **the absence of automated trust is the speed problem.**
Donna's "fast over perfect" is a coping strategy for a world where perfect takes two
days — not a preference the product should encode.

> **The job: "Hand me a number I can defend, while the moment is still alive."**

Fast-but-wrong ends Garry's credibility. Slow-but-right loses Ron's moment. The product
exists to collapse that tradeoff — and the mechanism that collapses it is: **verify the
contract's rules once, against ground truth; then every scenario computed on those rules
is instant *and* defensible.** Trust is amortized up front; speed becomes free.

## Q2 — Whose feedback to prioritize, and what we consciously don't do (yet)

Weight the quotes by the size of the jurisdiction behind them — size is a proxy for
contract complexity, deal size, and how catastrophic a wrong number is:

| Voice | Size | What they're really asking | Weight |
|---|---|---|---|
| Chris (Finance Dir) | **Large** special district | defensible **loaded** cost — wages + pension + Medicare + workers' comp, line by line | anchor |
| Leslie (HR Dir) | **Large** county | what a position *actually* costs across systems | anchor |
| Ann (Finance Dir) | Medium city | extraction that admits fallibility + a correction loop | high |
| Ben (ER Mgr) | Medium city | side letters resolved, not remembered | high |
| Garry (HR Dir) | Small county | verified before staked | high (it's the same trust ask) |
| Ron (ER Mgr) | Small county | same-day scenario turnaround, fund splits | serve via composition |
| Donna (LR Lead) | Small city | fast rough scenarios | serve via composition |
| Tom (HR Lead) | Small city | benchmarking pairing | other module — out |

Read that way, the two **largest** buyers are asking the *same* question from two
directions: **"what does a position truly cost, and can I defend the decomposition?"**
Chris names the components (wages, pension, Medicare, workers' comp); Leslie's
cross-system reconciliation exists *because* no one system holds the loaded number.
That promotes the burden roll-up from a later enhancement into the MVP itself.

**Prioritize the trust cluster — Chris + Leslie (large), Ann + Ben (medium), Garry
(small, same ask).** They are the purchase blockers, they skew large, and one
architecture serves all five: rules extracted **under human review** (Ann), verified
against known-correct amounts (Garry), decomposed to the burden line (Chris, Leslie),
amendments resolved by effective date (Ben).

**Ron and Donna — the small-org speed camp — are the payoff, not the starting point.**
Once rules are trusted, a 27-fund allocation is a data mapping and "cost the 12%" is a
parameter change. Build trust first and speed is cheap; build speed first and every
output inherits Garry's credibility problem — and loses the large-org sale.

**Consciously not doing yet:**

- **Tom's benchmarking** — explicitly the other module, and a small-city voice.
  Costing and comparison pair *after* the costing number is trustworthy.
- **Ron's 27-fund allocation** — deferred, consciously: it's a position-to-fund
  mapping summed through the same engine, real but additive, and it serves a small
  county while the burden roll-up serves the large accounts. Month-three material.
- **Leslie's live budgeting/payroll integration** — her *job* (true position cost) is
  in the MVP via the loaded-cost roll-up; the *plumbing* (bidirectional sync) is an
  integration program that presupposes a trusted core. We start from an exported
  position/rate table and defer sync.
- **Full automated ingestion of any contract format** — Ann's quote is a warning label,
  not a feature request. We deliberately do *not* promise hands-free import; we promise
  extraction **plus a review loop that catches what extraction gets wrong** before it
  can touch a number.

## Q3 — What we'd test before committing engineering time

**H1 — Review-then-approve beats hands-free import.** Hypothesis: analysts will accept
(and prefer) approving a small set of extracted rules against the contract text, versus
trusting a bulk import. We tested a version of this while prototyping: extracting an
entire MOU at once produced ~33 competing rules and unusable review burden; scoping
extraction to what one verified scenario needs produced ~4 rules that a human can
actually check. *Test: put both flows in front of two analysts; measure time-to-first-
defensible-number and stated confidence.*

**H2 — "Verified against your own payroll" converts trust.** Hypothesis: Garry's trust
objection dissolves when the system reproduces a number his office already knows is
right (a real paystub, a past costed proposal) before he's asked to rely on it. *Test:
one demo per prospect seeded with their own known answer; watch whether the
conversation shifts from "can I trust it" to "when can I use it."*

**H3 — The sample data already contains the trust problem.** The provided roster shows
seven Step E Police Officers at five different hourly rates ($52.30–$57.77), and one
employee on Probation status five years after their 2021 hire date. Same grade, same
step, different pay means COLA vintages, longevity adds, or data drift — precisely the
"one misread longevity step" Garry fears, live in a 20-row sample. *Test: reconcile the
schedule-implied rate against the actual rate for every row; every mismatch is either a
rule we must model (longevity, COLA timing) or a data-quality finding the customer
wants surfaced. Either way the discrepancy report is a sales artifact.*

**H4 — Granularity: is per-classification enough for a first sale, or do fund
allocation and full burden roll-up block procurement?** *Test: ask one finance director
(a Chris) for a real costed proposal from a past negotiation and reproduce it; the gap
list is the true MVP scope.*

---

## Deliverable — the surface to build first, and the MVP

### The pick: a chat interface where you query and cost labor — with receipts

Before a tool can sit in a live negotiation, it has to be able to **answer questions
about labor at all**: what does the contract say, what is this classification's rate,
what does a shift cost, what is an employee entitled to — each answer defensible down
to the clause. That capability is the foundation everything in the research sits on:
Donna's fast scenarios, Ron's fund totals, and Chris's council packet are all *queries
against trusted rules*. So the first surface is the query-and-cost loop itself:

> Ask in plain language → get the cost, decomposed per classification, every line
> citing the contract clause that produced it, computed only from rules a human
> approved after they reproduced a known-correct amount.

### Why this is the right first step

1. **It is the purchase-blocker, solved.** Chris's line-by-line, Garry's verification,
   Ann's review loop, Ben's side-letter resolution — all live inside this one surface.
2. **Everything else composes on top.** "Cost a 3% raise" is this loop with modified
   rates, run twice, diffed. "Across 27 funds" is this loop summed through a
   position-to-fund table. Neither is credible until the underlying loop is.
3. **It's demonstrable in minutes with a jurisdiction's own documents** — which is the
   only demo that converts this audience.

### The MVP — built, not mocked

To test whether the trust mechanism actually works, I built a working prototype
(**Holly**) rather than a mockup:

- **Chat**: costing, entitlement ("how many bereavement days does a sergeant get"),
  rate lookup, and policy Q&A — every figure clicking through to the clause,
  highlighted on the source PDF.
- **Admin**: ingest contracts (searchable immediately); a **Verification** tab of
  known answers — press *"Draft the rules for this scenario"* and the system retrieves
  only the clauses that paystub needs, drafts the few rules they require, and checks
  them against the known amount; a **Review queue** where a human approves each rule
  against its highlighted clause; a tamper-evident audit ledger.
- **The trust rules, enforced in architecture**: the AI reads and proposes; it never
  computes. Every amount is produced by a deterministic engine from human-approved
  rules. A misdrafted rule cannot go live: approval is blocked unless the rule set
  reproduces the known answer (this gate caught real drafting errors during
  development — e.g. a weekend-bonus rule that dropped straight time).
- **Amendments handled by effective date**: the same question answered on two dates
  returns two different (correct) totals, because a side letter supersedes the base
  clause mid-term — Ben's problem, demonstrably solved.

**Live demo:** `[LIVE LINK]` · **Product spec:** PRD.md · **Technical design:**
ARCHITECTURE.md (both attached).

### Making it real with this case's data

The provided materials are exactly the onboarding shape the product expects:

- **The MOUs** at centralfiresc.org/2161/Salaries-Benefits become the document corpus —
  ingested, cited, queryable.
- **The attached roster CSV is already a classification table** — anonymized IDs (no
  PII), grade × step × rate — which matches the product's data model: costing keys on
  classifications, never on named people.
- **The rate discrepancies in that CSV** (H3 above) become the first verification
  scenarios: each one is either a longevity/COLA rule to model or a data finding to
  hand the customer.

### Scoping to the constraint: two months, one engineer, one designer, one PM

The prototype exists to make this plan credible — the architecture risk is already
retired, so the team hardens rather than invents:

- **Engineer (8 wks):** productionize the engine + ledger + deploy; add the **burden
  roll-up** (employer pension/FICA-Medicare/workers'-comp as configurable, cited rate
  rules — they are deterministic percentages, which is exactly what the rule engine
  already computes); single-tenant cloud deploy with auth.
- **Designer (8 wks):** the trust surfaces *are* the design problem — the review queue
  (approve a rule against its highlighted clause), the verification tab (known answers
  going green), and the cost breakdown a finance director would put in a council
  packet. The prototype's UI is a wireframe to react to, not a constraint.
- **PM:** onboard the real corpus (the case MOUs + roster), author the verification
  scenarios from known-correct amounts, run H1–H4 with 3–5 target users, own the
  demo script.

**In the MVP:** ingest → verify → approve loop; chat costing/lookup/entitlement/policy
with clause citations; **loaded cost** (wages + burden lines, Chris's exact list);
side-letter supersession; audit ledger. **Out:** benchmarking, fund allocation, live
HRIS sync, hands-free import — each named above with its reason.

### The next increment: cost a raise

With verified rules in place, proposal costing is a small, high-leverage addition: *"cost
a 3% across-the-board for the Police unit"* = adjust base rates, recompute through the
same approved rules, present the delta — same citations, same defensibility. That is
Donna's quote, answered: fast scenarios, on rules that were made trustworthy first.

---

*The evidence base for this document is the eight user quotes; the prototype exists to
make the proposal concrete and to de-risk the central assumption — that trust can be
made automatic enough to make speed free.*
