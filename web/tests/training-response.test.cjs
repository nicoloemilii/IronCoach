'use strict';
// Synthetic DOM and mock GET responses only. No server, browser or provider.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const source = fs.readFileSync(path.join(__dirname, '../training-response.js'), 'utf8');
const copy = value => JSON.parse(JSON.stringify(value));
const html = '<!doctype html><html><body><section class="page" id="today"></section><section class="page" id="athlete" hidden><section id="training-insights"><div class="insights-heading"><div><p>Il ritmo registrato.</p></div><form id="insights-period"><input id="insights-date" type="date" min="1970-01-01" value="2026-10-19"><button>Esplora</button></form></div></section></section></body></html>';
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function row(index = 0, source = 'intervals', changes = {}) {
  return { training_date: `2026-10-${String(index + 1).padStart(2, '0')}`, response_date: `2026-10-${String(index + 2).padStart(2, '0')}`, load: (index + 1) * 10, activities: 1, before: 50, after: 50 + index - 5, change: index - 5, eligible: true, exclusion_reason: null, exclusion_reasons: [], before_source: source, after_source: source, intervening_activities: 0, intervening_dates: [], ...changes };
}
function series(source = 'intervals', source_label = 'Intervals.icu', changes = {}) {
  return { source, source_label, rows: Array.from({ length: 12 }, (_, index) => row(index, source)), paired_days: 12, load_split_median: 65,
    groups: [{ key: 'lower', label: 'Carico fino alla mediana', count: 6, load_min: 10, load_max: 60, median_change: -2.5, q25_change: -4, q75_change: -1 }, { key: 'higher', label: 'Carico sopra la mediana', count: 6, load_min: 70, load_max: 120, median_change: 3.5, q25_change: 2, q75_change: 5 }],
    comparison: { available: true, reason: null, higher_minus_lower: 6, min_pairs_per_group: 5 }, ...changes };
}
function fixture(changes = {}) {
  return { version: 1, period: { start: '2026-07-27', end: '2026-10-18', days: 84, as_of: '2026-10-19', response_start: '2026-07-28', response_end: '2026-10-19', includes_partial_response_day: false }, lag_days: 1, timezone: 'Europe/Rome',
    coverage: { recorded_training_days: 12, complete_load_days: 12, eligible_training_days: 12, missing_load_days: 0, load_overflow_days: 0, duplicate_days: 0, days_without_activities: 72, intervening_activity_days: 0 },
    metrics: [{ kind: 'hrv_ms', label: 'HRV', unit: 'ms', series: [series(), series('garmin', 'Garmin')] }, { kind: 'resting_hr_bpm', label: 'Frequenza a riposo', unit: 'bpm', series: [series()] }, { kind: 'sleep_hours', label: 'Sonno', unit: 'ore', series: [series(), series('garmin', 'Garmin')] }],
    quality: { truncated: false, review_truncated: false, summary: 'La copertura riguarda soltanto le registrazioni disponibili.', exclusions: {} }, methodology: ['Le coppie usano la stessa fonte; non uniscono dispositivi diversi.'], ...changes };
}
function setup(t, { data = fixture(), respond, visible = false, markup = html } = {}) {
  const errors = [], calls = [], scrolls = [], virtualConsole = new VirtualConsole(); virtualConsole.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM(markup, { url: 'https://fixture.invalid/app', runScripts: 'outside-only', virtualConsole });
  const w = dom.window, $ = selector => w.document.querySelector(selector), NativeDate = w.Date;
  w.HTMLElement.prototype.scrollIntoView = function (options) { scrolls.push({ id: this.id, block: options.block }); };
  w.Date = class extends NativeDate { constructor(...args) { super(...(args.length ? args : ['2026-10-19T10:00:00Z'])); } static now() { return new NativeDate('2026-10-19T10:00:00Z').valueOf(); } };
  if ($('#athlete')) $('#athlete').hidden = !visible;
  w.fetch = async (url, options = {}) => { const call = { url: String(url), method: options.method || 'GET', signal: options.signal, body: options.body }; calls.push(call); return respond ? respond(call, calls.length) : { ok: true, json: async () => copy(data) }; };
  const flush = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); assert.deepEqual(errors, []); };
  const page = id => { if ($('#athlete')) $('#athlete').hidden = id !== 'athlete'; w.document.dispatchEvent(new w.CustomEvent('iron:pagechange', { detail: { id } })); };
  const loaded = () => w.document.dispatchEvent(new w.CustomEvent('iron:loaded'));
  const change = (selector, value) => { $(selector).value = value; $(selector).dispatchEvent(new w.Event('change', { bubbles: true })); };
  const submit = value => { if (value !== undefined) $('#insights-date').value = value; $('#insights-period').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true })); };
  t.after(() => { assert.deepEqual(errors, []); w.dispatchEvent(new w.Event('pagehide')); w.close(); });
  w.eval(source);
  return { w, $, calls, scrolls, data, page, loaded, flush, change, submit };
}

