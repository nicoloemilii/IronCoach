'use strict';
// Synthetic DOM and mocked application API only; no live browser/provider access.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const source = fs.readFileSync(path.join(__dirname, '../manual-training.js'), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));

function gymFixture(changes = {}) {
  return { workout_id: 12, revision: 0, date: '2026-10-18', name: 'Forza personale',
    exercises: [{ name: 'Squat', sets: 2, reps: 10, work_seconds: null, weight_kg: 20, rest_seconds: 90, notes: 'Controlla il movimento' }, { name: 'Plank', sets: 1, reps: null, work_seconds: 30, weight_kg: null, rest_seconds: 30, notes: '' }],
    progress: { completed_sets: [], active_exercise: 0, timer: { remaining_seconds: 0, running: false, ends_at: null }, notes: '' },
    completed_count: 0, total_sets: 3, finished: false, ...changes };
}

async function setup(t, { plan = gymFixture(), fail = {} } = {}) {
  const errors = [], calls = [], virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM('<!doctype html><section id="training"><div id="manual-training"></div></section><p id="notice"></p>', { url: 'https://fixture.invalid/app#training', runScripts: 'outside-only', virtualConsole });
  const w = dom.window, $ = selector => w.document.querySelector(selector);
  let now = new Date('2026-10-18T10:00:00Z').getTime();
  const NativeDate = w.Date;
  w.Date = class extends NativeDate { constructor(...args) { super(...(args.length ? args : [now])); } static now() { return now; } };
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new w.Event('close')); };
  const state = { busy: false, plan: clone(plan), fail: { ...fail }, loads: 0, workouts: [{ id: 12, date: plan.date, name: plan.name, sport: 'strength', duration_min: 40, status: 'planned', payload: { exercises: plan.exercises } }] };
  const el = (tag, text, css) => { const node = w.document.createElement(tag); if (text != null) node.textContent = text; if (css) node.className = css; return node; };
  const button = (text, callback, css) => { const node = el('button', text, css); node.type = 'button'; if (state.busy) { node.disabled = true; node.dataset.busyDisabled = 'true'; } node.addEventListener('click', callback); return node; };
  const action = async work => {
    if (state.busy) return; state.busy = true;
    const controls = [...w.document.querySelectorAll('button, input, select, textarea')], original = controls.map(node => node.disabled); controls.forEach(node => node.disabled = true);
    try { await work(); } catch (error) { $('#notice').textContent = error.message; }
    finally { state.busy = false; controls.forEach((node, index) => node.disabled = original[index]); w.document.querySelectorAll('[data-busy-disabled]').forEach(node => { node.disabled = false; delete node.dataset.busyDisabled; }); }
  };
  const api = async (url, options = {}) => {
    const method = options.method || 'GET', value = options.body ? JSON.parse(options.body) : null; calls.push({ url, method, value });
    if (state.fail[`${method} ${url}`]) throw new Error(state.fail[`${method} ${url}`]);
    if (url === '/coach/workouts?limit=500') return clone(state.workouts);
    if (url === '/training/manual' && method === 'POST') {
      const row = { id: 20, ...value, status: 'planned', payload: { ...value } }; state.workouts.push(row); return { ...row, warnings: ['Budget del giorno superato.'] };
    }
    if (url === '/training/gym/12/progress' && method === 'GET') return clone(state.plan);
    if (url === '/training/gym/12/progress' && method === 'PUT') {
      if (value.expected_revision !== state.plan.revision) throw new Error('Scheda modificata in un’altra finestra.');
      const { expected_revision, ...progress } = value; state.plan.progress = clone(progress); state.plan.revision++;
      state.plan.completed_count = progress.completed_sets.length; state.plan.finished = state.plan.completed_count === state.plan.total_sets;
      return clone(state.plan);
    }
    throw new Error(`Unexpected call: ${method} ${url}`);
  };
  w.IronCoachUI = { el, button, api, action, number: value => String(value), load: async () => { state.loads++; w.document.dispatchEvent(new w.CustomEvent('iron:loaded')); } };
  const intervals = new Set(), nativeInterval = w.setInterval.bind(w), nativeClear = w.clearInterval.bind(w);
  w.setInterval = (...args) => { const id = nativeInterval(...args); intervals.add(id); return id; };
  w.clearInterval = id => { intervals.delete(id); nativeClear(id); };
  t.after(() => { intervals.forEach(nativeClear); dom.window.close(); });
  w.eval(source);
  const flush = async () => { for (let index = 0; index < 12; index++) await new Promise(resolve => setImmediate(resolve)); assert.deepEqual(errors, []); };
  await flush();
  const click = text => { const target = [...w.document.querySelectorAll('button')].find(node => node.textContent.trim() === text); assert.ok(target, `button ${text}`); target.click(); return target; };
  const fill = (name, value) => { const node = $(`[name="${name}"]`); assert.ok(node, name); node.value = value; node.dispatchEvent(new w.Event('input', { bubbles: true })); };
  const submit = () => $('#manual-training-form').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  return { w, $, calls, state, flush, click, fill, submit, advance: seconds => { now += seconds * 1000; }, intervals };
}

