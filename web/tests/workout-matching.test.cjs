'use strict';
// Local source and synthetic API fixtures only. Browser inspection is separate.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require('jsdom');
const app = fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8');
const matching = fs.readFileSync(path.join(__dirname, '../workout-matching.js'), 'utf8');
const workflow = app.slice(app.indexOf('let workoutMatching ='), app.indexOf("$('#send-garmin').addEventListener"));
const candidate = (id = 912) => ({id, name:'Rulli VirtualRide', source:'intervals', source_id:`provider-${id}`, start_time:'2026-10-17T22:30:00Z', activity_date:'2026-10-18', sport:'VirtualRide', duration_min:54, day_delta:0, duration_delta_min:-6, available:true, unavailable_reason:null, duplicate_warning:null, match_token:`token-${id}`});
const proposal = items => ({window:{start:'2026-10-15',end:'2026-10-21',timezone:'Europe/Rome'}, items, counts:{eligible:items.filter(item=>item.available).length,returned:items.length,total:items.length,truncated:false},limits:['Proposte per data e disciplina, da confermare.']});
const workout = id => ({id, sport:'cycling',date:'2026-10-18',name:`Seduta ${id}`,duration_min:60,status:'planned',payload:{sport:'cycling',duration_min:60,steps:[],reason:'Continuità'}});
const feedback = (activity_id = null, notes = 'Salvato') => ({activity_id,rpe:5,completion_percent:100,feeling:'as_expected',notes,planned_duration_min:60,actual:{duration_min:54},evaluation:{completion:90},source:activity_id == null?'manual':'activity'});

