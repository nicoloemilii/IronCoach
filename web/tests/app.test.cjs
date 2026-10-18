'use strict';
// These tests evaluate local source in a synthetic DOM. They never start a browser,
// request a page, load external resources or contact any backend/provider.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const script = fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8');
const colorsScript = fs.readFileSync(path.join(__dirname, '../metric-status.js'), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
const plusDays = (value, count) => new Date(new Date(value + 'T12:00:00Z').valueOf() + count * 86400000).toISOString().slice(0, 10);

function fixture() {
  const plan = { id: 71, date: '2026-10-17', sport: 'cycling', duration_min: 60, name: 'Bici aerobica', purpose: 'Resistenza sostenibile', reason: 'Carico compatibile con il recupero.', steps: [{ type: 'warmup', duration_min: 10, target_rpe: 2 }, { type: 'endurance', duration_min: 45, target_power_w: 150 }, { type: 'cooldown', duration_min: 5, target_rpe: 1 }] };
  const workout = { id: 71, date: plan.date, sport: plan.sport, duration_min: 60, name: plan.name, status: 'planned', payload: clone(plan) };
  const profile = { experience: 'intermediate', race_distance: '70.3', goal_priority: 'finish', weekly_minutes: 480, recent_weekly_minutes: 420, availability: [60, 90, 60, 90, 0, 150, 90], pool_access: true, excluded_sports: [], limitations: '', event_name: 'Gara di prova', event_date: '2027-05-15', target_time: null, thresholds: { cycling_ftp_watts: { value: 220, source: 'measured', measured_on: '2026-10-01' }, running_threshold_pace_sec_per_km: { value: 300, source: 'estimated', measured_on: '2026-09-15' }, swimming_css_sec_per_100m: { value: null, source: 'unknown', measured_on: null } } };
  return { plan, workout, profile, generated: false, checkin: null, feedback: null, race: { nutrition: false, hydration: false, equipment: false, pacing: false, notes: '' }, activities: [{ id: 91, name: 'Giro di prova', sport: 'cycling', source: 'manual', start_time: '2026-10-17T08:00:00+02:00', duration_seconds: 3300, distance_meters: 27000 }] };
}
const healthFixture = () => ({ from: '2026-09-20', to: '2026-10-17', metrics: [
  { kind: 'ctl', label: 'Fitness · CTL', unit: 'punti', latest: { value: 36, date: '2026-10-17', source: 'intervals' }, points: [{ value: 34, date: '2026-10-16', source: 'intervals' }, { value: 36, date: '2026-10-17', source: 'intervals' }], baseline: null, stale: false, explanation: 'Carico cronico, non misura della prestazione.' },
  { kind: 'sleep_hours', label: 'Sonno', unit: 'ore', latest: { value: 0, date: '2026-10-14', source: 'manual' }, points: [{ value: 0, date: '2026-10-14', source: 'manual' }], baseline: null, stale: true, explanation: 'Leggi durata e sensazioni insieme.' },
  { kind: 'hrv_ms', label: 'HRV', unit: 'ms', latest: null, points: [], baseline: null, stale: false, explanation: 'Confronta misurazioni dello stesso metodo.' }
] });
const coachFixture = () => ({ score: null, judgment: 'Non valutabile', evidence: ['Una sola seduta valutabile.'], limits: ['Servono tre sedute valutabili.'], actions: [], rules_version: 'rules-1', upcoming: [{ id: 71, date: '2026-10-17', name: 'Bici aerobica', reason: 'Continuità aerobica.', expected_benefit: 'Stimolo atteso.', limits: 'Effetto non misurato.' }] });

async function setup(t) {
  const data = fixture(); const calls = []; const errors = []; const intervals = []; data.revision = 1;
  data.goals = []; data.duplicates = []; data.strava = { configured: false, connected: false, message: 'OAuth da configurare sul server.' };
  const console = new VirtualConsole(); console.on('jsdomError', error => errors.push(error));
  // runScripts outside-only prevents the HTML's script tag from loading anything.
  const dom = new JSDOM(html, { url: 'https://fixture.invalid/app', runScripts: 'outside-only', virtualConsole: console });
  const w = dom.window; const $ = selector => w.document.querySelector(selector);
  let now = new Date('2026-10-17T09:00:00Z').valueOf(); const NativeDate = w.Date;
  w.Date = class extends NativeDate { constructor(...args) { super(...(args.length ? args : [now])); } static now() { return now; } };
  w.setInterval = callback => { intervals.push(callback); return intervals.length; };
  w.clearInterval = () => {};
  w.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  w.HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); this.dispatchEvent(new w.Event('close')); };
  w.addEventListener('error', event => errors.push(event.error || new Error(event.message)));
  w.fetch = async (url, options = {}) => {
    const method = options.method || 'GET'; const payload = options.body ? JSON.parse(options.body) : null;
    const pathname = String(url); calls.push({ path: pathname, method, payload, headers: options.headers || {} }); let result;
    if (method === 'GET' && pathname === '/coach/today') result = { date: '2026-10-17', event: { name: data.profile.event_name, days_to_event: 210 }, fitness: { ctl: 36, atl: 40, tsb: -4 }, readiness: { readiness: 76, confidence: 65 }, reason: 'Recupero disponibile.', workout: data.plan,coach_context:data.coachContext,scheduled_workouts:data.scheduledWorkouts };
    else if (method === 'GET' && pathname === '/athlete/profile') result = { goals: { event: data.profile.event_name, event_date: data.profile.event_date } };
    else if (method === 'GET' && pathname === '/integrations/intervals/status') result = data.intervals || { configured: false, connected: false, message: 'Inserisci la chiave API.' };
    else if (method === 'POST' && pathname === '/integrations/intervals/connect') { data.intervals = {configured: true, connected: true, athlete_name: 'Atleta test', message: 'Collegamento verificato.'}; result = data.intervals; }
    else if (method === 'DELETE' && pathname === '/integrations/intervals') { data.intervals = {configured: false,connected:false,message:'Scollegato'}; result = data.intervals; }
    else if (method === 'POST' && pathname.startsWith('/sync/intervals?days=')) result = {new_activities:2,already_synced_or_matched:1,errors:[]};
    else if (method === 'GET' && pathname.startsWith('/dashboard/home')) {
      const selected = new URL(pathname,'https://fixture.invalid').searchParams.get('day') || '2026-10-17';
      const condition = healthFixture().metrics.map(item => {
        const points=item.points.filter(point=>point.date<=selected);
        return {...item,points,latest:points.at(-1)||null};
      });
      result = { date:selected, week_start:'2026-10-12', condition, recovery:data.recovery, week:data.week||{hours:0,activities:0,elevation_m:null,load:0}, week_history:data.week_history,week_assessments:data.week_assessments,load_trend_percent:null, coach:data.homeCoach||coachFixture() };
    }
    else if (method === 'POST' && pathname==='/sync/refresh') result = {runs:data.refreshRuns||[],messages:data.refreshMessages||[]};
    else if (method === 'GET' && /^\/sync\/runs\/\d+$/.test(pathname)) result = data.refreshResult || {status:'success',result:{}};
    else if (method === 'GET' && pathname.startsWith('/health/metrics')) result = healthFixture();
    else if (method === 'GET' && pathname === '/coach/overview') result = coachFixture();
    else if (method === 'GET' && pathname === '/integrations/strava/status') result = data.strava;
    else if (method === 'GET' && pathname === '/integrations/strava/activities') result = { activities: [], message: 'Vue temporaire uniquement.' };
    else if (method === 'DELETE' && pathname === '/integrations/strava') { data.strava.connected = false; result = {}; }
    else if (method === 'GET' && pathname === '/activities/duplicates/pending') result = data.duplicates;
    else if (method === 'POST' && /^\/activities\/duplicates\/\d+\/(merge|keep)$/.test(pathname)) { data.duplicates = []; result = {status:'kept'}; }
    else if (method === 'GET' && /^\/activities\/\d+\/assessment$/.test(pathname)) result = { score: null, judgment: 'Non valutabile', evidence: [], limits: ['Serve un abbinamento confermato.'], actions: [], benefits: 'Benefici attesi, non misurati.', rules_version: 'rules-1' };
    else if (pathname === '/goals') { if (method === 'POST') { const goal = { ...clone(payload), id: data.goals.length + 1 }; data.goals.push(goal); result = goal; } else result = data.goals; }
    else if (method === 'PATCH' && /^\/goals\/\d+$/.test(pathname)) { const goal = data.goals.find(g => g.id === Number(pathname.split('/')[2])); Object.assign(goal, clone(payload)); result = goal; }
    else if (method === 'DELETE' && /^\/goals\/\d+$/.test(pathname)) { data.goals = data.goals.filter(g => g.id !== Number(pathname.split('/')[2])); result = {}; }
    else if (method === 'GET' && /^\/goals\/\d+\/gap-analysis$/.test(pathname)) result = { score: null, window: { from: '2026-07-20', to: '2026-10-17' }, legs: [{ sport: 'running', terrain: 'trail', requirements: [{kind:'weekly_hours',reference:4,observed:null,score:null}] }], explanation: 'Copertura dei riferimenti, non probabilità di completamento.', limits: ['Riferimenti scelti dall’utente.'] };
    else if (method === 'GET' && pathname === '/coach/workouts?limit=200') result = [data.workout];
    else if (method === 'GET' && pathname === '/activities') result = data.activities;
    else if (pathname === '/planning/profile') {
      if (method === 'PUT') {
        if (options.headers?.['If-Match'] != null && Number(options.headers['If-Match']) !== data.revision) return {ok:false,status:412,json:async () => ({detail:{message:'Profilo modificato in un’altra finestra.',current_revision:data.revision}})};
        data.profile = clone(payload); data.revision++;
      }
      result = { profile: data.profile, revision: data.revision };
    }
    else if (method === 'PUT' && pathname === '/planning/goal') {
      const goal=data.goals.find(g=>g.id===payload.goal_id);data.profile.race_goal_id=payload.goal_id;
      if(goal)Object.assign(data.profile,{event_name:goal.name,event_date:goal.date,goal_priority:goal.priority,target_time:goal.target_time});data.revision++;result={profile:data.profile,revision:data.revision};
    }
    else if (method === 'POST' && pathname === '/planning/workouts/71/preview') {
      const after=clone(data.workout);if(payload.action==='shorten')after.duration_min=payload.duration_min;if(payload.action==='move')after.date=payload.date;if(payload.action==='skip')after.status='skipped';
      const afterMinutes=after.status==='skipped'?0:after.duration_min;
      result={before:data.workout,after,affected_workouts:[{before:data.workout,after}],impact:{before_minutes:60,after_minutes:afterMinutes,delta_minutes:afterMinutes-60,days:[]},rationale:['Disponibilità rispettata.'],preview_signature:'a'.repeat(64)};
    }
    else if (method === 'GET' && pathname.startsWith('/planning/week?start=')) {
      const start = pathname.split('=')[1]; result = { start, version: data.generated ? 1 : 0, phase: 'base', can_undo: data.generated, rationale: ['Disponibilità rispettata.'], block_preview: [{ start, focus: 'Continuità', indicative_minutes: 480 }], days: Array.from({ length: 7 }, (_, i) => ({ date: plusDays(start, i), available_minutes: data.profile.availability[i], planned_minutes: data.generated && i === 5 ? 60 : 0, sessions: data.generated && i === 5 ? [data.workout] : [] })) };
    }
    else if (method === 'POST' && pathname === '/planning/week') { data.generated = true; result = { version: 1 }; }
    else if (method === 'POST' && pathname === '/planning/undo') { data.generated = false; result = {}; }
    else if (method === 'PATCH' && pathname === '/planning/workouts/71') { if (payload.action === 'skip') data.workout.status = 'skipped'; result = {}; }
    else if (method === 'GET' && pathname === '/integrations/garmin/status') result = { configured: false, status: 'not_configured', message: 'Accesso ufficiale richiesto. Nessun dispositivo collegato.' };
    else if (method === 'GET' && pathname.startsWith('/experience/summary')) result = { review: 'La continuità conta.', sports: { cycling: { label: 'Bici', sessions: 1, minutes: 55, completed_workouts: 0 } }, note: 'Dati di prova.' };
    else if (pathname === '/experience/checkin') { if (method === 'PUT') data.checkin = payload; result = { checkin: data.checkin }; }
    else if (pathname === '/experience/race-preparation') { if (method === 'PUT') data.race = clone(payload); result = data.race; }
    else if (method === 'GET' && pathname === '/workouts/71/garmin/status') result = { status: 'blocked', message: 'Accesso Garmin ufficiale non configurato.' };
    else if (method === 'POST' && pathname === '/workouts/71/garmin/send') result = { status: 'blocked', message: 'Nessun invio eseguito: accesso Garmin ufficiale richiesto.' };
    else if (method === 'GET' && pathname === '/experience/workouts/71/review') result = { feedback: data.feedback, suggested_match: { activity_id: 91, confidence: 85 } };
    else if (method === 'PUT' && pathname === '/experience/workouts/71/feedback') { data.feedback = { ...payload, planned_duration_min: 60, actual: { duration_min: payload.activity_id ? 55 : 60 * payload.completion_percent / 100 }, evaluation: { completion: payload.activity_id ? 92 : payload.completion_percent }, source: payload.activity_id ? 'activity' : 'manual' }; result = data.feedback; }
    else if (method === 'POST' && pathname === '/coach/workouts') result = { ...data.plan, sport: payload.sport };
    else throw new Error(`Unstubbed fixture request: ${method} ${pathname}`);
    return { ok: true, status: 200, json: async () => clone(result) };
  };
  t.after(() => dom.window.close());
  const flush = async () => { for (let i = 0; i < 12; i++) await new Promise(resolve => setImmediate(resolve)); assert.equal(errors.length, 0, errors.map(e => e.stack || e.message).join('\n')); };
  const submit = async selector => { $(selector).dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true })); await flush(); };
  const set = (selector, value) => { const input = $(selector); assert.ok(input, `Missing control ${selector}`); if (input.type === 'checkbox') input.checked = value; else input.value = value; input.dispatchEvent(new w.Event('input', { bubbles: true })); };
  w.eval(fs.readFileSync(path.join(__dirname,'../charts.js'),'utf8')); w.eval(colorsScript); w.eval(script); await flush();
  assert.ok(!$('#notice').classList.contains('error'), $('#notice').textContent);
  return { w, $, data, calls, flush, submit, set, advance: ms => { now += ms; intervals.forEach(callback => callback()); } };
}

