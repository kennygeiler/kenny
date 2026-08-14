# Kenny — Labor Relations Costing & Intelligence for Government
### Product Requirements Document

> Kenny answers questions about a government's labor contracts — what a shift costs, what an
> employee is entitled to, what a clause says — with **deterministic computation** and
> **clause-level citations you can see on the page**. It is built for any public employer that
> negotiates and administers collective bargaining agreements: cities, counties, districts, states.
>
> This document is the product: who it serves, what it does, what it must never do. The companion
> `ARCHITECTURE.md` is the engineering contract. Where they overlap, this owns *intent*.

---

## 1. Problem

Public employers make money decisions governed by dense, unstructured contracts — memoranda of
understanding, salary schedules, civil-service rules. *What does an overtime mandate cost? What
changes if this proposal passes? How many bereavement days does this employee get?* Today those
answers are produced by hand: slow, inconsistent, and hard to defend when a union, a council, or an
attorney challenges them.

The stakes are asymmetric. **Get the math wrong and a legislature approves a contract it cannot
fund. Get the interpretation wrong and the union grieves.**

Generic AI cannot fill this gap, and the reason is specific: **language models hallucinate numbers
and cannot show their work.** A confident, uncited dollar figure is worse than no tool — it is
believed once and distrusted forever. What government HR needs is an answer that is **fast, correct,
and defensible**: every dollar traceable to the exact clause of the governing document.

## 2. What Kenny is

Kenny ingests a jurisdiction's contracts, converts the parts that compute money into an auditable,
machine-executable form **under human approval**, and answers in plain language.

**A contract is a rulebook, and pay is one chapter of it.** *"What does this holiday shift cost?"*,
*"How many bereavement days does a sergeant get?"*, and *"How long do I have to file a
grievance?"* are all determinate questions with a citation. Costing is one *kind* of question, not
the whole product.

**The whole product in one picture** — AI reads and proposes on the left; deterministic code decides
and computes on the right; a human approves in between:

```
  ┌────── AI: reads & proposes ──────┐   ┌── HUMAN ──┐   ┌──── DETERMINISTIC: decides & computes ────┐
  contract ─▶ clauses + citations ───▶                ─▶                                              │
                                        review against  ─▶ governance ─▶ engine ─▶ cited $ + audit    │
  known answer ─▶ drafted rules ──────▶  a known answer                                               │
  question ─▶ intent + parameters ────────────────────▶ route ─▶ answer, every figure boxed on the page
```

## 3. Users

| User | Needs | Uses |
|---|---|---|
| **HR analyst / labor relations** | a defensible number or a cited clause to hand a decision-maker | Chat |
| **HR operations / compliance** | load contracts, approve the rules drawn from them, inspect the record | Admin |
| **Union · council · legal** | — | Nothing. The product's job is to make its output survive their scrutiny |

That third row is the design constraint: **Kenny's real audience never logs in.**

## 4. What Kenny answers

| Intent | Example | How it is answered |
|---|---|---|
| **Costing** | "cost an 8-hour holiday shift for the graveyard classifications" | the deterministic engine computes it from approved rules; every step cited |
| **Entitlement** | "how many bereavement days does a sergeant get?" | same engine, a non-money result type (days, hours, a deadline) |
| **Lookup** | "what is a Sergeant's Step C rate?" | read the published figure off the salary schedule and quote it; nothing computed |
| **Policy Q&A** | "does a weekend worker still get the holiday premium?" | quote the governing clause; nothing computed |

**Costing and entitlement are the same machinery** — one engine, one approval gate, one audit record —
differing only in what the answer *means*. **Lookup and policy compute nothing**: a published rate is
*read*, a policy is *quoted*. The dividing line across all four is whether an arithmetic step exists.

## 5. Principles

These are the guarantees that make an answer defensible. Breaking one makes a whole class of the
product indefensible.

1. **The AI never decides the answer.** It translates language into structured data and proposes
   rules. Deterministic code selects the governing document, chooses the rules, and does all math.
   *The moment a number is computed, no model is in the loop.*
2. **Governance is a lookup, not a guess.** Which rules apply is fixed by **who** (bargaining unit)
   and **when** (date → which contract version is in force). Search is a fallback, never the spine.
3. **Nothing computes until a human approves it — and approval is earned.** A rule goes live only
   after it reproduces a *known answer* (§7). Approval alone is insufficient.
4. **Never bluff.** Unknown attribute, unresolvable governance, no approved rule → **ask** or
   **refuse with a reason**. Never guess silently.
5. **Everything is on the record.** Prompt, document chosen, each rule fired, each arithmetic step,
   every citation, every human decision → a tamper-evident ledger.
6. **Citations are anchored to the source.** Every figure links to a box on the original page. The
   reader sees the contract's own words, not a paraphrase.
7. **Configuration over code.** New jurisdictions, departments, and contracts are onboarded as
   **data** — never new engines.

## 6. The AI boundary — the answer to "what happens when the AI is wrong?"

Every AI touchpoint lives behind one interface, and none of them can act. The split is the product.

