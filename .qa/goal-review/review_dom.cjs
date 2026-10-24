'use strict';
// Actual isolated HTTP JSON through the production duplicate-review module.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const ROOT = path.resolve(__dirname, '../..');
const { JSDOM, VirtualConsole } = require(path.join(ROOT, 'web/tests/node_modules/jsdom'));
const inputPath = path.join(__dirname, 'review_http.json');
const input = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const fixtures = input.fixtures, pristine = JSON.stringify(fixtures);
const source = fs.readFileSync(path.join(ROOT, 'web/duplicate-review.js'), 'utf8');
const html = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');
const hash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const sourceHashes = Object.fromEntries(['web/duplicate-review.js', 'web/app.js', 'web/index.html'].map(file => [file, hash(path.join(ROOT, file))]));
for (const [file, expected] of Object.entries(input.source_sha256)) assert.equal(hash(path.join(ROOT, file)), expected, 'API fixture source is stale');
const clone = value => JSON.parse(JSON.stringify(value));
const response = body => ({ ok: true, status: 200, json: async () => clone(body) });
const reports = [];

function setup({ previewKey = 'preview', workflow = 'merge', mutationError = false, readResolver } = {}) {
  const errors = [], calls = [], virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM(html, { url: 'https://fixture.invalid/app#activities', runScripts: 'outside-only', virtualConsole });
  const w = dom.window, $ = selector => w.document.querySelector(selector);
  const state = { phase: 'initial', busy: false, loads: 0 };
  $('#activities').hidden = false;
  w.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  w.HTMLDialogElement.prototype.close = function () { if (this.open) { this.removeAttribute('open'); this.dispatchEvent(new w.Event('close')); } };
  w.HTMLElement.prototype.scrollIntoView = function () {};
  const byText = text => [...w.document.querySelectorAll('button')].find(node => node.textContent === text);
  function pending() {
    const values = state.phase === 'applied' ? fixtures.pending_after_merge : fixtures.pending_before;
    $('#duplicate-list').replaceChildren();
    $('#duplicate-card').hidden = !values.length;
    for (const entry of values) {
      const control = w.document.createElement('button');
      control.textContent = 'Apri confronto'; control.dataset.candidate = entry.id;
      control.addEventListener('click', () => w.IronCoachUI.openDuplicateReview(entry.id, control));
      $('#duplicate-list').append(control);
    }
  }
  const currentHistory = () => fixtures[state.phase === 'initial' ? 'history_initial' : state.phase === 'applied' ? 'history_applied' : state.phase === 'kept' ? 'history_kept' : workflow === 'keep' ? 'history_keep_undone' : 'history_undone'];
  w.fetch = async (url, options = {}) => {
    const call = { url: String(url), method: options.method || 'GET', signal: options.signal };
    calls.push(call); assert.equal(call.method, 'GET');
    if (readResolver) { const result = readResolver(call, calls); if (result !== undefined) return result; }
    if (call.url.includes('/review-history?')) return response(currentHistory());
    if (/\/\d+\/preview$/.test(call.url)) return response(fixtures[previewKey]);
    throw new Error('Unexpected read ' + call.url);
  };
  w.IronCoachUI = {
    sourceLabel(value) { return { fit: 'FIT', intervals: 'Intervals.icu', strava: 'Strava' }[value] || value; },
    async api(url, options = {}) {
      const call = { url, method: options.method || 'GET', payload: options.body ? JSON.parse(options.body) : null };
      calls.push(call); assert.equal(call.method, 'POST');
      if (mutationError) { const error = new Error(fixtures.stale_merge_error.detail); error.status = 409; throw error; }
      if (/\/reviews\/\d+\/undo$/.test(url)) {
        assert.deepEqual(call.payload, {}); state.phase = 'undone';
        return clone(fixtures[workflow === 'keep' ? 'keep_undo_response' : 'undo_response']);
      }
      assert.deepEqual(call.payload, { state_token: fixtures.preview.state_token });
      assert.match(call.payload.state_token, /^[a-f0-9]{64}$/);
      if (url.endsWith('/merge')) { state.phase = 'applied'; return clone(fixtures.merge_response); }
      if (url.endsWith('/keep')) { state.phase = 'kept'; return clone(fixtures.keep_response); }
      throw new Error('Unexpected mutation ' + url);
    },
    async load() {
      state.loads++; pending();
      w.document.dispatchEvent(new w.CustomEvent('iron:loaded'));
    },
    async action(work) {
      if (state.busy) return;
      state.busy = true;
      const controls = [...w.document.querySelectorAll('button,input,select')], original = controls.map(node => node.disabled);
      controls.forEach(node => { node.disabled = true; });
      try { await work(); } finally { state.busy = false; controls.forEach((node, index) => { node.disabled = original[index]; }); }
    },
  };
  pending(); w.eval(source);
  const flush = async () => { for (let i = 0; i < 14; i++) await new Promise(resolve => setImmediate(resolve)); assert.deepEqual(errors, []); };
  const open = () => {
    const trigger = $(`#duplicate-list [data-candidate="${fixtures.preview.candidate_id}"]`);
    trigger.focus(); trigger.click(); return trigger;
  };
  const close = () => {
    assert.deepEqual(errors, []);
    assert.equal(w.localStorage.length, 0); assert.equal(w.sessionStorage.length, 0);
    w.dispatchEvent(new w.Event('pagehide')); w.close();
  };
  return { w, $, calls, state, byText, flush, open, close };
}