test('the response view mounts once after insights and reads only while the athlete page is visible', async t => {
  const { w, $, calls, page, loaded, flush, submit } = setup(t);
  assert.equal($('#training-insights').nextElementSibling.id, 'training-response');
  assert.match($('#response-title').textContent, /Il lavoro e i giorni dopo/);
  w.eval(source); assert.equal(w.document.querySelectorAll('#training-response').length, 1);
  loaded(); submit(); page('health'); await flush(); assert.equal(calls.length, 0);
  page('athlete'); await flush();
  assert.equal(calls.length, 1); assert.equal(calls[0].url, '/dashboard/training-response?as_of=2026-10-19&days=84&lag=1');
  assert.equal($('#response-content').hidden, false); assert.equal($('#training-response').hasAttribute('aria-busy'), false);
  loaded(); await flush(); assert.equal(calls.length, 2);
  submit('2026-09-30'); await flush(); assert.match(calls[2].url, /as_of=2026-09-30&days=84&lag=1/);
  assert.ok(calls.every(call => call.method === 'GET' && call.body === undefined));
});

test('missing anchors leave the document and network untouched', async t => {
  const { $, calls, flush } = setup(t, { markup: '<html><body><section id="athlete"></section></body></html>' });
  await flush(); assert.equal($('#training-response'), null); assert.equal(calls.length, 0);
});

test('sparse observations retain coverage and exclusions without suggesting a health score or comparison', async t => {
  const sparse = series('unavailable', 'Fonte non disponibile', { rows: [row(0, null, { before: null, after: null, change: null, load: null, eligible: false, exclusion_reason: 'missing_load', exclusion_reasons: ['missing_load', 'missing_before', 'missing_after'] })], paired_days: 0, load_split_median: null,
    groups: ['lower', 'higher'].map(key => ({ key, count: 0, load_min: null, load_max: null, median_change: null, q25_change: null, q75_change: null })), comparison: { available: false, reason: 'Servono almeno cinque coppie in ciascun gruppo.', higher_minus_lower: null, min_pairs_per_group: 5 } });
  const data = fixture({ metrics: [{ kind: 'hrv_ms', label: 'HRV', unit: 'ms', series: [sparse] }], coverage: { recorded_training_days: 1, complete_load_days: 0, eligible_training_days: 0, missing_load_days: 1, load_overflow_days: 0, duplicate_days: 0, days_without_activities: 83 } });
  const { $, flush } = setup(t, { visible: true, data }); await flush();
  assert.equal($('.response-plot'), null); assert.match($('.response-empty').textContent, /Non ci sono coppie confrontabili/);
  assert.match($('.response-summary').textContent, /0 coppie confrontabili · 1 giorni esclusi/);
  assert.match($('.response-comparison').textContent, /Servono almeno cinque/);
  assert.doesNotMatch($('.response-comparison').textContent, /Differenza tra le mediane/);
  const cells = $('.response-table tbody tr').cells; assert.equal(cells[2].textContent, '—'); assert.equal(cells[4].textContent, '—');
  assert.match(cells[9].textContent, /Carico mancante.*Lettura del giorno attività assente.*Lettura del giorno successivo assente/);
  assert.match($('.response-coverage').textContent, /Carico completo0/);
  assert.doesNotMatch($('#response-content').textContent, /recupero ottimale|punteggio|100\/100|\b(?:NaN|Infinity)\b/i);
});