test('Intervals connection accepts key only for submission, syncs and disconnects', async t => {
  const { $, calls, set, submit, flush } = await setup(t);
  assert.equal($('#intervals-sync').disabled, true);
  set('#intervals-form [name="api_key"]', 'unit-test-key-not-real');
  await submit('#intervals-form');
  assert.equal($('#intervals-form [name="api_key"]').value, '');
  assert.ok(calls.some(c => c.path === '/integrations/intervals/connect' && c.payload.api_key === 'unit-test-key-not-real'));
  assert.ok(calls.some(c => c.path === '/sync/intervals?days=90&resources=both&background=true'));
  assert.equal($('#intervals-sync').disabled, false);
  assert.match($('#intervals-status').textContent, /Atleta test/);
  $('#intervals-disconnect').click(); await flush();
  assert.equal($('#intervals-sync').disabled, true);
});

test('initial fixture load renders dashboard and enables actionable daily controls', async t => {
  const { $, calls } = await setup(t);
  assert.equal($('#metrics').children.length, 4);
  assert.deepEqual([...$('#metrics').children].map(card => card.dataset.tone), ['neutral', 'unknown', 'unknown', 'positive']);
  assert.ok([...$('#metrics').children].every(card => card.querySelector('.metric-status')));
  assert.equal($('#coach-flash').closest('.coach-flash').dataset.tone, 'unknown');
  assert.match($('#daily-goal').textContent, /Gara di prova/);
  assert.match($('#weekly-review').textContent, /attività registrate/);
  for (const id of ['today-save', 'today-shorter', 'today-skip']) assert.equal($('#' + id).disabled, false, id);
  assert.equal($('#send-garmin').disabled, true);
  assert.ok(calls.some(c => c.path === '/dashboard/home'));
  assert.ok(calls.some(c => c.path === '/coach/overview'));
  assert.doesNotMatch($('#metrics').textContent, /Intervals.icu/);
  assert.match($('#home-week').textContent, /Trend non calcolabile/);
});

