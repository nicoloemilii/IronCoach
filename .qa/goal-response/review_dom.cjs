'use strict';
// Actual isolated API JSON -> production frontend module. Synthetic DOM only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const ROOT = path.resolve(__dirname, '../..');
const { JSDOM, VirtualConsole } = require(path.join(ROOT, 'web/tests/node_modules/jsdom'));
const input = JSON.parse(fs.readFileSync(path.join(__dirname, 'review_api.json'), 'utf8'));
const fixtures = input.fixtures;
const pristine = JSON.stringify(fixtures);
const html = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');
const source = fs.readFileSync(path.join(ROOT, 'web/training-response.js'), 'utf8');
const sourceHashes = Object.fromEntries(['web/training-response.js', 'web/index.html'].map(file => [file, crypto.createHash('sha256').update(fs.readFileSync(path.join(ROOT, file))).digest('hex')]));
const reports = [];
const clone = value => JSON.parse(JSON.stringify(value));
const response = body => ({ ok: true, status: 200, json: async () => clone(body) });
const selectedSeries = (body, kind, selectedSource) => body.metrics.find(item => item.kind === kind).series.find(item => item.source === selectedSource);

function setup(scenario, resolver) {
  const errors = [], calls = [];
  const console = new VirtualConsole();
  console.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM(html, { url: 'https://fixture.invalid/app#athlete', runScripts: 'outside-only', virtualConsole: console });
  const w = dom.window, $ = selector => w.document.querySelector(selector);
  const NativeDate = w.Date;
  w.Date = class extends NativeDate {
    constructor(...args) { super(...(args.length ? args : ['2026-10-19T12:00:00Z'])); }
    static now() { return new NativeDate('2026-10-19T12:00:00Z').valueOf(); }
  };
  w.HTMLElement.prototype.scrollIntoView = function () {};
  $('#athlete').hidden = false;
  $('#insights-date').value = '2026-10-18';
  w.IronCoachUI = { api() { throw new Error('Unexpected API mutation path'); }, action() { throw new Error('Unexpected action path'); } };
  w.fetch = async (value, options = {}) => {
    const url = new URL(String(value), 'https://fixture.invalid');
    const call = { path: url.pathname, asOf: url.searchParams.get('as_of'), lag: Number(url.searchParams.get('lag')), days: Number(url.searchParams.get('days')), method: options.method || 'GET', signal: options.signal };
    calls.push(call);
    assert.equal(call.path, '/dashboard/training-response');
    assert.equal(call.method, 'GET');
    assert.equal(call.days, 84);
    if (resolver) return resolver(call);
    const key = call.asOf === '2026-10-17' && call.lag === 2 ? 'historical_lag2' : `${scenario}_lag${call.lag}`;
    assert.ok(fixtures[key], `Missing actual API fixture ${key}`);
    return response(fixtures[key]);
  };
  w.eval(source);
  const flush = async () => {
    for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(errors, []);
  };
  const change = async (selector, value) => { $(selector).value = value; $(selector).dispatchEvent(new w.Event('change', { bubbles: true })); await flush(); };
  const page = async id => {
    $('#athlete').hidden = id !== 'athlete';
    w.document.dispatchEvent(new w.CustomEvent('iron:pagechange', { detail: { id, userInitiated: true } }));
    await flush();
  };
  const close = () => {
    assert.deepEqual(errors, []);
    assert.ok(calls.every(call => call.method === 'GET'));
    assert.equal(w.localStorage.length, 0);
    assert.equal(w.sessionStorage.length, 0);
    dom.window.close();
  };
  return { w, $, calls, errors, flush, change, page, close };
}

function checkPlot($) {
  const svg = $('.response-plot');
  if (!svg) return { rendered: false, samples: 0 };
  for (const node of svg.querySelectorAll('*')) {
    for (const attribute of [...node.attributes]) {
      assert.doesNotMatch(attribute.value, /\b(?:NaN|Infinity)\b/, `Invalid SVG ${attribute.name}=${attribute.value}`);
      if (['x', 'y', 'x1', 'x2', 'y1', 'y2', 'cx', 'cy', 'r'].includes(attribute.name)) {
        assert.ok(Number.isFinite(Number(attribute.value)), `Nonfinite SVG ${attribute.name}`);
      }
    }
  }
  return { rendered: true, samples: svg.querySelectorAll('.response-sample').length };
}