test('recorded zero remains distinct from missing values and only same-source finite pairs enter the scatter', async t => {
  const samples = [row(0, 'intervals', { load: 0, before: 50, after: 50, change: 0 }), row(1, 'intervals', { before: 0, after: null, change: null, eligible: false, exclusion_reason: 'missing_after', exclusion_reasons: ['missing_after'] }), row(2, 'intervals', { after_source: 'garmin' })];
  const s = series('intervals', 'Intervals.icu', { rows: samples, load_split_median: 0, groups: [{ key: 'lower', count: 1, load_min: 0, load_max: 0, median_change: 0, q25_change: 0, q75_change: 0 }, { key: 'higher', count: 0, load_min: null, load_max: null, median_change: null, q25_change: null, q75_change: null }], comparison: { available: false, reason: 'Campione insufficiente', higher_minus_lower: 999 } });
  const { $, flush } = setup(t, { visible: true, data: fixture({ metrics: [{ kind: 'hrv_ms', label: 'HRV', unit: 'ms', series: [s] }] }) }); await flush();
  assert.equal($('.response-plot').querySelectorAll('.response-point').length, 1);
  const rows = [...$('.response-table tbody').rows]; assert.equal(rows[0].cells[2].textContent, '0'); assert.equal(rows[0].cells[6].textContent, '0');
  assert.equal(rows[1].cells[4].textContent, '0'); assert.equal(rows[1].cells[5].textContent, '—');
  assert.match(rows[2].cells[9].textContent, /Fonti diverse/); assert.equal(rows[2].dataset.eligible, 'false');
  assert.equal($('.response-group[data-group="lower"] strong').textContent, '0 ms');
  assert.equal($('.response-group[data-group="higher"] strong').textContent, 'Non disponibile');
  assert.doesNotMatch($('.response-comparison').textContent, /999/);
  assert.match($('.response-readout').textContent, /carico 0 punti.*variazione 0 ms/);
});

test('metric and source controls keep independent series and preserve valid choices after refresh', async t => {
  let data = fixture();
  const { $, calls, flush, change, loaded } = setup(t, { visible: true, respond: async () => ({ ok: true, json: async () => copy(data) }) }); await flush();
  change('#response-metric', 'sleep_hours'); change('#response-source', 'garmin');
  assert.match($('.response-panel h3').textContent, /Sonno · Garmin/);
  assert.match($('.response-readout').textContent, /ore.*Fonte: Garmin/);
  assert.equal(calls.length, 1, 'Local series selection must not fetch or write.');
  loaded(); await flush(); assert.equal($('#response-metric').value, 'sleep_hours'); assert.equal($('#response-source').value, 'garmin');
  change('#response-metric', 'hrv_ms'); assert.equal($('#response-source').value, 'intervals');
  change('#response-metric', 'sleep_hours'); assert.equal($('#response-source').value, 'garmin');
  data = fixture(); data.metrics[2].series = [series()]; loaded(); await flush();
  assert.equal($('#response-metric').value, 'sleep_hours'); assert.equal($('#response-source').value, 'intervals');
});

test('scatter has one chart tab stop, keyboard sample navigation, touch targets and a dashed zero line', async t => {
  const { w, $, flush } = setup(t, { visible: true }); await flush();
  const chart = $('.response-plot'), samples = [...chart.querySelectorAll('.response-sample')]; chart.focus();
  assert.equal(w.document.activeElement, chart); assert.equal(chart.getAttribute('tabindex'), '0');
  assert.ok(samples.every(sample => sample.getAttribute('tabindex') === '-1'));
  assert.equal(chart.querySelector('path,polyline'), null); assert.equal(chart.querySelector('.response-zero').getAttribute('stroke-dasharray'), '5 5');
  assert.ok(chart.querySelector('.response-hit').getAttribute('r') >= 16);
  chart.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'End', bubbles: true, cancelable: true })); assert.match($('.response-readout').textContent, /2026-10-12 → 2026-10-13/);
  chart.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true, cancelable: true })); assert.match($('.response-readout').textContent, /2026-10-11 → 2026-10-12/);
  chart.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Home', bubbles: true, cancelable: true })); assert.match($('.response-readout').textContent, /2026-10-01 → 2026-10-02/);
  samples[2].querySelector('.response-hit').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  assert.match($('.response-readout').textContent, /2026-10-03 → 2026-10-04/); assert.equal(samples[2].getAttribute('aria-pressed'), 'true');
  assert.equal(samples[0].getAttribute('aria-pressed'), 'false'); assert.equal(w.document.activeElement, chart);
  assert.equal(chart.querySelectorAll('[data-group="higher"].response-point').length, 6);
});

