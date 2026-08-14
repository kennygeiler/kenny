// Chat surface: send prompt -> render answer -> click a dollar figure for the audit
// drawer (decision trace + citation with the docling bbox highlighted on the page).
let CASE = {};
const log = document.getElementById('log');

fetch('/api/case').then(r => r.json()).then(c => {
  CASE = c;
  document.getElementById('caseName').textContent = c.name || '';
  const b = document.getElementById('keyBadge');
  b.textContent = c.has_api_key ? 'LLM: Claude' : 'LLM: deterministic fallback';
  showBanner(c.banner);
});

// role="note" rather than an alert: it is standing context, not an event, so it should
// be reachable in the reading order without interrupting whatever is being announced.
function showBanner(text) {
  if (!text) return;
  const el = document.getElementById('demoBanner');
  if (!el) return;
  el.textContent = text;
  el.hidden = false;
}

function el(html) { const d = document.createElement('div'); d.innerHTML = html.trim(); return d.firstChild; }
function esc(s) { return (s ?? '').toString().replace(/[&<>]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[m])); }
function fmt(n) { return '$' + Number(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}); }

// An MOU is a rulebook: a rule's value may be money, days, hours, a date or a yes/no.
function fmtVal(n, type) {
  const v = Number(n);
  switch (type) {
    case 'days':    return v + (v === 1 ? ' day' : ' days');
    case 'hours':   return v + (v === 1 ? ' hour' : ' hours');
    case 'boolean': return v ? 'Yes' : 'No';
    case 'date':    return String(n);
    case 'text':    return String(n);
    default:        return fmt(v);
  }
}

// Extraction-tier chip (OCR-4): says where a cited passage's text CAME FROM. The tier
// itself is computed server-side (core/app.py::_extraction_tier — the one mapping);
// this only renders it, so client and server can never disagree. Unknown/absent tier
// renders nothing: no claim beats a wrong claim.
const TIER_TITLES = {
  'text layer': "Read directly from the PDF's digital text layer with exact clause positions",
  'recovered layout': 'The layout model misread this page; the text was recovered from raw span geometry',
  'page-level': 'Extracted as raw page text — citations open the page, not the exact clause',
  'sidecar extract': 'Loaded from a hash-bound sidecar extraction',
};
function tierChip(tier) {
  if (!tier || !TIER_TITLES[tier]) return '';
  return ` <span class="tier-chip" title="${esc(TIER_TITLES[tier])}">${esc(tier)}</span>`;
}

function addUser(text) { log.appendChild(el(`<div class="msg user">${esc(text)}</div>`)); scroll(); }
function addBot(node) { const m = el('<div class="msg bot"></div>'); m.appendChild(node); log.appendChild(m); scroll(); }
function scroll() { window.scrollTo(0, document.body.scrollHeight); }

async function send() {
  const input = document.getElementById('prompt');
  const prompt = input.value.trim();
  if (!prompt) return;
  addUser(prompt);
  input.value = '';
  const res = await fetch('/chat', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({prompt})}).then(r => r.json());
  render(res, prompt);
}

// Answering "which department?" — re-ask the same question, now scoped.
async function answerDepartment(prompt, queryId, dept) {
  addUser(dept);
  const res = await fetch('/chat', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({prompt, query_id: queryId, department: dept})}).then(r => r.json());
  render(res, prompt);
}

async function confirmDoc(prompt, queryId, docId) {
  const res = await fetch('/chat', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({prompt, query_id: queryId, doc_id: docId})}).then(r => r.json());
  render(res, prompt);
}