| The AI does (fallible, proposes) | Deterministic code does (trusted, decides) |
|---|---|
| classify the question's intent | resolve the governing contract (unit + date) |
| parse a prompt into structured parameters | select which rules apply and in what precedence |
| **draft** candidate rules from clauses | **all arithmetic**, rounding, flags |
| tag & summarize documents for search | verify rules against known answers |
| compose a policy answer **from retrieved text only** | the ledger, the audit trail, the citations |

The two sides exchange **structured data** — never a dollar figure. The drafter emits a proposed
rule; the engine emits a number. Every AI function has a **deterministic fallback**, so the product
runs end-to-end with no model available and still reproduces its known answers — proof the model is
not load-bearing.

**When a number appears on screen, it was computed by deterministic code from a rule a human
approved.** The AI's worst failure is a *worse draft a human rejects*, never a *wrong number a user
trusts*.

## 6a. The costing unit is a classification, not a person

Labor decisions are made about **classes of employee, not individuals**. Nobody asks "what does
*this named officer's* shift cost" — they ask *"what does a holiday shift cost for a graveyard
sergeant,"* or *"what does a 3% raise cost for teacher level 1s."* So the subject Kenny costs is a
**classification** — a bag of attributes (classification, step, shift, certifications, base rate) —
and **there are no employee names anywhere in the product.**

```
  classification:  "Sergeant Step C (Graveyard)"   base_rate 58 · shift Graveyard · Advanced POST
  answer        =  cost PER MEMBER of that classification
```

Every answer is **per member**. Kenny deliberately does not multiply by staffing counts — headcount
changes weekly, lives in the HR system, and the multiplication is arithmetic the reader does with a
number they trust. Kenny's job is the per-member figure that is *hard* to get right: the composed,
cited, contract-correct amount.

Why classifications, not people:
- **Stable across the year.** Staffing churns — hires, promotions, transfers. A named roster rots;
  *"Sergeant Step C, graveyard"* does not.
- **Privacy by construction.** No names, no PII. A classification is anonymous.
- **It matches the question.** Every member of a class computes identically, so the class *is* the
  answer.
- **Easier to provide.** The input is a **classification/position table** (class × step × shift ×
  certs × rate) — which HR already maintains — not a list of individuals.

A **known answer** is likewise a classification paystub — *"a graveyard bilingual Step C officer,
8-hour holiday = $1,063.44 per member"* — which verifies the rule for the whole class and does not
break when a person leaves.

**Where individuals still matter:** payroll *reconciliation* — auditing that a specific person was
paid correctly — needs the real population, and is a separate, audit-only mode layered on top. The
costing product itself never holds a name.

## 7. The trust model — how a contract becomes a defensible number

The claim Kenny has to earn: a real contract arrives as an unseen document, and the number that
comes out the other side survives a union challenge. It earns it by **never letting a rule compute
until it reproduces a known-correct answer.**

**A known answer is a real paystub** (or, absent one, a figure an analyst worked out by hand). It
names the classifications, the date, the inputs, and the per-member amount actually paid. To make Kenny able to cost a
scenario, you point it at that known answer; Kenny retrieves only the clauses that scenario needs,
drafts the handful of rules they require, and checks them against the amount. Only rules that
reproduce the known answer can be approved.

This inverts the naive approach. Kenny does **not** extract an entire contract into hundreds of
rules and ask a human to trust them. It models **only what a real paycheck can prove**, one scenario
at a time. A pay branch with no known answer is not broken — it is *not yet covered*, and honestly
shown as such.

**Why this is the right shape:**
- **Review is proportional to error, not to document size.** A human checks the handful of rules a
  known answer needs, and attention lands exactly where the machine and reality disagree.
- **Coverage is measurable and honest.** "We reproduce these paychecks to the cent" is a claim a
  court accepts; "we extracted 200 rules, trust us" is not.
- **Approval is meaningful.** A person signed off on *this rule against this clause*, and it
  reproduces *this real amount.*

**At scale, the known answer is the payroll export.** A jurisdiction's payroll run is thousands of
paychecks spanning every classification, step, shift, and hour count that actually occurred.
Reconciling rules against the whole run is population-level validation, not a spot check — and the
paychecks that *don't* reconcile are exactly the edge cases to review. (The engine already computes
per-person amounts; per-paycheck reconciliation is the concrete scale step — see `ARCHITECTURE.md`.)

**Honest limit:** prospective questions have no paycheck. *"What would a 3% raise cost?"* combines
approved rules in a way no historical check exercised — it is an **extrapolation** from validated
rules, and is flagged as such rather than presented as reconciled fact.

## 8. How pay composes

Compensation is **layered and additive**, and the rule model mirrors that or it produces
plausible-looking wrong numbers. A person's pay for a scenario is:

```
   ONE base formula        (regular XOR overtime XOR holiday-worked — mutually exclusive)
 × differentials           (a night-shift %, a hazard % — adjust the base rate, and stack)
 + premiums                (a bilingual stipend, a uniform allowance — independent, additive)
 − exceptions / caps       (a premium suppressed on a regular day off; a stacking limit)
   filtered by pay basis: a single-shift cost includes only per-hour and per-shift pay,
                          never a year of benefits
```

