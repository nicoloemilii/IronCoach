'use strict';
// Local source and API fixtures only. No server, browser or personal data.
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const source = fs.readFileSync(path.join(__dirname, '../duplicate-review.js'), 'utf8');
const appSource = fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => clone(data) });
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function preview(changes = {}) {
  const activity = { id: 11, name: 'Giro importato', source: 'intervals', source_id: 'a11', sport: 'cycling', start_time: '2026-10-19T07:00:00Z', duration_seconds: 3600, distance_meters: 25000, avg_power: 210, load: 55 };
  const other = { ...activity, id: 12, name: 'Giro conservato', source: 'garmin', source_id: 'g12', avg_power: null, load: 0 };
  const counts = { activity: 2, other: 1, total: 3 };
  return { candidate_id: 1, state_token: 'token-current-1', activity, other, result: { ...other, avg_power: 210 }, fields: [
    { field: 'load', before_keep: 0, before_remove: 55, result: 0, source: 'existing' },
    { field: 'avg_power', before_keep: null, before_remove: 210, result: 210, source: 'imported' },
    { field: 'distance_meters', before_keep: 25000, before_remove: 25000, result: 25000, source: 'existing' },
    { field: 'elevation_gain_meters', before_keep: null, before_remove: null, result: null, source: 'missing' }
  ], links: Object.fromEntries(['identities', 'observations', 'feedback', 'evaluations', 'candidates'].map(key => [key, { ...counts }])), merge_allowed: true, blocking_reasons: [], ...changes };
}
function review(changes = {}) {
  return { id: 21, candidate_id: 1, action: 'merge', status: 'applied', activity_id: 11, other_id: 12, created_at: '2026-10-19T09:00:00Z', undone_at: null, summary: { activity_name: 'Giro importato', other_name: 'Giro conservato', sources: ['intervals', 'garmin'] }, undo_available: true, undo_reason: null, ...changes };
}
function setup(t, { visible = true, history = [], previews = {}, read, mutate } = {}) {
  const errors = [], calls = [], virtualConsole = new VirtualConsole(); virtualConsole.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM('<!doctype html><html><body><section id="activities"><button id="trigger">Apri confronto</button><article id="duplicate-card" hidden><div id="duplicate-list"></div></article></section><section id="health" hidden></section></body></html>', { url: 'https://fixture.invalid/app#activities', runScripts: 'outside-only', virtualConsole });
  const w = dom.window, $ = selector => w.document.querySelector(selector), data = { previews: { 1: preview(), ...previews }, history: clone(history), limits: ['L’annullamento richiede che i dati non siano stati modificati.'], loads: 0, busy: false, failures: {} };
  $('#activities').hidden = !visible;
  w.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  w.HTMLDialogElement.prototype.close = function () { if (this.open) { this.removeAttribute('open'); this.dispatchEvent(new w.Event('close')); } };
  const loaded = () => w.document.dispatchEvent(new w.CustomEvent('iron:loaded'));
  const api = async (url, options = {}) => {
    const call = { url, method: options.method || 'GET', payload: options.body ? JSON.parse(options.body) : null }; calls.push(call);
    if (data.failures[`${call.method} ${url}`]) { const failure = data.failures[`${call.method} ${url}`], error = new Error(failure.message); error.status = failure.status; throw error; }
    if (mutate) { const result = await mutate(call, data); if (result !== undefined) return result; }
    if (/\/reviews\/\d+\/undo$/.test(url)) { const entry = data.history.find(item => item.id === Number(url.split('/').at(-2))); entry.status = 'undone'; entry.undone_at = '2026-10-19T10:00:00Z'; entry.undo_available = false; entry.undo_reason = 'Decisione già annullata.'; return { status: 'undone', review_id: entry.id }; }
    if (/\/(merge|keep)$/.test(url)) { const action = url.split('/').at(-1); data.history.unshift(review({ action })); return { status: action === 'merge' ? 'merged' : 'kept', review_id: 21, undo_available: true }; }
    throw new Error(`Unexpected mutation: ${call.method} ${url}`);
  };
  w.fetch = async (url, options = {}) => {
    const call = { url: String(url), method: options.method || 'GET', signal: options.signal, payload: options.body ? JSON.parse(options.body) : null }; calls.push(call);
    if (read) { const result = await read(call, data); if (result !== undefined) return result; }
    if (call.url.includes('/review-history?')) return response({ reviews: data.history, limits: data.limits });
    if (/\/\d+\/preview$/.test(call.url)) return response(data.previews[Number(call.url.split('/').at(-2))]);
    throw new Error(`Unexpected read: ${call.method} ${call.url}`);
  };
  w.IronCoachUI = { api, sourceLabel: value => ({ garmin: 'Garmin', intervals: 'Intervals.icu' }[value] || value), load: async () => { data.loads++; loaded(); }, action: async work => {
    if (data.busy) return; data.busy = true;
    const controls = [...w.document.querySelectorAll('button,input,select')], original = controls.map(node => node.disabled); controls.forEach(node => node.disabled = true);
    try { await work(); } finally { data.busy = false; controls.forEach((node, index) => node.disabled = original[index]); }
  } };
  const flush = async () => { for (let index = 0; index < 12; index++) await new Promise(resolve => setImmediate(resolve)); assert.deepEqual(errors, []); };
  const page = id => { $('#activities').hidden = id !== 'activities'; w.document.dispatchEvent(new w.CustomEvent('iron:pagechange', { detail: { id } })); };
  const byText = (text, root = w.document) => [...root.querySelectorAll('button')].find(button => button.textContent === text);
  const open = (id = 1) => w.IronCoachUI.openDuplicateReview(id, $('#trigger'));
  t.after(() => { w.dispatchEvent(new w.Event('pagehide')); w.close(); });
  w.eval(source);
  return { w, $, data, calls, loaded, flush, page, byText, open };
}