test('group comparison uses backend availability and reports median differences and IQR without interpretation', async t => {
  const { $, flush } = setup(t, { visible: true }); await flush();
  assert.match($('.response-group[data-group="lower"]').textContent, /6 coppie · carico 10–60 punti/);
  assert.match($('.response-group[data-group="lower"]').textContent, /Intervallo centrale 50%: -4 → -1 ms/);
  assert.match($('.response-comparison').textContent, /Differenza tra le mediane.*\+6 ms/);
  assert.match($('.response-comparison').textContent, /non un effetto dimostrato/);
});

test('changing lag supersedes an ignored abort and only the latest result is rendered', async t => {
  const pending = [];
  const { $, calls, change, flush } = setup(t, { visible: true, respond: call => { const job = deferred(); pending.push({ ...job, call }); return job.promise; } }); await flush();
  change('#response-lag', '2'); await flush(); assert.equal(calls.length, 2); assert.equal(calls[0].signal.aborted, true); assert.match(calls[1].url, /&lag=2$/);
  const latest = fixture({ lag_days: 2 }); latest.metrics[0].label = 'HRV più recente'; latest.metrics[0].series[0].rows[0].intervening_activities = 2;
  pending[1].resolve({ ok: true, json: async () => latest }); await flush();
  assert.match($('.response-panel h3').textContent, /HRV più recente/); assert.match($('.response-readout').textContent, /Giorno intermedio: 2 attività registrate/);
  pending[0].resolve({ ok: true, json: async () => fixture() }); await flush();
  assert.match($('.response-panel h3').textContent, /HRV più recente/); assert.equal($('#response-lag').value, '2');
  assert.match($('.response-method').textContent, /giorno intermedio/);
});

test('refresh, cutoff validation and navigation invalidate late responses and stale errors', async t => {
  const pending = [];
  const { $, calls, loaded, submit, page, flush } = setup(t, { visible: true, respond: call => { const job = deferred(); pending.push({ ...job, call }); return job.promise; } }); await flush();
  loaded(); await flush(); assert.equal(calls.length, 2); assert.equal(calls[0].signal.aborted, true);
  pending[1].resolve({ ok: true, json: async () => fixture() }); await flush();
  pending[0].reject(new Error('Errore superato')); await flush(); assert.equal($('#response-status').classList.contains('error'), false);
  submit('2026-09-30'); await flush(); assert.match(calls[2].url, /as_of=2026-09-30/);
  submit('2026-10-20'); await flush(); assert.equal(calls.length, 3); assert.equal(calls[2].signal.aborted, true);
  pending[2].resolve({ ok: true, json: async () => fixture() }); await flush();
  assert.match($('#response-status').textContent, /Scegli una data valida/); assert.equal($('#response-content').hidden, true);
  submit('2026-10-19'); await flush(); page('health'); assert.equal(calls[3].signal.aborted, true);
  pending[3].resolve({ ok: true, json: async () => fixture() }); await flush(); assert.equal($('#response-content').hidden, true);
  loaded(); await flush(); assert.equal(calls.length, 4);
  page('athlete'); await flush(); assert.equal(calls.length, 5);
  pending[4].resolve({ ok: true, json: async () => fixture() }); await flush(); assert.equal($('#response-content').hidden, false);
  assert.ok(calls.every(call => call.method === 'GET'));
});

test('unavailable reads hide previous data and a working retry remains read only', async t => {
  let fail = false;
  const { $, calls, flush, loaded } = setup(t, { visible: true, respond: async () => fail ? { ok: false, json: async () => ({ detail: 'Server indisponibile' }) } : { ok: true, json: async () => fixture() } }); await flush();
  fail = true; loaded(); await flush(); assert.equal($('#response-content').hidden, true); assert.match($('#response-status').textContent, /non disponibili/);
  const retry = [...$('#training-response').querySelectorAll('button')].find(button => button.textContent === 'Riprova'); assert.equal(retry.hidden, false);
  fail = false; retry.click(); await flush(); assert.equal($('#response-content').hidden, false); assert.equal(retry.hidden, true);
  assert.equal($('#training-response').hasAttribute('aria-busy'), false); assert.equal(calls.length, 3); assert.ok(calls.every(call => call.method === 'GET'));
});