test('Home removes promotional banner and legend and moves optional notes to Salute', async t => {
  const {$} = await setup(t);
  assert.equal($('#today .hero'), null); assert.equal($('#today .metric-guide'), null);
  assert.equal($('#today #checkin'), null); assert.ok($('#health #checkin'));
  assert.equal($('#health .wellness-notes'),null);assert.equal($('#health .legacy-checkin').hidden,true);
  assert.ok($('#daily-goal'));
});

test('weekly perspective is compact above the condition cards and no longer a bottom section',async t=>{
  const {$}=await setup(t);const brief=$('.home-week-brief');assert.ok(brief);
  assert.ok(brief.compareDocumentPosition($('#metrics'))&4);assert.equal($('#today .review-card'),null);
  assert.equal($('#weekly-review').querySelectorAll('.review-row').length,0);assert.match(brief.textContent,/Bici 1/);
});

test('all main detail dialogs place the back action before their title',async t=>{
  const {$}=await setup(t);for(const id of ['activity-dialog','metric-dialog','workout-dialog','session-edit-dialog']){
    const top=$('#'+id+' .dialog-top');assert.equal(top.firstElementChild.tagName,'BUTTON',id);assert.match(top.firstElementChild.textContent,/← Indietro/,id);
  }
});

test('health chart shades only a supported personal range and labels it as descriptive',async t=>{
  const {w,$,flush}=await setup(t);const points=Array.from({length:10},(_,i)=>({date:plusDays('2026-10-07',i),value:48+i%4,source:'intervals'}));
  w.IronCoachUI.openHealthMetric({kind:'hrv_ms',label:'HRV',unit:'ms',points,latest:points.at(-1),stale:false});await flush();
  assert.ok($('#metric-detail .reference-band'));assert.ok([...$('#metric-detail .performance-chart').querySelectorAll('line[stroke-width="3"]')].every(line=>line.getAttribute('stroke')!=='#ffffff'));assert.match($('#metric-detail .chart-data-note').textContent,/intervallo abituale.*non un obiettivo/);
  $('#close-metric').click();w.IronCoachUI.openHealthMetric({kind:'weight_kg',label:'Peso',unit:'kg',points:points.map(p=>({...p,value:65})),latest:{...points.at(-1),value:65},stale:false});
  assert.equal($('#metric-detail .reference-band'),null);assert.match($('#metric-detail .chart-data-note').textContent,/Nessuna fascia/);
});

test('other activity filter includes non-triathlon disciplines and strength has its own selection',async t=>{
  const {w,$,data,flush}=await setup(t);data.activities.push({id:92,name:'Escursione',sport:'Hike',source:'manual',start_time:'2026-10-17T08:00:00Z'},{id:93,name:'Pesi',sport:'WeightTraining',source:'manual',start_time:'2026-10-17T08:00:00Z'});
  $('#refresh').click();await flush();const select=$('#activity-sport');select.value='other';select.dispatchEvent(new w.Event('change'));assert.match($('#activity-list').textContent,/Escursione/);assert.doesNotMatch($('#activity-list').textContent,/Giro di prova|Pesi/);
  select.value='strength';select.dispatchEvent(new w.Event('change'));assert.match($('#activity-list').textContent,/Pesi/);assert.doesNotMatch($('#activity-list').textContent,/Escursione/);
});

test('condition details contain only a Coach judgment and advice without source or raw tables', async t => {
  const {$,flush} = await setup(t);
  assert.equal($('#metrics').querySelectorAll('button[aria-haspopup="dialog"]').length, 4);
  assert.equal($('#metrics .metric-explanation'), null);
  $('#metrics .metric-open').click(); await flush();
  assert.equal($('#metric-dialog').open, true);
  assert.match($('#metric-dialog-title').textContent, /CTL/);
  assert.equal($('#metric-detail svg, #metric-detail table'),null);
  assert.match($('#metric-detail').textContent, /Indicazione del Coach/);
  assert.doesNotMatch($('#metric-detail').textContent, /Intervals.icu/);
  $('#close-metric').click(); assert.equal($('#metric-dialog').open, false);
});

test('history navigation changes the date and hides commands for today until returning', async t => {
  const {$,calls,flush} = await setup(t);
  assert.equal($('#home-next').disabled, true);
  $('#home-prev').click(); await flush();
  assert.ok(calls.some(call=>call.path==='/dashboard/home?day=2026-10-16'));
  assert.ok(calls.some(call=>call.path==='/experience/summary?end=2026-10-16'));
  assert.equal($('#home-date').value,'2026-10-16');
  assert.equal($('#daily-proposal').hidden,true); assert.equal($('#home-history-note').hidden,false);
  assert.match($('#current-date').textContent,/16/); assert.equal($('#home-next').disabled,false);
  $('#refresh').click(); await flush(); assert.equal($('#home-date').value,'2026-10-16');
  $('nav [data-page="activities"]').click(); await flush(); assert.match($('#current-date').textContent,/17/);
  $('nav [data-page="today"]').click(); await flush(); assert.match($('#current-date').textContent,/16/);
  $('#home-today').click(); await flush();
  assert.equal($('#home-date').value,'2026-10-17'); assert.equal($('#daily-proposal').hidden,false);
  assert.equal($('#home-today').hidden,true); assert.equal($('#home-next').disabled,true);
});

test('history failures keep the last successful date and do not show mismatched values', async t => {
  const {w,$,flush} = await setup(t); const original=w.fetch;
  w.fetch=async(url,options)=>String(url).includes('?day=')?Promise.reject(new Error('Offline')):original(url,options);
  $('#home-prev').click(); await flush();
  assert.equal($('#home-date').value,'2026-10-17'); assert.equal($('#daily-proposal').hidden,false);
  assert.match($('#notice').textContent,/Offline/);
});

test('recovery details explain missing dates while Coach states the actual evidence gap', async t => {
  const {$,data,flush} = await setup(t);
  data.recovery={date:'2026-10-17',readiness:null,confidence:0,explanation:'Mancano misure del giorno. Ultime osservazioni: 2026-10-16.',available_inputs:[],missing_inputs:['Sonno','HRV'],latest_observations:[{label:'Sonno',value:7.2,unit:'ore',date:'2026-10-16',source:'intervals'}]};
  data.homeCoach={...coachFixture(),unavailable_reason:'0 attività valutabili su 6 registrate: servono abbinamenti confermati e RPE.'};
  $('#refresh').click(); await flush();
  assert.match($('#coach-flash').textContent,/0 attività valutabili su 6/);
  const recovery=$('#metrics [data-metric-kind="readiness"]');
  assert.match(recovery.textContent,/Ultime misure da aggiornare/);
  recovery.querySelector('button').click(); await flush();
  assert.match($('#metric-detail').textContent,/Mancano misure del giorno/);
  assert.doesNotMatch($('#metric-detail').textContent,/7,2 ore/);
  assert.match($('#metric-detail').textContent,/2026-10-16/);
});

test('date picker opens a past day and rejects future dates without sending a request', async t => {
  const {w,$,calls,flush} = await setup(t);
  $('#home-date').value='2026-10-15'; $('#home-date').dispatchEvent(new w.Event('change')); await flush();
  assert.equal($('#home-date').value,'2026-10-15'); assert.equal($('#daily-proposal').hidden,true);
  $('#home-date').value='2026-10-18'; $('#home-date').dispatchEvent(new w.Event('change')); await flush();
  assert.equal($('#home-date').value,'2026-10-15'); assert.match($('#notice').textContent,/Scegli una data/);
  assert.equal(calls.some(call=>call.path==='/dashboard/home?day=2026-10-18'),false);
});