test('history is independent from pending candidates and reads lazily without mutations', async t => {
  const { $, w, calls, page, flush } = setup(t, { visible: false }); await flush();
  assert.ok($('#duplicate-history-card')); assert.equal($('#duplicate-card').hidden, true); assert.equal(calls.length, 0);
  w.eval(source); assert.equal(w.document.querySelectorAll('#duplicate-history-card').length, 1);
  page('activities'); await flush();
  assert.equal(calls[0].url, '/activities/duplicates/review-history?limit=20'); assert.ok(calls.every(call => call.method === 'GET'));
  assert.match($('.duplicate-history-message').textContent, /Nessuna decisione precedente/); assert.match($('.duplicate-history-limits').textContent, /dati non siano stati modificati/);
});

test('preview shows exact retained, copied, tied and missing values plus linked records', async t => {
  const { w, $, calls, open, flush, byText } = setup(t); await flush(); $('#trigger').focus(); open(); await flush();
  const dialog = $('#duplicate-review-dialog'), top = $('.duplicate-review-top');
  assert.equal(dialog.open, true); assert.equal(dialog.getAttribute('aria-labelledby'), 'duplicate-review-title'); assert.equal(top.firstElementChild.textContent, '← Indietro');
  assert.equal(w.document.activeElement, top.firstElementChild); assert.equal(dialog.hasAttribute('aria-busy'), false);
  assert.match($('.duplicate-activities').textContent, /REGISTRAZIONE CONSERVATA.*Giro conservato.*REGISTRAZIONE DA RIUNIRE.*Giro importato/);
  assert.match($('.duplicate-activities').textContent, /19 ott 2026, 09:00.*Europe\/Rome/);
  assert.match($('.duplicate-activities').textContent, /Durata: 1 h · Distanza: 25 km/);
  assert.match($('.duplicate-activities').textContent, /Identificativo fonte: g12 · Disciplina: Bici/);
  assert.doesNotMatch($('.duplicate-activities').textContent, /T07:00:00|3600 secondi|25000 metri/);
  const rows = [...$('.duplicate-diff-table tbody').rows];
  assert.equal(rows[0].cells[1].textContent, '0'); assert.equal(rows[0].cells[3].textContent, '0'); assert.equal(rows[0].dataset.source, 'existing');
  assert.equal(rows[1].cells[1].textContent, '—'); assert.equal(rows[1].cells[3].textContent, '210'); assert.match(rows[1].cells[4].textContent, /Copiato/);
  assert.match(rows[2].cells[4].textContent, /Valori uguali/); assert.match(rows[3].cells[4].textContent, /Non disponibile in entrambe/);
  const links = $('.duplicate-links-table tbody tr').cells; assert.deepEqual([...links].slice(1).map(cell => cell.textContent), ['1', '2', '3']);
  for (const wrapper of w.document.querySelectorAll('.duplicate-review-table')) { assert.equal(wrapper.tabIndex, 0); assert.equal(wrapper.getAttribute('role'), 'region'); assert.match(wrapper.getAttribute('aria-label'), /Scorri orizzontalmente/); }
  assert.equal(calls.some(call => call.method !== 'GET'), false);
  byText('← Indietro').click(); assert.equal(dialog.open, false); assert.equal(w.document.activeElement, $('#trigger'));
});