test('stored labels, source names, quality, methodology and exclusion text are rendered as inert text', async t => {
  const unsafe = '<img src=x onerror=alert(1)>', data = fixture();
  data.metrics[0].label = unsafe; data.metrics[0].series[0].source_label = unsafe; data.quality.summary = unsafe; data.methodology = [unsafe];
  data.metrics[0].series[0].rows.push(row(12, 'intervals', { eligible: false, exclusion_reason: unsafe, exclusion_reasons: [unsafe] }));
  const { $, flush } = setup(t, { visible: true, data }); await flush();
  assert.equal($('#training-response img'), null); assert.equal($('#training-response script'), null); assert.equal($('#training-response [onerror]'), null);
  assert.ok($('.response-panel h3').textContent.includes(unsafe)); assert.ok($('.response-method').textContent.includes(unsafe)); assert.ok($('.response-table').textContent.includes(unsafe));
  assert.match($('.response-method').textContent, /actuellement|attualmente conservate/); assert.match($('.response-method').textContent, /Non ricostruisce quando i dati erano disponibili/);
  assert.match($('.response-method').textContent, /non verificano.*prima o dopo/);
});

test('the insights jump focuses the response title and scrolls without submitting or changing the route', async t => {
  const { w, $, calls, scrolls, flush } = setup(t, { visible: true }); await flush();
  const jump = $('.insights-heading > div .response-jump'), initialRoute = w.location.href;
  assert.ok(jump); assert.equal(jump.type, 'button'); assert.equal(jump.closest('form'), null);
  jump.click(); await flush();
  assert.equal(w.document.activeElement, $('#response-title'));
  assert.deepEqual(scrolls, [{ id: 'training-response', block: 'start' }]);
  assert.equal(w.location.href, initialRoute); assert.equal(calls.length, 1);
});

test('previous and next sample buttons provide a read-only touch alternative and disable at the boundaries', async t => {
  const { w, $, calls, flush } = setup(t, { visible: true }); await flush();
  const [previous, next] = $('.response-chart-controls').querySelectorAll('button');
  assert.equal(previous.type, 'button'); assert.equal(next.type, 'button');
  assert.equal(previous.disabled, true); assert.equal(next.disabled, false);
  next.click(); assert.match($('.response-readout').textContent, /2026-10-02 → 2026-10-03/);
  assert.equal(previous.disabled, false); assert.equal($('.response-sample[data-selected="true"]').getAttribute('aria-label'), $('.response-readout').textContent);
  previous.click(); assert.match($('.response-readout').textContent, /2026-10-01 → 2026-10-02/); assert.equal(previous.disabled, true);
  $('.response-plot').dispatchEvent(new w.KeyboardEvent('keydown', { key: 'End', bubbles: true, cancelable: true }));
  assert.equal(next.disabled, true); assert.equal(previous.disabled, false);
  previous.click(); assert.match($('.response-readout').textContent, /2026-10-11 → 2026-10-12/); assert.equal(next.disabled, false);
  assert.equal(calls.length, 1); assert.ok(calls.every(call => call.method === 'GET'));
});

test('daily readings are labelled by calendar day instead of suggesting pre/post-exercise timing', async t => {
  const { $, change, flush } = setup(t, { visible: true, respond: async call => ({ ok: true, json: async () => fixture({ lag_days: Number(new URL(call.url, 'https://fixture.invalid').searchParams.get('lag')) }) }) }); await flush();
  const headers = () => [...$('.response-table thead').querySelectorAll('th')].map(th => th.textContent);
  assert.ok(headers().includes('Lettura del giorno attività · ms'));
  assert.ok(headers().includes('Lettura giorno +1 · ms'));
  assert.ok(headers().includes('Fonte giorno attività')); assert.ok(headers().includes('Fonte giorno +1'));
  assert.match($('.response-readout').textContent, /lettura del giorno attività: 50 ms; lettura giorno \+1: 45 ms/);
  assert.doesNotMatch(headers().join(' '), /\bPrima\b|\bDopo\b/i);
  change('#response-lag', '2'); await flush();
  assert.ok(headers().includes('Lettura giorno +2 · ms')); assert.ok(headers().includes('Fonte giorno +2'));
  assert.match($('.response-readout').textContent, /lettura giorno \+2:/);
});

