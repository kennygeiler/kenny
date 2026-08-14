// Guided demo tour: one shared step engine, per-page step lists. Loaded by BOTH
// chat.html and admin.html; which list runs is keyed by <body data-page>. The visitor
// presses one button and the tour performs the clicks — typing the question, opening
// the drawer, stepping the admin viewers — with a Next button to advance.
//
// Everything in this file is inert until startTour() runs: the only load-time work is
// wiring the start button (chat) and the ?tour=1 resume hook (admin), so the tour has
// zero effect on normal use. Any selector that has moved skips its step with a
// console.warn — the tour must never crash or trap a visitor.
(() => {
  'use strict';

  const PAGE = document.body.dataset.page
    || (location.pathname.startsWith('/admin') ? 'admin' : 'chat');

  /* ------------- tiny helpers (no dependence on either page's globals) ------------- */
  const q = sel => { try { return document.querySelector(sel); } catch (e) { return null; } };
  const qa = sel => { try { return [...document.querySelectorAll(sel)]; } catch (e) { return []; } };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const SMOOTH = matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';

  async function poll(fn, timeout, every = 200) {
    const t0 = Date.now();
    for (;;) {
      const v = fn();
      if (v) return v;
      if (Date.now() - t0 > timeout) return null;
      await sleep(every);
    }
  }

  // Visible fast-type effect — the demo should look driven, not teleported.
  async function typeInto(input, text) {
    input.focus();
    input.value = '';
    for (let i = 0; i < text.length; i += 3) {
      input.value = text.slice(0, i + 3);
      await sleep(8);
    }
    input.value = text;
  }

  // Find a document-card button by its onclick prefix, preferring one whose doc id
  // (in the onclick attribute) matches `re`, then one whose card text does — the tour
  // survives documents being reordered or retitled. Matching card TEXT first was a
  // bug: the salary schedule's summary mentions "firefighter" rows, so the X-ray step
  // opened the wrong document.
  function docButton(prefix, re) {
    const btns = qa(`#docs button[onclick^="${prefix}"]`);
    return btns.find(b => re.test(b.getAttribute('onclick') || ''))
        || btns.find(b => re.test((b.closest('.card') || b).textContent || ''))
        || btns[0] || null;
  }

  // Record which dialogs the tour itself opened, so ending mid-way closes them and
  // never leaves the visitor under a modal they did not ask for.
  const mark = tag => { if (state) state.opened.add(tag); };

  const QUESTION_COSTING =
    'Cost an 8-hour overtime shift for a Firefighter/Paramedic (56 hr, top step)';
  const QUESTION_POLICY =
    'What does the Firefighters Local 3535 MOU say about overtime?';

  /* ---------------- step lists, keyed by page ----------------
     A step: { title, body, target (selector | fn -> element | absent = centered card),
               pre (async action performed on entering the step),
               waitFor (selector; waitForNew waits for a NEW match, not a leftover),
               waitTimeout, timeoutNote, nextLabel, advance (replaces plain Next) }. */
  const STEPS = {
    chat: [
      {
        title: 'Welcome to Kenny',
        body: 'Kenny answers HR costing and policy questions straight from contract '
          + 'PDFs. This badge says who is answering — Claude, or the deterministic '
          + 'fallback. The money math is deterministic either way. Press Next and the '
          + 'tour will drive; Esc ends it any time.',
        target: '#keyBadge',
      },
      {
        title: 'Watch a costing question',
        body: 'That figure was computed by a deterministic engine from human-approved '
          + 'rules — the AI only routed the question into structured fields. No model '
          + 'produced the number.',
        pre: async () => {
          const input = q('#prompt');
          if (!input) return;
          await typeInto(input, QUESTION_COSTING);
          await sleep(150);
          if (typeof window.send === 'function') window.send();
          else { const f = q('#askForm'); if (f && f.requestSubmit) f.requestSubmit(); }
        },
        waitFor: '#log .msg.bot .total',
        waitForNew: true,
        waitTimeout: 30000,
        timeoutNote: 'Still computing — the deterministic fallback can be slower. '
          + 'The figure will appear in the conversation when it lands.',
        target: () => qa('#log .msg.bot .total').pop(),
      },
      {
        title: 'Every number opens its audit trail',
        body: 'Clicking an amount opens the drawer: the decision trace, whether AI was '
          + 'involved at each step, and the source clause boxed in red on the rendered '
          + 'PDF page. The "number I can defend", made literal.',
        pre: async () => {
          const msgs = qa('#log .msg.bot');
          for (let i = msgs.length - 1; i >= 0; i--) {
            const a = msgs[i].querySelector('.amount');
            if (a) { mark('drawer'); a.click(); return; }
          }
        },
        waitFor: '#drawer.open',
        waitTimeout: 8000,
        target: '#drawer .db',
      },
      {
        title: 'Where the cited text came from',
        body: 'Every citation wears an extraction-tier chip: "text layer" means it was '
          + 'read from the PDF’s digital text layer with exact clause positions; a '
          + 'scanned document would say "recovered layout" or "page-level" instead. The '
          + 'trust signal, stated at the moment of reading.',
        target: '#drawer .tier-chip',
      },
      {
        title: 'Policy answers quote, never compute',
        body: 'A policy answer is composed only from the clauses shown beneath it. Each '
          + 'source chip carries the same tier chip and clicking one opens the clause '
          + 'highlighted on its page — read it yourself.',
        pre: async () => {
          if (typeof window.closeDrawer === 'function' && q('#drawer.open')) window.closeDrawer();
          const input = q('#prompt');
          if (!input) return;
          await typeInto(input, QUESTION_POLICY);
          await sleep(150);
          if (typeof window.send === 'function') window.send();
          else { const f = q('#askForm'); if (f && f.requestSubmit) f.requestSubmit(); }
        },
        waitFor: '#log .source-chip',
        waitForNew: true,
        waitTimeout: 30000,
        timeoutNote: 'Still composing — the deterministic fallback can be slower. '
          + 'The quoted answer will appear in the conversation when it lands.',
        target: () => {
          const m = qa('#log .msg.bot').pop();
          return m ? m.querySelector('.source-chip') : null;
        },
      },
      {
        title: 'Now the ops side',
        body: 'Everything you just saw rests on an admin loop: documents are ingested '
          + 'and X-rayed, rules are drafted against known answers and human-approved, '
          + 'and every event lands in a hash-chained ledger. Next takes you there.',
        target: 'header nav a[href="/admin"]',
        nextLabel: 'Go to Admin',
        advance: () => { location.href = '/admin?tour=1'; },
      },
    ],

    admin: [
      {
        title: 'The extraction scorecard',
        body: 'Every document Kenny holds, with how well the machine read it: the green '
          + 'tier badge means docling parsed the digital text layer, the counts show '
          + 'clauses and table rows, and the grey hash is the SHA-256 binding of the '
          + 'exact PDF that was ingested.',
        waitFor: '#docs .score',
        waitTimeout: 20000,
        target: '#docs .score',
      },
      {
        title: 'Uploads run in visible stages',
        body: 'Add a contract here and you watch it being read live: saving → '
          + 'parsing → tagging → indexing, ending in a card that opens the '
          + 'new document’s X-ray. (The tour won’t upload anything now.)',
        target: '#panel-docs .toolbar',
      },
      {
        title: 'X-ray: proof the machine read it',
        body: 'This is the Firefighters MOU with every extracted clause boxed on the '
          + 'page it came from — the machine’s reading laid over the paper. The '
          + 'arrows above step through the pages.',
        pre: async () => {
          await poll(() => q('#docs button[onclick^="openXray"]'), 15000);
          const b = docButton('openXray', /3535|firefighter/i);
          if (b) { mark('xray'); b.click(); }
        },
        waitFor: '#xrayStage .xbox',
        waitTimeout: 15000,
        target: '#xrayStage',
      },
      {
        title: 'What the boxes mean',
        body: 'Blue is normal text, amber a table row, purple text recovered after the '
          + 'layout model misread a page, grey a page-level fallback. Hover any box to '
          + 'read exactly what was extracted from it.',
        target: '#xray .xlegend',
      },
      {
        title: 'Compare: check the extraction by eye',
        body: 'Same page, split view: the rendered PDF left, the extracted text in '
          + 'reading order right. Hovering either side lights its counterpart, so a '
          + 'mismatched extraction is findable in seconds.',
        pre: async () => {
          const b = docButton('openCompare', /3535|firefighter/i);
          if (b) { mark('xray'); b.click(); }
        },
        waitFor: '#xrayText .xrow',
        waitTimeout: 15000,
        target: '#xrayText',
      },
      {
        title: 'Table X-ray: the salary schedule',
        body: 'A page that is mostly table rows is rebuilt as the grid it was on paper. '
          + 'Click a row and its exact source band highlights on the page image — every '
          + 'rate traceable to its cell.',
        pre: async () => {
          const b = docButton('openCompare', /salary/i);
          if (!b) return;
          mark('xray');
          b.click();
          // The structured table appears on majority-table pages; hunt a few pages in.
          for (let i = 0; i < 6; i++) {
            if (await poll(() => q('#xrayText .xtable'), 2500)) return;
            const nxt = q('#xrayNext');
            if (!nxt || nxt.disabled) return;
            nxt.click();
          }
        },
        waitFor: '#xrayText .xtable',
        waitTimeout: 4000,
        timeoutNote: 'No majority-table page surfaced on this document — the grid '
          + 'appears automatically when a page is mostly table rows.',
        target: '#xrayText .xtable-wrap',
      },
      {
        title: 'Verification: known answers first',
        body: 'Each card is ground truth — a real paystub or a hand-verified figure. '
          + 'Rules are drafted per scenario and cannot go live unless they reproduce '
          + 'the known answer. Trust is verified up front, not promised.',
        pre: async () => {
          if (q('#xray:not([hidden])') && typeof window.closeXray === 'function') window.closeXray();
          const t = q('#tab-verify');
          if (t) t.click();
        },
        waitFor: '#verify .card',
        waitTimeout: 20000,
        target: '#verify .card',
      },
      {
        title: 'Audit: the tamper-evident ledger',
        body: 'Every question, draft, approval and ingest is a hash-chained event. '
          + '"Record intact" means the chain verified end to end — altering any entry '
          + 'breaks it and is detected. The whole record exports for an auditor.',
        pre: async () => { const t = q('#tab-audit'); if (t) t.click(); },
        waitFor: '#verifyChain .badge-ratified, #verifyChain .bad, #ledger .evt',
        waitTimeout: 15000,
        target: '#panel-audit h2',
      },
      {
        title: 'That’s the loop',
        body: 'Documents in, verified rules, defensible numbers. Go back to chat and '
          + 'ask your own question — or hand the Audit tab to an auditor.',
        nextLabel: 'Back to chat',
        advance: () => { location.href = '/'; },
      },
    ],
  };

  /* ---------------- engine ---------------- */
  let state = null;   // null = not running; every entry point checks it

  function startTour() {
    if (state) return;
    const steps = STEPS[PAGE] || [];
    if (!steps.length) return;
    const card = document.createElement('div');
    card.className = 'tour-card';
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-live', 'polite');
    card.setAttribute('aria-label', 'Guided tour');
    document.body.appendChild(card);
    state = { steps, card, i: -1, seq: 0, target: null, opened: new Set() };
    document.addEventListener('keydown', onKey);
    window.addEventListener('resize', reposition);
    window.addEventListener('scroll', reposition, true);
    next();
  }

  function endTour() {
    if (!state) return;
    const s = state;
    state = null;   // stops any in-flight step the moment its next await resolves
    qa('.tour-glow').forEach(n => n.classList.remove('tour-glow'));
    document.removeEventListener('keydown', onKey);
    window.removeEventListener('resize', reposition);
    window.removeEventListener('scroll', reposition, true);
    s.card.remove();
    // Close anything the tour itself opened, so ending never traps the visitor.
    if (s.opened.has('drawer') && q('#drawer.open')
        && typeof window.closeDrawer === 'function') window.closeDrawer();
    if (s.opened.has('xray') && q('#xray:not([hidden])')
        && typeof window.closeXray === 'function') window.closeXray();
  }

  function onKey(e) { if (e.key === 'Escape') endTour(); }

  const resolveTarget = s =>
    typeof s.target === 'function' ? s.target() : (s.target ? q(s.target) : null);

  async function next() {
    if (!state) return;
    const seq = ++state.seq;
    const i = ++state.i;
    if (i >= state.steps.length) { endTour(); return; }
    const s = state.steps[i];
    qa('.tour-glow').forEach(n => n.classList.remove('tour-glow'));
    state.target = null;
    render(s, i, { busy: !!(s.pre || s.waitFor), note: '' });
    position(null);
    // Watchdog: whatever happens inside this step — an action that never settles, a
    // silent seq race, a wait that outlives its poll — the card must not stay busy
    // forever. Verified live: a slow model answer left "performing…" up with Next
    // disabled, which is exactly the trap this engine promises never to spring.
    if (s.pre || s.waitFor) {
      const deadline = (s.waitTimeout || 15000) + 5000;
      setTimeout(() => {
        if (state && state.seq === seq && state.i === i) {
          render(s, i, { busy: false, note: s.timeoutNote || '' });
          position(resolveTarget(s));
        }
      }, deadline);
    }
    // Baseline BEFORE the action, so "wait for the response" means a NEW element,
    // not one left over from an earlier answer in the same session.
    const base = (s.waitFor && s.waitForNew) ? qa(s.waitFor).length : 0;
    if (s.pre) {
      try { await s.pre(); }
      catch (e) { console.warn('[tour] step action failed:', s.title, e); }
    }
    if (!state || state.seq !== seq) return;
    let note = '';
    if (s.waitFor) {
      const ok = await poll(() => {
        const n = qa(s.waitFor).length;
        return s.waitForNew ? n > base : n > 0;
      }, s.waitTimeout || 15000);
      if (!state || state.seq !== seq) return;
      if (!ok) {
        note = s.timeoutNote || 'This part did not load in time — continuing anyway.';
        console.warn('[tour] waitFor timed out:', s.title, s.waitFor);
      }
    }
    const target = resolveTarget(s);
    if (!target && s.target && !note) {
      // The feature moved or is absent: never crash, never trap — skip the step.
      console.warn('[tour] target missing, skipping step:', s.title, s.target);
      next();
      return;
    }
    if (target) {
      target.classList.add('tour-glow');
      state.target = target;
      target.scrollIntoView({ block: 'center', behavior: SMOOTH });
      await sleep(SMOOTH === 'smooth' ? 350 : 0);
      if (!state || state.seq !== seq) return;
    }
    render(s, i, { busy: false, note });
    position(target);
  }

  function render(s, i, { busy, note }) {
    const n = state.steps.length;
    const card = state.card;
    card.innerHTML = '';
    const h = document.createElement('h3');
    h.textContent = s.title;
    const p = document.createElement('p');
    p.textContent = busy ? 'One moment — performing this step for you…' : s.body;
    card.append(h, p);
    if (note) {
      const w = document.createElement('p');
      w.className = 'tour-note';
      w.textContent = note;
      card.appendChild(w);
    }
    const foot = document.createElement('div');
    foot.className = 'tour-foot';
    const prog = document.createElement('span');
    prog.className = 'tour-progress';
    prog.textContent = `Step ${i + 1} of ${n}`;
    const nextBtn = document.createElement('button');
    nextBtn.type = 'button';
    nextBtn.className = 'tour-next';
    // Never disabled: while the step is still performing, the button reads "Skip" and
    // simply advances past the wait. A disabled Next plus any stalled action is a
    // trapped visitor; seq guards make skipping mid-action safe (the stale step's
    // remaining work no-ops when it sees the sequence moved on).
    nextBtn.textContent = busy ? 'Skip'
      : (s.nextLabel || (i + 1 === n ? 'Finish' : 'Next'));
    nextBtn.onclick = () => { if (s.advance) { endTour(); s.advance(); } else next(); };
    const endBtn = document.createElement('button');
    endBtn.type = 'button';
    endBtn.className = 'tour-end';
    endBtn.textContent = 'End tour';
    endBtn.onclick = endTour;
    foot.append(prog, nextBtn, endBtn);
    card.appendChild(foot);
    if (!busy) nextBtn.focus({ preventScroll: true });
  }

  // Viewport-aware placement: below the target, else above, else beside, else centered.
  function position(target) {
    if (!state) return;
    const c = state.card, pad = 14;
    const cw = c.offsetWidth, ch = c.offsetHeight;
    const r = target ? target.getBoundingClientRect() : null;
    let top, left;
    if (!r || (r.width === 0 && r.height === 0)) {
      top = (innerHeight - ch) / 2;
      left = (innerWidth - cw) / 2;
    } else if (r.bottom + pad + ch <= innerHeight) { top = r.bottom + pad; left = r.left; }
    else if (r.top - pad - ch >= 0) { top = r.top - pad - ch; left = r.left; }
    else if (r.left - pad - cw >= 0) { top = Math.max(16, r.top); left = r.left - pad - cw; }
    else if (r.right + pad + cw <= innerWidth) { top = Math.max(16, r.top); left = r.right + pad; }
    else { top = (innerHeight - ch) / 2; left = (innerWidth - cw) / 2; }
    c.style.left = Math.min(Math.max(8, left), Math.max(8, innerWidth - cw - 8)) + 'px';
    c.style.top = Math.min(Math.max(8, top), Math.max(8, innerHeight - ch - 8)) + 'px';
  }

  function reposition() { if (state) position(state.target); }

  /* ------------- entry points (the only work done at load time) ------------- */
  const startBtn = document.getElementById('tourStart');
  if (startBtn) startBtn.addEventListener('click', startTour);
  if (PAGE === 'admin' && new URLSearchParams(location.search).get('tour') === '1') {
    // Strip the flag so reloading /admin does not restart the tour uninvited.
    history.replaceState(null, '', location.pathname + location.hash);
    startTour();
  }
})();