function checkSeries($, body, kind, selectedSource) {
  const series = selectedSeries(body, kind, selectedSource);
  const usable = series.rows.filter(row => row.eligible && row.before_source === selectedSource && row.after_source === selectedSource);
  assert.equal($('.response-table tbody').children.length, series.rows.length);
  assert.equal($('.response-table tbody').querySelectorAll('[data-eligible="true"]').length, usable.length);
  const plot = checkPlot($);
  if (plot.rendered) assert.equal(plot.samples, usable.length);
  else assert.ok(usable.length === 0 || $('.response-chart').textContent.length > 30, 'Chart omission needs a visible reason');
  for (const node of $('#response-content').querySelectorAll('td,p,strong')) {
    assert.doesNotMatch(node.textContent, /\b(?:NaN|Infinity|dataset_truncated|change_overflow|estimated_before|estimated_after)\b/);
  }
  return { source: selectedSource, metric: kind, usable: usable.length, rows: series.rows.length, plot };
}

async function normal() {
  const qa = setup('normal'), { w, $, calls, flush, change, page } = qa;
  try {
    await flush();
    assert.equal(calls.length, 1);
    assert.equal($('#response-content').hidden, false);
    assert.equal($('#response-lag').value, '1');
    assert.equal($('#response-metric').value, 'hrv_ms');
    assert.equal($('#response-source').value, 'intervals');
    const initial = checkSeries($, fixtures.normal_lag1, 'hrv_ms', 'intervals');
    const svg = $('.response-plot');
    assert.ok(svg);
    svg.focus();
    assert.equal(w.document.activeElement, svg);
    const first = $('.response-readout').textContent;
    svg.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    assert.notEqual($('.response-readout').textContent, first);
    svg.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'End', bubbles: true }));
    const end = $('.response-readout').textContent;
    const eligible = selectedSeries(fixtures.normal_lag1, 'hrv_ms', 'intervals').rows.filter(row => row.eligible);
    assert.ok(end.includes(eligible.at(-1).training_date));
    svg.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Home', bubbles: true }));
    assert.equal($('.response-readout').textContent, first);
    assert.equal(calls.length, 1, 'Keyboard sample selection must not access an endpoint');
    await change('#response-source', 'manual');
    checkSeries($, fixtures.normal_lag1, 'hrv_ms', 'manual');
    await change('#response-metric', 'resting_hr_bpm');
    assert.equal($('#response-source').value, 'intervals');
    checkSeries($, fixtures.normal_lag1, 'resting_hr_bpm', 'intervals');
    await change('#response-source', 'manual');
    await change('#response-metric', 'hrv_ms');
    assert.equal($('#response-source').value, 'manual', 'Retain a separate source choice for each metric');
    assert.equal(calls.length, 1);
    await change('#response-metric', 'sleep_hours');
    const sleep = checkSeries($, fixtures.normal_lag1, 'sleep_hours', 'intervals');
    assert.match($('.response-table thead').textContent, /ore/);
    await change('#response-source', 'manual');
    checkSeries($, fixtures.normal_lag1, 'sleep_hours', 'manual');
    await change('#response-metric', 'hrv_ms');
    await change('#response-source', 'intervals');
    await change('#response-lag', '2');
    assert.equal(calls.length, 2);
    const lag2 = checkSeries($, fixtures.normal_lag2, 'hrv_ms', 'intervals');
    assert.match($('.response-period').textContent, /2026-07-25.*2026-10-16/);
    assert.match($('.response-readout').textContent, /intermedi/i);
    $('#insights-date').value = '2026-10-17';
    $('#insights-period').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
    await flush();
    assert.equal(calls.length, 3);
    checkSeries($, fixtures.historical_lag2, 'hrv_ms', 'intervals');
    assert.match($('.response-period').textContent, /2026-10-15/);
    const historicText = $('.response-period').textContent;
    $('#insights-date').value = '';
    $('#insights-period').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
    await flush();
    assert.equal(calls.length, 3);
    assert.equal($('#response-content').hidden, true);
    assert.match($('#response-status').textContent, /data valida/i);
    $('#insights-date').value = '2026-10-18';
    await page('today');
    const before = calls.length;
    await page('athlete');
    assert.equal(calls.length, before + 1);
    reports.push({ scenario: 'actual-normal-api-json', initial, lag2, sleep, historicText,
                   keyboard: 'ArrowRight/End/Home correct, no extra requests',
                   source_metric_changes: 'retained independently, no requests',
                   invalid_date: 'hidden result, no request', all_methods: 'GET' });
  } finally { qa.close(); }
}