test('refresh starts provider synchronization, waits for its result, then reloads data',async t=>{
  const {$,data,calls,flush}=await setup(t);
  data.refreshRuns=[{source:'intervals',run_id:77}];
  const before=calls.length;$('#refresh').click();await flush();
  const fresh=calls.slice(before);
  assert.equal(fresh[0].path,'/sync/refresh');assert.equal(fresh[0].method,'POST');
  assert.ok(fresh.findIndex(c=>c.path==='/sync/runs/77')<fresh.findIndex(c=>c.path==='/dashboard/home'));
  assert.match($('#notice').textContent,/Sincronizzazione completata/);
});

test('a partial synchronization reloads local data and reports failure rather than success',async t=>{
  const {$,data,calls,flush}=await setup(t);
  data.refreshRuns=[{run_id:78}];data.refreshResult={status:'partial',result:{errors:[{error:'Wellness non aggiornato'}]}};
  const before=calls.length;$('#refresh').click();await flush();
  assert.ok(calls.slice(before).some(c=>c.path==='/dashboard/home'));
  assert.match($('#notice').textContent,/Wellness non aggiornato/);assert.equal($('#notice').classList.contains('error'),true);
});

test('weekly cards sum recorded ascent and open judgment with prior comparable weeks',async t=>{
  const {$,data,flush}=await setup(t);
  data.week={hours:8,activities:6,elevation_m:null,elevation_known_m:750,elevation_missing_activities:2,load:391};
  data.week_history=[{start:'2026-10-05',end:'2026-10-10',hours:9,activities:7,elevation_m:900,load:430}];
  const judgment={scope:'recorded_training',tone:'good',judgment:'Carico coerente con lo storico',reference:400,explanation:'Carico stabile nei dati registrati.',actions:['Mantieni il piano.']};
  data.week_assessments={load:judgment};data.homeCoach={...coachFixture(),training_assessment:judgment};
  $('#refresh').click();await flush();
  assert.match($('#home-week').children[2].textContent,/750 m/);assert.doesNotMatch($('#home-week').children[2].textContent,/parziale|≥/);
  assert.equal($('#home-week').children[3].dataset.tone,'good');
  assert.match($('#coach-flash').textContent,/Carico stabile/);assert.doesNotMatch($('#coach-flash').textContent,/Non valutabile/);
  $('#home-week').children[3].querySelector('button').click();await flush();
  assert.match($('#metric-detail').textContent,/Mantieni il piano/);assert.match($('#metric-detail svg').textContent,/430/);assert.equal([...$('#metric-detail svg').querySelectorAll('rect')].at(-1).getAttribute('fill'),'#138a56');assert.doesNotMatch($('#metric-detail').textContent,/Sono confrontati gli stessi giorni/);
  assert.doesNotMatch($('#metric-detail').textContent,/Intervals.icu/);
});

test('daily proposal states practical purpose, execution and next decision',async t=>{
  const {$}=await setup(t);
  assert.match($('#coach-title').textContent,/Oggi: Bici · 60 minuti/);
  assert.match($('#daily-brief').textContent,/Obiettivo della seduta/);
  assert.match($('#daily-brief').textContent,/Come eseguirla/);
  assert.match($('#daily-brief').textContent,/Decisione pratica/);
});

test('calendar navigation, generation and undo preserve the app contract', async t => {
  const { $, calls, flush } = await setup(t);
  $('nav [data-page="calendar"]').click(); await flush();
  assert.equal($('#calendar').hidden, false); assert.equal($('#today').hidden, true);
  assert.equal($('#week-days').children.length, 7);
  $('#generate-week').click(); await flush();
  const request = calls.find(c => c.method === 'POST' && c.path === '/planning/week');
  assert.deepEqual(request.payload, { start: '2026-10-12', regenerate: false });
  assert.equal($('#undo-week').disabled, false, 'Fresh generated week must expose undo');
  assert.match($('#week-days').textContent, /Bici/);
  $('#undo-week').click(); await flush(); assert.equal($('#undo-week').disabled, true);
  $('#week-next').click(); await flush(); assert.ok(calls.some(c => c.path === '/planning/week?start=2026-10-19'));
});

test('athlete form submits availability and retains independent threshold provenance', async t => {
  const { $, calls, set, submit } = await setup(t);
  set('#athlete-form [name="experience"]', 'advanced');
  set('#athlete-form [name="weekly_minutes"]', '540');
  set('#athlete-form [name="day_0"]', '75');
  set('#athlete-form [name="ftp_watts"]', '235');
  set('#athlete-form [name="ftp_watts_source"]', 'measured');
  set('#athlete-form [name="ftp_watts_date"]', '2026-10-10');
  await submit('#athlete-form');
  const payload = calls.find(c => c.method === 'PUT' && c.path === '/planning/profile').payload;
  assert.equal(payload.experience, 'advanced'); assert.equal(payload.weekly_minutes, 540); assert.equal(payload.availability[0], 75);
  assert.equal(payload.thresholds.cycling_ftp_watts.value, 235);
  assert.equal(payload.thresholds.cycling_ftp_watts.measured_on, '2026-10-10');
  assert.equal(payload.thresholds.running_threshold_pace_sec_per_km.source, 'estimated');
  assert.equal(payload.thresholds.running_threshold_pace_sec_per_km.measured_on, '2026-09-15');
  assert.equal($('#athlete-form').dataset.dirty, undefined);
});

test('goal form persists through the planning profile without losing athlete fields', async t => {
  const { calls, set, submit } = await setup(t);
  set('#goal-form [name="event"]', '  La mia gara  '); set('#goal-form [name="event_date"]', '2027-06-20'); set('#goal-form [name="target_time"]', '05:45:00');
  await submit('#goal-form');
  const payload = calls.find(c => c.method === 'PUT' && c.path === '/planning/profile').payload;
  assert.equal(payload.event_name, 'La mia gara'); assert.equal(payload.event_date, '2027-06-20'); assert.equal(payload.target_time, '05:45:00'); assert.equal(payload.weekly_minutes, 480);
});

test('unsaved workout has a save action, a working timer and preserves Garmin controls', async t => {
  const { $, flush, advance } = await setup(t);
  $('#today-workout button').click(); await flush();
  assert.equal($('#workout-dialog').open, true); assert.equal($('#workout-detail .steps').children.length, 3);
  assert.ok($('#send-garmin')); assert.equal($('#send-garmin').disabled, true); assert.equal($('#feedback-form').hidden, true);
  assert.match($('#workout-actions').textContent, /Salva questa proposta/);
  assert.equal($('#timer-display').textContent, '10:00');
  $('#timer-start').click(); advance(2000); assert.equal($('#timer-display').textContent, '09:58');
  $('#timer-pause').click(); advance(3000); assert.equal($('#timer-display').textContent, '09:58');
  $('#timer-next').click(); assert.equal($('#timer-display').textContent, '45:00'); assert.match($('#timer-step-name').textContent, /2\/3/);
  $('#timer-reset').click(); assert.equal($('#timer-display').textContent, '10:00');
  $('#close-dialog').click(); assert.equal($('#workout-dialog').open, false);
});

test('saved workout exposes export, reports blocked Garmin truthfully and saves matched feedback', async t => {
  const { $, calls, flush, set, submit } = await setup(t);
  $('#workouts button').click(); await flush();
  assert.equal($('#feedback-form').hidden, false); assert.equal($('#export-workout').hidden, false); assert.equal($('#export-workout').getAttribute('href'), '/workouts/71/export.json');
  assert.match($('#garmin-workout-status').textContent, /non configurato/);
  assert.equal($('#feedback-activity').value, '91');
  $('#send-garmin').click(); await flush(); assert.match($('#garmin-workout-status').textContent, /Nessun invio eseguito/); assert.equal($('#notice').classList.contains('error'), true);
  set('#feedback-form [name="rpe"]', '6'); set('#feedback-form [name="completion_percent"]', '90'); set('#feedback-form [name="feeling"]', 'harder'); set('#feedback-form [name="notes"]', '  Gambe pesanti  ');
  await submit('#feedback-form');
  assert.deepEqual(calls.find(c => c.method === 'PUT' && c.path.endsWith('/feedback')).payload, { rpe: 6, completion_percent: 90, feeling: 'harder', notes: 'Gambe pesanti', activity_id: 91 });
  assert.match($('#feedback-result').textContent, /60 \/ 55 minuti/);
  assert.match($('#feedback-result').textContent, /misurata/);
});

