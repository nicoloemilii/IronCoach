'use strict';
// Local source in a synthetic DOM only. No browser, backend or provider access.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const source = fs.readFileSync(path.join(__dirname, '../wellness.js'), 'utf8');
const colorsSource = fs.readFileSync(path.join(__dirname, '../metric-status.js'), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));

function reportFixture(changes = {}) {
  return { id: 11, date: '2026-10-17', kind: 'contracture', label: 'Contrattura', body_location: 'polpaccio sinistro',
    severity: 2, status: 'active', suspected_cause: '', notes: '', analysis: {
      summary: 'Il carico registrato è aumentato prima della segnalazione. La causa non è dimostrata.',
      associations: [{ observation: 'Carico registrato: 60 punti/giorno, rispetto a 30 nel periodo precedente.' }]
    }, ...changes };
}

function profileFixture(changes = {}) {
  return { name: 'Atleta di prova', headline: 'Profilo attuale concentrato sulla bici', total_activities: 150,
    weekly_minutes: 480, weekly_load: 300, sports: [{ sport: 'bike', label: 'Bici', activities: 12, weekly_minutes: 420 }],
    goal: { name: 'Triathlon di prova' }, strengths: ['Quattro settimane con attività registrate.'],
    priorities: ['Ricostruisci continuità nel nuoto.'], restrictions: { reasons: [] }, ...changes };
}

async function setup(t, { page = 'health', reports = [], profile = profileFixture(), fail = {} } = {}) {
  const errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM(html, { url: 'https://fixture.invalid/app', runScripts: 'outside-only', virtualConsole });
  const w = dom.window;
  const $ = selector => w.document.querySelector(selector);
  const calls = [];
  const data = { reports: clone(reports), profile: clone(profile), loads: 0, failures: { ...fail }, busy: false, openedActivities: [], openedMetrics: [] };
  const NativeDate = w.Date;
  w.Date = class extends NativeDate {
    constructor(...args) { super(...(args.length ? args : ['2026-10-18T10:00:00Z'])); }
    static now() { return new NativeDate('2026-10-18T10:00:00Z').valueOf(); }
  };
  w.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  w.HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); this.dispatchEvent(new w.Event('close')); };
  w.fetch = () => { throw new Error('Network access is forbidden in this fixture.'); };
  const el = (tag, text, css) => {
    const node = w.document.createElement(tag);
    if (text != null) node.textContent = text;
    if (css) node.className = css;
    return node;
  };
  const button = (text, callback, css) => {
    const node = el('button', text, css);
    node.type = 'button';
    if (data.busy) { node.disabled = true; node.dataset.busyDisabled = 'true'; }
    node.addEventListener('click', callback);
    return node;
  };
  // Match the shared UI contract: action disables controls before the callback.
  // This catches FormData being captured too late and silently losing all fields.
  const action = async work => {
    if (data.busy) return;
    data.busy = true;
    $('#notice').textContent = '';
    const controls = [...w.document.querySelectorAll('button:not([data-page]), input, select, textarea')];
    const disabled = controls.map(node => node.disabled);
    controls.forEach(node => node.disabled = true);
    try { await work(); }
    catch (error) { $('#notice').textContent = error.message; $('#notice').hidden = false; }
    finally {
      data.busy = false;
      controls.forEach((node, index) => node.disabled = disabled[index]);
      w.document.querySelectorAll('[data-busy-disabled]').forEach(node => { node.disabled = false; delete node.dataset.busyDisabled; });
    }
  };
  const api = async (url, options = {}) => {
    const method = options.method || 'GET';
    const payload = options.body == null ? null : JSON.parse(options.body);
    calls.push({ url, method, payload });
    const key = `${method} ${url}`;
    if (data.failures[key]) throw new Error(data.failures[key]);
    if (url === '/health/reports' && method === 'GET') return { reports: clone(data.reports), restrictions: {} };
    if (url === '/health/reports' && method === 'POST') {
      if (!payload.date || !payload.kind || !Number.isInteger(payload.severity)) throw new Error('Segnalazione incompleta.');
      const report = reportFixture({ id: 21, label: payload.kind === 'cold' ? 'Raffreddore' : 'Contrattura', ...payload });
      data.reports.push(report);
      return clone(report);
    }
    if (/^\/health\/reports\/\d+$/.test(url)) {
      const id = Number(url.split('/')[3]);
      if (method === 'PATCH') { Object.assign(data.reports.find(report => report.id === id), payload); return clone(data.reports.find(report => report.id === id)); }
      if (method === 'DELETE') { data.reports = data.reports.filter(report => report.id !== id); return { deleted: id }; }
    }
    if (url === '/athlete/derived-profile' && method === 'GET') return clone(data.profile);
    throw new Error(`Unexpected fixture call: ${key}`);
  };
  const loaded = () => w.document.dispatchEvent(new w.CustomEvent('iron:loaded'));
  const navigate = id => {
    for (const item of w.document.querySelectorAll('.page')) item.hidden = item.id !== id;
    w.document.dispatchEvent(new w.CustomEvent('iron:pagechange', { detail: { id, userInitiated: true } }));
  };
  for (const item of w.document.querySelectorAll('.page')) item.hidden = item.id !== page;
  w.IronCoachUI = { el, button, api, action, openActivity: async id => data.openedActivities.push(id), openHealthMetric: item => data.openedMetrics.push(item), number: (value, digits = 1) => value == null ? '—' : new Intl.NumberFormat('it-IT', { maximumFractionDigits: digits }).format(value),
    load: async () => { data.loads++; loaded(); } };
  const flush = async () => {
    for (let index = 0; index < 12; index++) await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(errors, []);
    assert.equal(data.busy, false, 'The shared action must release its busy state.');
  };
  const byText = (text, root = w.document) => [...root.querySelectorAll('button')].find(node => node.textContent === text);
  const open = () => { byText('+ Segnala un problema').click(); return $('#health-report-form'); };
  const submit = () => $('#health-report-form').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  t.after(() => { assert.deepEqual(errors, []); w.close(); });
  w.eval(colorsSource);w.eval(source);
  await flush();
  return { w, $, calls, data, flush, navigate, loaded, byText, open, submit };
}