async function variants() {
  for (const scenario of ['empty', 'extreme', 'truncated', 'estimated']) {
    const qa = setup(scenario), { $, flush, change } = qa;
    try {
      await flush();
      assert.equal($('#response-content').hidden, false);
      const body = fixtures[`${scenario}_lag1`];
      const metric = body.metrics.find(item => item.kind === 'hrv_ms');
      const selected = metric.series[0].source;
      const result = checkSeries($, body, 'hrv_ms', selected);
      if (scenario === 'empty') {
        assert.equal($('.response-plot'), null);
        assert.match($('.response-summary').textContent, /0 coppie/);
      }
      if (scenario === 'truncated') {
        assert.equal($('.response-plot'), null);
        assert.doesNotMatch($('#response-content').textContent, /dataset_truncated/);
      }
      if (scenario === 'estimated') {
        assert.equal($('.response-plot'), null);
        assert.match($('.response-summary').textContent, /0 coppie/);
        assert.match($('.response-table tbody').textContent, /stim[ae]/i);
      }
      if (scenario === 'extreme') {
        await change('#response-lag', '2');
        checkSeries($, fixtures.extreme_lag2, 'hrv_ms', selected);
      }
      reports.push({ scenario: `actual-${scenario}-api-json`, ...result, no_console_errors: true });
    } finally { qa.close(); }
  }
}

async function staleResponses() {
  let resolveFirst;
  const firstPending = new Promise(resolve => { resolveFirst = resolve; });
  const qa = setup('normal', call => call.lag === 1 ? firstPending : response(fixtures.normal_lag2));
  try {
    await qa.flush();
    await qa.change('#response-lag', '2');
    assert.equal(qa.calls[0].signal.aborted, true);
    checkSeries(qa.$, fixtures.normal_lag2, 'hrv_ms', 'intervals');
    const rendered = qa.$('.response-period').textContent;
    resolveFirst(response(fixtures.normal_lag1));
    await qa.flush();
    assert.equal(qa.$('.response-period').textContent, rendered);
    assert.equal(qa.$('#response-lag').value, '2');
    reports.push({ scenario: 'stale-real-api-json', result: 'lag1 aborted/ignored; lag2 retained' });
  } finally { qa.close(); }
}

(async () => {
  await normal();
  await variants();
  await staleResponses();
  assert.equal(JSON.stringify(fixtures), pristine, 'Frontend must not mutate actual API fixture objects');
  for (const [file, hash] of Object.entries(sourceHashes)) assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(ROOT, file))).digest('hex'), hash, 'Source changed during DOM check');
  const output = { result: 'passed', input: 'review_api.json (actual isolated FastAPI responses)',
                   scope: 'Production frontend in JSDOM, not a real browser', source_sha256: sourceHashes, reports,
                   source_fixtures_unchanged: true, storage_writes: 0 };
  fs.writeFileSync(path.join(__dirname, 'review_dom.json'), JSON.stringify(output, null, 2));
  console.log(JSON.stringify(output));
})().catch(error => { console.error(error); process.exitCode = 1; });