function render(res, prompt) {
  if (res.mode === 'clarify') { renderClarify(res); return; }
  if (res.mode === 'entitlement') { renderEntitlement(res); return; }
  // 'lookup' renders exactly like 'policy' — same retrieve-and-quote shape, with the
  // answer read from a rate table instead of prose. Listed explicitly rather than
  // defaulted: a mode this renderer does not know silently renders NOTHING, which is how
  // adding `lookup` server-side left the chat blank while the API returned 200.
  if (res.mode === 'policy' || res.mode === 'lookup') { renderPolicy(res); return; }
  if (res.mode === 'blocked') {
    const w = el('<div></div>');
    w.appendChild(el(`<div class="flagline">${esc(res.message)}</div>`));
    addBot(w);
    return;
  }
  if (res.needs_confirmation) {
    const wrap = el('<div></div>');
    wrap.appendChild(el(`<div>${esc(res.message)}</div>`));
    const c = el('<div class="confirm"></div>');
    res.options.forEach(o => {
      const btn = el(`<button class="ghost">${esc(o.title)} (score ${o.score ?? '—'})</button>`);
      btn.onclick = () => confirmDoc(prompt, res.query_id, o.doc_id);
      c.appendChild(btn);
    });
    wrap.appendChild(c);
    addBot(wrap);
    return;
  }
  // Costing is the last branch, so anything the server sends that this renderer does not
  // know lands here with no `result` and renders a blank bubble — an answer that arrived
  // and was thrown away. Say so instead.
  if (!res.result) {
    addBot(el(`<div class="flagline">This answer came back in a form the page does not
      know how to display (mode: ${esc(res.mode || 'unset')}). The answer is not lost —
      it is in the audit record for query ${esc(res.query_id || '?')}.</div>`));
    return;
  }
  const r = res.result;
  const wrap = el('<div></div>');
  const pathLabel = {
    'governance': 'unit + date lookup',
    'retrieval-fallback': 'document retrieval (fallback)',
    'user-confirmed': 'user-confirmed'
  }[res.routing_path] || res.routing_path;
  const units = (res.bargaining_units || []).join(', ');
  wrap.appendChild(el(`<div class="route">Governing doc <strong>${esc(res.chosen_doc)}</strong>
    via <strong>${esc(pathLabel)}</strong>${units ? ` · unit: ${esc(units)}` : ''}${res.shift_date ? ` · date: ${esc(res.shift_date)}` : ''}
    · parsed via ${esc(res.params.source)}</div>`));
  // The headline is honest about what it is. For ONE classification, the total IS the
  // per-member answer. For several, a grand total would be "one member of each class" —
  // a number that corresponds to no real staffing — so the per-member rows are the
  // answer and the sum is labelled as exactly what it is.
  if ((r.line_items || []).length > 1) {
    wrap.appendChild(el(`<div class="total">${fmt(r.total)}
      <span class="muted" style="font-size:.45em; font-weight:400; display:block">
        sum of ONE member of each of the ${r.line_items.length} classifications below —
        multiply each row by your staffing to cost a real shift</span></div>`));
  } else {
    wrap.appendChild(el(`<div class="total">${fmt(r.total)}
      <span class="muted" style="font-size:.45em; font-weight:400; display:block">per member</span></div>`));
  }

  // Amounts are PER MEMBER of each classification — multiplying by a headcount is the
  // reader's arithmetic, deliberately not ours (PRD §6a).
  const table = el(`<table class="lines"><caption>Cost per member, by classification — select an amount to see how it was calculated</caption>
    <thead><tr><th scope="col">Classification</th><th scope="col">Rule</th><th scope="col">Amount (per member)</th></tr></thead><tbody></tbody></table>`);
  const tb = table.querySelector('tbody');
  r.line_items.forEach(li => {
    const row = document.createElement('tr');
    const amount = document.createElement('button');
    amount.type = 'button';
    amount.className = 'amount';
    amount.setAttribute('aria-label',
      `${fmtVal(li.total, li.result_type)} per member for ${li.subject} — open the audit trail`);
    amount.textContent = fmtVal(li.total, li.result_type);
    amount.onclick = () => openAudit(res.query_id, li);
    const c1 = document.createElement('td'); c1.textContent = li.subject;
    const c2 = document.createElement('td'); c2.textContent = li.rule_id;
    const c3 = document.createElement('td'); c3.appendChild(amount);
    row.append(c1, c2, c3);
    tb.appendChild(row);
  });
  wrap.appendChild(table);

  r.line_items.filter(li => li.needs_human_confirmation).forEach(li => {
    li.flags.forEach(f => {
      wrap.appendChild(el(`<div class="flagline">⚑ <strong>${esc(li.subject)}:</strong>
        ${esc(f.message)} (primary ${fmt(f.primary)}, alternate ${fmt(f.alternate)})</div>`));
    });
  });
  wrap.appendChild(el('<div class="muted" style="margin-top:8px">Click any amount to open its audit trail.</div>'));
  addBot(wrap);
}

// "Which department?" — the answer differs per contract, so ask rather than pick.
function renderClarify(res) {
  const wrap = el('<div></div>');
  wrap.appendChild(el(`<div class="policy-answer">${esc(res.question)}</div>`));
  const row = el('<div class="confirm"></div>');
  (res.options || []).forEach(d => {
    const b = el(`<button class="ghost">${esc(d)}</button>`);
    b.onclick = () => answerDepartment(res.prompt_echo, res.query_id, d);
    row.appendChild(b);
  });
  wrap.appendChild(row);
  if (res.considered && res.considered.length) {
    wrap.appendChild(el(`<div class="muted" style="margin-top:8px">Matching documents span:
      ${esc([...new Set(res.considered.map(c => c.department).filter(Boolean))].join(', '))}</div>`));
  }
  addBot(wrap);
}