test('symptom report has an accessible dialog and a working back control', async t => {
  const { $, open, byText } = await setup(t);
  const form = open();
  assert.equal($('#health-report-dialog').open, true);
  assert.equal($('#health-report-dialog').getAttribute('aria-labelledby'), 'health-report-title');
  assert.equal(form.elements.date.value, '2026-10-18');
  assert.equal(form.elements.date.max, '2026-10-18');
  assert.equal(form.elements.severity.value, '2');
  byText('← Indietro', $('#health-report-dialog')).click();
  assert.equal($('#health-report-dialog').open, false);
});

test('report submission preserves complete form data while controls are disabled and renders hypotheses as inert text', async t => {
  const { $, calls, data, flush, open, submit } = await setup(t);
  const form = open();
  const hypothesis = '<img src=x onerror="throw new Error(\'unsafe\')">';
  form.elements.date.value = '2026-10-16';
  form.elements.kind.value = 'injury';
  form.elements.body_location.value = 'Ginocchio destro';
  form.elements.severity.value = '4';
  form.elements.suspected_cause.value = hypothesis;
  form.elements.notes.value = 'Fastidio dopo la discesa.';
  form.dataset.dirty = 'true';
  submit();
  await flush();
  const posted = calls.find(call => call.method === 'POST');
  assert.deepEqual(posted.payload, { date: '2026-10-16', kind: 'injury', body_location: 'Ginocchio destro', severity: 4,
    suspected_cause: hypothesis, notes: 'Fastidio dopo la discesa.' });
  assert.equal(data.loads, 1);
  assert.equal($('#health-report-dialog').open, false);
  assert.equal(form.dataset.dirty, undefined);
  assert.match($('.symptom-journal').textContent, /La tua ipotesi:/);
  assert.ok($('.symptom-journal').textContent.includes(hypothesis));
  assert.equal($('.symptom-journal img'), null);
  assert.equal($('.symptom-journal script'), null);
  assert.match($('.symptom-journal .coach-findings').textContent, /60 punti\/giorno/);
});