Each rule declares its **role** in this stack, so the engine composes rather than picks a winner.
This is what stops an annual uniform allowance or a court-time rule from being *added to*, or
*winning*, an eight-hour shift. **Precedence between rules is derived from the documents' own
authority** — a side letter overrides the MOU it amends, an MOU overrides a citywide policy — not
from a number the model invents.

## 9. Surfaces

**Chat** — ask in plain language. Every answer carries **click-into-proof**: a cost decomposes into
its rules and arithmetic; a policy answer quotes the clause; either drills to the exact box on the
PDF. Every answer also shows **whether the AI was involved and where** — so a reader can confirm no
figure was produced by a model.

**Admin** — organized by the flow that authors and audits rules:

| Section | Purpose |
|---|---|
| **Documents** | load contracts; each becomes searchable immediately (policy Q&A works before any rule exists) |
| **Verification** | the known answers; press *Draft the rules for this scenario* to author and check the rules a paystub needs |
| **Review queue** | the handful of drafted rules awaiting approval, each checkable against its clause |
| **Rule library** | the contract as Kenny executes it, in three honest parts: what **computes** (approved rules), what Kenny **declined to model** (clauses paying on a dimension the classification table lacks — named, so it can be added), and what is answered by **quoting**. The page to hand an auditor |
| **Audit** | the tamper-evident ledger; export for council, union, or legal |

## 10. Build status (honest)

**Real and working:** contract ingestion with clause-level citations (including recovered rate
tables); policy Q&A, lookup, entitlement, and costing over a multi-department corpus; deterministic
composition engine (base + differentials + premiums, pay-basis filtered, precedence by document
authority); scenario-scoped drafting and verification; human approval as a regression guard;
tamper-evident ledger with export; deployable behind two-role auth with a rate limit.

**Runs on a deterministic fallback where the model is absent** — proving the model is not
load-bearing. **Uses hand-seeded known answers and a synthetic sample corpus** for demonstration;
production replaces both with the customer's real contracts and payroll.

**Designed, not built:** payroll-export import with per-paycheck reconciliation (§7); an in-product
form to create a scenario without editing config; conversational resolution of missing parameters;
cross-contract comparison; a statutory layer (e.g. FLSA) as an additional cited source; the scale
data stores (§ARCHITECTURE).

## 11. Onboarding a jurisdiction

1. **Load the contracts** and declare, per document, who it binds and when it is in force. *(This is
   the one thing no machine can extract.)* Policy Q&A works immediately.
2. **Provide a classification table** — class × step × shift × certifications × rate, mapped to a
   baseline attribute vocabulary (§6a). Never named individuals. Missing attributes become
   questions, not blockers.
3. **Provide known answers** — real paystubs. Each is a scenario.
4. Per scenario, **draft, review, and approve** the few rules it needs.
5. **Ask.** Costing, lookup, entitlement, and policy — every answer cited, every assumption stated.

The correction path stays cheap: a new intent is a handler behind the router; a new attribute is one
vocabulary entry; new pay math is a new rule verified against a new paystub. **The spine does not
move.**

## 12. Guided test path (live demo, current corpus)

The deployed instance serves the **real published labor corpus of the Central Fire District of
Santa Cruz County** — four MOUs (all arrived as scans; OCR'd) and the master salary schedule. One
login opens both surfaces. A reviewer can verify every claim in this document in ten minutes:

1. **Ask the pre-filled question** — *"Cost an 8-hour overtime shift for a Firefighter/Paramedic
   (56 hr, top step)"* → **$640.80**, computed by the deterministic engine from an approved rule
   (1.5× per the Local 3535 MOU's Overtime Rate section; $53.40/hr from the Master Salary
   Schedule). Click the amount: the full arithmetic trace and the clause, boxed on the page.
2. **Ask a policy question** — *"What does the Firefighters Local 3535 MOU say about overtime?"* →
   a grounded quote with clickable citations. Open **AI involvement**: which steps a model
   performed, which were deterministic.
3. **Ask an entitlement** — *"How much bereavement leave does a firefighter get?"* → the
   contract's own words: *three (3) shifts* (Article XIV).
4. **Admin → Verification** — two known answers, both green, each naming its analyst-derived
   source. **Rule library** — the two approved rules, each citing its clause, plus what was
   *declined* (the drafted FLSA 182-hour cycle rules were reviewed and not approved — a
   different pay branch).
5. **Ask something unanswerable** — a costing question for a unit with no approved rules →
   Kenny **refuses with a reason** rather than guessing.
6. **Audit** — every step of everything above, in the tamper-evident ledger.

## 13. Non-goals

Kenny does not give legal advice, does not decide contract interpretation where the language is
genuinely ambiguous (it surfaces the ambiguity and asks), does not act on the user's behalf beyond
answering, and does not present an extrapolation as a reconciled fact. It is a decision-support tool
whose output a human owns.