test('gym templates create editable drafts without writing or changing existing progress',async t=>{
  const { $,calls,flush,submit }=await setup(t);
  $('.gym-template-grid button').click();await flush();
  assert.equal($('[name=sport]').value,'strength');assert.equal($('[name=name]').value,'Gambe e core');
  assert.equal($('.gym-builder-list').children.length,3);
  assert.equal(calls.filter(call=>call.method==='POST'||call.method==='PUT').length,0);
  submit();await flush();const created=calls.find(call=>call.url==='/training/manual');
  assert.equal(created.value.exercises.length,3);assert.equal(created.value.exercises[2].work_seconds,25);
});

test('gym catalogue search and exercise reordering preserve exact exercise configuration',async t=>{
  const {w,$,fill,flush,click}=await setup(t);fill('sport','strength');$('[name=sport]').dispatchEvent(new w.Event('change'));
  fill('exercise_name','Esercizio personalizzato');
  fill('exercise_search','Dead bug');assert.equal($('.exercise-catalogue-grid').children.length,1);
  $('.exercise-catalogue-grid button').click();await flush();assert.equal($('.gym-builder-list').children.length,2);
  const row=$('.gym-builder-list').lastElementChild;assert.equal(row.querySelector('[name=exercise_name]').value,'Dead bug');
  [...row.querySelectorAll('button')].find(n=>n.textContent==='Sposta su').click();
  assert.equal($('.gym-builder-list').firstElementChild.querySelector('[name=exercise_name]').value,'Dead bug');
  assert.equal($('.gym-builder-list').firstElementChild.querySelector('[name=exercise_reps]').value,'8');
});

test('expanded catalogue combines muscle, equipment and alias search and paginates without modifying the draft',async t=>{
  const {w,$,fill,calls}=await setup(t);assert.match($('.exercise-catalogue summary').textContent,/201 esercizi/);
  $('[name=sport]').value='strength';$('[name=sport]').dispatchEvent(new w.Event('change'));
  const group=$('[name=exercise_group]'),equipment=$('[name=exercise_equipment]');assert.equal(group.options.length,12);assert.equal(equipment.options.length,13);
  assert.equal($('.exercise-catalogue-grid').children.length,24);
  const more=[...$('.exercise-catalogue').querySelectorAll('button')].find(b=>b.textContent==='Mostra altri 24 esercizi');more.click();assert.equal($('.exercise-catalogue-grid').children.length,48);
  group.value='Petto';group.dispatchEvent(new w.Event('change'));equipment.value='Manubri';equipment.dispatchEvent(new w.Event('change'));fill('exercise_search','DUMBBELL BENCH');
  assert.equal($('.exercise-catalogue-grid').children.length,1);assert.match($('.exercise-catalogue-grid').textContent,/Panca piana con manubri/);
  fill('exercise_search','nessuna corrispondenza');assert.match($('.exercise-catalogue-grid').textContent,/Azzera i filtri/);assert.equal(more.hidden,true);
  [...$('.exercise-catalogue-toolbar').querySelectorAll('button')][0].click();assert.equal(group.value,'');assert.equal(equipment.value,'');assert.equal($('.exercise-catalogue-grid').children.length,24);
  fill('exercise_search','mobilita caviglia');assert.match($('.exercise-catalogue-grid').textContent,/Mobilità caviglia/);assert.equal($('#manual-training-form').dataset.dirty,undefined);assert.equal(calls.some(c=>c.method!=='GET'),false);
});