test('resolving reopening and deleting an episode update the journal through explicit API mutations', async t => {
  const { $, calls, data, flush, byText } = await setup(t, { reports: [reportFixture()] });
  byText('Segna risolto', $('.symptom-journal')).click();
  await flush();
  assert.match($('.symptom-journal').textContent, /Risolto/);
  assert.equal($('.symptom-card').dataset.tone, 'positive');
  byText('Riapri segnalazione', $('.symptom-journal')).click();
  await flush();
  assert.match($('.symptom-journal').textContent, /In corso/);
  byText('Elimina', $('.symptom-journal')).click();
  await flush();
  assert.equal($('.symptom-journal').children.length, 0);
  assert.equal(data.loads, 3);
  assert.deepEqual(calls.filter(call => call.method !== 'GET'), [
    { url: '/health/reports/11', method: 'PATCH', payload: { status: 'resolved' } },
    { url: '/health/reports/11', method: 'PATCH', payload: { status: 'active' } },
    { url: '/health/reports/11', method: 'DELETE', payload: null }
  ]);
});

test('the derived profile refreshes after data loads and preserves all manual preference drafts', async t => {
  const { $, calls, data, flush, loaded } = await setup(t, { page: 'athlete' });
  const form = $('#athlete-form');
  form.elements.weekly_minutes.value = '555';
  form.elements.day_0.value = '35';
  form.elements.ftp_watts.value = '237';
  form.elements.limitations.value = 'Preferisco evitare le discese ripide';
  form.elements.pool_access.checked = false;
  form.dataset.dirty = 'true';
  const before = [...form.elements].filter(node => node.name).map(node => ({ name: node.name, value: node.value, checked: node.checked }));
  assert.match($('#derived-profile').textContent, /Atleta di prova/);
  assert.match($('#derived-profile').textContent, /150/);
  data.profile = profileFixture({ total_activities: 151, weekly_minutes: 660, headline: 'Continuità multisport in crescita',
    priorities: ['Nuovo obiettivo specifico'], restrictions: { reasons: ['Contrattura ancora attiva.'] } });
  loaded();
  await flush();
  assert.match($('#derived-profile').textContent, /151/);
  assert.match($('#derived-profile').textContent, /Continuità multisport in crescita/);
  assert.match($('#derived-profile .derived-grid').textContent, /11/);
  assert.match($('#derived-profile .error').textContent, /Contrattura/);
  const after = [...form.elements].filter(node => node.name).map(node => ({ name: node.name, value: node.value, checked: node.checked }));
  assert.deepEqual(after, before);
  assert.equal(form.dataset.dirty, 'true');
  assert.equal(calls.filter(call => call.url === '/athlete/derived-profile').length, 2);
  assert.equal(calls.some(call => call.method !== 'GET'), false);
});

test('data loading is limited to the current page and navigation requests fresh data', async t => {
  const { calls, flush, navigate, data, $ } = await setup(t, { page: 'today' });
  assert.equal(calls.length, 0);
  navigate('health');
  await flush();
  assert.deepEqual(calls.map(call => call.url), ['/health/reports']);
  data.profile.headline = 'Profilo aggiornato dopo la sincronizzazione';
  navigate('athlete');
  await flush();
  assert.equal(calls.at(-1).url, '/athlete/derived-profile');
  assert.match($('#derived-profile').textContent, /dopo la sincronizzazione/);
  navigate('coach');
  await flush();
  assert.equal(calls.length, 2);
});

