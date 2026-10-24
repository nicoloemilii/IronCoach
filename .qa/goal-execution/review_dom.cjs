'use strict';
// Full production HTML/assets + real HTTP, against the named synthetic server.
// This checks DOM behavior. It does not run a browser or verify visual layout.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const {createRequire} = require('node:module');
const ROOT = path.resolve(__dirname, '../..');
const {JSDOM, VirtualConsole, requestInterceptor} = createRequire(path.join(ROOT, 'web/tests/package.json'))('jsdom');
const BASE = 'http://127.0.0.1:8001';
const OUTPUT = path.join(__dirname, 'review_dom.json');
const requests = []; const httpErrors = []; const domErrors = []; const blockedExternal = []; const loadedAssets = {};
const completed = []; let dom; let activeRequests = 0; let lastRequest = 0;
const sourcePaths = ['web/index.html','web/style.css','web/app.js','web/progress.js','web/workout-matching.js'];
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const sourceHashes = () => Object.fromEntries(sourcePaths.map(name => [name, hash(fs.readFileSync(path.join(ROOT, name)))]));
const sourceBefore = sourceHashes();
const result = {scope:'Production HTML and JavaScript in JSDOM with actual synthetic localhost API; not browser or visual QA',
  base:BASE, synthetic_only:true, personal_database_opened:false, visual_verification:false, browser_used:false,
  environment_stubs:['HTMLDialogElement.showModal/close update open attribute and close event; JSDOM has no native modal rendering'],
  source_sha256:sourceBefore, completed};
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(predicate, label, milliseconds = 15000) {
  const end = Date.now() + milliseconds;
  while (!predicate()) { if (Date.now() > end) throw new Error(`Timed out: ${label}`); await sleep(25); }
}
async function idle() { await until(() => activeRequests === 0 && Date.now() - lastRequest > 150, 'HTTP activity settled'); }
function localURL(input) {
  const value = new URL(typeof input === 'string' ? input : input.url, `${BASE}/`);
  assert.equal(value.origin, BASE, 'All HTTP requests stay on the dedicated synthetic server.');
  assert.equal(value.username + value.password, '', 'No URL credentials accepted.'); return value;
}
async function request(input, options = {}) {
  const url = localURL(input); const record = {method:options.method || 'GET',path:url.pathname+url.search};
  if (options.body && typeof options.body === 'string') {
    const payload = JSON.parse(options.body);
    record.payload = {...payload};
    if (record.payload.activity_match_token) { record.match_token_present = true; record.payload.activity_match_token = '[present]'; }
  }
  requests.push(record); activeRequests++; lastRequest = Date.now();
  try {
    const response = await fetch(url, {...options, headers:{Origin:BASE, ...Object.fromEntries(new Headers(options.headers || {}))}});
    record.status = response.status;
    if (!response.ok) httpErrors.push(`${record.method} ${record.path}: HTTP ${response.status}`);
    if (url.pathname.startsWith('/web/') || url.pathname === '/app') loadedAssets[url.pathname] = hash(Buffer.from(await response.clone().arrayBuffer()));
    return response;
  } catch (error) { httpErrors.push(`${record.method} ${record.path}: ${error.message}`); throw error; }
  finally { activeRequests--; lastRequest = Date.now(); }
}
async function read(url) { const response = await request(url); assert.ok(response.ok, url); return response.json(); }
function mutations() { return requests.filter(item => !['GET','HEAD','OPTIONS'].includes(item.method)); }
async function run() {
  const before = await read('/_qa/state');
  assert.equal(before.synthetic,true);assert.deepEqual(before.feedbacks.map(row=>row.workout_id),[3], 'One seeded invalid feedback is required; refuse to rerun completed mutations.');
  const names = new Map(before.week.sessions.map(row=>[row.id,row.name]));
  assert.equal(names.get(1),'Bici facile · da confermare'); assert.equal(names.get(2),'Palestra · durata sconosciuta');
  assert.equal(before.week.sessions.find(row=>row.id===3).status,'unconfirmed');
  result.before = {counts:before.week.counts,totals:before.week.totals};
  const console = new VirtualConsole(); console.on('jsdomError', error => domErrors.push(error.message));
  dom = await JSDOM.fromURL(`${BASE}/app#calendar`, {
    runScripts:'dangerously', pretendToBeVisual:true, virtualConsole:console,
    resources:{interceptors:[requestInterceptor(async resource => {
      if (new URL(resource.url).origin !== BASE) { blockedExternal.push(resource.url); throw new Error(`Nonlocal resource blocked: ${resource.url}`); }
      return request(resource.url,{method:resource.method,headers:Object.fromEntries(resource.headers)});
    })]},
    beforeParse(window) {
      window.Headers = Headers; window.Request = Request; window.Response = Response;
      window.fetch = (input, options = {}) => request(input, options);
      window.HTMLDialogElement.prototype.showModal = function(){this.setAttribute('open','');};
      window.HTMLDialogElement.prototype.close = function(){this.removeAttribute('open');this.dispatchEvent(new window.Event('close'));};
      window.addEventListener('error', event => domErrors.push(event.error?.message || event.message));
    }
  });
  const window = dom.window; const $ = selector => window.document.querySelector(selector);
  const findButton = (root,text) => [...root.querySelectorAll('button')].find(button=>button.textContent.trim()===text);
  const row = id => [...window.document.querySelectorAll('.reflection-session')].find(element=>element.querySelector('h3')?.textContent===names.get(id));
  async function click(control,label) { assert.ok(control, label); await until(()=>control.isConnected && !control.disabled,label);control.click();await idle(); }
  function input(control,value) { assert.ok(control);control.value=value;control.dispatchEvent(new window.Event('input',{bubbles:true})); }
  async function submit(form) { assert.ok(form);assert.equal(form.querySelector('button[type="submit"]').disabled,false);form.dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await idle(); }
  await until(()=>window.IronCoachUI && window.IronWorkoutMatching && row(1) && $('#calendar').hidden===false,'production calendar and weekly rows');await idle();
  assert.equal($('#week-reflection details').open,false);assert.match($('#reflection-summary').textContent,/3 sedute da confermare/);
  assert.ok(findButton(row(3),'Apri seduta e conferma'),'Legacy invalid feedback has a repair path even though rest/skip resolution is blocked.');
  assert.equal($('#coach #week-reflection'),null);assert.equal(mutations().length,0);
  $('#week-reflection summary').click();assert.equal($('#week-reflection details').open,true);
  completed.push('Calendar loads actual weekly API into a collapsed review, outside Coach, without mutations.');

  await click(findButton(row(1),'Apri seduta e conferma'),'open cycling from weekly review');
  await until(()=>$('#workout-matching [data-match-activity="1"] button'),'cycling proposal');await idle();
  assert.equal($('#feedback-activity').value,'');assert.equal($('#workout-dialog').open,true);
  const cyclingCard=$('#workout-matching [data-match-activity="1"]');
  assert.match(cyclingCard.textContent,/Rulli · attività sintetica/);assert.match(cyclingCard.textContent,/75 min/);assert.match(cyclingCard.textContent,/Differenza durata: \+15 min/);
  await click(cyclingCard.querySelector('button'),'choose explicit VirtualRide');assert.equal($('#feedback-activity').value,'1');assert.equal(mutations().length,0,'Choosing a proposal must only change a local draft.');
  input($('#feedback-form [name="rpe"]'),'4');input($('#feedback-form [name="notes"]'),'Prova DOM sintetica: confermo Rulli VirtualRide.');
  await submit($('#feedback-form'));
  const afterCycling=await read('/_qa/state');const savedCycling=afterCycling.feedbacks.find(value=>value.workout_id===1);
  assert.equal(savedCycling.activity_id,1);assert.equal(savedCycling.content.actual.duration_min,75);
  assert.equal(afterCycling.week.sessions.find(value=>value.id===1).status,'modified');assert.equal(afterCycling.week.sessions.find(value=>value.id===1).actual_minutes,75);
  assert.equal(mutations().length,1);assert.equal(mutations()[0].path,'/experience/workouts/1/feedback');assert.equal(mutations()[0].match_token_present,true);
  assert.match($('#feedback-result').textContent,/60 \/ 75 minuti/);assert.match($('#reflection-summary').textContent,/1 esecuzion[ei] confermat[ae] · 2 sedute da confermare/);
  completed.push('VirtualRide proposal remains unselected until choice; no write until explicit feedback save; measured 75/60 minutes render and canonical state is modified.');

  await click($('#close-dialog'),'back to calendar');
  await click(findButton(row(2),'Apri seduta e conferma'),'open gym from weekly review');
  await until(()=>$('#workout-matching [data-match-activity="2"] button'),'gym proposal');await idle();
  assert.equal($('#feedback-activity').value,'');const gymCard=$('#workout-matching [data-match-activity="2"]');
  assert.match(gymCard.textContent,/Palestra · durata non registrata/);assert.match(gymCard.textContent,/— min/);assert.doesNotMatch(gymCard.textContent,/\b0 min/);
  await click(gymCard.querySelector('button'),'choose unknown-duration gym record');assert.equal(mutations().length,1);
  input($('#feedback-form [name="rpe"]'),'4');input($('#feedback-form [name="notes"]'),'Prova DOM sintetica: attività confermata, durata non disponibile.');
  await submit($('#feedback-form'));
  const after=await read('/_qa/state');const gym=after.feedbacks.find(value=>value.workout_id===2);
  assert.equal(gym.activity_id,2);assert.equal(gym.content.actual.duration_min,null);assert.equal(gym.content.evaluation.completion,null);
  const gymSession=after.week.sessions.find(value=>value.id===2);assert.equal(gymSession.status,'completed');assert.equal(gymSession.actual_minutes,null);assert.equal(gymSession.completion_percent,null);
  assert.match($('#feedback-result').textContent,/45 \/ — minuti · Completamento —%/);assert.doesNotMatch($('#feedback-result').textContent,/45 \/ 0 minuti|Completamento 0%/);
  assert.match($('#feedback-result').textContent,/durata non disponibile/i);assert.doesNotMatch($('#feedback-result').textContent,/Durata misurata/);
  result.unknown_duration_ui_text=$('#feedback-result').textContent;
  await click($('#close-dialog'),'return to final calendar');await idle();
  assert.match(row(2).textContent,/— min confermati/);assert.doesNotMatch(row(2).textContent,/0 min confermati/);
  completed.push('Unknown-duration gym record confirms occurrence while duration, completion and adherence stay null; UI displays missing values rather than zero.');

  const calendar=await read(`/planning/week?start=${encodeURIComponent(after.week.start)}`);
  const sessions=calendar.days.flatMap(day=>day.sessions);const calendarGym=sessions.find(value=>value.id===2);const invalid=sessions.find(value=>value.id===3);
  assert.equal(calendarGym.execution_status,'completed');assert.equal(calendarGym.confirmed_actual_minutes,null);
  assert.equal(invalid.status,'completed');assert.equal(invalid.execution_status,'unconfirmed');assert.equal(invalid.evidence_issue,'future_activity');
  assert.equal(after.week.counts.unconfirmed,1);assert.equal(after.week.counts.modified,1);assert.equal(after.week.counts.completed,1);assert.equal(after.week.adherence.evaluated_sessions,1);
  assert.deepEqual(after.feedbacks.find(value=>value.workout_id===3),before.feedbacks.find(value=>value.workout_id===3),'Seeded invalid feedback must remain untouched.');
  assert.equal(mutations().length,2);assert.ok(mutations().every(value=>value.method==='PUT' && /\/experience\/workouts\/[12]\/feedback$/.test(value.path)));
  completed.push('Calendar, weekly review and canonical feedback agree; invalid future-linked legacy completion stays unconfirmed and its original record is unchanged.');
  result.after={counts:after.week.counts,totals:after.week.totals,adherence:after.week.adherence,
    sessions:after.week.sessions.map(value=>({id:value.id,status:value.status,actual_minutes:value.actual_minutes,completion_percent:value.completion_percent,evidence_issue:value.evidence_issue})),
    calendar_summary:calendar.summary || null};
  result.asset_sha256=loadedAssets;result.source_after_sha256=sourceHashes();
  assert.deepEqual(result.source_after_sha256,sourceBefore,'Frontend source must not change during this integration run.');
  for(const name of sourcePaths.filter(value=>value!=='web/index.html'))assert.equal(loadedAssets[`/${name}`],sourceBefore[name],`HTTP asset bytes match ${name}`);
  assert.equal(blockedExternal.length,0,'No third-party resource request.');assert.deepEqual(httpErrors,[]);assert.deepEqual(domErrors,[]);
  result.ok=true;
}
run().catch(error=>{result.ok=false;result.error=error.stack || error.message;result.notice=dom?.window.document.querySelector('#notice')?.textContent;process.exitCode=1;}).finally(()=>{
  result.requests=requests;result.request_count=requests.length;result.mutations=mutations();result.http_errors=httpErrors;result.dom_errors=domErrors;result.blocked_external=blockedExternal;
  fs.writeFileSync(OUTPUT,JSON.stringify(result,null,2)+'\n');dom?.window.close();
  console.log(JSON.stringify({ok:result.ok,completed:completed.length,requests:requests.length,mutations:result.mutations.length,http_errors:httpErrors,dom_errors:domErrors,output:OUTPUT,error:result.error},null,2));
});