test('blocked previews explain the reason and cannot send a merge', async t => {
  const { $, calls, open, flush } = setup(t, { previews: { 1: preview({ merge_allowed: false, blocking_reasons: ['Entrambe le attività hanno un feedback.'] }) } }); await flush(); open(); await flush();
  assert.match($('.duplicate-blocking').textContent, /Entrambe le attività hanno un feedback/);
  const merge = $('.duplicate-review-actions .primary'); assert.equal(merge.disabled, true); merge.click(); await flush();
  assert.equal(calls.some(call => call.method === 'POST'), false);
});

test('a merge uses the reviewed token only after explicit confirmation and refreshes journal and history', async t => {
  const { $, calls, data, open, flush, byText } = setup(t); await flush(); open(); await flush();
  assert.equal(calls.some(call => call.method === 'POST'), false);
  byText('Conferma unione · conserva #12').click(); await flush();
  const writes = calls.filter(call => call.method === 'POST'); assert.deepEqual(writes.map(({ url, payload }) => ({ url, payload })), [{ url: '/activities/duplicates/1/merge', payload: { state_token: 'token-current-1' } }]);
  assert.equal($('#duplicate-review-dialog').open, false); assert.equal(data.loads, 1); assert.match($('.duplicate-history-row').textContent, /Unione delle fonti/); assert.ok(byText('Annulla questa decisione'));
});

for (const code of [409, 428]) test(`a ${code} merge response requires a fresh preview and another explicit decision`, async t => {
  const { $, calls, data, open, flush, byText } = setup(t); await flush(); open(); await flush();
  data.failures['POST /activities/duplicates/1/merge'] = { status: code, message: 'Confronto scaduto.' };
  byText('Conferma unione · conserva #12').click(); await flush();
  assert.match($('.duplicate-review-status').textContent, /Ricaricalo.*conferma di nuovo.*Nessun nuovo tentativo/);
  assert.equal(byText('Conferma unione · conserva #12').disabled, true); assert.equal(byText('Ricarica confronto').hidden, false);
  assert.equal(calls.filter(call => call.method === 'POST').length, 1); assert.equal(calls.filter(call => call.url.endsWith('/preview')).length, 1);
  data.previews[1].state_token = 'token-refreshed'; delete data.failures['POST /activities/duplicates/1/merge'];
  byText('Ricarica confronto').click(); await flush(); assert.equal(calls.filter(call => call.method === 'POST').length, 1);
  byText('Conferma unione · conserva #12').click(); await flush();
  assert.equal(calls.filter(call => call.method === 'POST').length, 2); assert.equal(calls.filter(call => call.method === 'POST').at(-1).payload.state_token, 'token-refreshed');
});

test('mutation controls block double submission and Escape while the decision is in flight', async t => {
  const pending = deferred(); const { w, $, calls, open, flush, byText } = setup(t, { mutate: call => call.url.endsWith('/merge') ? pending.promise : undefined }); await flush(); open(); await flush();
  const merge = byText('Conferma unione · conserva #12'); merge.click(); merge.dispatchEvent(new w.MouseEvent('click', { bubbles: true })); await flush();
  assert.equal(calls.filter(call => call.method === 'POST').length, 1); assert.equal(byText('← Indietro').disabled, true); assert.equal(merge.disabled, true);
  const cancel = new w.Event('cancel', { cancelable: true }); $('#duplicate-review-dialog').dispatchEvent(cancel); assert.equal(cancel.defaultPrevented, true);
  pending.resolve({ status: 'merged', review_id: 21 }); await flush(); assert.equal($('#duplicate-review-dialog').open, false);
});

