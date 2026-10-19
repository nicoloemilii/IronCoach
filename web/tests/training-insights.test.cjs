'use strict';
// Synthetic DOM and exact API response shapes only: no browser, account or server.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const source = fs.readFileSync(path.join(__dirname, '../training-insights.js'), 'utf8');
const copy = value => JSON.parse(JSON.stringify(value));
const offset = (day, amount) => new Date(new Date(`${day}T12:00:00Z`).valueOf() + amount * 86400000).toISOString().slice(0, 10);
const coverage = (known, total) => ({ known, total, percent: total ? known / total * 100 : null, complete: known === total });
const cover = (total, known = total) => Object.fromEntries(['minutes', 'load', 'distance', 'elevation'].map(key => [key, coverage(known, total)]));

function fixture(end = '2026-10-18') {
  const start = offset(end, -27), previousStart = offset(start, -28), previousEnd = offset(start, -1);
  const current = { activities: 2, minutes: 90, load: 70, distance_km: 25, elevation_m: 100, coverage: cover(2) };
  const previous = { activities: 1, minutes: 60, load: 25, distance_km: 20, elevation_m: 50, coverage: cover(1) };
  const sports = [['cycling', 'Bici', 60, 60, 66.7, 100], ['running', 'Corsa', 30, 0, 33.3, 0]];
  return {
    version: 'observed-training-patterns-1', as_of: end, timezone: 'Europe/Rome', status: 'available',
    period: { start, end, days: 28, includes_partial_day: false }, comparison_period: { start: previousStart, end: previousEnd, days: 28 },
    continuity: { active_days: 2, previous_active_days: 1, active_weeks: 2, total_weeks: 4, longest_gap_days: 20,
      days: Array.from({ length: 28 }, (_, i) => ({ date: offset(start, i), activities: i === 0 || i === 27 ? 1 : 0, minutes: i === 0 ? 60 : i === 27 ? 30 : 0, has_training: i === 0 || i === 27, known_duration_activities: i === 0 || i === 27 ? 1 : 0 })) },
    volume: { current, previous, minutes_change: 30, minutes_change_pct: 50, comparable: true, summary: '90 minuti contro 60 minuti.' },
    sports: sports.map(([key, label, minutes, prior, share, priorShare]) => ({ key, label, current_minutes: minutes, previous_minutes: prior, current_share_pct: share, previous_share_pct: priorShare, share_change_pp: share - priorShare, coverage: coverage(minutes ? 1 : 0, minutes ? 1 : 0) })),
    weeks: Array.from({ length: 8 }, (_, i) => {
      const start = offset(previousStart, i * 7), minutes = i === 3 || i === 4 ? 60 : i === 7 ? 30 : 0;
      return { start, end: offset(start, 6), days: 7, period: i < 4 ? 'previous' : 'current', activities: minutes ? 1 : 0, minutes, load: i === 3 ? 25 : i === 4 ? 50 : i === 7 ? 20 : 0, load_coverage_pct: minutes ? 100 : null, partial_day: false, coverage: cover(minutes ? 1 : 0) };
    }),
    coverage: { current: current.coverage, previous: previous.coverage },
    methodology: ['Solo attività registrate, senza modificare il piano.', 'La copertura riguarda i campi disponibili, non tutte le attività realmente svolte.'],
  };
}