test('an unavailable journal offers retry and recovers without duplicating report cards', async t => {
  const { $, data, flush, byText, calls } = await setup(t, { reports: [reportFixture()], fail: { 'GET /health/reports': 'Offline di prova' } });
  assert.match($('.symptom-journal').textContent, /Segnalazioni non disponibili/);
  delete data.failures['GET /health/reports'];
  byText('Riprova', $('.symptom-journal')).click();
  await flush();
  assert.equal($('.symptom-journal').querySelectorAll('.symptom-card').length, 1);
  assert.equal(byText('Riprova', $('.symptom-journal')), undefined);
  assert.equal(calls.filter(call => call.url === '/health/reports' && call.method === 'GET').length, 2);
});

test('an unavailable automatic profile can be retried without changing manual settings', async t => {
  const { $, data, flush, byText } = await setup(t, { page: 'athlete', fail: { 'GET /athlete/derived-profile': 'Offline di prova' } });
  $('#athlete-form').elements.ftp_watts.value = '245';
  assert.match($('#derived-profile').textContent, /non è disponibile/);
  delete data.failures['GET /athlete/derived-profile'];
  byText('Riprova', $('#derived-profile')).click();
  await flush();
  assert.match($('#derived-profile').textContent, /Atleta di prova/);
  assert.equal($('#athlete-form').elements.ftp_watts.value, '245');
});

test('a failed report save keeps the dialog and draft ready for a successful retry', async t => {
  const { $, data, calls, flush, open, submit } = await setup(t, { fail: { 'POST /health/reports': 'Server non disponibile. Riprova.' } });
  const form = open();
  form.elements.body_location.value = 'Spalla destra';
  form.elements.suspected_cause.value = 'Dopo il nuoto';
  form.dataset.dirty = 'true';
  submit();
  await flush();
  assert.equal($('#health-report-dialog').open, true);
  assert.equal(form.elements.body_location.value, 'Spalla destra');
  assert.equal(form.elements.suspected_cause.value, 'Dopo il nuoto');
  assert.equal(form.dataset.dirty, 'true');
  assert.equal(data.loads, 0);
  assert.match($('#notice').textContent, /Server non disponibile/);
  assert.equal(form.querySelector('button[type="submit"]').disabled, false);
  delete data.failures['POST /health/reports'];
  submit();
  await flush();
  assert.equal($('#health-report-dialog').open, false);
  assert.equal(calls.filter(call => call.method === 'POST').length, 2);
  assert.equal(data.reports.length, 1);
});

test('a failed resolution does not falsely mark a report resolved and leaves a working retry', async t => {
  const { $, data, flush, byText } = await setup(t, { reports: [reportFixture()], fail: { 'PATCH /health/reports/11': 'Salvataggio non riuscito.' } });
  byText('Segna risolto', $('.symptom-journal')).click();
  await flush();
  assert.match($('.symptom-journal').textContent, /In corso/);
  assert.equal(data.loads, 0);
  assert.equal(byText('Segna risolto', $('.symptom-journal')).disabled, false);
  delete data.failures['PATCH /health/reports/11'];
  byText('Segna risolto', $('.symptom-journal')).click();
  await flush();
  assert.match($('.symptom-journal').textContent, /Risolto/);
});

test('a failed deletion retains the episode and permits deletion after recovery', async t => {
  const { $, data, flush, byText } = await setup(t, { reports: [reportFixture()], fail: { 'DELETE /health/reports/11': 'Eliminazione non riuscita.' } });
  byText('Elimina', $('.symptom-journal')).click();
  await flush();
  assert.equal($('.symptom-journal').querySelectorAll('.symptom-card').length, 1);
  assert.equal(data.reports.length, 1);
  assert.match($('#notice').textContent, /Eliminazione non riuscita/);
  delete data.failures['DELETE /health/reports/11'];
  byText('Elimina', $('.symptom-journal')).click();
  await flush();
  assert.equal($('.symptom-journal').children.length, 0);
});