test('partial-dataset and overflowing-change exclusions expose readable Italian labels', async t => {
  const rows = [row(0, 'intervals', { eligible: false, change: null, exclusion_reasons: ['dataset_truncated'] }), row(1, 'intervals', { eligible: false, change: null, exclusion_reasons: ['change_overflow'] })];
  const data = fixture({ metrics: [{ kind: 'hrv_ms', label: 'HRV', unit: 'ms', series: [series('intervals', 'Intervals.icu', { rows })] }] });
  const { $, flush } = setup(t, { visible: true, data }); await flush();
  const text = $('.response-table tbody').textContent;
  assert.match(text, /Storico parziale: coppia esclusa dal confronto/); assert.match(text, /Variazione fuori intervallo/);
  assert.doesNotMatch(text, /dataset_truncated|change_overflow/);
});

test('local estimates retain their raw table readings but are visibly excluded from observed pairs', async t => {
  const rows = [row(0, 'local_estimate', { eligible: false, before: 55, after: 60, change: null, exclusion_reasons: ['estimated_before', 'estimated_after'] })];
  const data = fixture({ metrics: [{ kind: 'hrv_ms', label: 'HRV', unit: 'ms', series: [series('local_estimate', 'Stima locale', { rows, paired_days: 0, comparison: { available: false, reason: 'Le stime locali non sono osservazioni confrontabili.' } })] }] });
  const { $, flush } = setup(t, { visible: true, data }); await flush();
  assert.equal($('.response-plot'), null);
  const cells = $('.response-table tbody tr').cells;
  assert.equal(cells[4].textContent, '55'); assert.equal(cells[5].textContent, '60'); assert.equal(cells[6].textContent, '—');
  assert.match(cells[9].textContent, /Stima locale del giorno attività: esclusa.*Stima locale del giorno successivo: esclusa/);
  assert.doesNotMatch($('.response-table').textContent, /estimated_before|estimated_after/);
});

test('the initial source uses the most paired days and preserves an explicit less-populated source on refresh', async t => {
  const data = fixture();
  data.metrics[0].series = [series('manual', 'Manuale', { paired_days: 2, rows: [row(0, 'manual'), row(1, 'manual')] }), series('intervals', 'Intervals.icu', { paired_days: 12 })];
  const { $, calls, loaded, change, flush } = setup(t, { visible: true, data }); await flush();
  assert.equal($('#response-source').value, 'intervals'); assert.match($('.response-panel h3').textContent, /Intervals.icu/);
  change('#response-source', 'manual'); assert.equal($('.response-plot').querySelectorAll('.response-point').length, 2);
  loaded(); await flush(); assert.equal($('#response-source').value, 'manual'); assert.match($('.response-panel h3').textContent, /Manuale/);
  assert.equal(calls.length, 2);
});

for (const [name, rows, available] of [
  ['large finite loads', [row(0, 'intervals', { load: 1e308 }), row(1, 'intervals', { load: 8e307 })], true],
  ['large finite daily changes', [row(0, 'intervals', { before: 0, after: 1e308, change: 1e308 }), row(1, 'intervals', { before: 0, after: 8e307, change: 8e307 })], true],
  ['opposite extreme changes', [row(0, 'intervals', { before: 0, after: 1e308, change: 1e308 }), row(1, 'intervals', { before: 1e308, after: 0, change: -1e308 })], false],
  ['overflowing load scale', [row(0, 'intervals', { load: Number.MAX_VALUE })], false]
]) test(`${name} never generate invalid SVG and keep the actual values in the table`, async t => {
  const data = fixture({ metrics: [{ kind: 'hrv_ms', label: 'HRV', unit: 'ms', series: [series('intervals', 'Intervals.icu', { rows })] }] });
  const { $, flush } = setup(t, { visible: true, data }); await flush();
  assert.equal(Boolean($('.response-plot')), available);
  const tableRows = [...$('.response-table tbody').rows]; assert.equal(tableRows.length, rows.length);
  rows.forEach((row, index) => { assert.equal(tableRows[index].cells[2].textContent, row.load.toLocaleString('it-IT', { maximumFractionDigits: 1 })); assert.equal(tableRows[index].cells[5].textContent, row.after.toLocaleString('it-IT', { maximumFractionDigits: 1 })); });
  if (available) {
    for (const node of $('.response-plot').querySelectorAll('*')) for (const attribute of node.attributes) assert.doesNotMatch(attribute.value, /\b(?:NaN|Infinity)\b/);
    assert.doesNotMatch($('.response-plot').textContent, /\b(?:NaN|Infinity)\b/);
  } else assert.match($('.response-empty').textContent, /Grafico non disponibile.*tabella/);
});