function setup(t, { data = fixture(), respond, visible = false, qualityRespond } = {}) {
  const errors = [], calls = [], qualityCalls = [], navigations = [], console = new VirtualConsole();
  console.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM(html, { url: 'https://fixture.invalid/app#today', runScripts: 'outside-only', virtualConsole: console });
  const w = dom.window, $ = selector => w.document.querySelector(selector);
  const NativeDate = w.Date;
  w.Date = class extends NativeDate { constructor(...args) { super(...(args.length ? args : ['2026-10-19T10:00:00Z'])); } static now() { return new NativeDate('2026-10-19T10:00:00Z').valueOf(); } };
  w.HTMLElement.prototype.scrollIntoView = function () {};
  let qualityLoads = 0;
  w.IronCoachUI = {
    navigate(id) { navigations.push(id); $('#today').hidden = true; $('#athlete').hidden = false; page(id); },
    async api(route, options = {}) {
      const call = { route, method: options.method || 'GET' }; qualityCalls.push(call);
      return qualityRespond ? qualityRespond(call, qualityCalls.length) : { possible_pairs: 0, unreviewed_pairs: 0, pending_candidates: 0, summary: 'Nessuna coppia da rivedere.', truncated: false };
    },
    async action(work) { try { await work(); } catch (error) { $('#notice').textContent = error.message; $('#notice').hidden = false; } },
    async load() { qualityLoads++; },
  };
  w.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), method: options.method || 'GET', signal: options.signal });
    return respond ? respond(calls.at(-1), calls.length) : { ok: true, status: 200, json: async () => copy(data) };
  };
  $('#athlete').hidden = !visible;
  const page = id => w.document.dispatchEvent(new w.CustomEvent('iron:pagechange', { detail: { id, userInitiated: true } }));
  const loaded = () => w.document.dispatchEvent(new w.CustomEvent('iron:loaded'));
  const flush = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); assert.deepEqual(errors, []); };
  const submit = async value => { if (value !== undefined) $('#insights-date').value = value; $('#insights-period').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true })); await flush(); };
  t.after(() => { assert.deepEqual(errors, []); w.close(); });
  w.eval(source);
  return { w, $, calls, qualityCalls, qualityLoads: () => qualityLoads, navigations, data, page, loaded, flush, submit };
}

test('training insights lazily loads registered history and refreshes using GET requests only', async t => {
  const { $, calls, page, loaded, flush } = setup(t);
  await flush(); assert.equal(calls.length, 0);
  page('health'); await flush(); assert.equal(calls.length, 0);
  page('athlete'); await flush();
  assert.equal(calls.length, 1); assert.match(calls[0].url, /^\/dashboard\/training-insights\?as_of=2026-10-19$/);
  assert.equal($('#insights-content').hidden, false); assert.equal($('#training-insights').hasAttribute('aria-busy'), false);
  assert.match($('#insights-content').textContent, /1,5 h/);
  page('athlete'); await flush(); assert.equal(calls.length, 1);
  $('#athlete').hidden = false; loaded(); await flush(); assert.equal(calls.length, 2);
  assert.ok(calls.every(call => call.method === 'GET'));
});

test('an empty history shows useful import navigation without inventing a readiness score', async t => {
  const data = fixture();
  data.status = 'empty'; data.volume.current = { activities: 0, minutes: 0, coverage: cover(0) }; data.volume.previous = { activities: 0, minutes: 0, coverage: cover(0) };
  data.volume.minutes_change_pct = null; data.continuity.active_days = 0; data.continuity.active_weeks = 0;
  data.coverage = { current: cover(0), previous: cover(0) };
  const { $, page, flush, calls } = setup(t, { data }); page('athlete'); await flush();
  assert.equal($('#insights-content').hidden, false);
  assert.match($('.insight-empty').textContent, /Non risultano registrazioni/);
  assert.equal($('.insight-empty a').getAttribute('href'), '#connections');
  assert.equal($('.rhythm-grid'), null); assert.match($('.insight-kpis').textContent, /0 h/);
  assert.doesNotMatch($('#insights-content').textContent, /NaN|Infinity|100\/100|recupero ottimale/i);
  assert.equal(calls.length, 1);
});