test('profile records switch between all time and ninety days and open the actual recorded activity', async t => {
  const record = (value,id,name) => ({ value,activity_id:id,activity_name:name,label:'Distanza massima',unit:'km',scope:'whole_activity' });
  const profile=profileFixture({totals:{all_time:{hours:100,distance_km:3500}},records_note:'Record sulle attività intere, non su segmenti.',
    sports:[{sport:'bike',label:'Bici',activities:2,weekly_minutes:240,totals:{all_time:{activities:20,hours:60,distance_km:1600},days_90:{activities:6,hours:20,distance_km:450}},
      records:{all_time:{longest_distance_km:record(200,77,'<img src=x onerror=alert(1)>')},days_90:{longest_distance_km:record(80,88,'Uscita recente')}}}]});
  const {$,data,byText}=await setup(t,{page:'athlete',profile});
  assert.match($('.profile-record-grid').textContent,/200 km/);
  assert.equal($('.profile-record-grid img'),null);
  assert.equal(byText('Tutto lo storico').getAttribute('aria-pressed'),'true');
  $('.profile-record-tile button').click();
  assert.deepEqual(data.openedActivities,[77]);
  byText('Ultimi 90 giorni').click();
  assert.match($('.profile-record-grid').textContent,/80 km/);
  assert.doesNotMatch($('.profile-record-grid').textContent,/200 km/);
  assert.equal(byText('Ultimi 90 giorni').getAttribute('aria-pressed'),'true');
  $('.profile-record-tile button').click();
  assert.deepEqual(data.openedActivities,[77,88]);
  byText('Tutto lo storico').click();
  assert.match($('.profile-record-grid').textContent,/200 km/);
});

test('observed weight has an accessible history action and opens four weeks without inventing missing values', async t => {
  const weight={kind:'weight_kg',label:'Peso',unit:'kg',latest:{value:70.8,date:'2026-10-18',source:'intervals'},
    points:[{value:74,date:'2026-08-01',source:'intervals'},{value:71.5,date:'2026-10-01',source:'intervals'},{value:70.8,date:'2026-10-18',source:'intervals'}],stale:false};
  const {$,data}=await setup(t,{page:'athlete',profile:profileFixture({health:{metrics:[weight],weight:{...weight,change_28d_kg:-.7}}})});
  const card=$('.profile-health-card[data-kind="weight_kg"]');
  assert.match(card.textContent,/70,8 kg/);
  assert.match(card.textContent,/-0,7 kg nelle ultime 4 settimane/);
  assert.equal(card.querySelector('button').getAttribute('aria-label'),'Apri andamento Peso');
  card.querySelector('button').click();
  assert.equal(data.openedMetrics.length,1);
  assert.deepEqual(Array.from(data.openedMetrics[0].points,point=>point.date),['2026-10-01','2026-10-18']);
  assert.equal(data.openedMetrics[0].latest.value,70.8);
});

test('configured thresholds stay distinct from whole activity power records', async t => {
  const profile=profileFixture({thresholds:{cycling_ftp_watts:{value:235,source:'measured',measured_on:'2026-10-01'}},
    sports:[{sport:'bike',label:'Bici',activities:1,weekly_minutes:60,totals:{all_time:{activities:1,hours:1,distance_km:30},days_90:{activities:1,hours:1,distance_km:30}},
      records:{all_time:{highest_avg_power_w:{value:350,unit:'W',label:'Potenza media massima di un’attività',activity_name:'Uscita intensa',activity_id:77,scope:'whole_activity'}},days_90:{}}}]});
  const {$}=await setup(t,{page:'athlete',profile});
  assert.match($('.profile-thresholds').textContent,/FTP bici235 WTest misurato/);
  assert.match($('.profile-record-tile').textContent,/350 W/);
  assert.doesNotMatch($('.profile-thresholds').textContent,/350/);
});