test('saving an unsaved proposal opens its persisted detail and calendar edits send the selected action', async t => {
  const { $, calls, flush, set, submit } = await setup(t);
  $('#today-save').click(); await flush();
  assert.deepEqual(calls.find(c => c.method === 'POST' && c.path === '/coach/workouts').payload, { sport: 'cycling' });
  assert.equal($('#workout-dialog').open, true);
  assert.equal($('#feedback-form').hidden, false);
  assert.equal($('#send-garmin').disabled, false);
  assert.match($('#garmin-workout-status').textContent, /non configurato/);
  $('#close-dialog').click();
  $('#today-shorter').click(); await flush();
  assert.equal($('#session-edit-dialog').open, true);
  assert.equal($('#session-edit-form [name="date"]').disabled, true);
  assert.equal($('#session-edit-form [name="duration_min"]').disabled, false);
  assert.equal($('#session-edit-form [name="duration_min"]').min, '15');
  assert.equal($('#session-edit-form [name="duration_min"]').max, '59');
  set('#session-edit-form [name="duration_min"]', '35');
  set('#session-edit-form [name="reason"]', 'Meno tempo disponibile');
  await submit('#session-edit-form');
  assert.equal(calls.some(c=>c.method==='PATCH'),false,'An impact preview must not mutate the calendar');
  assert.match($('#session-change-preview').textContent,/60 → 35/);
  $('#session-change-preview button').click();await flush();
  assert.deepEqual(calls.find(c => c.method === 'PATCH' && c.path === '/planning/workouts/71').payload, { action: 'shorten', duration_min: 35, reason: 'Meno tempo disponibile' });
  assert.equal(calls.find(c=>c.method==='PATCH').headers['X-Plan-Preview'],'a'.repeat(64));
  assert.equal($('#session-edit-dialog').open, false);
});

test('imported activity text is rendered without interpreting markup', async t => {
  const { $, data, flush } = await setup(t);
  data.activities[0].name = '<img src=x onerror="window.__injected=true">';
  $('#refresh').click(); await flush();
  assert.equal($('#activity-list img'), null);
  assert.match($('#activity-list').textContent, /<img src=x/);
});

test('check-in and race preparation submit only the corresponding form fields', async t => {
  const { calls, set, submit } = await setup(t);
  set('#checkin [name="sleep_hours"]', '7.5'); set('#checkin [name="fatigue"]', '4'); set('#checkin [name="pain"]', true); set('#checkin [name="notes"]', 'Stanchezza'); await submit('#checkin');
  const checkin = calls.find(c => c.method === 'PUT' && c.path === '/experience/checkin').payload;
  assert.equal(checkin.sleep_hours, 7.5); assert.equal(checkin.fatigue, 4); assert.equal(checkin.pain, true); assert.equal(checkin.motivation, null);
  set('#race-prep-form [name="nutrition"]', true); set('#race-prep-form [name="notes"]', 'Provato nei lunghi'); await submit('#race-prep-form');
  const race = calls.find(c => c.method === 'PUT' && c.path === '/experience/race-preparation').payload;
  assert.deepEqual(race, { nutrition: true, hydration: false, equipment: false, pacing: false, notes: 'Provato nei lunghi' });
});

test('a late workout review does not replace feedback already being entered', async t => {
  const { w, $, flush, set } = await setup(t);
  const originalFetch = w.fetch; let release;
  w.fetch = (url, options) => url === '/experience/workouts/71/review' ? new Promise(resolve => { release = () => resolve({ ok: true, status: 200, json: async () => ({ feedback: { rpe: 3, completion_percent: 100, feeling: 'easier', notes: 'Vecchia nota', activity_id: null, planned_duration_min: 60, actual: { duration_min: 60 }, evaluation: { completion: 100 }, source: 'manual' } }) }); }) : originalFetch(url, options);
  $('#workouts button').click(); await flush();
  set('#feedback-form [name="rpe"]', '8'); set('#feedback-form [name="notes"]', 'Nuovo feedback in corso');
  release(); await flush();
  assert.equal($('#feedback-form [name="rpe"]').value, '8');
  assert.equal($('#feedback-form [name="notes"]').value, 'Nuovo feedback in corso');
});

test('feedback failure is announced inside the open modal and retains entered values', async t => {
  const { w, $, flush, set, submit } = await setup(t);
  $('#workouts button').click(); await flush();
  const originalFetch = w.fetch;
  w.fetch = (url, options) => url.endsWith('/feedback') ? Promise.resolve({ ok: false, status: 422, json: async () => ({ detail: 'Feedback non valido.' }) }) : originalFetch(url, options);
  set('#feedback-form [name="rpe"]', '8'); set('#feedback-form [name="notes"]', 'Non perdere questa nota');
  await submit('#feedback-form');
  assert.equal($('#workout-dialog').open, true);
  assert.equal($('#feedback-form [name="notes"]').value, 'Non perdere questa nota');
  const announcements = [...$('#workout-dialog').querySelectorAll('[role="alert"], [role="status"]')];
  assert.ok(announcements.some(node => !node.hidden && node.textContent.includes('Feedback non valido.')), 'The global notice is inaccessible behind a modal; announce its error inside the dialog.');
});

test('an empty check-in response clears previously loaded values', async t => {
  const { $, data, flush } = await setup(t);
  data.checkin = { sleep_hours: 6.5, fatigue: 4, pain: true, notes: 'Ieri' };
  $('#refresh').click(); await flush(); assert.equal($('#checkin [name="sleep_hours"]').value, '6.5');
  data.checkin = null;
  $('#refresh').click(); await flush();
  assert.equal($('#checkin [name="sleep_hours"]').value, '');
  assert.equal($('#checkin [name="fatigue"]').value, '');
  assert.equal($('#checkin [name="pain"]').checked, false);
  assert.equal($('#checkin [name="notes"]').value, '');
});

test('new modal action buttons stay disabled while saving and become available after completion', async t => {
  const { w, $, flush } = await setup(t);
  const originalFetch = w.fetch; let release;
  w.fetch = (url, options) => url === '/workouts/71/garmin/status' ? new Promise(resolve => { release = () => resolve({ ok: true, status: 200, json: async () => ({ status: 'blocked', message: 'Accesso ufficiale richiesto.' }) }); }) : originalFetch(url, options);
  $('#today-save').click(); await flush();
  assert.equal($('#workout-dialog').open, true);
  const newAction = $('#session-actions button'); assert.ok(newAction);
  assert.equal(newAction.disabled, true, 'A new action must follow the active request state.');
  release(); await flush(); assert.equal(newAction.disabled, false);
});

test('health distinguishes zero, absent observations and stale data, with exact accessible values', async t => {
  const { $, flush } = await setup(t);
  $('nav [data-page="health"]').click(); await flush();
  assert.equal($('#health').hidden, false);
  const cards = [...$('#health-metrics').children];
  assert.match(cards[1].querySelector('.health-value').textContent, /^0 ore$/);
  assert.match(cards[1].textContent, /Dato da aggiornare/);
  assert.equal(cards[2].querySelector('.health-value').textContent, 'N/D');
  assert.deepEqual(cards.map(card => card.dataset.tone), ['neutral', 'unknown', 'unknown']);
  assert.equal(cards[1].querySelector('table'),null);assert.ok(cards[1].querySelector('.metric-open'));
  assert.ok(cards[0].querySelector('svg[role="img"] title'));
});