test('failed reads hide retained charts and can be retried without writes', async t => {
  let failure = false;
  const { $, page, flush, submit, calls } = setup(t, { respond: async () => failure ? { ok: false, status: 503, json: async () => ({ detail: 'Unavailable' }) } : { ok: true, json: async () => fixture() } });
  page('athlete'); await flush(); assert.equal($('#insights-content').hidden, false);
  failure = true; await submit('2026-10-17');
  assert.equal($('#insights-content').hidden, true); assert.match($('#insights-status').textContent, /Premi Esplora per riprovare/);
  assert.equal($('#training-insights').hasAttribute('aria-busy'), false);
  failure = false; await submit('2026-10-17'); assert.equal($('#insights-content').hidden, false); assert.equal($('#insights-status').classList.contains('error'), false);
  assert.equal(calls.length, 3); assert.ok(calls.every(call => call.method === 'GET'));
});

test('invalid dates never issue requests and selected historical dates reach the endpoint', async t => {
  const { $, submit, calls } = setup(t);
  assert.equal($('#insights-date').max, '2026-10-19');
  await submit('2026-10-20'); await submit(''); await submit('1969-12-31');
  assert.equal(calls.length, 0); assert.match($('#insights-status').textContent, /Scegli una data valida/);
  await submit('2026-09-30'); assert.equal(calls.length, 1); assert.match(calls[0].url, /as_of=2026-09-30$/);
});

test('an older response cannot replace a more recent selected period even if abort is ignored', async t => {
  const pending = [];
  const { $, page, flush, submit } = setup(t, { respond: call => new Promise(resolve => pending.push({ call, resolve })) });
  page('athlete'); await flush(); assert.equal(pending.length, 1);
  await submit('2026-09-30'); assert.equal(pending.length, 2); assert.equal(pending[0].call.signal.aborted, true);
  const newest = fixture('2026-09-30'); newest.continuity.active_days = 9;
  pending[1].resolve({ ok: true, json: async () => newest }); await flush();
  assert.match($('.insight-kpis').textContent, /9 \/ 28/);
  pending[0].resolve({ ok: true, json: async () => fixture() }); await flush();
  assert.match($('.insight-kpis').textContent, /9 \/ 28/); assert.equal($('#training-insights').hasAttribute('aria-busy'), false);
});

test('an invalid date cancels an in-flight read and its late response cannot hide the validation error', async t => {
  let resolve;
  const { $, calls, page, flush, submit } = setup(t, { respond: () => new Promise(done => { resolve = done; }) });
  page('athlete'); await flush(); assert.equal(calls.length, 1);
  await submit(''); assert.equal(calls[0].signal.aborted, true);
  assert.equal($('#training-insights').hasAttribute('aria-busy'), false);
  resolve({ ok: true, json: async () => fixture() }); await flush();
  assert.match($('#insights-status').textContent, /Scegli una data valida/);
  assert.equal($('#insights-content').hidden, true); assert.equal(calls.length, 1);
});

test('refreshing the same date supersedes its in-flight read so imported activity changes cannot be lost', async t => {
  const pending = [];
  const { $, calls, page, loaded, flush } = setup(t, { respond: () => new Promise(resolve => pending.push(resolve)) });
  $('#athlete').hidden = false; page('athlete'); await flush(); loaded(); await flush();
  assert.equal(calls.length, 2); assert.equal(calls[0].signal.aborted, true);
  const latest = fixture(); latest.continuity.active_days = 3;
  pending[1]({ ok: true, json: async () => latest }); await flush();
  pending[0]({ ok: true, json: async () => fixture() }); await flush();
  assert.match($('.insight-kpis').textContent, /3 \/ 28/);
});