// Non-money rule answers: "5 days per §11.3" — same engine, same proof, different unit.
function renderEntitlement(res) {
  const r = res.result;
  const wrap = el('<div></div>');
  wrap.appendChild(el(`<div class="route">Rule answer · ${esc(res.department || 'corpus')} ·
    computed from ratified rules · parsed via ${esc((res.params||{}).source || '')}</div>`));
  const t = el(`<table class="lines"><caption>Answer by employee — select an answer to see the rule and clause behind it</caption>
    <thead><tr><th scope="col">Employee</th><th scope="col">Rule</th><th scope="col">Answer</th></tr></thead><tbody></tbody></table>`);
  const tb = t.querySelector('tbody');
  r.line_items.forEach(li => {
    const row = document.createElement('tr');
    const val = document.createElement('button');
    val.type = 'button';
    val.className = 'amount';
    val.setAttribute('aria-label',
      `${fmtVal(li.total, li.result_type)} for ${li.subject} — open the audit trail`);
    val.textContent = fmtVal(li.total, li.result_type);
    val.onclick = () => openAudit(res.query_id, li);
    const c1 = document.createElement('td'); c1.textContent = li.subject;
    const c2 = document.createElement('td'); c2.textContent = li.topic || li.rule_id;
    const c3 = document.createElement('td'); c3.appendChild(val);
    row.append(c1, c2, c3);
    tb.appendChild(row);
  });
  wrap.appendChild(t);
  wrap.appendChild(el('<div class="muted" style="margin-top:8px">Click the answer to see the rule and the clause it came from.</div>'));
  addBot(wrap);
}

function renderPolicy(res) {
  const wrap = el('<div></div>');
  const scope = res.department ? `${res.department} documents` : 'the corpus';
  const n = (res.considered || []).length;
  const kind = res.mode === 'lookup' ? 'Published figure' : 'Policy answer';
  wrap.appendChild(el(`<div class="route">${kind} · searched <strong>${n}</strong>
    of ${esc(res.corpus_size || n)} documents (${esc(scope)}) · composed via ${esc(res.answer_source)}</div>`));
  wrap.appendChild(el(`<div class="policy-answer">${esc(res.answer)}</div>`));
  // These are the answers a model actually WROTE, so this is where "was that AI?" matters
  // most — the costing drawer is not reachable from here (there are no line items).
  const ai = el('<div style="margin-top:8px"></div>');
  wrap.appendChild(ai);
  renderAiTrail(res.query_id, ai, res.mode === 'lookup' ? 'lookup' : 'policy');
  if (res.sources && res.sources.length) {
    wrap.appendChild(el('<div class="muted" style="margin:10px 0 4px">Read it yourself — click a section to see it on the page:</div>'));
    res.sources.forEach(s => {
      const btn = el(`<button type="button" class="source-chip"><strong>${esc(s.title || s.doc_id)}</strong>
        <span class="tag">${esc(s.department || '')}</span> <strong>§${esc(s.clause)}</strong>
        <span class="muted"> p.${esc(s.page)}</span>${tierChip(s.tier)}<br>
        <span class="src-text">${esc(s.text || '')}</span></button>`);
      btn.onclick = () => openSource(s);
      wrap.appendChild(btn);
    });
  }
  if (res.considered && res.considered.length > 1) {
    const used = new Set((res.sources || []).map(s => s.doc_id));
    const others = res.considered.filter(c => !used.has(c.doc_id));
    if (others.length) {
      wrap.appendChild(el(`<details style="margin-top:8px"><summary class="muted">
        Also considered (${others.length}) — not used</summary>
        <div class="muted" style="margin-top:4px">${others.map(o =>
          esc(o.title || o.doc_id)).join(' · ')}</div></details>`));
    }
  }
  addBot(wrap);
}

function openSource(s) {
  const body = document.getElementById('drawerBody');
  body.innerHTML = `<h3>${esc(s.doc_id)} §${esc(s.clause)}</h3>
    <div class="muted">page ${esc(s.page)} · relevance ${esc(s.score)}${tierChip(s.tier)}</div>
    <div class="trace-step">${esc(s.text || '')}</div>`;
  const box = (s.bbox || []).join(',');
  const cite = el(`<div class="cite"><div class="muted">Source section highlighted on the PDF:</div></div>`);
  const img = new Image(); img.src = `/doc/${s.doc_id}/page/${s.page}?bbox=${box}`;
  img.alt = `Page ${s.page} of ${s.doc_id} with clause ${s.clause} outlined in red`;
  img.onerror = () => { img.remove(); cite.appendChild(el('<div class="muted">(page render unavailable)</div>')); };
  cite.appendChild(img);
  body.appendChild(cite);
  openDrawer();
}