test('keeping activities distinct is an explicit journaled decision with the same preview token', async t => {
  const { calls, open, flush, byText, $ } = setup(t); await flush(); open(); await flush(); byText('Sono attività distinte', $('#duplicate-review-dialog')).click(); await flush();
  assert.deepEqual(calls.filter(call => call.method === 'POST').map(call => [call.url, call.payload]), [['/activities/duplicates/1/keep', { state_token: 'token-current-1' }]]);
  assert.match($('.duplicate-history-row').textContent, /Attività mantenute distinte/);
});

test('history undo opens a confirmation before the atomic POST and renders the resulting status', async t => {
  const { $, calls, flush, byText, data } = setup(t, { history: [review()] }); await flush();
  byText('Annulla questa decisione').click(); await flush(); assert.equal(calls.some(call => call.method === 'POST'), false);
  assert.match($('.duplicate-review-content').textContent, /stato attuale lo consente/);
  byText('Conferma annullamento').click(); await flush();
  assert.deepEqual(calls.filter(call => call.method === 'POST').map(call => [call.url, call.payload]), [['/activities/duplicates/reviews/21/undo', {}]]);
  assert.equal(data.loads, 1); assert.equal($('#duplicate-review-dialog').open, false); assert.match($('.duplicate-history-row').textContent, /Decisione già annullata/); assert.equal(byText('Annulla questa decisione'), undefined);
  assert.equal($('.duplicate-history-row').dataset.status, 'undone');
});

test('changed current state blocks undo without automatically retrying or claiming success', async t => {
  const { $, calls, flush, byText, data } = setup(t, { history: [review()] }); await flush();
  data.failures['POST /activities/duplicates/reviews/21/undo'] = { status: 409, message: 'Dati modificati.' };
  byText('Annulla questa decisione').click(); byText('Conferma annullamento').click(); await flush();
  assert.match($('.duplicate-review-status').textContent, /annullamento non eseguito.*Aggiorna la cronologia/); assert.equal(byText('Conferma annullamento').disabled, true);
  assert.equal(calls.filter(call => call.method === 'POST').length, 1); assert.equal(data.loads, 0); assert.equal(data.history[0].status, 'applied');
});

test('closing or changing pages cancels a preview and ignores its late result', async t => {
  const pending = deferred(); const { $, calls, open, page, flush, byText } = setup(t, { read: call => call.url.endsWith('/preview') ? pending.promise : undefined }); await flush(); open(); await flush();
  const call = calls.find(call => call.url.endsWith('/preview')); page('health'); assert.equal(call.signal.aborted, true); assert.equal($('#duplicate-review-dialog').open, false);
  pending.resolve(response(preview())); await flush(); assert.equal($('#duplicate-review-dialog').open, false); assert.equal($('.duplicate-diff-table'), null);
  assert.equal(byText('Conferma unione').disabled, true); assert.equal(calls.some(call => call.method === 'POST'), false);
});

test('reload invalidates an open preview until its current data are reviewed again', async t => {
  const { $, calls, open, loaded, flush, byText } = setup(t); await flush(); open(); await flush(); loaded(); await flush();
  assert.match($('.duplicate-review-status').textContent, /I dati sono stati aggiornati/); assert.equal(byText('Conferma unione · conserva #12').disabled, true);
  assert.equal(byText('Ricarica confronto').hidden, false); assert.equal(calls.filter(call => call.url.endsWith('/preview')).length, 1);
  byText('Ricarica confronto').click(); await flush(); assert.equal(byText('Conferma unione · conserva #12').disabled, false); assert.equal(calls.some(call => call.method === 'POST'), false);
});

test('stale preview and history responses cannot replace newer versions', async t => {
  const firstPreview = deferred(), secondPreview = deferred(), firstHistory = deferred(), secondHistory = deferred(); let historyCalls = 0;
  const { $, open, flush, w } = setup(t, { read: call => call.url.includes('review-history') ? (++historyCalls === 1 ? firstHistory.promise : secondHistory.promise) : call.url.includes('/1/preview') ? firstPreview.promise : secondPreview.promise });
  open(1); open(2); w.IronCoachUI.loadDuplicateReviews(); await flush();
  const newer = preview({ candidate_id: 2, other: { ...preview().other, name: 'Registrazione nuova' } }); secondPreview.resolve(response(newer)); secondHistory.resolve(response({ reviews: [review({ summary: { activity_name: 'Cronologia nuova' } })], limits: [] })); await flush();
  firstPreview.resolve(response(preview())); firstHistory.resolve(response({ reviews: [], limits: [] })); await flush();
  assert.match($('.duplicate-activities').textContent, /Registrazione nuova/); assert.match($('.duplicate-history-row').textContent, /Cronologia nuova/);
});