test('day controls are keyboard focusable and describe missing duration separately from no registration', async t => {
  const data = fixture(); data.continuity.days[1] = { date: data.continuity.days[1].date, activities: 1, minutes: null };
  const { $, w, page, flush } = setup(t, { data }); page('athlete'); await flush();
  const days = [...$('.rhythm-grid').querySelectorAll('button')]; assert.equal(days.length, 28);
  assert.ok(days.every(day => day.type === 'button' && day.tabIndex === 0 && day.getAttribute('aria-label')));
  days[1].focus(); assert.equal(w.document.activeElement, days[1]); days[1].click();
  assert.equal(days[1].dataset.level, 'unknown'); assert.match($('.rhythm-detail').textContent, /1 attività, durata non disponibile/);
  assert.equal(days[1].getAttribute('aria-pressed'), 'true'); days[2].click();
  assert.equal(days[1].getAttribute('aria-pressed'), 'false'); assert.equal(days[2].getAttribute('aria-pressed'), 'true');
  assert.match($('.rhythm-detail').textContent, /Nessuna attività registrata/);
  assert.match($('.rhythm-grid').parentElement.textContent, /non prova che tu abbia riposato/);
});

test('weekly controls distinguish a recorded zero from missing load and expose incomplete coverage', async t => {
  const data = fixture();
  data.weeks[4].load = 0; data.weeks[4].load_coverage_pct = 100;
  data.weeks[5].activities = 1; data.weeks[5].load = null; data.weeks[5].load_coverage_pct = 0;
  data.weeks[6].activities = 2; data.weeks[6].load = 20; data.weeks[6].load_coverage_pct = 50;
  const { $, page, flush } = setup(t, { data }); page('athlete'); await flush();
  assert.equal($('.insight-week-chart').children.length, 8);
  $('[data-metric="load"]').click();
  const bars = [...$('.insight-week-chart').querySelectorAll('button')];
  assert.equal(bars[4].querySelector('strong').textContent, '0'); assert.equal(bars[4].querySelector('.insight-week-bar').classList.contains('partial'), false);
  assert.equal(bars[5].querySelector('strong').textContent, 'N/D'); assert.equal(bars[5].querySelector('.insight-week-bar').classList.contains('partial'), true);
  assert.equal(bars[6].querySelector('strong').textContent, '20*'); bars[6].click();
  assert.match($('.insight-week-chart').nextElementSibling.textContent, /copertura 50%/);
  assert.equal($('[data-metric="load"]').getAttribute('aria-pressed'), 'true');
  assert.equal($('[data-metric="minutes"]').getAttribute('aria-pressed'), 'false');
  assert.equal($('table tbody').rows.length, 8); assert.match($('table').textContent, /Copertura carico/);
  assert.equal($('table tbody').rows[5].cells[3].textContent, '—');
  assert.doesNotMatch($('#insights-content').innerHTML, /NaN|Infinity/);
});

test('partially known duration is marked in the KPI and volume bars instead of being presented as a complete total', async t => {
  const data = fixture(); data.volume.current.coverage.minutes = coverage(1, 2);
  data.volume.minutes_change_pct = null; data.volume.comparable = false;
  data.weeks[7].activities = 2; data.weeks[7].coverage.minutes = coverage(1, 2);
  const { $, page, flush } = setup(t, { data }); page('athlete'); await flush();
  assert.match($('.insight-kpis').textContent, /1,5 h \*/);
  assert.match($('.insight-kpis').textContent, /Totale parziale: alcune durate mancano/);
  const bar = $('.insight-week-chart').lastElementChild;
  assert.equal(bar.querySelector('strong').textContent, '30*');
  assert.equal(bar.querySelector('.insight-week-bar').classList.contains('partial'), true);
  assert.match(bar.getAttribute('aria-label'), /copertura 50%/);
});

test('period colors identify windows and sport shares compare observed minutes without scoring them', async t => {
  const data = fixture(); data.volume.minutes_change_pct = null; data.sports[0].share_change_pp = null;
  const { $, page, flush } = setup(t, { data }); page('athlete'); await flush();
  const bars = [...$('.insight-week-chart').children];
  assert.ok(bars.slice(0, 4).every(bar => bar.classList.contains('previous')));
  assert.ok(bars.slice(4).every(bar => bar.classList.contains('current')));
  assert.match($('.insight-week-chart').parentElement.textContent, /senza dare un voto al carico/);
  assert.match($('.insight-kpis').textContent, /Confronto non disponibile/);
  assert.match($('.insight-sport').textContent, /Quota precedente non confrontabile/);
  assert.match($('.insight-sport-heading').textContent, /60 min · 66,7%/);
  assert.equal($('.insight-sport-track span').style.width, '66.7%');
  assert.doesNotMatch($('#insights-content').textContent, /punteggio|prestazione garantita|obiettivo ottimale/i);
});