test('activity assessment and filters do not claim a grade with insufficient evidence', async t => {
  const { $, set, flush } = await setup(t);
  $('#activity-list button').click(); await flush();
  assert.equal($('#activity-dialog').open, true);
  assert.match($('#activity-detail').textContent, /Non valutabile/);
  assert.match($('#activity-detail').textContent, /Serve un abbinamento/);
  $('#close-activity').click();
  set('#activity-search', 'non esiste');
  assert.match($('#activity-list').textContent, /Nessuna attività corrisponde/);
  assert.match($('#coach-overview').textContent, /Servono tre sedute/);
  assert.match($('#coach-upcoming').textContent, /Stimolo atteso/);
});

test('activity and goal errors reset a previous favorable color and leave an explicit message', async t => {
  const { w, $, data, flush } = await setup(t); const original = w.fetch;
  data.goals.push({id:1,name:'Gara test',date:'2027-06-01',legs:[{sport:'running',terrain:'road',distance_km:21,elevation_gain_m:0}]});
  let failing = false;
  w.fetch = async (url, options) => {
    const activity = String(url).endsWith('/assessment'); const gap = String(url).endsWith('/gap-analysis');
    if (activity || gap) {
      if (failing) throw new Error('Test offline');
      const value = activity ? {score:90,judgment:'Eccellente',evidence:[],actions:[],limits:[],rules_version:'test'} : {score:100,window:{from:'2026-07-20',to:'2026-10-17'},explanation:'Riferimenti coperti.',legs:[],limits:[]};
      return {ok:true,status:200,json:async()=>value};
    }
    return original(url, options);
  };
  $('#refresh').click(); await flush();
  $('#activity-list button').click(); await flush(); assert.equal($('#activity-detail .activity-coach').dataset.tone, 'good');
  $('#close-activity').click();
  const gapButton = [...w.document.querySelectorAll('#race-goal-list button')].find(n => n.textContent === 'Vedi preparazione');
  gapButton.click(); await flush(); assert.equal($('.gap-result').dataset.tone, 'good');
  failing = true;
  $('#activity-list button').click(); await flush(); assert.equal($('#activity-detail .activity-coach').dataset.tone, 'unknown');
  assert.match($('#activity-detail').textContent, /Valutazione non disponibile/); $('#close-activity').click();
  gapButton.click(); await flush(); assert.equal($('.gap-result').dataset.tone, 'unknown');
  assert.match($('.gap-result').textContent, /Analisi non disponibile/);
});

test('detailed goal creates independent ordered legs, preserves unknown confirmations and can be edited', async t => {
  const { $, calls, set, submit, flush } = await setup(t);
  $('#new-race-goal').click();
  set('#race-goal-form [name="name"]', 'Triathlon in quota'); set('#race-goal-form [name="date"]', '2027-06-20');
  set('#race-goal-legs [name="distance_km"]', '32'); set('#race-goal-legs [name="terrain"]', 'trail'); set('#race-goal-legs [name="weekly_hours"]', '4');
  set('#race-goal-legs [name="altitude_max_m"]', '1800');
  $('#add-goal-leg').click(); const second = $('#race-goal-legs').children[1];
  second.querySelector('[name="sport"]').value = 'cycling'; second.querySelector('[name="distance_km"]').value = '160';
  await submit('#race-goal-form');
  const created = calls.find(c => c.path === '/goals' && c.method === 'POST').payload;
  assert.equal(created.legs.length, 2); assert.equal(created.legs[1].sport, 'cycling');
  assert.equal(created.legs[0].requirements.weekly_hours, 4); assert.equal(created.legs[0].requirements.altitude_confirmed, null);
  assert.equal(created.legs[0].requirements.longest_distance_km, null);
  assert.equal($('#race-goal-form').hidden, true);
  $('#race-goal-list .action-group button').click(); await flush();
  assert.match($('#race-goal-list .gap-result').textContent, /Dati insufficienti/);
  assert.match($('#race-goal-list .gap-result').textContent, /non probabilità/);
  $('#race-goal-list .action-group button:nth-child(2)').click();
  set('#race-goal-form [name="name"]', 'Gara aggiornata'); await submit('#race-goal-form');
  assert.ok(calls.some(c => c.path === '/goals/1' && c.method === 'PATCH' && c.payload.name === 'Gara aggiornata'));
});

test('candidate duplicates require an explicit decision', async t => {
  const { $, data, calls, flush } = await setup(t);
  data.duplicates = [{ id: 1, activity: data.activities[0], other: {...data.activities[0], id: 92, source: 'intervals'} }];
  $('#refresh').click(); await flush();
  assert.equal($('#duplicate-card').hidden, false);
  assert.equal(calls.filter(c => c.method === 'POST' && c.path.includes('/duplicates/')).length, 0);
  $('#duplicate-list button:nth-child(2)').click(); await flush();
  assert.ok(calls.some(c => c.path === '/activities/duplicates/1/keep'));
  assert.equal($('#duplicate-card').hidden, true);
});

test('Intervals asynchronous run is polled and partial failures stay visible', async t => {
  const { w, $, data, calls, flush } = await setup(t); const original = w.fetch;
  w.fetch = async (url, options) => {
    if (String(url).startsWith('/sync/intervals?')) return {ok:true,status:202,json:async () => ({run_id:12})};
    if (url === '/sync/runs/12') { calls.push({path:url,method:'GET'}); return {ok:true,status:200,json:async () => ({id:12,status:'partial',result:{new_activities:1,errors:[{error:'Wellness non importato'}]}})}; }
    return original(url, options);
  };
  data.intervals = {configured:true,connected:true,message:'Collegato'};
  $('#refresh').click(); await flush();
  $('#intervals-sync').click(); await flush();
  assert.ok(calls.some(c => c.path === '/sync/runs/12'));
  assert.match($('#intervals-result').textContent, /Wellness non importato/);
  assert.equal($('#notice').classList.contains('error'), true);
});

test('Strava stays in a separate temporary view and does not create activity assessments', async t => {
  const { w, $, data, flush } = await setup(t); const original = w.fetch;
  data.strava = {configured:true,connected:true,message:'Collegato',scopes:['read','activity:read']};
  w.fetch = async (url, options) => url === '/integrations/strava/activities' ? {ok:true,status:200,json:async () => ({activities:[{id:555,name:'Solo Strava',sport_type:'Ride',start_date:'2026-10-17T10:00:00Z',moving_time:3600,distance:22000}],message:'Vista temporanea'})} : original(url, options);
  $('#refresh').click(); await flush();
  assert.equal($('#strava-sync').disabled, false);
  assert.equal($('#strava-activities a').href, 'https://www.strava.com/activities/555');
  assert.doesNotMatch($('#activity-list').textContent, /Solo Strava/);
  assert.doesNotMatch($('#home-week').textContent, /Solo Strava/);
});

test('selecting a detailed goal for the plan preserves availability and athlete thresholds', async t => {
  const { $, data, calls, flush } = await setup(t);
  data.goals = [{ id: 4, name: 'Gara in montagna', date: '2027-06-20', priority: 'improve', target_time: null, legs: [{sport:'running'}] }];
  $('#refresh').click(); await flush();
  $('#race-goal-list .action-group button:nth-child(3)').click(); await flush();
  const request = calls.find(c => c.method === 'PUT' && c.path === '/planning/goal');
  assert.deepEqual(request.payload,{goal_id:4});assert.equal(request.headers['If-Match'],'1');
  assert.equal(data.profile.race_goal_id,4);assert.equal(data.profile.weekly_minutes,480);assert.equal(data.profile.thresholds.cycling_ftp_watts.value,220);
  assert.deepEqual(data.profile.availability,[60,90,60,90,0,150,90]);
});

test('an invalid health range retains the selected dates and skips the request', async t => {
  const { $, calls, set, submit } = await setup(t);
  const before = calls.filter(c => c.path.startsWith('/health/metrics')).length;
  set('#health-range [name="from"]', '2026-10-17'); set('#health-range [name="to"]', '2026-10-01');
  await submit('#health-range');
  assert.equal(calls.filter(c => c.path.startsWith('/health/metrics')).length, before);
  assert.equal($('#health-range [name="from"]').value, '2026-10-17');
  assert.match($('#notice').textContent, /La data iniziale supera/);
});