async function roundtrip(workflow) {
  const qa = setup({ workflow }), { w, $, calls, byText, flush, open, state } = qa;
  try {
    await flush(); assert.ok(calls.every(call => call.method === 'GET'));
    assert.match($('.duplicate-history-message').textContent, /Nessuna decisione precedente/);
    open(); await flush();
    const dialog = $('#duplicate-review-dialog');
    assert.equal(dialog.open, true);
    assert.equal($('.duplicate-review-top').firstElementChild.textContent, '← Indietro');
    assert.equal(w.document.activeElement, $('.duplicate-review-top').firstElementChild);
    assert.equal(calls.filter(call => call.method === 'POST').length, 0);
    const cards = $('.duplicate-activities').textContent;
    assert.match(cards, /Identificativo fonte: keep.*Identificativo fonte: remove/);
    assert.match(cards, /Disciplina: Corsa/); assert.match(cards, /12:11/); assert.match(cards, /1 h.*10 km/);
    const rows = [...$('.duplicate-diff-table tbody').rows];
    const heart = rows.find(row => row.cells[0].textContent.includes('Frequenza media'));
    assert.deepEqual([...heart.cells].slice(1, 4).map(node => node.textContent), ['120', '130', '120']);
    const load = rows.find(row => row.cells[0].textContent.includes('Carico'));
    assert.deepEqual([...load.cells].slice(1, 4).map(node => node.textContent), ['—', '70', '70']);
    assert.equal(rows.length, fixtures.preview.fields.length);
    const choice = workflow === 'merge' ? [...dialog.querySelectorAll('button')].find(node => /^Conferma unione/.test(node.textContent)) : byText('Sono attività distinte');
    choice.click(); await flush();
    assert.equal(calls.filter(call => call.method === 'POST').length, 1);
    assert.equal(dialog.open, false);
    assert.equal(state.loads, 1);
    assert.equal($('.duplicate-history-row').dataset.status, 'applied');
    if (workflow === 'merge') {
      assert.equal($('#duplicate-card').hidden, true);
      assert.equal(w.document.activeElement, $('.duplicate-history-card h2'));
    }
    const postsBeforeUndo = calls.filter(call => call.method === 'POST').length;
    byText('Annulla questa decisione').click(); await flush();
    assert.equal(dialog.open, true);
    assert.equal(calls.filter(call => call.method === 'POST').length, postsBeforeUndo, 'Opening undo must remain a read-only confirmation');
    byText('Conferma annullamento').click(); await flush();
    assert.equal(dialog.open, false);
    assert.equal(calls.filter(call => call.method === 'POST').length, 2);
    assert.equal($('.duplicate-history-row').dataset.status, 'undone');
    assert.equal(byText('Annulla questa decisione'), undefined);
    assert.match($('.duplicate-history-row').textContent, /Annullata/);
    reports.push({ case: workflow + '-actual-http-json-roundtrip', result: 'explicit confirmation and exact API token; refreshed applied/undone history',
                   preview_fields: rows.length, post_requests: 2, source_dates_units: 'checked',
                   history_without_pending: workflow === 'merge' ? 'checked' : 'not applicable', storage_writes: 0 });
  } finally { qa.close(); }
}