test('partial period and source coverage remain visible instead of implying full history', async t => {
  const data = fixture(); data.period.includes_partial_day = true; data.weeks[3].partial_day = true; data.weeks[7].partial_day = true;
  data.coverage.current.load = coverage(1, 2); data.coverage.previous.load = coverage(0, 1);
  const { $, page, flush } = setup(t, { data }); page('athlete'); await flush();
  assert.match($('.insight-period-label').textContent, /oggi è ancora in corso/);
  const bars = [...$('.insight-week-chart').children]; assert.match(bars[3].getAttribute('aria-label'), /giorno in corso/); assert.match(bars[7].getAttribute('aria-label'), /giorno in corso/);
  assert.match($('#insights-content').textContent, /carico: 1\/2/);
  assert.match($('#insights-content').textContent, /copertura riguarda i campi disponibili/);
});

test('the insights shortcut navigates and focuses the section without writing data', async t => {
  const { $, w, navigations, calls, flush } = setup(t);
  assert.ok($('[data-open-insights]'), 'The Home view needs an explicit shortcut to these metrics.');
  $('[data-open-insights]').click(); await flush();
  assert.deepEqual(navigations, ['athlete']); assert.equal(w.document.activeElement, $('#insights-title'));
  assert.equal(calls.length, 1); assert.equal(calls[0].method, 'GET');
});

test('history quality reads a summary initially and after refresh while scanning remains explicit', async t => {
  const { $, page, loaded, flush, qualityCalls } = setup(t);
  await flush(); assert.ok($('#history-quality')); assert.equal(qualityCalls.length, 1);
  assert.deepEqual(qualityCalls[0], { route: '/activities/duplicates/summary', method: 'GET' });
  page('health'); await flush(); assert.equal(qualityCalls.length, 1);
  page('activities'); await flush();
  assert.equal(qualityCalls.length, 2); loaded(); await flush(); assert.equal(qualityCalls.length, 3);
  assert.ok(qualityCalls.every(call => call.route === '/activities/duplicates/summary' && call.method === 'GET'));
  assert.match($('#history-quality').textContent, /Nessuna coppia da rivedere/);
  assert.equal($('#history-quality').dataset.hasDuplicates, 'false');
  assert.equal($('#history-quality-warning').hidden, true);
});

test('duplicate scanning requires its explicit action and focuses review without merging any activity', async t => {
  let pending = 0;
  const { $, w, page, flush, qualityCalls, qualityLoads } = setup(t, { qualityRespond: async call => {
    if (call.route.endsWith('/scan')) { pending = 1; $('#duplicate-card').hidden = false; return { new_candidates: 1, summary: 'Una coppia da verificare.' }; }
    return { possible_pairs: 1, unreviewed_pairs: pending ? 0 : 1, pending_candidates: pending, summary: 'Una possibile registrazione doppia.', truncated: false };
  } });
  page('activities'); await flush(); assert.equal(qualityCalls.some(call => call.method === 'POST'), false);
  $('#history-quality button').click(); await flush();
  assert.equal(qualityCalls.filter(call => call.route === '/activities/duplicates/scan').length, 1);
  assert.equal(qualityCalls.find(call => call.route.endsWith('/scan')).method, 'POST');
  assert.equal(qualityLoads(), 1); assert.match($('#history-quality').textContent, /1 nuova coppia da rivedere/);
  assert.equal(w.document.activeElement, $('#duplicate-card'));
  assert.equal($('#history-quality').dataset.hasDuplicates, 'true');
  assert.equal($('#history-quality-warning').hidden, false, 'Pending candidates still need a personal review.');
  assert.ok(qualityCalls.every(call => !/merge|keep|delete/.test(call.route)));
});

