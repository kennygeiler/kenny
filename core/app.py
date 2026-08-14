"""FastAPI application — two surfaces over one pipeline (PRD 5.7).

  Chat  (/)       end-user: prompt -> route -> parse -> deterministic engine -> cited answer
  Admin (/admin)  ops: ingest, catalog/storage, rule-review gate, taxonomy, ledger, history

Every step appends to the hash-chained ledger. The LLM only routes/translates; the
engine does all math.
"""
from __future__ import annotations

import os
import re
import threading
import uuid

import yaml
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import audit, auth, governance, index, ingest, llm
from .caseio import default_case_dir, load_case
from .catalog import Catalog
from .engine import NoRuleApplies, calculate
from .pdfview import page_dims, render_page_with_bbox
from .retriever import CatalogLLMRetriever
from .ruledsl import SHIFT_BASES, Rule, load_rules, validate_rules

def _load_dotenv() -> None:
    """Load the repo's .env at startup so the server works with a plain `uvicorn`
    command — no --env-file flag required. Real environment variables always win.
    (Only the app entrypoint does this; tests import the modules directly and stay
    deterministic on the offline fallbacks.)"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, ".env")
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v and not os.environ.get(k):
            os.environ[k] = v


_load_dotenv()

CASE_DIR = default_case_dir()
TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
DEFAULT_YEAR = 2026  # assumed when a prompt gives a date without a year

app = FastAPI(title="Kenny")
# No-op locally; required on any shared deploy (see core/auth.py). The factory lets the
# middleware ledger auth events (failed logins, role denials, CSRF blocks) without a
# circular import.
auth.install(app, ledger_factory=lambda: _case().ledger())

# In-process job registry for async ingestion (large PDFs). Single-worker; a
# multi-worker deploy swaps this for a shared queue (PRD §8B).
_JOBS: dict[str, dict] = {}
# Guards check-then-register: bulk ingest (sync, threadpool) and upload (async, event
# loop) both create jobs, so the single-flight test must be atomic across them.
_JOBS_LOCK = threading.Lock()


def _register_job(**fields) -> str | None:
    """Register a new running job; None when one is already running.

    Single-flight across BOTH bulk ingest and uploads (TICKETS.md C5): each job is a
    thread plus docling doing minutes of torch work — two concurrent jobs double
    memory on a 2GB instance and race on the same catalog/index files. Completed jobs
    are pruned so the registry cannot grow without bound."""
    with _JOBS_LOCK:
        if any(j.get("status") == "running" for j in _JOBS.values()):
            return None
        while len(_JOBS) > 20:
            _JOBS.pop(next(iter(_JOBS)))
        job_id = uuid.uuid4().hex[:12]
        _JOBS[job_id] = {"status": "running", "result": None, "error": None, **fields}
        return job_id

_MAX_UPLOAD_BYTES = int(os.environ.get("KENNY_MAX_UPLOAD_MB", "50")) << 20


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _case():
    return load_case(CASE_DIR)


def _catalog(case) -> Catalog:
    return Catalog(case.path("catalog", "catalog.json"))


def _backend(case):
    return index.make_backend(case)


def _taxonomy(case) -> dict:
    p = case.path("taxonomy")
    if p and os.path.exists(p):
        with open(p) as f:
            return yaml.safe_load(f) or {}
    return {}


def _extraction(case) -> dict:
    p = case.path("extraction")
    if p and os.path.exists(p):
        with open(p) as f:
            return yaml.safe_load(f) or {}
    return {}


# SHA-256 of source PDFs, cached by (mtime, size) so serving doesn't re-hash a multi-MB
# file per request. An attacker who can also forge mtime+size defeats the cache until
# restart — the cache is a hot-path economy, not the security boundary; ingest and the
# stale-rule gate re-hash for real.
_HASH_CACHE: dict[str, tuple[float, int, str]] = {}


def _file_sha256(path: str) -> str:
    try:
        st = os.stat(path)
    except OSError:
        return ""
    cached = _HASH_CACHE.get(path)
    if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
        return cached[2]
    sha = ingest.sha256_file(path)
    _HASH_CACHE[path] = (st.st_mtime, st.st_size, sha)
    return sha


def _check_source_hash(cat, doc_id: str, pdf_path: str) -> tuple[bool, str, str]:
    """Does the file on disk still match the bytes that were ingested? (TICKETS.md A1)
    Returns (ok, expected, actual). A catalog entry with no recorded hash (pre-hashing
    ingest) passes — there is nothing to verify against until the next re-ingest."""
    entry = cat.get(doc_id) or {}
    expected = entry.get("pdf_sha256", "")
    if not expected:
        return True, "", ""
    actual = _file_sha256(pdf_path)
    return actual == expected, expected, actual


def _doc_integrity(case, cat, doc_ids: list[str], rules) -> list[str]:
    """Every provenance failure that must BLOCK a costing answer:
    a source PDF on disk that no longer matches its ingested hash, or a ratified rule
    whose citation was bound (at draft time) to different bytes than the catalog now
    holds. Returns human-readable problems; empty means the chain is intact."""
    problems: list[str] = []
    for d in doc_ids:
        entry = cat.get(d)
        if not entry:
            continue
        pdf_path = _resolve_pdf(case, d)
        if pdf_path:
            ok, expected, actual = _check_source_hash(cat, d, pdf_path)
            if not ok:
                problems.append(f"{d}: file on disk (sha {actual[:12]}) no longer "
                                f"matches the ingested document (sha {expected[:12]})")
    for r in rules:
        cit = r.citation
        entry = cat.get(cit.doc_id) or {}
        now = entry.get("pdf_sha256", "")
        if cit.doc_sha256 and now and cit.doc_sha256 != now:
            problems.append(f"rule {r.id}: ratified against {cit.doc_id} sha "
                            f"{cit.doc_sha256[:12]}, but the ingested document is now "
                            f"sha {now[:12]}")
    return problems


def _extraction_tier(parse_source: str, kind: str) -> str | None:
    """Human label for HOW a piece of cited text was extracted (OCR-4) — the trust
    signal at the moment of reading an answer. The ONE mapping; app.js only renders
    the `tier` field this stamps, so client and server can never disagree.

      recovered-* kind        -> "recovered layout"  (layout model misread the page;
                                                      text regrouped from span geometry)
      docling + normal/table  -> "text layer"        (digital text layer, exact bboxes)
      raw-text-fallback       -> "page-level"        (page text only; citations open
                                                      the page, not the clause)
      sidecar                 -> "sidecar extract"   (hash-bound sidecar extraction)
      anything else           -> None                (no claim beats a wrong claim)
    """
    if kind in ("recovered-row", "recovered-text"):
        return "recovered layout"
    if parse_source == "docling" and kind in ("text", "table-row"):
        return "text layer"
    if parse_source == "raw-text-fallback":
        return "page-level"
    if parse_source == "sidecar":
        return "sidecar extract"
    return None


def _enrich_citations(cat, result_dict: dict) -> dict:
    """Fill in each citation's page + bbox from the ingested document (docling) when
    the rule left them blank — so highlights land on the real section even for large
    PDFs whose coordinates aren't known at authoring time. Also stamps every citation
    with its extraction provenance (`parse_source`, `kind`, `tier` — OCR-4), looked up
    from the catalog entry of the cited document."""
    clause_cache: dict[str, list] = {}
    entry_cache: dict[str, dict] = {}
    for li in result_dict.get("line_items", []):
        for c in li.get("citations", []):
            doc_id = c.get("doc_id", "")
            entry = entry_cache.setdefault(doc_id, cat.get(doc_id) or {})
            clauses = clause_cache.setdefault(doc_id, cat.clauses(doc_id))
            kind, kind_found = "text", False
            for cl in clauses:
                if str(cl.get("clause")) != str(c.get("clause")):
                    continue
                if not kind_found:
                    kind = cl.get("kind") or "text"
                    kind_found = True
                if c.get("bbox"):
                    break                       # nothing left to fill
                if cl.get("bbox"):              # first match WITH coordinates wins,
                    c["bbox"] = cl["bbox"]      # exactly as before OCR-4
                    c["page"] = cl.get("page", c.get("page"))
                    break
            c["parse_source"] = entry.get("parse_source", "")
            c["kind"] = kind
            c["tier"] = _extraction_tier(c["parse_source"], kind)
    return result_dict


def _revalidate_citations(case, cat, doc_ids: list[str], led) -> list[dict]:
    """Re-check every ratified rule citing the (re-)ingested documents (TICKETS.md A3).

    A rule freezes its citation (clause, page, bbox, source sha) at draft time. After a
    re-ingest the evidence may have moved — a revised MOU renumbers a section, replaces
    a page, or is simply a different file. Such a rule is marked `stale` (with the
    reason), which excludes it from the engine via load_rules' ratified-only filter,
    and the change is ledgered. Silently keeping the old coordinates would highlight
    the wrong text under a confident citation — the exact failure this product exists
    to prevent. Rules whose evidence still checks out get their citation's source hash
    backfilled, so pre-hashing approvals become bound going forward."""
    library = _raw_ratified(case)
    stale, backfilled = [], 0
    for r in library:
        if r.get("status") != "ratified":
            continue
        cit = r.get("citation") or {}
        d = cit.get("doc_id")
        if d not in doc_ids:
            continue
        entry = cat.get(d) or {}
        problems = []
        sha_then, sha_now = cit.get("doc_sha256", ""), entry.get("pdf_sha256", "")
        if sha_then and sha_now and sha_then != sha_now:
            problems.append("the source PDF changed since ratification")
        clause = str(cit.get("clause") or "")
        if clause:
            matches = [c for c in entry.get("clauses", [])
                       if str(c.get("clause")) == clause]
            if not matches:
                problems.append(f"cited clause {clause} no longer exists in {d}")
            elif cit.get("page") and not any(c.get("page") == cit.get("page")
                                             for c in matches):
                problems.append(f"cited clause {clause} is no longer on page "
                                f"{cit.get('page')}")
        if problems:
            r["status"] = "stale"
            r["stale_reason"] = "; ".join(problems)
            stale.append({"rule_id": r.get("id"), "reason": r["stale_reason"]})
        elif not sha_then and sha_now:
            cit["doc_sha256"] = sha_now
            r["citation"] = cit
            backfilled += 1
    if stale or backfilled:
        _write_ratified(case, library)
    for s in stale:
        led.append("authoring.stale", s, actor="system")
    return stale


def _doc_meta(case, doc_id: str) -> dict:
    s = case.source_by_id(doc_id) or {}
    return {"doc_id": doc_id, "title": s.get("title", doc_id),
            "department": s.get("department"), "doc_type": s.get("doc_type")}


def _source_entry(case, cat, h: dict) -> dict:
    """One chat source chip's payload: document metadata + the hit's citation fields +
    extraction provenance (parse_source / kind / tier — OCR-4). `kind` may be missing
    from a hit produced by a backend that predates it (OpenSearch) — treat as text."""
    parse_source = (cat.get(h["doc_id"]) or {}).get("parse_source", "")
    kind = h.get("kind") or "text"
    return {**_doc_meta(case, h["doc_id"]), "clause": h["clause"], "page": h["page"],
            "bbox": h["bbox"], "text": h["text"], "score": h["score"],
            "parse_source": parse_source, "kind": kind,
            "tier": _extraction_tier(parse_source, kind)}


def _is_rate_row(hit: dict) -> bool:
    """A retrieved chunk that is a row of a rate table rather than prose about pay."""
    text = hit.get("text") or ""
    return "|" in text and "$" in text


def _dept_scope(case, cat, dept) -> list[str]:
    """Candidate documents for a department, restricted to what is actually INGESTED.

    The ONE scoping helper for every retrieval path (TICKETS.md B1). It encodes two
    hard-won rules together so no path can drift with only one of them again:
      1. case.yaml declares the intended corpus; the catalog holds what really parsed.
         A document with no clauses in the index is a plan, not a candidate.
      2. Callers must treat an EMPTY scope as "nothing to search" — never pass [] to
         backend.search, which reads it as "no filter" and would leak every other
         department's contracts into the answer.
    """
    ingested = {d["doc_id"] for d in cat.documents()}
    return [d for d in case.docs_for_department(dept) if d in ingested]


_ASKS_EVERYONE_RE = re.compile(r"\b(all|every|each|entire|whole|everyone|roster)\b", re.I)


def _clarify(qid: str, prompt: str, question: str, options: list | None = None) -> dict:
    return {"query_id": qid, "mode": "clarify", "prompt_echo": prompt,
            "question": question, "options": options or []}


def _policy_answer(case, led, qid: str, prompt: str, department: str | None = None,
                   lookup: bool = False) -> dict:
    """Policy Q&A over a multi-department CORPUS.

    1. Identify the department (stated in the question, or asked for).
    2. Shortlist candidate documents from metadata (department + citywide policies).
    3. Hybrid-search within the candidates for the governing clause(s).
    4. Answer strictly from retrieved text, showing which documents were considered and
       which were used — each drilling to the exact section on the PDF.

    `lookup=True` serves a figure the documents already PUBLISH — a Step C rate off a
    salary schedule. It is the same retrieve-and-quote spine, and deliberately so: a
    published rate needs no rule, no golden and no engine, because there is no arithmetic
    to get wrong. Routing it through costing made Kenny refuse a number it was holding.
    """
    cat = _catalog(case)
    backend = _backend(case)
    departments = case.departments()

    # 1. Which unit's contract? Stated, previously confirmed, or unknown.
    dept = department or llm.extract_department(prompt, departments)
    led.append("policy.department", {"department": dept, "stated": bool(department),
                                     "known_departments": departments},
               actor="chat", query_id=qid)

    # 2. Candidate shortlist from metadata — via the one shared scoping helper (see
    #    _dept_scope for why declared-vs-ingested and the empty-scope guard must always
    #    travel together).
    ingested = {d["doc_id"] for d in cat.documents()}
    scope = _dept_scope(case, cat, dept)
    led.append("retrieval.candidates",
               {"department": dept, "candidate_docs": scope, "corpus_size": len(ingested),
                "declared": len(case.manifest.get("sources", []))},
               actor="chat", query_id=qid)

    # 3. Hybrid search within candidates. An empty scope means nothing relevant has been
    #    ingested yet — do NOT call search([]), which the backend reads as "no filter, use
    #    the whole index" and would leak another department's documents.
    hits = backend.search(prompt, doc_ids=scope, k=6) if scope else []
    if lookup:
        # A published rate lives in a table row, and an MOU's prose ABOUT pay ("the base
        # rate shall be as set forth in Appendix A") out-scores the row that holds the
        # number. Float the rows the recovered-table parser produced; keep the prose
        # behind them rather than dropping it, since the schedule may not be the whole
        # answer.
        hits.sort(key=lambda h: 0 if _is_rate_row(h) else 1)
    led.append("retrieval.hits",
               {"hits": [{k: h[k] for k in ("doc_id", "clause", "page", "score")} for h in hits]},
               actor="chat", query_id=qid)

    # If the department wasn't stated and the evidence spans several units, the answer
    # differs by contract -> ask rather than pick one (PRD §3.3 never bluff).
    if not dept and hits:
        hit_depts = []
        for h in hits:
            d = (case.source_by_id(h["doc_id"]) or {}).get("department")
            if d and d != "citywide" and d not in hit_depts:
                hit_depts.append(d)
        if len(hit_depts) > 1:
            led.append("policy.clarify",
                       {"reason": "evidence spans multiple departments",
                        "options": hit_depts}, actor="chat", query_id=qid)
            return {"query_id": qid, "mode": "clarify", "prompt_echo": prompt,
                    "question": "That's answered differently by each unit's contract. "
                                "Which department?",
                    "options": hit_depts,
                    "considered": [_doc_meta(case, h["doc_id"]) for h in hits]}

    if not hits:
        return {"query_id": qid, "mode": "lookup" if lookup else "policy",
                "answer_source": "none",
                "answer": "I couldn't find a clause covering that in "
                          + (f"the {dept} documents." if dept else "the corpus."),
                "sources": [], "department": dept,
                "considered": [_doc_meta(case, d) for d in scope]}

    # 4. Grounded answer + provenance.
    ans = llm.answer_policy(prompt, hits, lookup=lookup)
    led.append("policy.answer", {"answer": ans["answer"], "source": ans["source"],
                                 "lookup": lookup,
                                 "docs_used": sorted({h["doc_id"] for h in hits[:4]})},
               actor="chat", query_id=qid)
    for h in hits[:4]:
        led.append("citation", {k: h[k] for k in ("doc_id", "clause", "page", "bbox")},
                   actor="engine", query_id=qid)
    return {"query_id": qid, "needs_confirmation": False,
            "mode": "lookup" if lookup else "policy",
            "answer": ans["answer"], "answer_source": ans["source"], "department": dept,
            "corpus_size": len(ingested),
            "considered": [_doc_meta(case, d) for d in scope],
            "sources": [_source_entry(case, cat, h) for h in hits[:4]]}


def _entitlement_answer(case, led, qid: str, prompt: str,
                        department: str | None = None) -> dict:
    """Answer a non-money rule question — "how many bereavement days?", "what's the
    grievance deadline?" — with the SAME guarantees as a dollar figure.

    An MOU is a rulebook; pay is one chapter. These clauses are equally determinate and
    deserve the deterministic engine + citation, not an LLM paraphrase.

    Retrieval finds the governing clause; if a ratified rule CITES that clause, the
    engine computes the typed value. If no rule covers it, fall back to quoting the
    contract (policy), which is always better than guessing.
    """
    backend = _backend(case)
    departments = case.departments()
    dept = department or llm.extract_department(prompt, departments)
    # Same scoping discipline as _policy_answer (TICKETS.md B1). This path used to scope
    # by the DECLARED corpus and pass an empty list straight to search — which the
    # backend reads as "no filter" — so a department with no ingested documents was
    # answered from every OTHER department's contracts.
    scope = _dept_scope(case, _catalog(case), dept)
    hits = backend.search(prompt, doc_ids=scope, k=6) if scope else []
    led.append("entitlement.retrieval",
               {"department": dept, "candidates": len(scope),
                "hits": [{k: h[k] for k in ("doc_id", "clause", "score")} for h in hits]},
               actor="chat", query_id=qid)

    # rules whose cited clause is among the retrieved evidence, excluding currency
    hit_keys = {(h["doc_id"], str(h["clause"])) for h in hits if h.get("clause")}
    rules = [r for r in case.rules()
             if r.result_type != "currency"
             and (r.citation.doc_id, str(r.citation.clause)) in hit_keys]
    subjects_all = case.subjects()
    params = llm.parse_intent(prompt, _extraction(case), subjects_all)
    if params.get("unverified_numbers"):
        return _clarify(qid, prompt,
                        "I read a number out of that question that it doesn't actually "
                        "state — can you restate it with the amount spelled out?")
    named = set(params.get("subjects") or [])
    subjects = [s for s in subjects_all if s.get("name") in named]

    if not rules or not subjects:
        # No ratified rule for this clause (or nobody named) -> quote the contract.
        led.append("entitlement.fallback",
                   {"reason": "no ratified non-currency rule for the retrieved clauses"
                              if not rules else "no subject named"},
                   actor="chat", query_id=qid)
        return _policy_answer(case, led, qid, prompt, department)

    eng = {"hours": params.get("hours", 0.0), "date": params.get("date", ""),
           "date_iso": governance.parse_date(params.get("date"), DEFAULT_YEAR) or "",
           "holiday_weekday": params.get("holiday_weekday", "")}
    try:
        result = calculate(eng, subjects, rules, case.rounding_places())
    except ValueError:
        return _policy_answer(case, led, qid, prompt, department)

    rd = _enrich_citations(_catalog(case), result.to_dict())
    led.append("answer.snapshot", {"total": result.total, "intent": "entitlement"},
               actor="engine", query_id=qid)
    return {"query_id": qid, "needs_confirmation": False, "mode": "entitlement",
            "department": dept, "params": params, "result": rd,
            "corpus_size": len(case.manifest.get("sources", []))}


def _retriever() -> CatalogLLMRetriever:
    return CatalogLLMRetriever()


# --------------------------------------------------------------------------- #
# pages
# --------------------------------------------------------------------------- #
_NOCACHE = {"Cache-Control": "no-store, max-age=0"}


@app.get("/healthz")
def healthz():
    """Platform liveness probe. Verifies the ledger chain so a corrupted audit trail
    surfaces as an unhealthy instance rather than as a wrong answer months later —
    but this endpoint is UNAUTHENTICATED, so it reports only pass/fail; the tamper
    detail ("event at seq N") is on /admin/ledger, behind the admin credential."""
    ok, _msg = _case().ledger().verify()
    return JSONResponse({"status": "ok" if ok else "degraded"},
                        status_code=200 if ok else 503)


@app.get("/", response_class=HTMLResponse)
def chat_page():
    return FileResponse(os.path.join(TEMPLATES, "chat.html"), headers=_NOCACHE)


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return FileResponse(os.path.join(TEMPLATES, "admin.html"), headers=_NOCACHE)


@app.get("/static/app.js")
def app_js():
    return FileResponse(os.path.join(TEMPLATES, "app.js"),
                        media_type="application/javascript", headers=_NOCACHE)


@app.get("/static/tour.js")
def tour_js():
    """Guided demo tour: one shared step engine for both surfaces (chat starts it,
    /admin?tour=1 resumes it). Served like app.js so both templates can include it."""
    return FileResponse(os.path.join(TEMPLATES, "tour.js"),
                        media_type="application/javascript", headers=_NOCACHE)


@app.get("/static/styles.css")
def styles_css():
    return FileResponse(os.path.join(TEMPLATES, "styles.css"),
                        media_type="text/css", headers=_NOCACHE)


@app.get("/api/case")
def api_case():
    case = _case()
    return {"name": case.manifest.get("name"), "department": case.manifest.get("department"),
            "has_api_key": llm.have_key(),
            # Set on the shared deploy. A hosted link gets mistaken for a product; this
            # is a prototype on a synthetic corpus and every viewer must be told so
            # before they read a dollar figure off it.
            "banner": os.environ.get("KENNY_BANNER", "")}


# --------------------------------------------------------------------------- #
# CHAT
# --------------------------------------------------------------------------- #
@app.post("/chat")
async def chat(request: Request):
    """Public entrypoint. Wraps the handler so that EVERY AI call made while answering
    lands in the ledger, whichever of the handler's several exits is taken — including
    the ones that refuse, and including the ones that fail.

    A try/finally rather than draining before each return: an exit that forgets to record
    is an answer with no evidence of how it was reached, and the handler grows exits.
    """
    body = await request.json()
    qid = body.get("query_id") or uuid.uuid4().hex[:12]
    led = _case().ledger()
    with llm.record() as trail:
        try:
            return await _chat(body, qid)
        finally:
            for call in trail:
                led.append("llm.call", call, actor="llm", query_id=qid)


async def _chat(body: dict, qid: str):
    prompt = (body.get("prompt") or "").strip()
    forced_doc = body.get("doc_id")  # set when the user confirms a document

    case = _case()
    led = case.ledger()
    cat = _catalog(case)

    department = body.get("department")  # set when the user answers "which department?"
    if not forced_doc:
        led.append("chat.prompt", {"text": prompt, "department": department},
                   actor="chat", query_id=qid)
        # Router: costing vs policy Q&A. Both return an answer WITH clickable proof.
        intent = llm.classify_intent(prompt)
        led.append("intent.classify", {"intent": intent}, actor="chat", query_id=qid)
        if intent == "policy":
            return _policy_answer(case, led, qid, prompt, department)
        if intent == "lookup":
            return _policy_answer(case, led, qid, prompt, department, lookup=True)
        if intent == "entitlement":
            return _entitlement_answer(case, led, qid, prompt, department)

    # 1. Parse intent (LLM translation layer, with deterministic fallback).
    subjects_all = case.subjects()
    params = llm.parse_intent(prompt, _extraction(case), subjects_all)
    led.append("llm.parse_intent", params, actor="chat", query_id=qid)

    # A model-extracted number the question doesn't contain never reaches the engine
    # (TICKETS.md B3) — ask, don't multiply by it.
    unverified = params.get("unverified_numbers")
    if unverified:
        led.append("costing.clarify",
                   {"reason": "model-extracted number absent from the question",
                    "unverified": unverified}, actor="chat", query_id=qid)
        return _clarify(qid, prompt,
                        f"I read {unverified.get('hours')} hours out of that, but the "
                        "question doesn't state that number. How long is the shift?")

    # 2. Select the subjects named in the prompt, and derive their bargaining unit(s)
    #    + the shift date — the governance keys. A question that names NOBODY is asked
    #    who it is for (TICKETS.md B4) — silently costing the entire roster turned a
    #    vague question into one large confident total.
    named = set(params.get("subjects") or [])
    subjects = [s for s in subjects_all if s.get("name") in named]
    if not subjects:
        if _ASKS_EVERYONE_RE.search(prompt):
            subjects = subjects_all
        else:
            led.append("costing.clarify", {"reason": "no subject named"},
                       actor="chat", query_id=qid)
            examples = ", ".join(str(s.get("name")) for s in subjects_all[:3])
            return _clarify(qid, prompt,
                            "Who is this for? Name a classification (e.g. "
                            f"{examples}) — or say 'all classifications' to cost the "
                            "whole roster.")
    units = sorted({s.get("bargaining_unit") for s in subjects if s.get("bargaining_unit")})
    date_iso = governance.parse_date(params.get("date"), default_year=DEFAULT_YEAR)
    led.append("data.read",
               {"adapter": case.manifest.get("data", {}).get("adapter"),
                "rows": [s.get("name") for s in subjects],
                "bargaining_units": units, "shift_date": date_iso,
                "fields": list((subjects[0].keys() if subjects else []))},
               actor="chat", query_id=qid)

    # 3. Resolve the governing document(s). PRIMARY path = deterministic governance
    #    (unit + date -> MOU). FALLBACK = LLM retrieval when governance can't resolve.
    sources = case.manifest.get("sources", [])
    routing_path = None
    if forced_doc:
        chosen_docs = [forced_doc]
        routing_path = "user-confirmed"
        led.append("retrieval.select", {"chosen": forced_doc, "confirmed_by_user": True},
                   actor="chat", query_id=qid)
    else:
        gov = governance.resolve(units, date_iso, sources)
        led.append("governance.resolve",
                   {"units": gov.units, "date": gov.date, "resolved": gov.resolved,
                    "matched": gov.matched, "reason": gov.reason},
                   actor="chat", query_id=qid)
        if gov.resolved:
            chosen_docs = gov.doc_ids
            routing_path = "governance"
        else:
            routing = _retriever().route(prompt, cat, _backend(case))
            led.append("retrieval.shortlist",
                       {"candidates": routing.candidates, "reason": routing.reason},
                       actor="chat", query_id=qid)
            if routing.needs_confirmation:
                led.append("retrieval.confirm",
                           {"candidates": routing.candidates, "reason": routing.reason},
                           actor="chat", query_id=qid)
                options = [
                    {"doc_id": c["doc_id"],
                     "title": (cat.get(c["doc_id"]) or {}).get("title", c["doc_id"]),
                     "score": c.get("score")}
                    for c in routing.candidates
                ]
                return {"query_id": qid, "needs_confirmation": True,
                        "reason": routing.reason, "options": options,
                        "message": "I couldn't determine the governing document from the "
                                   "employees' bargaining unit. Which document should I use?"}
            chosen_docs = [routing.chosen_doc_id]
            routing_path = "retrieval-fallback"
            led.append("retrieval.select",
                       {"chosen": routing.chosen_doc_id,
                        "within_doc_matches": routing.within_doc_matches},
                       actor="chat", query_id=qid)

    chosen = ", ".join(chosen_docs)

    # 4. Deterministic engine — rules from the governing document(s) only.
    rules = [r for r in case.rules()
             if (not r.citation.doc_id) or (r.citation.doc_id in chosen_docs)]
    # Costing answers a MONEY question: only currency rules compete. Leave/deadline
    # rules live in the same library and are answered by the entitlement handler.
    rules = [r for r in rules if r.result_type == "currency"]
    # An amendment replaces clauses of its base MOU. Without this, both the base rule
    # and the amending rule load and STACK (see governance.apply_supersession).
    rules, dropped = governance.apply_supersession(
        rules, case.manifest.get("sources", []), chosen_docs)
    if dropped:
        led.append("governance.supersession", {"dropped": dropped},
                   actor="engine", query_id=qid)
    if not rules:
        # No human-ratified rules for this document yet — never guess a number. If rules
        # exist but were marked STALE (their cited evidence changed on re-ingest), say
        # that: the fix is re-verification, not authoring from scratch.
        stale = [r.get("id") for r in _raw_ratified(case)
                 if r.get("status") == "stale"
                 and (r.get("citation") or {}).get("doc_id") in chosen_docs]
        led.append("costing.blocked",
                   {"reason": "rules pending re-verification" if stale
                    else "no ratified rules", "doc": chosen_docs, "stale": stale},
                   actor="engine", query_id=qid)
        if stale:
            return {"query_id": qid, "needs_confirmation": False, "mode": "blocked",
                    "chosen_doc": chosen, "message":
                        f"I can't cost this right now: the rules for **{chosen}** are "
                        f"pending re-verification ({len(stale)} rule(s) were marked "
                        "stale because their source document changed since they were "
                        "ratified). Re-verify them in Admin → Rule review before "
                        "costing resumes."}
        return {"query_id": qid, "needs_confirmation": False, "mode": "blocked",
                "chosen_doc": chosen, "message":
                    f"I can't cost this yet: **{chosen}** has no human-ratified rules. "
                    "Policy questions still work (I can quote the document). To enable "
                    "costing, go to Admin → Ingest, review the drafted rules, and "
                    "approve them — nothing computes until a human ratifies it."}
    problems = _doc_integrity(case, cat, chosen_docs, rules)
    if problems:
        led.append("costing.blocked", {"reason": "provenance mismatch",
                                       "problems": problems},
                   actor="engine", query_id=qid)
        return {"query_id": qid, "needs_confirmation": False, "mode": "blocked",
                "chosen_doc": chosen, "message":
                    "I can't cost this: the source documents no longer match what the "
                    "rules were ratified against. Re-ingest and re-verify before "
                    "answering. Details: " + "; ".join(problems)}
    eng_params = {"hours": params.get("hours", 0.0),
                  "date": params.get("date", ""),
                  "date_iso": date_iso or "",
                  "holiday_weekday": params.get("holiday_weekday", "")}
    try:
        # A shift-cost question includes hourly and per-shift pay only — never a year of
        # benefits. Annual/monthly/per-period terms are the wrong unit for "what does this
        # shift cost?" and are filtered out (they answer a different question).
        result = calculate(eng_params, subjects, rules, case.rounding_places(),
                           basis_scope=SHIFT_BASES)
    except ValueError as e:
        led.append("costing.blocked", {"reason": str(e), "doc": chosen_docs},
                   actor="engine", query_id=qid)
        return {"query_id": qid, "needs_confirmation": False, "mode": "blocked",
                "chosen_doc": chosen, "message":
                    f"The ratified rules for **{chosen}** don't cover this scenario "
                    f"({e}). Approve the rules that do in Admin → Rule review, or ask a "
                    "policy question — I can still quote the document."}

    # 5. Ledger: decision logic + math + citations, straight from the engine trace.
    for li in result.line_items:
        for step in li.trace:
            if step.kind in ("modifier", "selector-considered", "selector-chosen", "math", "flag"):
                led.append(f"rule.{step.kind.replace('-', '_')}",
                           {"subject": li.subject, "rule_id": step.rule_id,
                            "detail": step.detail, "value": step.value},
                           actor="engine", query_id=qid)
        for c in li.citations:
            led.append("citation", {"subject": li.subject, **c},
                       actor="engine", query_id=qid)

    # 6. Snapshot + answer.
    snap = audit.snapshot(case.path("snapshots", "snapshots"), qid, eng_params, rules,
                          result.to_dict())
    led.append("answer.snapshot", {"total": result.total, "snapshot": os.path.basename(snap)},
               actor="engine", query_id=qid)

    result_dict = _enrich_citations(_catalog(case), result.to_dict())
    return {"query_id": qid, "needs_confirmation": False, "mode": "costing",
            "chosen_doc": chosen, "routing_path": routing_path, "bargaining_units": units,
            "shift_date": date_iso, "params": params, "result": result_dict}


@app.get("/chat/audit/{query_id}")
def chat_audit(query_id: str):
    case = _case()
    events = audit.trail(case.ledger(), query_id)
    # Summarise the AI involvement up front. "How did AI reach this answer" starts with
    # whether AI was involved at all — and with a deterministic fallback behind every
    # touchpoint, that is a real question with a non-obvious answer.
    calls = [e["payload"] for e in events if e.get("type") == "llm.call"]
    ai = {"calls": calls,
          "used_model": any(c.get("source") == "claude" for c in calls),
          "fell_back": [c["fn"] for c in calls if c.get("source") == "fallback"],
          "errors": [c for c in calls if c.get("source") == "error"],
          "total_ms": sum(c.get("ms", 0) for c in calls)}
    return {"query_id": query_id, "events": events, "ai": ai}


def _resolve_pdf(case, doc_id: str) -> str | None:
    """Map a doc_id to a PDF on disk, or None.

    doc_id arrives from the URL, so it is never joined into a path directly: it must
    match a document the case declares or the catalog holds, and the file it names must
    resolve inside the case directory. Otherwise "../../../etc/passwd" is a doc_id and
    the file-serving route below is an arbitrary-read.
    """
    src = case.source_by_id(doc_id)
    if src:
        pdf_path = src["file"]
        if not os.path.isabs(pdf_path):
            pdf_path = os.path.join(case.dir, pdf_path)
    else:
        entry = _catalog(case).get(doc_id)  # uploaded doc
        if not entry:
            return None
        pdf_path = entry["file"]
    pdf_path = os.path.realpath(pdf_path)
    if os.path.commonpath([pdf_path, os.path.realpath(case.dir)]) != os.path.realpath(case.dir):
        return None
    return pdf_path if os.path.exists(pdf_path) else None


@app.get("/doc/{doc_id}/file")
def doc_file(doc_id: str):
    """Serve the original PDF.

    The rendered-page image proves a citation, but a reader who wants to check the
    contract as a whole — scroll it, search it, print it for a council packet — needs
    the document itself, not a picture of one page of it. Inline so a click opens the
    browser's viewer rather than downloading.
    """
    case = _case()
    pdf_path = _resolve_pdf(case, doc_id)
    if not pdf_path:
        return JSONResponse({"error": "unknown or unavailable document"}, status_code=404)
    ok, expected, actual = _check_source_hash(_catalog(case), doc_id, pdf_path)
    if not ok:
        case.ledger().append("provenance.mismatch",
                             {"doc_id": doc_id, "expected": expected, "actual": actual,
                              "route": "doc_file"}, actor="system")
        return JSONResponse({"error": "source document changed since ingestion — "
                             "re-ingest and re-verify before it can be served"},
                            status_code=409)
    return FileResponse(pdf_path, media_type="application/pdf",
                        headers={"Content-Disposition":
                                 f'inline; filename="{os.path.basename(pdf_path)}"'})


@app.get("/doc/{doc_id}/page/{page}")
def doc_page(doc_id: str, page: int, bbox: str = ""):
    case = _case()
    pdf_path = _resolve_pdf(case, doc_id)
    if not pdf_path:
        return JSONResponse({"error": "unknown doc"}, status_code=404)
    ok, expected, actual = _check_source_hash(_catalog(case), doc_id, pdf_path)
    if not ok:
        case.ledger().append("provenance.mismatch",
                             {"doc_id": doc_id, "expected": expected, "actual": actual,
                              "route": "doc_page"}, actor="system")
        return JSONResponse({"error": "source document changed since ingestion — "
                             "the citation cannot be rendered against it"},
                            status_code=409)
    try:
        box = [float(x) for x in bbox.split(",")] if bbox else []
    except ValueError:
        return JSONResponse({"error": "bbox must be comma-separated numbers"},
                            status_code=400)
    png = render_page_with_bbox(pdf_path, page, box)
    if png is None:
        return JSONResponse({"error": "render unavailable"}, status_code=503)
    return Response(content=png, media_type="image/png")


@app.get("/doc/{doc_id}/clauses")
def doc_clauses(doc_id: str, page: int = 1):
    """Every clause extracted from one page, plus the page's dimensions in PDF points
    (OCR_TICKETS.md OCR-1). The X-ray overlay draws each bbox over the rendered page
    image; without width/height the client cannot scale point-space boxes onto the
    image, whose pixel size depends on the render scale.

    `kind` is normalised here — the catalog omits it for normal text, but the overlay
    colour-codes by kind and an absent key would make every consumer re-implement the
    default. Likewise `low_confidence` (OCR-7): the catalog stamps it only on clauses
    from pages docling's OCR scored below LOW_OCR_CONFIDENCE, and the endpoint
    normalises it to an always-present boolean plus a page-level `ocr_confidence`
    score (null on pages that were never OCR'd — every digital page).
    """
    case = _case()
    pdf_path = _resolve_pdf(case, doc_id)
    if not pdf_path:
        return JSONResponse({"error": "unknown doc"}, status_code=404)
    dims = page_dims(pdf_path, page)
    if dims is None:
        return JSONResponse({"error": "page metrics unavailable"}, status_code=503)
    page, page_count, width, height = dims
    cat = _catalog(case)
    clauses = [{"clause": c.get("clause", ""), "kind": c.get("kind") or "text",
                "page": page, "bbox": c.get("bbox", []), "text": c.get("text", ""),
                "low_confidence": bool(c.get("low_confidence"))}
               for c in cat.clauses(doc_id) if c.get("page") == page]
    entry = cat.get(doc_id) or {}
    conf = (entry.get("page_confidence") or {}).get(str(page))
    return {"doc_id": doc_id, "page": page, "page_count": page_count,
            "width": width, "height": height, "clauses": clauses,
            "ocr_confidence": conf,
            "low_confidence": conf is not None and conf < ingest.LOW_OCR_CONFIDENCE}


# --------------------------------------------------------------------------- #
# ADMIN
# --------------------------------------------------------------------------- #
def _ingest_warning(entry: dict) -> str | None:
    """A doc that extracts nothing is silently unanswerable — surface it rather than
    listing it as ingested. Shared by bulk ingest and upload so both report degraded
    extraction identically."""
    if not entry["clauses"]:
        return "NO CONTENT EXTRACTED — this document is not searchable"
    if entry.get("index_error"):
        return (f"indexing failed ({entry['index_error']}) — catalogued but absent "
                "from search")
    if entry["parse_source"] == "raw-text-fallback":
        return "degraded extraction (page-level citations only)"
    return None


def _ingest_worker(job_id: str):
    """Runs ingestion off the request path so large PDFs (docling parse can take
    minutes on a 50-page MOU) never time out the HTTP request (PRD §8B)."""
    job = _JOBS[job_id]
    try:
        case = _case()
        led = case.ledger()
        cat = _catalog(case)
        tax = _taxonomy(case)
        backend = _backend(case)
        sources = case.manifest.get("sources", [])
        job["total"] = len(sources)
        ingested, missing = [], []
        for i, src in enumerate(sources):
            job["current"] = src["id"]
            job["done"] = i
            pdf_path = src["file"]
            if not os.path.isabs(pdf_path):
                pdf_path = os.path.join(case.dir, pdf_path)
            # A declared document with no file is a gap in the LIBRARY, not a document to
            # catalogue. Collect them and report at the end: one missing PDF must not
            # abort the ingest of the twelve that are present.
            if not os.path.exists(pdf_path):
                missing.append({"doc_id": src["id"],
                                "title": src.get("title", src["id"]),
                                "file": src["file"]})
                continue
            entry = ingest.ingest_document(pdf_path, src["id"], src.get("title", src["id"]),
                                           tax, cat, backend=backend)
            led.append("authoring.ingest",
                       {"doc_id": src["id"], "parse_source": entry["parse_source"],
                        "pdf_sha256": entry.get("pdf_sha256", ""),
                        "tags": entry["tags"], "clauses": len(entry["clauses"])},
                       actor="admin")
            # A re-ingest may have moved or replaced the evidence ratified rules cite —
            # re-check them now, not at answer time (TICKETS.md A3).
            _revalidate_citations(case, cat, [src["id"]], led)
            # Ingest EXTRACTS and INDEXES; it does NOT draft rules. Drafting the whole MOU
            # up front produced 33 rules per contract to review and an approve-all that
            # could never match one grand total. Rules are now drafted PER SCENARIO, scoped
            # to the clauses a known paystub actually needs (see _draft_scenario). Policy
            # Q&A works from the index immediately; costing rules are authored on demand.
            ingested.append({"doc_id": src["id"], "tags": entry["tags"],
                             "summary": entry["summary"], "clauses": len(entry["clauses"]),
                             "parse_source": entry["parse_source"],
                             "warning": _ingest_warning(entry)})
        # Ingest does NOT touch the review queue — rules are drafted per scenario, and a
        # re-ingest must never wipe drafts already sitting there awaiting approval.
        if missing:
            led.append("authoring.ingest_missing",
                       {"doc_ids": [m["doc_id"] for m in missing]}, actor="admin")
        # RECONCILE (TICKETS.md F4): a document deleted from case.yaml must leave the
        # catalog and the search index too, or the library forever lists (and search
        # forever answers from) a contract the case no longer declares. Uploaded
        # documents are catalog-only by design and are left alone.
        declared = {s["id"] for s in sources}
        for entry in list(cat.documents()):
            did = entry["doc_id"]
            if did in declared or entry.get("uploaded"):
                continue
            cat.remove(did)
            try:
                backend.delete(did)
            except Exception:
                pass
            led.append("authoring.reconciled",
                       {"doc_id": did, "reason": "no longer declared in case.yaml"},
                       actor="admin")
        job["done"] = job["total"]
        job["result"] = {"ingested": ingested, "proposed_rules": [], "missing": missing}
        job["status"] = "done"
    except Exception as e:  # surface the failure to the poller
        job["status"] = "error"
        job["error"] = str(e)


@app.post("/admin/ingest")
def admin_ingest():
    """Kick off async ingestion; returns a job id to poll. See /admin/ingest/status.
    Single-flight with uploads — see _register_job."""
    job_id = _register_job(total=0, done=0, current=None)
    if job_id is None:
        return JSONResponse({"error": "an ingest is already running — poll its status "
                             "or wait for it to finish"}, status_code=409)
    threading.Thread(target=_ingest_worker, args=(job_id,), daemon=True).start()
    return {"job_id": job_id, "status": "running"}


@app.get("/admin/ingest/status/{job_id}")
def admin_ingest_status(job_id: str):
    job = _JOBS.get(job_id)
    if not job:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    return job


def _upload_worker(job_id: str, dest: str, doc_id: str, title: str, fname: str):
    """Runs an uploaded document's ingest off the request path (OCR_TICKETS.md OCR-6):
    a docling parse can take minutes, and the staged job (`stage`: parsing → tagging →
    indexing → done) lets the UI show the extraction happening live instead of a
    request that hangs until timeout."""
    job = _JOBS[job_id]
    try:
        case = _case()
        led = case.ledger()
        cat = _catalog(case)
        entry = ingest.ingest_document(
            dest, doc_id, title, _taxonomy(case), cat, backend=_backend(case),
            progress=lambda stage: job.__setitem__("stage", stage))
        # Uploaded docs are catalog-only (not declared in case.yaml); the marker keeps
        # the post-ingest reconciliation pass from garbage-collecting them.
        entry["uploaded"] = True
        cat.upsert(entry)
        led.append("authoring.upload",
                   {"doc_id": doc_id, "filename": fname,
                    "parse_source": entry["parse_source"],
                    "pdf_sha256": entry.get("pdf_sha256", ""),
                    "tags": entry["tags"], "clauses": len(entry["clauses"])},
                   actor="admin")
        # An upload can replace an existing source file by name — same drift risk as a
        # bulk re-ingest, so ratified rules citing this document are re-checked now.
        _revalidate_citations(case, cat, [doc_id], led)
        # Upload = extract + index, same as bulk ingest. It does NOT draft rules — this
        # was the last surviving bulk-draft path after the scenario-scoped pivot, and it
        # put a whole document's worth of competing rules back into the review queue.
        # Policy Q&A over the new document works immediately; costing rules are
        # authored per scenario.
        job["result"] = {
            "doc_id": doc_id, "title": title, "parse_source": entry["parse_source"],
            "tags": entry["tags"], "summary": entry["summary"],
            "clauses": len(entry["clauses"]), "proposed_rules": [],
            # OCR-7: a freshly scanned upload should announce shaky OCR immediately,
            # with the same signal its scorecard row will carry in the document list.
            "low_confidence_pages": _extraction_stats(entry)["low_confidence_pages"],
            "warning": _ingest_warning(entry),
            "note": ("Document read and indexed — ask about it in chat right away. "
                     "Costing rules are drafted per scenario on the Verification tab.")}
        job["stage"] = "done"
        job["status"] = "done"
    except Exception as e:  # surface the failure to the poller
        job["status"] = "error"
        job["error"] = str(e)


@app.post("/admin/upload")
async def admin_upload(file: UploadFile = File(...)):
    """Upload a PDF: save it into the case, then ingest it ASYNC (parse -> tag ->
    index) under a staged job — poll /admin/ingest/status/{job_id} (OCR-6). Saving
    and validation stay in-request so a bad file fails fast with a real status code."""
    case = _case()
    src_dir = os.path.join(case.dir, "sources")
    os.makedirs(src_dir, exist_ok=True)
    fname = os.path.basename(file.filename or "upload.pdf")
    if not fname.lower().endswith(".pdf"):
        return JSONResponse({"error": "only .pdf files are accepted"}, status_code=400)
    doc_id = _slug(os.path.splitext(fname)[0])
    title = os.path.splitext(fname)[0]

    # Claim the single-flight slot BEFORE touching the destination file: an upload can
    # replace a source PDF by name, and doing that while a running ingest is mid-parse
    # on the same file would bind the catalog to bytes that no longer exist.
    job_id = _register_job(stage="saving", doc_id=doc_id, filename=fname)
    if job_id is None:
        return JSONResponse({"error": "an ingest is already running — wait for it to "
                             "finish before uploading"}, status_code=409)

    def _reject(resp: JSONResponse) -> JSONResponse:
        _JOBS.pop(job_id, None)  # a rejected upload never ran — leave no ghost job
        return resp

    # Stream to a temp file with a hard size cap (TICKETS.md C5): reading the whole
    # upload into RAM let any credential holder OOM the 2GB instance, and writing the
    # destination directly could leave a half-written PDF over a good one.
    dest = os.path.join(src_dir, fname)
    tmp = dest + ".uploading"
    size = 0
    try:
        with open(tmp, "wb") as f:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > _MAX_UPLOAD_BYTES:
                    return _reject(JSONResponse(
                        {"error": f"file exceeds the "
                                  f"{_MAX_UPLOAD_BYTES // (1 << 20)}MB upload limit"},
                        status_code=413))
                f.write(chunk)
        with open(tmp, "rb") as f:
            if f.read(5) != b"%PDF-":
                return _reject(JSONResponse({"error": "not a PDF (bad magic bytes)"},
                                            status_code=400))
        os.replace(tmp, dest)
    except Exception:
        _JOBS.pop(job_id, None)
        raise
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    _JOBS[job_id]["stage"] = "parsing"
    threading.Thread(target=_upload_worker, args=(job_id, dest, doc_id, title, fname),
                     daemon=True).start()
    return {"job_id": job_id, "status": "running", "doc_id": doc_id, "filename": fname}


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s or "doc"


@app.get("/admin/catalog")
def admin_catalog():
    """Catalog + the declared governance metadata, so the UI can show a document's
    identity hierarchically (department > unit > type > version > effective dates)
    rather than a flat bag of auto-tags."""
    case = _case()
    out = []
    for d in _catalog(case).summaries():
        src = case.source_by_id(d["doc_id"]) or {}
        out.append({**d,
                    "declared_title": src.get("title", ""),
                    "department": src.get("department"),
                    "bargaining_unit": src.get("bargaining_unit"),
                    "doc_type": src.get("doc_type"),
                    "mou_version": src.get("mou_version"),
                    "effective_start": src.get("effective_start"),
                    "effective_end": src.get("effective_end"),
                    "supersedes": src.get("supersedes")})
    return {"documents": out}


@app.get("/admin/proposed")
def admin_proposed():
    case = _case()
    path = case.path("proposed_rules", "rules/rules_proposed.json")
    data = {"rules": [], "needs_data": []}
    if path and os.path.exists(path):
        import json
        with open(path) as f:
            data = json.load(f)
    # which proposals are already live, so the gate shows state instead of a
    # permanently-checked box
    ratified = {r.id: {"approver": r.approver, "result_type": r.result_type}
                for r in case.rules()}
    data["ratified_ids"] = sorted(ratified.keys())
    data["ratified_meta"] = ratified
    # Rules whose cited evidence changed on a re-ingest (marked by _revalidate_citations)
    # — excluded from the engine until a human re-verifies and re-approves them.
    data["stale_rules"] = [r for r in _raw_ratified(case) if r.get("status") == "stale"]
    # The FULL ratified library, verbatim from disk. The rule-library UI must render from
    # this — not from the proposed queue filtered to ratified ids. The queue is ephemeral
    # (cleared on reseed, not shipped in the image), so deriving the library display from
    # it showed "4" in the tab count and an empty page beneath it.
    data["ratified_rules"] = _raw_ratified(case)
    return data


def _ratified_dicts(case) -> list[dict]:
    """The live library as plain dicts, so it can be judged alongside a pending selection."""
    return [{"id": r.id, "kind": r.kind, "result_type": r.result_type, "topic": r.topic,
             "priority": r.priority, "when": r.when, "set": r.set, "compute": r.compute,
             "citation": r.citation.to_dict(),
             "flags": [f.__dict__ for f in r.flags]} for r in case.rules()]


def _with_live(case, extra: list[dict]) -> list[dict]:
    """The live library merged with candidate rules, BY ID — never concatenated.

    A rule can be both ratified and still sitting in the proposed queue (approving it
    does not remove it from the queue). Concatenating the two lists put the same
    differential in the candidate set twice, and differentials ACCUMULATE — graveyard
    ×1.055 applied twice, bilingual ×1.05 applied twice — so a re-approval of already-live
    rules "broke" a passing scenario at 4747.05. Merge by id; the candidate (newer draft)
    wins over the stored copy.
    """
    merged = {r["id"]: r for r in _ratified_dicts(case)}
    for r in extra:
        merged[r["id"]] = r
    return list(merged.values())


def _check_golden(case, rule_dicts: list[dict], golden: dict) -> tuple[bool, dict]:
    """Run the candidate rules against the hand-verified scenario.

    Returns (ok, detail). `detail["status"]` is one of:
      "pass"    — reproduces the known answer.
      "fail"    — produces a DIFFERENT number. Blocks. This is what the gate is for.
      "pending" — produces no number at all: no rule in this library covers the
                  scenario. NOT a failure, and must not block.

    The pending/fail distinction is load-bearing. A golden covers one bargaining unit;
    a corpus has several. Treating "no rule covers this yet" as a failure meant approving
    the police rules was blocked by the public-works golden, and every corpus had to be
    ratified in one atomic all-or-nothing action — which is precisely the workflow the
    review queue exists to avoid. A library that cannot answer a scenario REFUSES at run
    time, which is safe; a library that answers it wrongly is the actual danger. Only the
    latter blocks. Completeness is reported by the Verification tab, not enforced here.
    """
    try:
        rules = case.assign_scope(
            [Rule.from_dict({**r, "status": "ratified", "approver": "candidate"})
             for r in rule_dicts])
        names = set(golden.get("subjects") or [])
        subs = [s for s in case.subjects() if not names or s.get("name") in names]
        # Mirror run time: governance narrows rules to the document(s) that actually
        # govern these subjects, so rules from other units' MOUs can't contaminate the
        # check (a corpus has many MOUs; only one governs a given employee).
        units = sorted({s.get("bargaining_unit") for s in subs if s.get("bargaining_unit")})
        if units:
            gov = governance.resolve(units, golden.get("params", {}).get("date_iso"),
                                     case.manifest.get("sources", []))
            if gov.resolved:
                rules = [r for r in rules
                         if (not r.citation.doc_id) or (r.citation.doc_id in gov.doc_ids)]
                rules, _ = governance.apply_supersession(
                    rules, case.manifest.get("sources", []), gov.doc_ids)
        # A golden verifies ONE kind of answer. Only rules of that result_type compete,
        # mirroring run time (a "5 days" bereavement selector must not hijack a money
        # golden). Pay is one chapter of the rulebook, not all of it.
        want = golden.get("result_type", "currency")
        rules = [r for r in rules if r.result_type == want]
        # Seed every declared query param so a rule referencing a legitimate fact the
        # golden didn't set (e.g. date_iso) evaluates to a safe default instead of
        # exploding. Mirrors run time, where chat always supplies all of them.
        params = {"hours": 0.0, "date": "", "date_iso": "", "holiday_weekday": ""}
        params.update(golden.get("params", {}))
        # A currency scenario is a shift cost — same basis filter as run time, so approving
        # a whole MOU (uniform allowance, medical, life insurance) does not blow the check
        # up with a year of benefits. Non-currency scenarios (days/hours entitlements) are
        # not periodic pay and are not filtered.
        scope = SHIFT_BASES if want == "currency" else None
        res = calculate(params, subs, rules, case.rounding_places(), basis_scope=scope)
        expected = float(golden["expected_total"])
        actual = float(res.total)
        ok = abs(actual - expected) < 0.005
        # A wrong number is only a FAILURE when the scenario is fully authored. If a
        # governing document contributes no rules yet (a side letter whose replacement
        # rule nobody has drafted), the number is computed from an incomplete library —
        # that is "pending", not "the rules are wrong". Observed live: ratifying the 2025
        # base-MOU rules made the 2026 side-letter scenario read FAIL at 4388.80, because
        # the 6.5% amendment rule did not exist yet. Fail must mean exactly one thing —
        # authored rules that disagree with the paystub — or the Verification tab trains
        # people to ignore red.
        unauthored = []
        if not ok and units and gov.resolved:
            covered = {r.citation.doc_id for r in rules}
            unauthored = [d for d in gov.doc_ids if d not in covered]
        status = "pass" if ok else ("pending" if unauthored else "fail")
        detail = {"scenario": golden.get("name", "golden"), "expected": expected,
                  "actual": actual, "status": status,
                  "per_subject": {li.subject: li.total for li in res.line_items},
                  # Which selector actually WON for each subject. On a failure this is the
                  # actionable fact: "compare the rule against its clause" is only useful
                  # if the reviewer knows WHICH rule fired.
                  "fired": sorted({li.rule_id for li in res.line_items})}
        if unauthored:
            detail["note"] = (f"Not fully authored: {', '.join(unauthored)} governs this "
                              f"scenario but has no approved rules yet. Draft the rules "
                              f"for this scenario to complete it.")
        return (ok or bool(unauthored)), detail
    except NoRuleApplies as e:
        # Nothing in this library covers the scenario. Run time would refuse, which is
        # the correct and safe outcome — so this cannot block a ratification.
        return True, {"scenario": golden.get("name", "golden"), "status": "pending",
                      "expected": golden.get("expected_total"), "actual": None,
                      "error": str(e),
                      "note": "No live rule covers this scenario yet, so Kenny refuses "
                              "to cost it rather than guessing. Approve the rules for "
                              "this unit and this check will run."}
    except Exception as e:
        return False, {"scenario": golden.get("name", "golden"), "status": "fail",
                       "error": str(e),
                       "expected": golden.get("expected_total"), "actual": None}


def _extraction_stats(entry: dict) -> dict:
    """How well did we READ this document? (OCR_TICKETS.md OCR-3)

    Pure math over one catalog entry — separated from the endpoint so the scorecard
    arithmetic is testable without the HTTP stack. Returns:
      kinds           histogram of clause kinds (the catalog omits `kind` for normal
                      text, so missing/empty counts as "text" — same normalisation as
                      the X-ray endpoint)
      pages_empty     pages in 1..page_count from which nothing was extracted
      recovered_pages pages whose every clause is recovered-* — the layout-model
                      rescue (docling called the page a Picture; spans were regrouped
                      into rows from raw geometry)
      pdf_sha256_short first 12 hex chars of the PDF's sha ("" when not recorded)
      index_error     passthrough — catalogued but absent from search when set
      low_confidence_pages  pages whose docling OCR score fell below
                      LOW_OCR_CONFIDENCE (OCR-7) — [] for digital documents and for
                      catalogs ingested before confidence capture existed
    """
    kinds: dict[str, int] = {}
    kinds_by_page: dict[int, set] = {}
    for c in entry.get("clauses") or []:
        kind = c.get("kind") or "text"
        kinds[kind] = kinds.get(kind, 0) + 1
        page = c.get("page")
        if isinstance(page, int) and page >= 1:
            kinds_by_page.setdefault(page, set()).add(kind)
    page_count = entry.get("page_count") or 0
    return {
        "kinds": kinds,
        "pages_empty": [p for p in range(1, page_count + 1) if p not in kinds_by_page],
        "recovered_pages": sorted(
            p for p, ks in kinds_by_page.items()
            if ks <= {"recovered-row", "recovered-text"}),
        "pdf_sha256_short": (entry.get("pdf_sha256") or "")[:12],
        "index_error": entry.get("index_error"),
        "low_confidence_pages": sorted(
            int(p) for p, s in (entry.get("page_confidence") or {}).items()
            if isinstance(s, (int, float)) and s < ingest.LOW_OCR_CONFIDENCE),
    }


@app.get("/admin/coverage")
def admin_coverage():
    """How much of each contract has Kenny actually modelled?

    The question an HR Director asks, which a "64 rules drafted" counter cannot answer.
    Per document: clauses extracted, rules live, rules pending review, clauses blocked
    on missing data, clauses that are narrative (nothing to compute, correctly).
    """
    case = _case()
    cat = _catalog(case)
    ratified = case.rules()
    proposed = admin_proposed()
    live_ids = {r.id for r in ratified}
    live_by_doc: dict[str, int] = {}
    for r in ratified:
        live_by_doc[r.citation.doc_id] = live_by_doc.get(r.citation.doc_id, 0) + 1
    pending_by_doc: dict[str, int] = {}
    for r in proposed.get("rules", []):
        if r.get("id") in live_ids:
            continue
        d = (r.get("citation") or {}).get("doc_id") or r.get("_doc_id") or ""
        pending_by_doc[d] = pending_by_doc.get(d, 0) + 1
    blocked_by_doc: dict[str, int] = {}
    narrative_by_doc: dict[str, int] = {}
    for n in proposed.get("needs_data", []):
        d = n.get("doc_id") or ""
        if n.get("category") == "narrative":
            narrative_by_doc[d] = narrative_by_doc.get(d, 0) + 1
        else:
            blocked_by_doc[d] = blocked_by_doc.get(d, 0) + 1

    docs = []
    for entry in cat.documents():
        did = entry["doc_id"]
        src = case.source_by_id(did) or {}
        docs.append({
            # The document's own header wins over the name case.yaml files it under: the
            # library should show what a user reading the contract would see on it.
            "doc_id": did,
            "title": entry.get("title") or src.get("title", did),
            "declared_title": src.get("title", ""),
            "department": src.get("department"), "doc_type": src.get("doc_type"),
            "bargaining_unit": src.get("bargaining_unit"),
            "mou_version": src.get("mou_version"),
            "effective_start": src.get("effective_start"),
            "effective_end": src.get("effective_end"),
            "supersedes": src.get("supersedes"),
            "summary": entry.get("summary", ""), "tags": entry.get("tags", []),
            "clauses": len(entry.get("clauses", [])),
            "parse_source": entry.get("parse_source"),
            "live": live_by_doc.get(did, 0),
            "pending": pending_by_doc.get(did, 0),
            "blocked": blocked_by_doc.get(did, 0),
            "narrative": narrative_by_doc.get(did, 0),
            # Extraction scorecard (OCR-3): how well was the document READ, as
            # opposed to how much of it has been modelled.
            **_extraction_stats(entry),
        })
    return {"documents": docs, "corpus_size": len(case.manifest.get("sources", []))}


# A gap is only actionable as a FIELD, not as 31 separate clause rows. The LLM's
# "missing" text is prose, so normalise it to the field a data owner would add.
_GAP_FIELDS = [
    ("subject_assignment", ("detective", "investigator", "motorcycle", "swat",
                            "special response", "assignment to", "bureau")),
    ("subject_k9_handler", ("k-9", "k9", "canine")),
    ("subject_paramedic", ("paramedic", "medic unit")),
    ("event", ("holdover", "callback", "call-back", "standby", "court", "recall",
               "mandated to work", "held over", "event")),
    ("subject_step", ("step",)),
    ("subject_seniority_years", ("years of service", "seniority", "length of service")),
    ("subject_acting_assignment", ("acting",)),
    ("subject_hire_date", ("hire date", "appointment date", "probation")),
    ("subject_separation_date", ("separation", "retire")),
    ("subject_coverage_tier", ("coverage tier", "medical plan", "dependent")),
]


def _gap_field(text: str) -> str:
    t = (text or "").lower()
    import re as _re
    m = _re.search(r"subject_[a-z0-9_]+", t)
    if m:
        return m.group(0)
    for field, cues in _GAP_FIELDS:
        if any(c in t for c in cues):
            return field
    return "other"


@app.get("/admin/gaps")
def admin_gaps():
    """Data gaps grouped by the FIELD required — a spec a data owner can act on
    ("add `assignment` and these four premiums start working"), not a clause list."""
    case = _case()
    proposed = admin_proposed()
    groups: dict[str, dict] = {}
    for n in proposed.get("needs_data", []):
        if n.get("category") == "narrative":
            continue
        key = n.get("missing_field") or _gap_field(f"{n.get('missing','')} {n.get('reason','')}")
        g = groups.setdefault(key, {"field": key, "unlocks": []})
        src = case.source_by_id(n.get("doc_id", "")) or {}
        g["unlocks"].append({"doc_id": n.get("doc_id"), "clause": n.get("clause"),
                             "title": src.get("title", n.get("doc_id")),
                             "department": src.get("department"),
                             "reason": n.get("reason", "")})
    # Named fields first (they are the actionable ones); the unclassified catch-all
    # sorts last however big it is — "add `other`" is not a request anyone can action.
    out = sorted(groups.values(),
                 key=lambda g: (g["field"] == "other", -len(g["unlocks"])))
    return {"gaps": out, "total_clauses_blocked": sum(len(g["unlocks"]) for g in out)}


def _scenario_query(scenario: dict) -> str:
    """Retrieval query for the pay branch a scenario exercises. An explicit `branch_query`
    on the scenario wins; otherwise fall back to its name plus generic pay vocabulary."""
    if scenario.get("branch_query"):
        return scenario["branch_query"]
    base = "pay premium rate differential hours worked base"
    return f"{scenario.get('name', '')} {base}"


@app.post("/admin/draft_scenario")
async def admin_draft_scenario(request: Request):
    """Draft ONLY the rules a known scenario (a paystub) needs, and verify them.

    This is the rebuilt authoring model. Instead of drafting a whole 140-clause MOU into
    33 rules and hoping approve-all matches one grand total, a scenario names a real
    known answer; Kenny retrieves the handful of clauses that answer it, drafts just
    those, and checks them against the paystub. Review collapses from ~33 rules to ~5,
    and a small focused drafting task is one the model gets right (roles, pay_basis,
    compounding differentials) where the whole-MOU task was fragile.
    """
    body = await request.json()
    name = body.get("scenario")
    case = _case()
    led = case.ledger()
    scenario = next((g for g in case.golden_cases() if g.get("name") == name), None)
    if scenario is None:
        return JSONResponse({"error": f"no scenario named {name!r}"}, status_code=404)

    # 1. Which document(s) govern the scenario's people on its date.
    subs = [s for s in case.subjects() if s["name"] in set(scenario.get("subjects") or [])]
    units = sorted({s.get("bargaining_unit") for s in subs if s.get("bargaining_unit")})
    gov = governance.resolve(units, scenario.get("params", {}).get("date_iso"),
                             case.manifest.get("sources", []))
    doc_ids = gov.doc_ids or None

    # 2. Retrieve only the clauses this scenario's pay branch needs.
    cat = _catalog(case)
    ingested = {d["doc_id"] for d in cat.documents()}
    scope = [d for d in (doc_ids or list(ingested)) if d in ingested]
    if not scope:
        return JSONResponse({"error": "governing documents are not ingested yet — run "
                             "Ingest first"}, status_code=400)
    hits = _backend(case).search(_scenario_query(scenario), doc_ids=scope, k=8)
    want = {(h["doc_id"], str(h["clause"])) for h in hits}
    # Never re-draft a clause that already has a LIVE rule. A scenario completes the
    # library incrementally: the 2026 side-letter scenario needs ONE new rule (the 6.5%
    # amendment), not a re-draft of the base rules the 2025 scenario already ratified —
    # a re-draft carries the same ids, would overwrite the approved rules on ratify, and
    # the regression guard then (correctly) blocks the whole approval. Draft only the gap.
    live = {(r.citation.doc_id, str(r.citation.clause)) for r in case.rules()}
    want -= live
    clauses_by_doc: dict[str, list] = {}
    for d, cl in want:
        for c in cat.clauses(d):
            if str(c.get("clause")) == cl:
                clauses_by_doc.setdefault(d, []).append(c)

    # 3. Draft ONLY those clauses (per document, so ids namespace correctly). The drafter
    #    also reports clauses it correctly REFUSED to draft (event pay the roster cannot
    #    see) — that is the Data-gaps feed, kept per scenario.
    drafted: list[dict] = []
    needs: list[dict] = []
    with llm.record() as trail:
        for d, clauses in clauses_by_doc.items():
            rules = llm.draft_rules(clauses, d, case.known_facts(),
                                    case.field_values(), case.bool_facts())
            doc_sha = (cat.get(d) or {}).get("pdf_sha256", "")
            for r in rules:
                r["_doc_id"] = d
                if not str(r.get("id", "")).startswith(d + ":"):
                    r["id"] = f"{d}:{r.get('id')}"
                # Bind the citation to the exact file the reviewer will be shown
                # (TICKETS.md A1): if the PDF later changes, the mismatch is detectable.
                if doc_sha:
                    cit = r.get("citation") or {}
                    cit["doc_sha256"] = doc_sha
                    r["citation"] = cit
            drafted.extend(rules)
            needs.extend(getattr(llm.draft_rules, "last_needs_data", []) or [])
    for call in trail:
        led.append("llm.call", {**call, "scenario": name}, actor="llm")

    # Keep only rules of the scenario's own type — a currency paystub authors currency
    # rules, not the vacation-accrual clause that happened to sit near a holiday clause.
    # Cross-type clauses that surfaced in retrieval are answered by policy Q&A, not costed.
    want_type = scenario.get("result_type", "currency")
    drafted = [r for r in drafted if r.get("result_type", "currency") == want_type]

    # 4. Verify the drafted set against the paystub, and only offer it if it reproduces.
    ok, detail = _check_golden(case, _with_live(case, drafted), scenario)

    # 5. Merge into the review queue (replace this scenario's prior drafts + gaps).
    prior = admin_proposed()
    keep = [r for r in prior.get("rules", []) if r.get("_scenario") != name]
    keep_needs = [n for n in prior.get("needs_data", []) if n.get("_scenario") != name]
    for r in drafted:
        r["_scenario"] = name
    for n in needs:
        n["_scenario"] = name
    _write_proposed(case, keep + drafted, keep_needs + needs)
    led.append("authoring.draft_scenario",
               {"scenario": name, "clauses": sorted(f"{d}:{c}" for d, c in want),
                "rule_ids": [r["id"] for r in drafted], "verify": detail.get("status")},
               actor="admin")

    return {"scenario": name, "considered_clauses": sorted(f"{d}§{c}" for d, c in want),
            "drafted": drafted, "verify": detail}


@app.get("/admin/verification")
def admin_verification():
    """The golden cases and what they actually exercise.

    Ratification is only meaningful because a golden must reproduce a known answer —
    so the goldens belong in the UI, not just in case.yaml. A ratified rule that NO
    golden exercises is unverified, and this is where you can see it.
    """
    case = _case()
    rules = case.rules()
    goldens = case.golden_cases()
    ingested = {d["doc_id"] for d in _catalog(case).documents()}
    results = []
    exercised: set[str] = set()
    live = _ratified_dicts(case)
    for g in goldens:
        ok, detail = _check_golden(case, live, g)
        # rules of this golden's type are the ones it could have exercised
        want = g.get("result_type", "currency")
        for r in rules:
            if r.result_type == want:
                exercised.add(r.id)
        # Which documents this scenario needs, and whether each is ingested yet — so the
        # card can say "needs X (not ingested)" instead of only failing on the button.
        subs = [s for s in case.subjects() if s["name"] in set(g.get("subjects") or [])]
        units = sorted({s.get("bargaining_unit") for s in subs if s.get("bargaining_unit")})
        gov = governance.resolve(units, g.get("params", {}).get("date_iso"),
                                 case.manifest.get("sources", []))
        needs_docs = [{"doc_id": d, "ingested": d in ingested,
                       "title": (case.source_by_id(d) or {}).get("title", d)}
                      for d in (gov.doc_ids or [])]
        results.append({"name": g.get("name"), "result_type": want,
                        "source": g.get("source", ""),
                        "expected": detail.get("expected"), "actual": detail.get("actual"),
                        "passed": ok, "status": detail.get("status", "fail"),
                        "per_subject": detail.get("per_subject"),
                        "note": detail.get("note"),
                        "error": detail.get("error"),
                        "needs_docs": needs_docs,
                        "ready": all(d["ingested"] for d in needs_docs) and bool(needs_docs)})
    unverified = [{"id": r.id, "result_type": r.result_type, "topic": r.topic,
                   "doc_id": r.citation.doc_id, "clause": r.citation.clause}
                  for r in rules if r.id not in exercised]
    # "Pending" is not passing — a scenario nothing covers is unproven, not proven. It
    # simply does not BLOCK approval (see _check_golden).
    return {"goldens": results, "rule_count": len(rules),
            "unverified": unverified,
            "pending": sum(1 for g in results if g["status"] == "pending"),
            "all_passing": (all(g["status"] == "pass" for g in results)
                            if results else None)}


@app.get("/admin/clause")
def admin_clause(doc_id: str, clause: str = "", page: int | None = None):
    """The source text + bbox behind a rule's citation, so a reviewer can check the
    drafted rule against the actual contract language before approving it.

    A numbered contract resolves by clause number. But an OCR'd document has NO clause
    numbers — the rule cites a page and a bbox instead, and matching by clause returned
    404, making View source unusable on exactly the real-world (scanned) documents. So
    when the clause does not resolve, fall back to the page: return the text of the
    clauses on that page and let the citation's own bbox draw the highlight.
    """
    clauses = _catalog(_case()).clauses(doc_id)
    for c in clauses:
        if clause and str(c.get("clause")) == str(clause):
            return {"doc_id": doc_id, "clause": clause, "page": c.get("page"),
                    "bbox": c.get("bbox"), "text": c.get("text", "")}
    if page is not None:
        # Page-level context text; bbox is left null on purpose so the caller draws the
        # highlight from the RULE's own citation bbox (the exact cited passage), not the
        # first clause that happens to sit on the page.
        on_page = [c for c in clauses if c.get("page") == page]
        text = "\n\n".join((c.get("text") or "").strip() for c in on_page)[:1500]
        return {"doc_id": doc_id, "clause": clause, "page": page,
                "bbox": None, "text": text, "resolved_by": "page"}
    return JSONResponse({"error": f"clause {clause!r} not found in {doc_id}"},
                        status_code=404)


@app.get("/admin/validate")
def admin_validate():
    """Static validation of the current proposed rules against the case's real fact
    vocabulary. This is what the ratify gate enforces (PRD §8A)."""
    case = _case()
    proposed = admin_proposed().get("rules", [])
    errors = validate_rules(proposed, case.known_facts())
    return {"known_facts": sorted(case.known_facts()),
            "rule_count": len(proposed), "errors": errors,
            "valid_ids": [r.get("id") for r in proposed if r.get("id") not in errors]}


@app.post("/admin/ratify")
async def admin_ratify(request: Request):
    """Human gate: approve proposed rules -> ratified library (PRD 5.2 / §8A).

    A rule may only be ratified if it PASSES VALIDATION — its expressions must parse
    and may only reference facts that actually exist in the case's data schema. A
    human approving a broken rule is still blocked; approval is necessary, not
    sufficient.
    """
    body = await request.json()
    approver = body.get("approver", "admin")
    approved_ids = body.get("rule_ids")  # None -> approve all
    case = _case()
    led = case.ledger()
    proposed = admin_proposed().get("rules", [])

    selected = [r for r in proposed
                if approved_ids is None or r.get("id") in approved_ids]
    # A denial is an audit event too — record what the reviewer rejected and why.
    if approved_ids is not None:
        denied = [r.get("id") for r in proposed if r.get("id") not in approved_ids]
        if denied:
            led.append("authoring.denied",
                       {"rule_ids": denied, "approver": approver,
                        "reason": body.get("deny_reason", "not approved by reviewer")},
                       actor="admin")
    errors = validate_rules(selected, case.known_facts())
    if errors:
        led.append("authoring.rejected",
                   {"errors": errors, "approver": approver}, actor="admin")
        return {"ratified": [], "rejected": errors,
                "warning": "Validation failed — nothing was ratified. These rules "
                           "reference facts that don't exist or can't execute. Fix the "
                           "rule (or the data schema) and re-approve."}

    # KNOWN-ANSWER GATE — a REGRESSION guard, not a completeness demand. Two questions:
    #   1. Does every scenario these rules were DRAFTED FOR now reproduce its paystub?
    #      (the rules carry `_scenario`; that is the answer they are meant to produce.)
    #   2. Does approving them BREAK any scenario that was already passing?
    # Anything else — a scenario still pending because its own rules are not drafted yet
    # (e.g. the 2026 side letter, not yet authored) — is left alone. It never blocks an
    # unrelated approval, which is what made incremental, scenario-by-scenario ratification
    # deadlock before.
    before = {g["name"]: _check_golden(case, _ratified_dicts(case), g)[1].get("status")
              for g in case.golden_cases()}
    candidate = _with_live(case, selected)
    targets = {r.get("_scenario") for r in selected if r.get("_scenario")}
    for golden in case.golden_cases():
        name = golden["name"]
        ok, detail = _check_golden(case, candidate, golden)
        led.append("authoring.golden_check", {"passed": ok, **detail}, actor="admin")
        status = detail.get("status")
        # (2) regression: something that worked now doesn't.
        if before.get(name) == "pass" and status == "fail":
            return {"ratified": [], "golden_failed": detail,
                    "warning": f"Nothing was approved — this would BREAK a check that was "
                               f"passing. “{name}” dropped to {detail.get('actual')} "
                               f"(known answer {detail.get('expected')}). Remove the rule "
                               f"that changed it."}
        # (1) the scenario these rules are FOR must actually reproduce its answer.
        if name in targets and status != "pass":
            actual = detail.get("actual")
            fired = detail.get("fired") or []
            why = (f"could not be computed ({detail.get('error', 'unknown')})"
                   if actual is None else
                   f"produced {actual}" + (f" — the rule that fired was “{fired[0]}”"
                                           if fired else ""))
            return {"ratified": [], "golden_failed": detail,
                    "warning": f"Nothing was approved — “{name}” must come to "
                               f"{detail.get('expected')}, but the drafted rules {why}. "
                               f"Compare each rule against its clause, or re-draft the "
                               f"scenario."}

    import time
    newly = []
    for r in selected:
        r = {k: v for k, v in r.items() if k != "_doc_id"}
        r["status"] = "ratified"
        r["approver"] = approver
        r["approved_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        newly.append(r)
    if not newly:
        # Never let an empty/failed draft wipe a working ratified library.
        return {"ratified": [], "warning": "No proposed rules to ratify — the existing "
                "ratified library was left untouched."}

    # Ratification ADDS to the library, it does not replace it. Merge the existing live
    # rules with the newly-approved ones, keyed by id (a re-approval updates in place).
    # Writing only the new selection silently dropped every previously-ratified rule:
    # approve A -> library {A}; approve B,C -> library {B,C}, and A was gone. The golden
    # gate above already judges the UNION (_ratified_dicts + selected), so the write must
    # persist that same union or the gate and the library disagree.
    existing = _raw_ratified(case)
    merged = {r["id"]: r for r in existing}
    for r in newly:
        merged[r["id"]] = r
    library = list(merged.values())

    for r in newly:
        led.append("authoring.ratify",
                   {"rule_id": r.get("id"), "approver": approver}, actor="admin")
    _write_ratified(case, library)
    return {"ratified": [r.get("id") for r in newly],
            "library_size": len(library)}


@app.get("/admin/ledger")
def admin_ledger():
    led = _case().ledger()
    ok, msg = led.verify()
    return {"verified": ok, "verify_message": msg, "events": list(led.read())}


@app.get("/admin/ledger/export")
def admin_ledger_export():
    return PlainTextResponse(_case().ledger().export(), media_type="text/plain")


@app.get("/admin/history")
def admin_history():
    return {"history": audit.history(_case().ledger())}


@app.get("/admin/taxonomy")
def admin_taxonomy():
    case = _case()
    tax = _taxonomy(case)
    proposed = []
    for d in _catalog(case).documents():
        proposed.extend(d.get("proposed_tags", []))
    return {"taxonomy": tax, "proposed_tags": sorted(set(proposed))}


# --------------------------------------------------------------------------- #
def _write_proposed(case, rules: list[dict], needs_data: list[dict] | None = None) -> None:
    import json
    path = case.path("proposed_rules", "rules/rules_proposed.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"rules": rules, "needs_data": needs_data or []}, f, indent=2)


def _raw_ratified(case) -> list[dict]:
    """The ratified library exactly as stored — full fields (approver, timestamp,
    status), unlike `case.rules()` which parses and drops metadata. Used when merging so
    a prior approval is preserved byte-for-byte, not round-tripped lossily."""
    import json
    path = case.path("rules")
    if path and os.path.exists(path):
        with open(path) as f:
            return json.load(f).get("rules", [])
    return []


def _write_ratified(case, rules: list[dict]) -> None:
    """Write the ratified library, backing up the previous one first. Ratification
    replaces the live rule set, so the prior version is always recoverable."""
    import json
    import shutil
    import time as _t
    path = case.path("rules")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        stamp = _t.strftime("%Y%m%d-%H%M%S")
        shutil.copy(path, f"{path}.{stamp}.bak")
    with open(path, "w") as f:
        json.dump({"rules": rules}, f, indent=2)