test('catalogue inserts a timed exercise into the pristine row and saves editable fields without catalogue metadata',async t=>{
  const {w,$,fill,submit,flush,calls}=await setup(t);fill('sport','strength');$('[name=sport]').dispatchEvent(new w.Event('change'));fill('name','La mia scheda');
  fill('exercise_search','Side plank');$('.exercise-catalogue-grid button').click();assert.equal($('.gym-builder-list').children.length,1);assert.equal($('[name=exercise_mode]').value,'time');assert.equal($('[name=exercise_seconds]').value,'30');assert.match($('[name=exercise_notes]').value,/per lato/);
  fill('exercise_sets','4');fill('exercise_seconds','40');fill('exercise_rest','75');submit();await flush();
  const saved=calls.find(c=>c.method==='POST').value.exercises;assert.equal(saved.length,1);assert.equal(saved[0].name,'Side plank');assert.equal(saved[0].sets,4);assert.equal(saved[0].work_seconds,40);assert.equal(saved[0].reps,null);assert.equal(saved[0].weight_kg,null);assert.equal(saved[0].rest_seconds,75);assert.equal(saved[0].group,undefined);
  assert.equal($('[name=exercise_search]').value,'');assert.equal($('.exercise-catalogue-grid').children.length,24);
});

test('catalogue preserves edited blank rows and explains the 30 exercise limit without losing selections',async t=>{
  const {w,$,fill}=await setup(t);fill('sport','strength');$('[name=sport]').dispatchEvent(new w.Event('change'));fill('exercise_sets','5');fill('exercise_search','Dead bug');
  const add=$('.exercise-catalogue-grid button');add.click();assert.equal($('.gym-builder-list').children.length,2);assert.equal($('.gym-builder-list').firstElementChild.querySelector('[name=exercise_sets]').value,'5');
  for(let i=0;i<29;i++)add.click();assert.equal($('.gym-builder-list').children.length,30);assert.match($('.exercise-catalogue-feedback').textContent,/30 esercizi/);assert.equal($('.gym-builder-list').firstElementChild.querySelector('[name=exercise_sets]').value,'5');
});

test('manual builder captures all inputs before controls are disabled and persists to calendar', async t => {
  const { $, fill, submit, calls, state, flush } = await setup(t);
  fill('name', 'Fondo facile'); fill('duration_min', '50'); fill('purpose', 'Resistenza'); submit(); await flush();
  const saved = calls.find(call => call.url === '/training/manual'); assert.ok(saved);
  assert.equal(saved.value.name, 'Fondo facile'); assert.equal(saved.value.duration_min, 50); assert.equal(saved.value.date, '2026-10-18'); assert.equal(saved.value.target_rpe, 4);
  assert.equal(state.loads, 1); assert.match($('.manual-training-status').textContent, /Seduta salvata/); assert.match($('.manual-training-status').textContent, /Budget/);
});

test('invalid phase totals are explained without a write and removing a phase works', async t => {
  const { $, fill, submit, click, calls, flush } = await setup(t);
  fill('name', 'Corsa facile'); click('+ Aggiungi fase'); fill('phase_name', 'Riscaldamento'); submit(); await flush();
  assert.match($('.manual-training-status').textContent, /somma dei minuti/); assert.equal(calls.some(call => call.method === 'POST'), false);
  click('Rimuovi fase'); assert.equal($('.manual-phase-list').children.length, 0);
});

test('gym builder submits reps and time alternatives with nullable weight', async t => {
  const { w, $, fill, submit, click, calls, flush } = await setup(t);
  $('[name="sport"]').value = 'strength'; $('[name="sport"]').dispatchEvent(new w.Event('change'));
  assert.equal($('.manual-gym').hidden, false); assert.equal($('.manual-phases').disabled, true);
  fill('name', 'Forza gambe'); fill('exercise_name', 'Squat'); click('+ Aggiungi esercizio');
  const second = $('.gym-builder-list').children[1]; second.querySelector('[name="exercise_name"]').value = 'Plank'; const mode = second.querySelector('[name="exercise_mode"]'); mode.value = 'time'; mode.dispatchEvent(new w.Event('change'));
  submit(); await flush();
  const saved = calls.find(call => call.method === 'POST'); assert.equal(saved.value.exercises.length, 2);
  assert.equal(saved.value.exercises[0].reps, 10); assert.equal(saved.value.exercises[0].weight_kg, null);
  assert.equal(saved.value.exercises[1].reps, null); assert.equal(saved.value.exercises[1].work_seconds, 30);
});