test('quality read errors retain an explicit retry action and scan errors never report completed changes', async t => {
  const { $, page, flush, qualityCalls, qualityLoads } = setup(t, { qualityRespond: async () => { throw Error('QA offline'); } });
  page('activities'); await flush(); assert.match($('#history-quality').textContent, /non è disponibile/);
  assert.ok($('#history-quality button')); $('#history-quality button').click(); await flush();
  assert.match($('#notice').textContent, /QA offline/); assert.equal(qualityLoads(), 0);
  assert.equal($('#history-quality-warning').hidden, true);
  assert.doesNotMatch($('#history-quality').textContent, /nuove coppie da rivedere/);
  assert.ok(qualityCalls.every(call => ['GET', 'POST'].includes(call.method)));
});

test('unresolved pairs warn on Home before interpretation using a generic accessible message', async t => {
  const { $, flush, qualityCalls } = setup(t, { qualityRespond: async () => ({ possible_pairs: 7, unresolved_pairs: 7, pending_candidates: 0, summary: 'QA summary with private activity titles', truncated: false }) });
  await flush(); const warning = $('#history-quality-warning');
  assert.equal($('#today').hidden, false); assert.equal(warning.hidden, false);
  assert.equal($('#notice').nextElementSibling, warning);
  assert.equal(warning.querySelector('p').getAttribute('role'), 'status');
  assert.equal(warning.querySelector('a').getAttribute('href'), '#activities');
  assert.match(warning.textContent, /totali e i confronti potrebbero includere doppioni/);
  assert.doesNotMatch(warning.textContent, /7|private activity titles|QA summary/);
  assert.ok(qualityCalls.every(call => call.method === 'GET'));
});

test('reviewed pairs and unavailable summaries hide an obsolete global warning', async t => {
  let state = 'unresolved';
  const { $, loaded, flush, qualityCalls } = setup(t, { qualityRespond: async () => {
    if (state === 'error') throw Error('QA offline');
    return { possible_pairs: 1, unresolved_pairs: state === 'unresolved' ? 1 : 0, pending_candidates: 0, summary: 'Una coppia rilevata.', truncated: false };
  } });
  await flush(); assert.equal($('#history-quality-warning').hidden, false);
  state = 'kept'; loaded(); await flush(); assert.equal($('#history-quality-warning').hidden, true);
  assert.equal($('#history-quality').dataset.hasDuplicates, 'false');
  state = 'unresolved'; loaded(); await flush(); assert.equal($('#history-quality-warning').hidden, false);
  state = 'error'; loaded(); await flush(); assert.equal($('#history-quality-warning').hidden, true);
  assert.ok(qualityCalls.every(call => call.method === 'GET'));
});

test('suspected duplicates in either period explain why comparisons require review', async t => {
  for (const key of ['current_possible_duplicates', 'previous_possible_duplicates']) {
    const data = fixture(); data.quality = { current_possible_duplicates: 0, previous_possible_duplicates: 0, summary: 'Possibili duplicati: verifica lo storico prima del confronto.' }; data.quality[key] = 1;
    data.volume.minutes_change_pct = null;
    const { $, page, flush } = setup(t, { data }); page('athlete'); await flush();
    assert.match($('.insight-quality-note').textContent, /verifica lo storico/);
    assert.equal($('.insight-quality-note a').getAttribute('href'), '#activities');
    assert.match($('.insight-kpis').textContent, /Confronto non disponibile/);
    assert.equal($('.rhythm-grid').children.length, 28, 'Suspected duplicates stay visible until reviewed.');
  }
});
