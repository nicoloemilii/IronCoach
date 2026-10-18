'use strict';
// Opt-in live DOM smoke for the disposable synthetic server only. This is not a
// browser or visual test and is deliberately excluded from the normal npm suite.
const BASE = 'http://127.0.0.1:8766';
if (process.env.IRON_QA_LIVE !== '1' || (process.argv[2] && process.argv[2] !== BASE)) {
  console.error(`Refusing live smoke. Require IRON_QA_LIVE=1 and base URL exactly ${BASE}.`);
  process.exit(2);
}
const assert = require('node:assert/strict');
const { JSDOM, VirtualConsole, requestInterceptor } = require('jsdom');
const nativeFetch = globalThis.fetch;
const requests = []; const failures = []; const jsErrors = []; const completed = [];
let pending = 0; let lastRequest = 0; let dom;
const guardedURL = input => {
  const url = new URL(String(input), `${BASE}/`);
  assert.equal(url.origin, BASE, 'All requests must stay on the disposable synthetic server.');
  assert.equal(url.username + url.password, '', 'URL credentials are not accepted.');
  return url;
};
async function request(input, options = {}) {
  const url = guardedURL(input); const record = {method:options.method || 'GET', path:url.pathname + url.search};
  requests.push(record); pending++; lastRequest = Date.now();
  try {
    const response = await nativeFetch(url, {...options, headers:{Origin:BASE, ...options.headers}});
    record.status = response.status;
    if (!response.ok) failures.push(`${record.method} ${record.path}: HTTP ${response.status}`);
    return response;
  } catch (error) { failures.push(`${record.method} ${record.path}: ${error.message}`); throw error; }
  finally { pending--; lastRequest = Date.now(); }
}
async function read(path) { const response = await request(path); assert.ok(response.ok, `GET ${path}`); return response.json(); }
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(predicate, description, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error(`Timed out: ${description}`);
    await sleep(25);
  }
}
async function idle() { await until(() => pending === 0 && Date.now() - lastRequest > 100, 'local API requests to settle'); }