test('read failures offer a working retry and untrusted preview/history content stays inert', async t => {
  let fail = true; const unsafe = '<img src=x onerror=alert(1)>', p = preview(); p.activity.name = unsafe; p.fields[0].field = unsafe;
  const { $, open, flush, byText } = setup(t, { previews: { 1: p }, history: [review({ summary: { activity_name: unsafe, other_name: unsafe, sources: [unsafe] }, undo_available: false, undo_reason: unsafe })], read: call => call.url.endsWith('/preview') && fail ? response({ detail: unsafe }, 503) : undefined }); await flush(); open(); await flush();
  assert.ok($('.duplicate-review-status').textContent.includes(unsafe)); assert.equal($('#duplicate-review-dialog img'), null);
  fail = false; byText('Ricarica confronto').click(); await flush();
  assert.ok($('.duplicate-review-content').textContent.includes(unsafe)); assert.ok($('.duplicate-history-list').textContent.includes(unsafe)); assert.equal($('img,[onerror]'), null);
});

test('the existing renderer opens comparison and never performs an immediate merge', async t => {
  const { w, $, calls, flush } = setup(t); await flush();
  w.$ = selector => w.document.querySelector(selector); w.el = (tag, text, css) => { const node = w.document.createElement(tag); if (text != null) node.textContent = text; if (css) node.className = css; return node; };
  w.button = (label, work) => { const node = w.el('button', label); node.type = 'button'; node.addEventListener('click', work); return node; };
  w.sourceLabel = value => value; w.activityDate = value => value; w.number = value => value; w.scaled = (value, divisor) => value / divisor; w.notice = () => {}; w.action = w.IronCoachUI.action; w.api = w.IronCoachUI.api; w.load = w.IronCoachUI.load; w.json = (method, payload) => ({ method, body: JSON.stringify(payload) });
  const renderer = appSource.slice(appSource.indexOf('function renderDuplicates('), appSource.indexOf('const requirementLabels'));
  w.eval(renderer); w.renderDuplicates([{ id: 1, activity: preview().activity, other: preview().other }]);
  const compare = $('#duplicate-list button'); assert.equal(compare.textContent, 'Apri confronto'); assert.equal(compare.getAttribute('aria-haspopup'), 'dialog'); compare.click(); await flush();
  assert.equal($('#duplicate-review-dialog').open, true); assert.equal(calls.some(call => call.method === 'POST'), false);
});

test('completed decisions focus a stable history heading after the pending trigger is removed by reload', async t => {
  const { w, $, data, loaded, open, byText, flush } = setup(t); await flush();
  w.IronCoachUI.load = async () => { data.loads++; $('#trigger').remove(); loaded(); };
  open(); await flush(); byText('Conferma unione · conserva #12').click(); await flush();
  assert.equal($('#trigger'), null); assert.equal(w.document.activeElement, $('#duplicate-history-card h2'));
});

test('naive UTC dates use Rome time, zeros stay explicit and corrupt numeric markers are explained', async t => {
  const p = preview(); p.other.start_time = '2026-10-18T07:00:00.000000'; p.other.duration_seconds = 0; p.other.distance_meters = 0;
  p.fields.push({ field: 'avg_hr', before_keep: { invalid_number: 'inf' }, before_remove: 140, result: { invalid_number: 'inf' }, source: 'existing' });
  const { $, open, flush } = setup(t, { previews: { 1: p } }); await flush(); open(); await flush();
  const card = $('.duplicate-activity').textContent; assert.match(card, /18 ott 2026, 09:00/); assert.match(card, /Durata: 0 min · Distanza: 0 km/);
  const row = [...$('.duplicate-diff-table tbody').rows].at(-1); assert.equal(row.cells[0].textContent, 'Frequenza media · bpm'); assert.equal(row.cells[1].textContent, 'Dato non valido');
  assert.doesNotMatch(row.textContent, /invalid_number|"inf"/);
});