async function blockedAndStale() {
  for (const key of ['self_preview', 'invalid_numeric_preview']) {
    const qa = setup({ previewKey: key });
    try {
      await qa.flush(); qa.open(); await qa.flush();
      const merge = [...qa.$('#duplicate-review-dialog').querySelectorAll('button')].find(node => /^Conferma unione/.test(node.textContent));
      assert.equal(merge.disabled, true); merge.click(); await qa.flush();
      assert.equal(qa.calls.filter(call => call.method === 'POST').length, 0);
      assert.ok(qa.$('.duplicate-blocking').textContent.length > 20);
      if (key === 'invalid_numeric_preview') {
        assert.match(qa.$('.duplicate-diff-table').textContent, /Dato non valido/);
        assert.doesNotMatch(qa.$('.duplicate-diff-table').textContent, /invalid_number|\bInfinity\b/);
      }
      reports.push({ case: key, result: 'actual server block displayed; merge disabled, no POST' });
    } finally { qa.close(); }
  }
  const qa = setup({ mutationError: true });
  try {
    await qa.flush(); qa.open(); await qa.flush();
    [...qa.$('#duplicate-review-dialog').querySelectorAll('button')].find(node => /^Conferma unione/.test(node.textContent)).click();
    await qa.flush();
    assert.equal(qa.calls.filter(call => call.method === 'POST').length, 1);
    assert.match(qa.$('.duplicate-review-status').textContent, /conferma di nuovo/);
    const merge = [...qa.$('#duplicate-review-dialog').querySelectorAll('button')].find(node => /^Conferma unione/.test(node.textContent));
    assert.equal(merge.disabled, true);
    qa.byText('Ricarica confronto').click(); await qa.flush();
    assert.equal(qa.calls.filter(call => call.method === 'POST').length, 1, 'Reload cannot retry a mutation automatically');
    assert.equal(merge.disabled, false);
    reports.push({ case: 'actual-stale-token-error', result: '409 invalidates preview; reload is GET only and requires another explicit confirmation' });
  } finally { qa.close(); }
}

async function lateReads() {
  let resolveFirst, readCount = 0;
  const first = new Promise(resolve => { resolveFirst = resolve; });
  const qa = setup({ readResolver(call) { if (/\/preview$/.test(call.url)) { readCount++; return readCount === 1 ? first : response(fixtures.preview); } } });
  try {
    await qa.flush(); qa.open(); await qa.flush();
    const old = qa.calls.find(call => /\/preview$/.test(call.url));
    qa.w.IronCoachUI.openDuplicateReview(fixtures.preview.candidate_id); await qa.flush();
    assert.equal(old.signal.aborted, true);
    resolveFirst(response(fixtures.self_preview)); await qa.flush();
    assert.equal(qa.$('.duplicate-blocking'), null);
    const merge = [...qa.$('#duplicate-review-dialog').querySelectorAll('button')].find(node => /^Conferma unione/.test(node.textContent));
    assert.equal(merge.disabled, false);
    qa.$('#activities').hidden = true;
    qa.w.document.dispatchEvent(new qa.w.CustomEvent('iron:pagechange', { detail: { id: 'health' } }));
    await qa.flush(); assert.equal(qa.$('#duplicate-review-dialog').open, false);
    assert.equal(qa.calls.filter(call => call.method === 'POST').length, 0);
    reports.push({ case: 'late-preview-and-navigation', result: 'old blocked JSON ignored after newer preview; page change closes safely without mutations' });
  } finally { qa.close(); }
}

(async () => {
  await roundtrip('merge'); await roundtrip('keep'); await blockedAndStale(); await lateReads();
  assert.equal(JSON.stringify(fixtures), pristine);
  for (const [file, expected] of Object.entries(sourceHashes)) assert.equal(hash(path.join(ROOT, file)), expected);
  const output = { result: 'passed', scope: 'Production duplicate-review.js in JSDOM using actual isolated HTTP JSON; not a real browser or live provider',
                   input_sha256: hash(inputPath), source_sha256: sourceHashes, backend_source_sha256: input.source_sha256,
                   reports, fixture_mutations: 0, storage_writes: 0, personal_database_opened: false, provider_calls: 0 };
  fs.writeFileSync(path.join(__dirname, 'review_dom.json'), JSON.stringify(output, null, 2));
  console.log(JSON.stringify(output));
})().catch(error => { console.error(error); process.exitCode = 1; });