test('refresh keeps navigation and inputs usable while preserving a dirty profile', async t => {
  const { w, $, data, set, flush } = await setup(t); const original = w.fetch;
  set('#athlete-form [name="weekly_minutes"]', '615');
  let release;
  w.fetch = (url, options) => url === '/dashboard/home' ? new Promise(resolve => { release = () => resolve(original(url, options)); }) : original(url, options);
  $('#refresh').click(); await flush();
  assert.equal($('#athlete-form [name="weekly_minutes"]').disabled, false);
  assert.equal($('nav [data-page="athlete"]').disabled, false);
  $('nav [data-page="athlete"]').click(); assert.equal($('#athlete').hidden, false);
  data.profile.weekly_minutes = 450;
  release(); await flush();
  assert.equal($('#athlete-form [name="weekly_minutes"]').value, '615');
});

test('partial refresh provides a human retry message without hiding usable data', async t => {
  const { w, $, flush } = await setup(t); const original = w.fetch;
  w.fetch = async (url, options) => url === '/health/metrics' ? Promise.reject(new Error('network unavailable')) : original(url, options);
  $('#refresh').click(); await flush();
  assert.equal($('#data-status').hidden, false); assert.match($('#data-status').textContent, /Salute/);
  assert.doesNotMatch($('#data-status').textContent, /\/health\/|network unavailable/);
  assert.equal($('#notice').classList.contains('error'), false);
  assert.ok([...$('#health-metrics').children].every(card => card.dataset.tone === 'unknown'));
  assert.match($('#health-metrics').textContent, /Aggiornamento non riuscito/);
  assert.match($('#daily-goal').textContent, /Gara di prova/);
  w.fetch = original; $('#data-status button').click(); await flush();
  assert.equal($('#data-status').hidden, true);
  assert.equal($('#health-metrics').firstElementChild.dataset.tone, 'neutral');
});

test('retained recovery and home judgments lose their color after a failed refresh and recover on retry', async t => {
  const { w, $, flush } = await setup(t); const original = w.fetch;
  assert.equal($('#metrics [data-metric-kind="readiness"]').dataset.tone, 'positive');
  w.fetch = async (url, options) => ['/coach/today', '/dashboard/home', '/coach/overview'].includes(url) ? Promise.reject(new Error('offline')) : original(url, options);
  $('#refresh').click(); await flush();
  assert.ok([...$('#metrics').children].every(card => card.dataset.tone === 'unknown'));
  assert.equal($('#coach-overview').dataset.tone, 'unknown');
  assert.match($('#metrics [data-metric-kind="readiness"]').textContent, /Aggiornamento non riuscito/);
  assert.doesNotMatch($('#metrics [data-metric-kind="readiness"]').textContent, /2026-10-17/);
  w.fetch = original; $('#data-status button').click(); await flush();
  assert.equal($('#metrics [data-metric-kind="readiness"]').dataset.tone, 'positive');
  assert.equal($('#metrics').firstElementChild.dataset.tone, 'neutral');
});

test('recovery from a preceding day stays gray even after a successful response', async t => {
  const { $, advance, flush } = await setup(t);
  advance(86400000); $('#refresh').click(); await flush();
  const card = $('#metrics [data-metric-kind="readiness"]');
  assert.equal(card.dataset.tone, 'unknown'); assert.match(card.textContent, /Stima da aggiornare/);
});

test('failed health range requests gray retained values and a successful retry restores context', async t => {
  const { w, $, set, submit } = await setup(t); const original = w.fetch;
  set('#health-range [name="from"]', '2026-10-01'); set('#health-range [name="to"]', '2026-10-17');
  w.fetch = async (url, options) => String(url).startsWith('/health/metrics?') ? Promise.reject(new Error('Test offline')) : original(url, options);
  await submit('#health-range');
  assert.equal($('#health-metrics').firstElementChild.dataset.tone, 'unknown');
  w.fetch = original; await submit('#health-range');
  assert.equal($('#health-metrics').firstElementChild.dataset.tone, 'neutral');
});

test('profile revision conflict preserves the draft and requires an explicit reload', async t => {
  const { $, data, calls, set, submit, flush } = await setup(t);
  set('#athlete-form [name="weekly_minutes"]', '615');
  data.profile.weekly_minutes = 450; data.revision = 2;
  $('#refresh').click(); await flush(); // New server data must not rebase an unfinished draft.
  await submit('#athlete-form');
  const writes = calls.filter(c => c.path === '/planning/profile' && c.method === 'PUT');
  assert.equal(writes.length, 1); assert.equal(writes[0].headers['If-Match'], '1');
  assert.equal(data.profile.weekly_minutes, 450);
  assert.equal($('#athlete-form [name="weekly_minutes"]').value, '615');
  assert.equal($('#athlete-form').dataset.dirty, 'true');
  assert.match($('#notice').textContent, /Le tue modifiche restano/);
  $('#notice button').click(); await flush();
  assert.equal($('#athlete-form [name="weekly_minutes"]').value, '450');
  assert.equal($('#athlete-form').dataset.dirty, undefined);
});

test('large activity history is progressive, filterable and preserves unknown distances', async t => {
  const { w, $, data, flush } = await setup(t);
  data.activities = Array.from({length:121}, (_,i) => ({id:i+1,name:`Sessione ${i+1}`,sport:i===120?'trailrun':'cycling',source:'intervals',start_time:'2026-10-17T07:00:00Z',duration_seconds:3600,distance_meters:null}));
  $('#refresh').click(); await flush();
  assert.equal($('#activity-list .list-row') !== null, true);
  assert.equal(w.document.querySelectorAll('#activity-list .list-row').length, 50);
  assert.match($('#activity-list').textContent, /50 di 121/);
  assert.match($('#activity-list .list-row').textContent, /— km/);
  [...w.document.querySelectorAll('#activity-list button')].find(b => /Mostra altre/.test(b.textContent)).click();
  assert.equal(w.document.querySelectorAll('#activity-list .list-row').length, 100);
  $('#activity-sport').value = 'running'; $('#activity-sport').dispatchEvent(new w.Event('change'));
  assert.equal(w.document.querySelectorAll('#activity-list .list-row').length, 1);
  assert.match($('#activity-list').textContent, /Sessione 121/);
});

test('selected health dates persist through a general refresh', async t => {
  const { w, $, calls, set, submit, flush } = await setup(t); const original = w.fetch;
  w.fetch = async (url, options) => {
    const result = await original(url, options);
    if (!String(url).startsWith('/health/metrics?')) return result;
    const params = new URL(String(url), 'https://fixture.invalid').searchParams;
    return {ok:true,status:200,json:async () => ({...healthFixture(),from:params.get('from'),to:params.get('to')})};
  };
  set('#health-range [name="from"]', '2026-09-01'); set('#health-range [name="to"]', '2026-09-30');
  await submit('#health-range'); $('#refresh').click(); await flush();
  assert.equal(calls.filter(c => c.path === '/health/metrics?from=2026-09-01&to=2026-09-30').length, 2);
  assert.equal($('#health-range [name="from"]').value, '2026-09-01');
  assert.equal($('#health-status').textContent,'');
});

test('reopening a workout keeps unsaved feedback and saving clears the draft', async t => {
  const { $, set, flush, submit } = await setup(t);
  $('#workouts .list-row').click(); await flush(); set('#feedback-form [name="notes"]', 'Bozza da conservare');
  $('#close-dialog').click(); $('#workouts .list-row').click(); await flush();
  assert.equal($('#feedback-form [name="notes"]').value, 'Bozza da conservare');
  set('#feedback-form [name="rpe"]', '5'); await submit('#feedback-form');
  assert.equal($('#feedback-form').dataset.dirty, undefined);
});

test('a rest proposal without workout phases opens without a crash', async t => {
  const { w, $, flush } = await setup(t); const original = w.fetch;
  w.fetch = async (url, options) => {
    const result = await original(url, options); if (url !== '/coach/today') return result;
    const day = await result.json(); day.training_paused = true; day.workout = {sport:'recovery',duration_min:0,reason:'Recupero e rivalutazione.',steps:[]};
    return {ok:true,status:200,json:async () => day};
  };
  $('#refresh').click(); await flush(); $('#today-workout button').click(); await flush();
  assert.equal($('#workout-dialog').open, true);
  assert.match($('#workout-detail').textContent, /Recupero e rivalutazione/);
});

