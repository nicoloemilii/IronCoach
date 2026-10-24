'use strict';
// Real FastAPI HTTP traffic, local source evaluated in a synthetic DOM.
// No browser automation, external resources, providers or personal dataset.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('../../web/tests/node_modules/jsdom');
const base = 'http://127.0.0.1:8777';
const project = path.resolve(__dirname, '../..');
const nativeFetch = global.fetch;
const calls = [], errors = [], pending = new Set();
const virtualConsole = new VirtualConsole();
virtualConsole.on('jsdomError', error => errors.push(error.stack || error.message));
const dom = new JSDOM(fs.readFileSync(path.join(project, 'web/index.html'), 'utf8'), {
  url: `${base}/app#training`, runScripts: 'outside-only', virtualConsole,
});
const w = dom.window, $ = selector => w.document.querySelector(selector);
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new w.Event('close')); };
w.addEventListener('error', event => errors.push(event.error?.stack || event.message));
w.fetch = (relative, options = {}) => {
  const url = new URL(relative, base);
  assert.equal(url.origin, base, 'Only the isolated FastAPI origin is permitted.');
  assert.ok(!url.pathname.startsWith('/sync'), 'This smoke test never requests synchronization.');
  const work = nativeFetch(url, options).then(response => {
    calls.push({ method: options.method || 'GET', path: url.pathname + url.search, status: response.status }); return response;
  }).finally(() => pending.delete(work));
  pending.add(work); return work;
};
async function api(relative, method = 'GET', data = null) {
  const response = await nativeFetch(base + relative, { method, headers: { 'Content-Type': 'application/json' }, ...(data == null ? {} : { body: JSON.stringify(data) }) });
  const value = await response.json(); assert.equal(response.ok, true, JSON.stringify({ relative, status: response.status, value })); return value;
}
async function settle() {
  const until = Date.now() + 30000;
  for (;;) {
    if (Date.now() > until) throw new Error('Synthetic UI did not become idle.');
    await new Promise(resolve => setTimeout(resolve, 25));
    if (!pending.size && $('#refresh').textContent !== 'Caricamento…') {
      await new Promise(resolve => setTimeout(resolve, 50));
      if (!pending.size) break;
    }
  }
  assert.deepEqual(errors, [], 'No unhandled DOM errors.');
  assert.equal($('#notice').classList.contains('error'), false, $('#notice').textContent);
}
function fill(selector, value) {
  const input = $(selector); assert.ok(input, selector); input.value = value; input.dispatchEvent(new w.Event('input', { bubbles: true }));
}
function button(text, root = w.document) {
  const node = [...root.querySelectorAll('button')].find(item => item.textContent.trim() === text);
  assert.ok(node, `Button: ${text}`); assert.equal(node.disabled, false, `Button enabled: ${text}`); node.click(); return node;
}
async function submit() { $('#manual-training-form').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true })); await settle(); }
(async () => {
  try {
    assert.equal((await api('/activities')).length, 0, 'Fresh isolated database.');
    const settings = await api('/planning/profile');
    await api('/planning/profile', 'PUT', { ...settings.profile, name: 'Synthetic smoke athlete', weekly_minutes: 1200, availability: [180, 180, 180, 180, 180, 180, 180] });
    for (const file of ['metric-status.js', 'app.js', 'usability.js', 'progress.js', 'wellness.js', 'manual-training.js']) w.eval(fs.readFileSync(path.join(project, 'web', file), 'utf8'));
    await settle();
    assert.equal($('#training').hidden, false);
    const today = $('#manual-training-form [name="date"]').value;
    fill('#manual-training-form [name="name"]', 'Smoke bike with custom phases');
    fill('#manual-training-form [name="duration_min"]', '45');
    fill('#manual-training-form [name="purpose"]', 'Synthetic aerobic planning check');
    for (const [name, minutes, effort] of [['Warm', 10, 2], ['Main', 30, 4], ['Cool', 5, 1]]) {
      button('+ Aggiungi fase'); const row = $('.manual-phase-list').lastElementChild;
      row.querySelector('[name="phase_name"]').value = name; row.querySelector('[name="phase_duration"]').value = minutes; row.querySelector('[name="phase_rpe"]').value = effort;
    }
    await submit();
    let workouts = await api('/coach/workouts?limit=500');
    const bike = workouts.find(row => row.name === 'Smoke bike with custom phases'); assert.ok(bike);
    assert.equal(bike.status, 'planned'); assert.deepEqual(bike.payload.steps.map(step => step.duration_min), [10, 30, 5]);
    const sport = $('#manual-training-form [name="sport"]'); sport.value = 'strength'; sport.dispatchEvent(new w.Event('change'));
    fill('#manual-training-form [name="name"]', 'Smoke guided gym');
    fill('#manual-training-form [name="duration_min"]', '30');
    fill('.gym-builder-list [name="exercise_name"]', 'Squat');
    fill('.gym-builder-list [name="exercise_sets"]', '2');
    fill('.gym-builder-list [name="exercise_reps"]', '8');
    fill('.gym-builder-list [name="exercise_weight"]', '15');
    fill('.gym-builder-list [name="exercise_rest"]', '90');
    button('+ Aggiungi esercizio'); const second = $('.gym-builder-list').lastElementChild;
    second.querySelector('[name="exercise_name"]').value = 'Plank'; second.querySelector('[name="exercise_sets"]').value = '1';
    const mode = second.querySelector('[name="exercise_mode"]'); mode.value = 'time'; mode.dispatchEvent(new w.Event('change'));
    second.querySelector('[name="exercise_seconds"]').value = '35';
    await submit();
    workouts = await api('/coach/workouts?limit=500'); const gym = workouts.find(row => row.name === 'Smoke guided gym');
    assert.ok(gym, JSON.stringify({status:$('.manual-training-status').textContent,notice:$('#notice').textContent,form:$('#manual-training-form').checkValidity(),invalid:[...$('#manual-training-form').querySelectorAll(':invalid')].map(input=>({name:input.name,value:input.value,message:input.validationMessage})),posts:calls.filter(call=>call.method==='POST'),names:workouts.map(row=>row.name)}));
    assert.equal(gym.payload.exercises.length, 2); assert.equal(gym.payload.exercises[0].reps, 8); assert.equal(gym.payload.exercises[1].work_seconds, 35);
    await w.IronCoachUI.action(() => w.IronManualTraining.openGym(gym.id)); await settle();
    assert.equal($('#gym-session-dialog').open, true);
    $('.gym-set-check input').click(); await settle();
    let progress = await api(`/training/gym/${gym.id}/progress`);
    assert.equal(progress.completed_count, 1); assert.equal(progress.progress.timer.running, true);
    assert.ok(progress.progress.timer.remaining_seconds > 0 && progress.progress.timer.remaining_seconds <= 90);
    button('Pausa', $('#gym-session-dialog')); await settle();
    progress = await api(`/training/gym/${gym.id}/progress`); assert.equal(progress.progress.timer.running, false);
    const remaining = progress.progress.timer.remaining_seconds;
    $('#gym-session-dialog').close();
    await w.IronCoachUI.action(() => w.IronManualTraining.openGym(gym.id)); await settle();
    assert.equal($('.gym-set-check input').checked, true);
    assert.equal($('.gym-timer-display').textContent, `${String(Math.floor(remaining / 60)).padStart(2, '0')}:${String(remaining % 60).padStart(2, '0')}`);
    button('Riprendi', $('#gym-session-dialog')); await settle();
    assert.equal((await api(`/training/gym/${gym.id}/progress`)).progress.timer.running, true);
    button('Azzera', $('#gym-session-dialog')); await settle();
    assert.equal((await api(`/training/gym/${gym.id}/progress`)).progress.timer.remaining_seconds, 0);
    $('#gym-session-dialog').close();
    // Real planner integration retains both explicit manual plans, even on first generation.
    const week = await api('/planning/week', 'POST', { start: today });
    const saved = week.days.flatMap(day => day.sessions);
    assert.equal(saved.find(row => row.id === bike.id).payload.source, 'manual');
    assert.equal(saved.find(row => row.id === gym.id).payload.exercises.length, 2);
    assert.equal((await api('/activities')).length, 0, 'Plans/checklist do not fabricate activities.');
    assert.equal(calls.filter(call => call.status >= 400).length, 0, JSON.stringify(calls.filter(call => call.status >= 400)));
    const result = { passed: true, date: today, source_modules: 6, frontend_requests: calls.length,
      bike_custom_phases: bike.payload.steps.length, gym_exercises: gym.payload.exercises.length,
      gym_completed_sets: progress.completed_count, gym_timer_pause_reload_resume_reset: true,
      manual_sessions_preserved_after_generation: 2, activities_created: 0,
      dom_errors: errors.length, http_errors: calls.filter(call => call.status >= 400).length };
    fs.writeFileSync(path.join(__dirname, 'result.json'), JSON.stringify(result, null, 2)); console.log(JSON.stringify(result));
  } finally { dom.window.close(); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