async function run() {
  // Require named fixture records before permitting any UI mutation.
  const [goals, workouts, daily] = await Promise.all([read('/goals'), read('/coach/workouts?limit=200'), read('/coach/today')]);
  const goal = goals.find(value => value.name === 'Synthetic QA trail');
  const past = workouts.find(value => value.name === 'QA review past' && value.date < daily.date);
  assert.ok(goal && past, 'Synthetic QA fixtures missing; refusing to mutate this server.');
  const future = workouts.filter(value => value.status === 'planned' && value.sport === 'running' && value.date > daily.date && value.duration_min > 15).sort((a,b) => a.date.localeCompare(b.date))[0];
  assert.ok(future, 'Seed a future planned running workout longer than 15 minutes.');
  const virtualConsole = new VirtualConsole(); virtualConsole.on('jsdomError', error => jsErrors.push(error.message));
  dom = await JSDOM.fromURL(`${BASE}/app`, {
    runScripts:'dangerously', resources:{interceptors:[requestInterceptor(async resource => {
      if (new URL(resource.url).origin !== BASE) {
        // jsdom29 replaced ResourceLoader with interceptors. Satisfy the optional
        // external font import locally without making any outbound request.
        if (new URL(resource.url).hostname === 'fonts.googleapis.com') return new Response('', {headers:{'Content-Type':'text/css'}});
        throw new Error(`Blocked nonlocal resource: ${resource.url}`);
      }
      return request(resource.url, {method:resource.method, headers:Object.fromEntries(resource.headers)});
    })]}, pretendToBeVisual:true, virtualConsole,
    beforeParse(window) {
      window.fetch = async (input, options = {}) => {
        let body = options.body;
        if (body instanceof window.Blob) {
          body = await new Promise((resolve,reject) => {
            const reader = new window.FileReader();
            reader.onload = () => resolve(Buffer.from(new Uint8Array(reader.result)));
            reader.onerror = () => reject(reader.error); reader.readAsArrayBuffer(body);
          });
        }
        return request(input, {...options, body});
      };
      window.HTMLDialogElement.prototype.showModal = function() { this.setAttribute('open',''); };
      window.HTMLDialogElement.prototype.close = function() { this.removeAttribute('open'); this.dispatchEvent(new window.Event('close')); };
      window.addEventListener('error', event => jsErrors.push(event.error?.message || event.message));
    }
  });
  const window = dom.window; const $ = selector => window.document.querySelector(selector);
  const buttons = (scope, text) => [...scope.querySelectorAll('button')].find(node => node.textContent.trim() === text);
  async function click(node, description) {
    await until(() => node && node.isConnected && !node.disabled, description || 'enabled control');
    node.click(); await idle();
  }
  function input(node, value) { assert.ok(node, 'Input exists'); node.value = value; node.dispatchEvent(new window.Event('input',{bubbles:true})); }
  async function submit(form) { assert.ok(form, 'Form exists'); form.dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})); await idle(); }
  async function navigate(id) { await click($(`nav [data-page="${id}"]`), `navigate to ${id}`); assert.equal($('#' + id).hidden, false); }
  const rowForPast = () => [...window.document.querySelectorAll('.reflection-session')].find(row => row.querySelector('h3')?.textContent === past.name);
  await until(() => window.IronCoachUI && window.ironViewSnapshot && $('#metrics')?.children.length === 7, 'seven Home condition cards'); await idle();
  assert.equal($('#metrics').querySelectorAll('.metric[data-tone] .metric-status').length, 7);
  const homeTones = new Set([...$('#metrics').children].map(card => card.dataset.tone));
  for (const tone of ['good','caution','critical','neutral']) assert.ok(homeTones.has(tone), `Seeded Home evidence should include a ${tone} state.`);
  completed.push('Home: seven condition cards with semantic labels');

  await navigate('health');
  await until(() => $('#recovery-trends .trend-card'), 'personal recovery trends');
  assert.ok($('#recovery-trends .metric-status'));
  completed.push('Salute: personal recovery trends loaded');

  await navigate('coach'); await until(() => rowForPast(), 'seeded unresolved past workout');
  // A previously interrupted run may have completed only its first transition.
  const previousUndo = buttons(rowForPast(), 'Annulla questa conferma');
  if (previousUndo) { await click(previousUndo, 'clear a previous synthetic rest confirmation'); await until(() => buttons(rowForPast(), 'Ho scelto il riposo'), 'past fixture ready for review'); }
  await click(buttons(rowForPast(), 'Ho scelto il riposo'), 'confirm explicit rest');
  await until(() => rowForPast()?.textContent.includes('Riposo confermato'), 'rest confirmation rendered');
  await click(buttons(rowForPast(), 'Annulla questa conferma'), 'undo rest confirmation');
  await until(() => buttons(rowForPast(), 'Ho scelto il riposo'), 'past workout restored to unresolved');
  completed.push('Coach: explicit rest confirmed and undone');

  await navigate('calendar'); await until(() => !$('#active-race-context').hidden, 'active race context');
  assert.match($('#active-race-context').textContent, /Synthetic QA trail/);
  const date = new Date(`${future.date}T12:00:00Z`); date.setUTCDate(date.getUTCDate() - (date.getUTCDay() + 6) % 7);
  const futureWeek = date.toISOString().slice(0,10);
  for (let count = 0; window.IronCoachUI.context().weekStart !== futureWeek && count < 8; count++) await click($('#week-next'), 'next calendar week');
  assert.equal(window.IronCoachUI.context().weekStart, futureWeek);
  const cell = [...window.document.querySelectorAll('.calendar-day')].find(day => [...day.querySelectorAll('.calendar-session small')].some(detail => detail.textContent.startsWith(future.date)) && day.textContent.includes('Corsa'));
  assert.ok(cell, 'Future running workout is visible in the calendar.');
  await click(buttons(cell, 'Sposta / accorcia / salta'), 'edit future running workout');
  const edit = $('#session-edit-form'); assert.equal(Number(edit.dataset.id), future.id);
  edit.elements.action.value = 'shorten'; edit.elements.action.dispatchEvent(new window.Event('change',{bubbles:true}));
  const shorter = Math.max(15, future.duration_min - 10);
  input(edit.elements.duration_min, String(shorter)); input(edit.elements.reason, 'Synthetic QA live smoke: confirmed shorter session.');
  await submit(edit); await until(() => $('#session-change-preview'), 'pure change preview');
  assert.equal((await read('/coach/workouts?limit=200')).find(value => value.id === future.id).duration_min, future.duration_min, 'Preview must not mutate duration.');
  await click(buttons($('#session-change-preview'), 'Conferma questa modifica'), 'confirm previewed change');
  await until(() => !$('#session-edit-dialog').open, 'edit dialog closed');
  assert.equal((await read('/coach/workouts?limit=200')).find(value => value.id === future.id).duration_min, shorter);
  completed.push('Calendario: active goal, pure preview and confirmed shortening');

  await navigate('goal');
  const goalCard = [...window.document.querySelectorAll('.goal-item')].find(card => card.querySelector('h3')?.textContent === goal.name);
  await until(() => goalCard?.querySelector('[data-preparation]'), 'operational preparation action');
  await click(goalCard.querySelector('[data-preparation]'), 'open operational preparation');
  await until(() => $('#goal-preparation-form textarea'), 'preparation notes');
  const notes = 'Synthetic QA live smoke: equipment and pacing tried in training.';
  input($('#goal-preparation-form textarea'), notes);
  await submit($('#goal-preparation-form'));
  await until(() => $('#goal-preparation-form textarea')?.value === notes && !$('#goal-preparation-form').dataset.dirty, 'saved operational notes reloaded');
  await click(buttons($('#goal-preparation-dialog'), 'Chiudi'), 'close operational preparation');
  completed.push('Obiettivi: operational preparation notes saved and reloaded');

  await navigate('connections');
  const exportLink = $('#personal-backup a[download]'); assert.ok(exportLink);
  const exported = await request(exportLink.getAttribute('href')); assert.ok(exported.ok);
  const content = Buffer.from(await exported.arrayBuffer()); assert.ok(content.length > 0);
  const file = new window.File([content], 'synthetic-qa-backup.json', {type:'application/json'});
  Object.defineProperty($('#backup-file'), 'files', {configurable:true,value:[file]});
  $('#backup-file').dispatchEvent(new window.Event('change',{bubbles:true})); await idle();
  await until(() => $('#restore-form'), 'backup preview and explicit confirmation');
  assert.equal($('#restore-form button[type="submit"]').disabled, true);
  input($('#restore-form [name="confirmation"]'), 'RIPRISTINA');
  assert.equal($('#restore-form button[type="submit"]').disabled, false);
  await submit($('#restore-form'));
  await until(() => $('#backup-preview').textContent.includes('Ripristino completato'), 'completed synthetic restore');
  completed.push('Connessioni: backup exported, file previewed, explicit restore completed');
  await idle();
  assert.deepEqual(failures, [], 'No local API request may fail.');
  assert.deepEqual(jsErrors, [], 'No JavaScript or asset-loading errors.');
  console.log(JSON.stringify({scope:'Synthetic local JSDOM integration; no browser or visual verification',base:BASE,completed,httpRequests:requests.length,httpFailures:failures.length,jsErrors:jsErrors.length},null,2));
}
run().catch(error => {
  console.error(JSON.stringify({error:error.message,completed,httpFailures:failures,jsErrors,lastRequests:requests.slice(-5),notice:dom?.window.document.querySelector('#notice')?.textContent},null,2));
  process.exitCode = 1;
}).finally(() => dom?.window.close());