async function openAudit(queryId, li) {
  const body = document.getElementById('drawerBody');
  body.innerHTML = `<h3>${esc(li.subject)} — ${fmtVal(li.total, li.result_type)}</h3>
    <div class="muted">Rule ${esc(li.rule_id)} · query ${esc(queryId)}</div>`;
  // decision trace
  li.trace.forEach(t => {
    const cls = t.kind === 'selector-chosen' ? 'chosen' : (t.kind === 'flag' ? 'flag' : '');
    body.appendChild(el(`<div class="trace-step ${cls}"><span class="k">${esc(t.kind)}</span><br>${esc(t.detail)}</div>`));
  });
  // citations with bbox highlight
  const seen = new Set();
  li.citations.forEach(c => {
    const key = c.doc_id + c.clause;
    if (seen.has(key)) return; seen.add(key);
    const box = (c.bbox || []).join(',');
    const url = `/doc/${c.doc_id}/page/${c.page}?bbox=${box}`;
    const cite = el(`<div class="cite"><div class="muted">Source: ${esc(c.doc_id)} §${esc(c.clause)} (p.${esc(c.page)})${tierChip(c.tier)}</div></div>`);
    const img = new Image(); img.src = url;
  img.alt = `Page ${c.page} of ${c.doc_id} with clause ${c.clause} outlined in red`;
    img.onerror = () => { img.remove(); cite.appendChild(el('<div class="muted">(page render unavailable — bbox: ' + esc(box) + ')</div>')); };
    cite.appendChild(img);
    body.appendChild(cite);
  });
  openDrawer();
  renderAiTrail(queryId, body, 'costing');
}

// "How did the AI reach this answer?" begins with whether AI was involved at all. Every
// touchpoint has a deterministic fallback, so with an expired key the product answers
// exactly as before and says nothing — this section is what makes that visible.
async function renderAiTrail(queryId, body, mode) {
  let ai;
  try {
    ai = (await fetch(`/chat/audit/${queryId}`).then(r => r.json())).ai;
  } catch (e) { return; }
  if (!ai || !ai.calls.length) return;

  const rows = ai.calls.map(c => {
    const what = c.source === 'claude'
      ? `<span class="ok">AI</span> · ${esc(c.model)} · ${esc(c.ms)}ms`
      : c.source === 'error'
      ? `<span class="bad">AI failed</span> · ${esc(c.error || '')}`
      : `<span class="warn-text">no AI</span> · ${esc(c.rule || 'deterministic fallback')}`;
    return `<div class="trace-step"><span class="k">${esc(c.fn)}</span><br>${what}</div>`;
  }).join('');

  const headline = ai.errors.length
    ? `<span class="bad">The model failed on this answer</span> — deterministic code answered instead.`
    : ai.fell_back.length && !ai.used_model
    ? `<span class="warn-text">No AI was used.</span> Deterministic fallbacks produced this answer.`
    : ai.fell_back.length
    ? `<span class="warn-text">Partly AI.</span> ${ai.fell_back.length} step(s) fell back to deterministic code.`
    // Reports involvement only. What the AI was ALLOWED to do differs by mode and is
    // stated in the caveat below — saying "translation only" here would be false on a
    // lookup, where the model reads the figure itself.
    : `AI ran ${ai.calls.length} step(s), ${esc(ai.total_ms)}ms total.`;

  // What the AI did to THIS answer differs by mode, and saying the wrong one is worse
  // than saying nothing. A costing figure is computed by the engine and no model touches
  // it. A lookup figure is READ OUT of the document BY the model — grounded in the row
  // shown below, but model-written text. Claiming "no figure here came from a model" on a
  // lookup would be false, and it is the kind of overclaim a union exists to find.
  const CAVEAT = {
    lookup: `The figure above was <strong>read out of the document by the AI</strong>, not
      computed — and not covered by a ratified rule. It is quoted from the row shown below:
      check it against the source. Nothing here was calculated.`,
    policy: `The answer above was <strong>written by the AI</strong>, composed only from the
      clauses shown below. It quotes the contract; it does not compute anything. Read the
      cited sections yourself.`,
    costing: `<strong>No figure above was produced by a model.</strong> The AI only routed
      the question and read it into structured fields. Every amount was computed by the
      deterministic engine from human-ratified rules.`,
  };
  body.appendChild(el(`<details style="margin-top:10px" open>
    <summary><strong>AI involvement</strong></summary>
    <p class="muted" style="margin:6px 0">${headline}</p>
    ${rows}
    <p class="muted">${CAVEAT[mode] || CAVEAT.costing}</p>
  </details>`));
}
let LAST_FOCUS = null;
function openDrawer() {
  LAST_FOCUS = document.activeElement;
  const d = document.getElementById('drawer');
  d.hidden = false; d.classList.add('open');
  d.querySelector('button').focus();
}
function closeDrawer() {
  const d = document.getElementById('drawer');
  d.classList.remove('open'); d.hidden = true;
  if (LAST_FOCUS) LAST_FOCUS.focus();
}
document.addEventListener('keydown', e => {
  const d = document.getElementById('drawer');
  if (e.key === 'Escape' && !d.hidden) closeDrawer();
});