test('a delayed old review cannot overwrite feedback that has just been saved', async t => {
  const { w, $, set, submit, flush } = await setup(t); const original = w.fetch; let release;
  w.fetch = (url, options) => url === '/experience/workouts/71/review' ? new Promise(resolve => {
    release = () => resolve({ok:true,status:200,json:async () => ({feedback:{rpe:3,completion_percent:100,feeling:'as_expected',notes:'Vecchio feedback',activity_id:null,actual:{duration_min:60},planned_duration_min:60,evaluation:{completion:100},source:'manual'}})});
  }) : original(url, options);
  $('#workouts .list-row').click(); await flush();
  set('#feedback-form [name="notes"]', 'Feedback appena salvato'); set('#feedback-form [name="rpe"]', '5'); await submit('#feedback-form');
  release(); await flush();
  assert.equal($('#feedback-form [name="notes"]').value, 'Feedback appena salvato');
  assert.equal($('#feedback-form [name="rpe"]').value, '5');
});


test('load reduction remains visible instead of an older long calendar workout',async t=>{
  const {$,data,flush}=await setup(t);
  const old=clone(data.workout);old.date='2026-10-17';old.duration_min=90;old.payload.duration_min=90;
  data.plan.duration_min=30;data.plan.expected_rpe=3;
  data.scheduledWorkouts=[old];data.workout=old;
  data.coachContext={session_policy:{mode:'easy',max_duration_min:30},actions:['Oggi al massimo 30 minuti facili.']};
  $('#refresh').click();await flush();
  assert.match($('#coach-title').textContent,/30 minuti/);
  assert.match($('#daily-brief').textContent,/al massimo 30 minuti/);
  assert.match($('#today-save').textContent,/Rivedi/);
  assert.doesNotMatch($('#coach-title').textContent,/90/);
  $('#today-save').click();await flush();
  assert.equal($('#session-edit-form').elements.duration_min.value,'30');
});


test('condition colors are individual and cards omit dates even with a critical global judgment',async t=>{
 const {w,$,flush}=await setup(t);const original=w.fetch;
 w.fetch=async(url,options)=>{const response=await original(url,options);if(!String(url).startsWith('/dashboard/home'))return response;const value=await response.json();value.condition=[
 {kind:'tsb',label:'Forma',unit:'punti',latest:{value:-4.9,date:'2026-10-17',source:'intervals'},points:[]},
 {kind:'atl',label:'Fatica',unit:'punti',latest:{value:90,date:'2026-10-17',source:'intervals'},points:[]},
 {kind:'ctl',label:'Fitness',unit:'punti',latest:{value:48,date:'2026-10-17',source:'intervals'},points:[]}];value.metric_assessments={tsb:{tone:'critical',judgment:'Carico alto',explanation:'Bilancio e carico letti insieme.',actions:['Alleggerisci.']}};return {ok:true,json:async()=>value};};
 $('#refresh').click();await flush();assert.equal($('#metrics [data-metric-kind="tsb"]').dataset.tone,'good');assert.equal($('#metrics [data-metric-kind="atl"]').dataset.tone,'critical');
 assert.doesNotMatch($('#metrics').textContent,/2026-10-17/);
 $('#metrics [data-metric-kind="tsb"] button').click();await flush();assert.ok($('#metric-detail .coach-findings li'));assert.match($('#close-metric').textContent,/←/);$('#close-metric').click();assert.equal($('#metric-dialog').open,false);
});

test('health cards open charts with their own four-week range and preserve custom dates',async t=>{
 const {$,set,submit,flush}=await setup(t);$('#health-metrics .metric-open').click();await flush();
 assert.equal($('#metric-detail .chart-range [name="from"]').value,'2026-09-20');
 assert.equal($('#metric-detail .chart-range [name="to"]').value,'2026-10-17');assert.ok($('#metric-detail svg .axis-label'));
 set('#metric-detail .chart-range [name="from"]','2026-09-01');set('#metric-detail .chart-range [name="to"]','2026-10-01');await submit('#metric-detail .chart-range');
 assert.equal($('#metric-detail .chart-range [name="from"]').value,'2026-09-01');assert.equal($('#metric-detail .chart-range [name="to"]').value,'2026-10-01');
});

test('Coach navigation offers a back arrow to the originating section',async t=>{
 const {$,flush}=await setup(t);$('#today .coach-flash [data-page="coach"]').click();await flush();assert.equal($('#coach').hidden,false);$('#coach-back').click();assert.equal($('#today').hidden,false);
});


test('weekly chart has one reference and equal fully saturated colors for equal values',async t=>{
 const {$,data,flush}=await setup(t);data.week={hours:8.3,activities:6,elevation_m:0,load:391};
 data.week_history=[{start:'2026-10-05',end:'2026-10-11',hours:8.3,activities:6,elevation_m:0,load:434},{start:'2026-09-28',end:'2026-10-04',hours:6.2,activities:4,elevation_m:0,load:300}];
 data.week_assessments={hours:{tone:'critical',judgment:'Volume alto',reference:5.4,explanation:'Aumento documentato.',actions:['Alleggerisci.']},load:{tone:'critical',judgment:'Carico alto',reference:260,explanation:'Aumento documentato.'}};
 $('#refresh').click();await flush();$('#home-week').children[0].querySelector('button').click();await flush();
 const bars=[...$('#metric-detail .weekly-chart').querySelectorAll('rect[data-value]')];assert.equal(bars.at(-1).getAttribute('fill'),bars.at(-2).getAttribute('fill'));assert.ok(bars.every(bar=>bar.getAttribute('opacity')==='1'));
 assert.equal($('#metric-detail .weekly-chart').querySelectorAll('.reference-line').length,1);assert.match($('#metric-detail .weekly-chart').textContent,/2,5/);assert.match($('#metric-detail .weekly-chart').textContent,/7,5/);assert.match($('#metric-detail .chart-comparison').textContent,/5,4 ore/);
 $('#close-metric').click();$('#home-week').children[3].querySelector('button').click();await flush();const loads=[...$('#metric-detail .weekly-chart').querySelectorAll('rect[data-value]')];assert.equal(loads.at(-1).getAttribute('fill'),loads.at(-2).getAttribute('fill'));
});

test('health hides manual and context-only metrics and places range controls before plot',async t=>{
 const {w,$,flush}=await setup(t);const original=w.fetch;
 w.fetch=async(url,opts)=>{const response=await original(url,opts);if(!String(url).startsWith('/health/metrics'))return response;const value=await response.json();value.metrics.push(...['stress_score','daily_activity_load','daily_ctl_load','daily_atl_load','body_battery_min','body_battery_max','steps'].map(kind=>({kind,label:'Contesto '+kind,unit:'punti',points:[],latest:null})),{kind:'weight_kg',label:'Peso',unit:'kg',latest:{value:65,date:'2026-10-17',source:'intervals'},points:[{value:65,date:'2026-10-17',source:'intervals'}]});return {ok:true,json:async()=>value};};
 $('#refresh').click();await flush();assert.doesNotMatch($('#health-metrics').textContent,/Contesto/);assert.match($('#health-metrics').textContent,/Peso/);
 $('#health-metrics .metric-open').click();await flush();assert.equal($('#metric-detail').firstElementChild.className,'chart-range');assert.equal($('#metric-detail').children[1].classList.contains('chart-panel'),true);
});

test('one plan navigation entry contains both calendar and editable goals',async t=>{
 const {$,flush}=await setup(t);assert.equal($('nav [data-page="goal"]'),null);$('nav [data-page="calendar"]').click();await flush();
 assert.equal($('#calendar').hidden,false);assert.equal($('#goal').hidden,true);$('[data-plan-tab="goal"]').click();assert.equal($('#goal').hidden,false);assert.equal($('#plan-calendar-panel').hidden,true);assert.ok($('#goal-form'));$('[data-plan-tab="calendar"]').click();assert.equal($('#plan-calendar-panel').hidden,false);
});