test('saving a bike plan retains duration and effort defaults for a subsequent gym plan', async t => {
  const { w, $, fill, submit, calls, flush } = await setup(t);
  fill('name', 'Prima bici'); fill('duration_min', '50'); fill('target_rpe', '6'); submit(); await flush();
  assert.equal($('[name="duration_min"]').value, '45');
  assert.equal($('[name="target_rpe"]').value, '4');
  $('[name="sport"]').value = 'strength'; $('[name="sport"]').dispatchEvent(new w.Event('change'));
  fill('name', 'Poi palestra'); fill('exercise_name', 'Squat'); submit(); await flush();
  const saves = calls.filter(call => call.url === '/training/manual' && call.method === 'POST');
  assert.equal(saves.length, 2);
  assert.equal(saves[1].value.sport, 'strength'); assert.equal(saves[1].value.duration_min, 45); assert.equal(saves[1].value.target_rpe, 4);
  assert.equal(saves[1].value.exercises[0].sets, 3); assert.equal(saves[1].value.exercises[0].reps, 10);
});

test('server save failure preserves the complete manual draft and offers its reason', async t => {
  const { $, fill, submit, state, flush } = await setup(t, { fail: { 'POST /training/manual': 'Server non disponibile' } });
  fill('name', 'Bozza da conservare'); submit(); await flush();
  assert.equal($('[name="name"]').value, 'Bozza da conservare'); assert.match($('.manual-training-status').textContent, /Server non disponibile/); assert.equal(state.loads, 0);
});

test('gym checklist persists sets and timer once without creating an activity', async t => {
  const { $, click, calls, flush, advance } = await setup(t);
  $('.gym-library .list-row').click(); await flush(); assert.equal($('#gym-session-dialog').open, true);
  const check = $('.gym-set-check input'); check.click(); await flush();
  const writes = calls.filter(call => call.method === 'PUT'); assert.equal(writes.length, 1); assert.equal(writes[0].value.completed_sets.length, 1); assert.equal(writes[0].value.timer.running, true);
  advance(10); await new Promise(resolve => setTimeout(resolve, 275)); assert.equal($('.gym-timer-display').textContent, '01:20');
  assert.equal(calls.filter(call => call.method === 'PUT').length, 1);
  click('Pausa'); await flush(); assert.equal($('.gym-timer-display').textContent, '01:20');
  advance(100); click('Riprendi'); await flush(); assert.equal($('.gym-timer-display').textContent, '01:20');
  click('Azzera'); await flush(); assert.equal($('.gym-timer-display').textContent, '00:00');
  assert.equal(calls.some(call => call.url.includes('/activities') && call.method !== 'GET'), false);
});

test('future gym programs can be viewed but progression controls stay disabled after action finishes', async t => {
  const { $, flush, calls } = await setup(t, { plan: gymFixture({ date: '2026-10-19' }) });
  $('.gym-library .list-row').click(); await flush();
  assert.equal($('.gym-set-check input').disabled, true); assert.equal($('.gym-timer .primary').disabled, true);
  assert.match($('.gym-session-content').textContent, /giorno futuro/); assert.equal(calls.some(call => call.method === 'PUT'), false);
});

test('failed progress persistence reverts checks and offers explicit reload', async t => {
  const { $, flush, state } = await setup(t);
  $('.gym-library .list-row').click(); await flush(); state.fail['PUT /training/gym/12/progress'] = 'Scheda modificata in un’altra finestra.';
  $('.gym-set-check input').click(); await flush();
  assert.equal($('.gym-set-check input').checked, false); assert.match($('.gym-session-content [role="alert"]').textContent, /Ricarica progresso/);
});

test('GymRestClock handles pause, resume, resets and expired background deadlines', async t => {
  const { w } = await setup(t); let now = 100000;
  const clock = new w.IronManualTraining.GymRestClock({}, () => now);
  clock.start(90); now += 25000; assert.equal(clock.seconds(), 65); clock.pause(); now += 300000; assert.equal(clock.seconds(), 65);
  clock.resume(); now += 65000; assert.equal(clock.seconds(), 0); assert.equal(clock.snapshot().running, false);
  clock.reset(30); assert.equal(clock.snapshot().remaining_seconds, 30); assert.equal(clock.snapshot().ends_at, null);
  clock.restore({ remaining_seconds: 90, running: true, ends_at: new Date(now - 1).toISOString() }); assert.equal(clock.seconds(), 0);
});

test('mount is idempotent and a closed gym dialog releases its clock interval', async t => {
  const { w, $, flush, intervals } = await setup(t);
  w.IronManualTraining.mount($('#manual-training')); assert.equal(w.document.querySelectorAll('#manual-training-form').length, 1);
  $('.gym-library .list-row').click(); await flush(); assert.equal(intervals.size, 1);
  $('#gym-session-dialog').close(); assert.equal(intervals.size, 0);
});