async function setup(t) {
  const errors = []; const console = new VirtualConsole(); console.on('jsdomError', error => errors.push(error));
  const dom = new JSDOM(fs.readFileSync(path.join(__dirname,'../index.html'),'utf8'),{url:'https://fixture.invalid/app',runScripts:'outside-only',virtualConsole:console});
  t.after(()=>dom.window.close()); const w = dom.window; const $ = selector => w.document.querySelector(selector); const calls = [];
  w.HTMLDialogElement.prototype.showModal = function(){this.setAttribute('open','');};
  w.HTMLDialogElement.prototype.close = function(){this.removeAttribute('open');this.dispatchEvent(new w.Event('close'));};
  w.$ = $; w.state = {activities:Array.from({length:50},(_,id)=>({id,name:'Unrelated'})),selected:null,busy:false};
  w.el = (tag,text,css) => {const node=w.document.createElement(tag);if(text!=null)node.textContent=text;if(css)node.className=css;return node;};
  w.button = (text,fn,css) => {const node=w.el('button',text,css);node.type='button';node.addEventListener('click',fn);return node;};
  w.number = value => value == null?'—':String(value); w.sports = {cycling:'Bici'}; w.stepNames = {}; w.resetTimer = ()=>{}; w.json = (method,payload)=>({method,body:JSON.stringify(payload)});
  const data = {reviews:new Map(),requests:null,deliveries:null,loads:0,alerts:[],feedbackCalls:0};
  w.notice = message => data.alerts.push(message); w.load = async()=>{data.loads++;}; w.action=async work=>{try{return await work();}catch(error){data.alerts.push(error.message);}};
  w.api=async(url,options={})=>{
    const method=options.method||'GET'; const payload=options.body?JSON.parse(options.body):null; calls.push({url,method,payload});
    if(url.endsWith('/review')) {if(data.requests)return data.requests(url);const id=Number(url.split('/')[3]);return data.reviews.get(id)||{workout:workout(id),feedback:null,activity_candidates:proposal([candidate()])};}
    if(url.endsWith('/garmin/status')) {if(data.deliveries)return data.deliveries(url);return{message:'Configurazione Garmin necessaria.'};}
    if(url.endsWith('/feedback')) {data.feedbackCalls++;if(data.save)return data.save(payload);return feedback(payload.activity_id,payload.notes);}
    throw new Error(`Unexpected ${method} ${url}`);
  };
  const fit = w.el('a'); fit.id='export-fit'; $('#workout-actions').append(fit);
  for(const form of w.document.querySelectorAll('form'))form.addEventListener('input',()=>{form.dataset.dirty='true';});
  w.IronCoachUI = {el:w.el,button:w.button,number:w.number,sourceLabel:source=>source==='intervals'?'Intervals.icu':source};
  w.eval(matching); w.eval(`${workflow}\nwindow.executionTest={showWorkout,openWorkoutFromReview,loadWorkoutReview};`);
  const flush=async()=>{for(let i=0;i<10;i++)await new Promise(resolve=>setImmediate(resolve));assert.equal(errors.length,0,errors.map(error=>error.stack).join('\n'));};
  const open=id=>w.executionTest.showWorkout(workout(id).payload,workout(id));
  const submit=async()=>{$('#feedback-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await flush();};
  return {w,$,data,calls,flush,open,submit};
}

test('server candidate beyond the first fifty activities is explicit and saves its token only on submit',async t=>{
  const {w,$,data,calls,flush,open,submit}=await setup(t); await open(71); await flush();
  assert.equal(w.state.activities.some(item=>item.id===912),false);assert.ok($('#feedback-activity option[value="912"]'));
  assert.equal($('#feedback-activity').value,''); assert.equal(calls.some(call=>call.method!=='GET'),false);
  const card=$('#workout-matching [data-match-activity="912"]');
  assert.match(card.textContent,/2026-10-18 · Intervals.icu · 54 min/);assert.match(card.textContent,/Stesso giorno · Differenza durata: -6 min/);
  card.querySelector('button').click();await flush();assert.equal($('#feedback-activity').value,'912');assert.equal($('#feedback-form').dataset.dirty,'true');assert.equal(data.feedbackCalls,0);
  $('#feedback-form [name="rpe"]').value='6';$('#feedback-form [name="notes"]').value='Confermo la seduta';await submit();
  const saved=calls.find(call=>call.method==='PUT');assert.equal(saved.payload.activity_id,912);assert.equal(saved.payload.activity_match_token,'token-912');
  assert.equal(data.feedbackCalls,1);assert.equal(data.loads,1);
});

test('legacy confidence suggestion is never selected and manual feedback has no activity token',async t=>{
  const {$,data,calls,open,submit}=await setup(t);data.reviews.set(71,{workout:workout(71),feedback:null,suggested_match:{activity_id:912,confidence:99},activity_candidates:proposal([candidate()])});
  await open(71);assert.equal($('#feedback-activity').value,'');assert.doesNotMatch($('#feedback-form').textContent,/99%|confidenza/);
  $('#feedback-form [name="rpe"]').value='5';await submit();const payload=calls.find(call=>call.method==='PUT').payload;
  assert.equal(payload.activity_id,null);assert.equal(Object.hasOwn(payload,'activity_match_token'),false);
});

test('unavailable activities remain visible with reasons and cannot be chosen',async t=>{
  const {$,data,open}=await setup(t);data.reviews.set(71,{feedback:null,activity_candidates:proposal([{...candidate(),available:false,unavailable_reason:'Già associata a un’altra seduta.',duplicate_warning:'Possibile duplicato.'}])});await open(71);
  const card=$('#workout-matching [data-match-activity="912"]');assert.equal(card.querySelector('button').disabled,true);assert.match(card.textContent,/Già associata/);assert.match(card.textContent,/Possibile duplicato/);assert.equal($('#feedback-activity option[value="912"]'),null);
});

test('late review preserves notes, RPE and a manual draft selection',async t=>{
  const {w,$,data,flush,open}=await setup(t);let release;
  data.requests=()=>new Promise(resolve=>{release=resolve;});const loading=open(71);await flush();
  $('#feedback-form [name="rpe"]').value='8';$('#feedback-form [name="notes"]').value='Non sovrascrivere';$('#feedback-form [name="notes"]').dispatchEvent(new w.Event('input',{bubbles:true}));
  release({feedback:feedback(912,'Vecchio feedback'),activity_candidates:proposal([candidate()])});await loading;
  assert.equal($('#feedback-form [name="rpe"]').value,'8');assert.equal($('#feedback-form [name="notes"]').value,'Non sovrascrivere');assert.equal($('#feedback-activity').value,'');assert.equal($('#feedback-result'),null);
});

test('a saved activity binding is restored from feedback with its fresh token',async t=>{
  const {$,data,calls,open,submit}=await setup(t);data.reviews.set(71,{feedback:feedback(912),activity_candidates:proposal([candidate()])});await open(71);
  assert.equal($('#feedback-activity').value,'912');assert.match($('#workout-matching').textContent,/Abbinamento già salvato/);assert.equal($('#feedback-form').dataset.dirty,undefined);
  await submit();assert.equal(calls.find(call=>call.method==='PUT').payload.activity_match_token,'token-912');
});

test('manually changing the select switches or clears the token and never writes data',async t=>{
  const {w,$,data,calls,open,submit}=await setup(t);data.reviews.set(71,{feedback:null,activity_candidates:proposal([candidate(912),candidate(913)])});await open(71);
  $('#feedback-activity').value='913';$('#feedback-activity').dispatchEvent(new w.Event('change',{bubbles:true}));assert.equal($('#feedback-activity').dataset.matchToken,'token-913');assert.equal(calls.some(call=>call.method!=='GET'),false);
  $('#feedback-activity').value='';$('#feedback-activity').dispatchEvent(new w.Event('change',{bubbles:true}));assert.equal($('#feedback-activity').dataset.matchToken,'');await submit();assert.equal(calls.find(call=>call.method==='PUT').payload.activity_id,null);
});

test('unknown draft ids do not bypass the reviewed proposal token',async t=>{
  const {w,$,data,open,submit}=await setup(t);await open(71);const option=w.el('option','Unreviewed');option.value='1234';$('#feedback-activity').append(option);$('#feedback-activity').value='1234';await submit();
  assert.equal(data.feedbackCalls,0);assert.match(data.alerts.at(-1),/Ricontrolla le proposte/);
});

test('Garmin failure does not hide the independently available activity review',async t=>{
  const {$,data,open}=await setup(t);data.deliveries=async()=>{throw new Error('Garmin offline');};await open(71);
  assert.match($('#garmin-workout-status').textContent,/Garmin offline/);assert.ok($('#feedback-activity option[value="912"]'));
});

test('a late review for another workout cannot replace the current choices',async t=>{
  const {$,data,flush,open}=await setup(t);const pending=new Map();data.requests=url=>new Promise(resolve=>pending.set(Number(url.split('/')[3]),resolve));
  const first=open(71);await flush();const second=open(72);await flush();pending.get(72)({feedback:null,activity_candidates:proposal([candidate(914)])});await second;
  pending.get(71)({feedback:null,activity_candidates:proposal([candidate(912)])});await first;
  assert.ok($('#feedback-activity option[value="914"]'));assert.equal($('#feedback-activity option[value="912"]'),null);
});

test('opening a weekly row uses its returned workout even outside cached lists and discards stale openings',async t=>{
  const {w,$,data,flush,calls}=await setup(t);const pending=new Map();data.requests=url=>new Promise(resolve=>pending.set(Number(url.split('/')[3]),resolve));
  const first=w.executionTest.openWorkoutFromReview(771);await flush();const second=w.executionTest.openWorkoutFromReview(772);await flush();
  pending.get(772)({workout:workout(772),feedback:null,activity_candidates:proposal([candidate(915)])});await second;
  pending.get(771)({workout:workout(771),feedback:null,activity_candidates:proposal([candidate(916)])});await first;
  assert.equal(w.state.selected.id,772);assert.equal($('#workout-dialog').open,true);assert.ok($('#feedback-activity option[value="915"]'));
  assert.equal(calls.filter(call=>call.url==='/experience/workouts/772/review').length,1,'Returned review is reused without a second request');
});

test('weekly opening completing after navigation never opens a modal on the new page',async t=>{
  const {w,$,data,flush}=await setup(t);let release;data.requests=()=>new Promise(resolve=>{release=resolve;});const opening=w.executionTest.openWorkoutFromReview(771);await flush();
  w.document.dispatchEvent(new w.CustomEvent('iron:pagechange'));release({workout:workout(771),feedback:null,activity_candidates:proposal([candidate()])});await opening;
  assert.equal($('#workout-dialog').open,false);assert.equal(w.state.selected,null);
});

test('imported names and source labels are inert text and truncation is explicit',async t=>{
  const {$,data,open}=await setup(t);const candidates=proposal([{...candidate(),name:'<img src=x onerror=bad()>',source:'<svg onload=bad()>'}]);candidates.counts={eligible:21,total:48,returned:1,truncated:true};data.reviews.set(71,{feedback:null,activity_candidates:candidates});await open(71);
  assert.equal($('#workout-matching img'),null);assert.equal($('#workout-matching svg'),null);assert.match($('#workout-matching').textContent,/<img src=x/);assert.match($('#workout-matching').textContent,/1 proposte su 48/);
});

test('review failure leaves manual feedback usable and exposes an explicit retry',async t=>{
  const {$,data,flush,open}=await setup(t);data.requests=async()=>{throw new Error('Offline');};await open(71);assert.match($('#workout-matching').textContent,/feedback manuale/);
  data.requests=null;$('#workout-matching button').click();await flush();assert.ok($('#feedback-activity option[value="912"]'));
});

test('changed proposals require explicit reload and save without losing the draft',async t=>{
  const {w,$,data,calls,flush,open,submit}=await setup(t);await open(71);$('#workout-matching [data-match-activity="912"] button').click();
  $('#feedback-form [name="notes"]').value='Mantieni questa nota';$('#feedback-form [name="notes"]').dispatchEvent(new w.Event('input',{bubbles:true}));
  data.save=async()=>{throw new Error('L’abbinamento è cambiato. Rileggi le proposte.');};await submit();assert.equal(data.feedbackCalls,1);assert.equal($('#feedback-form').dataset.dirty,'true');
  data.reviews.set(71,{feedback:null,activity_candidates:proposal([{...candidate(),match_token:'new-token'}])});
  [...$('#workout-matching').querySelectorAll('button')].find(button=>button.textContent==='Ricontrolla proposte').click();await flush();
  assert.equal($('#feedback-form [name="notes"]').value,'Mantieni questa nota');assert.equal($('#feedback-activity').value,'912');assert.equal(data.feedbackCalls,1,'Refreshing must never resubmit');
  data.save=null;await submit();assert.equal(data.feedbackCalls,2);assert.equal(calls.filter(call=>call.method==='PUT').at(-1).payload.activity_match_token,'new-token');
});

test('an unreadable workout shows the server issue and cannot save feedback',async t=>{
  const {$,data,open,submit}=await setup(t);data.reviews.set(71,{feedback_available:false,feedback_issue:'Il programma contiene dati non leggibili.',feedback:null,activity_candidates:proposal([])});await open(71);
  assert.match($('#feedback-issue').textContent,/dati non leggibili/);assert.equal($('#feedback-form button[type="submit"]').disabled,true);await submit();assert.equal(data.feedbackCalls,0);assert.match(data.alerts.at(-1),/dati non leggibili/);
});

test('saving clears old proposal tokens while retaining the persisted activity binding',async t=>{
  const {$,data,open,submit}=await setup(t);await open(71);$('#workout-matching [data-match-activity="912"] button').click();await submit();
  assert.equal($('#feedback-activity').value,'912');assert.match($('#workout-matching').textContent,/Feedback salvato/);assert.equal($('#workout-matching').hasAttribute('aria-busy'),false);
  await submit();assert.equal(data.feedbackCalls,1);assert.match(data.alerts.at(-1),/Ricontrolla le proposte/);
});
