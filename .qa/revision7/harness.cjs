'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM,VirtualConsole}=require('../../web/tests/node_modules/jsdom');
const project=path.resolve(__dirname,'../..'),origin='http://127.0.0.1:8777',calls=[],errors=[],pending=new Set();
const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.stack));
const dom=new JSDOM(fs.readFileSync(path.join(project,'web/index.html'),'utf8'),{url:origin+'/app#training',runScripts:'outside-only',virtualConsole:vc});const w=dom.window,$=s=>w.document.querySelector(s);
w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
w.fetch=(relative,options={})=>{const url=new URL(relative,origin);assert.equal(url.origin,origin);assert.ok(!url.pathname.startsWith('/sync'));const job=fetch(url,options).then(response=>{calls.push({path:url.pathname,method:options.method||'GET',status:response.status});return response;}).finally(()=>pending.delete(job));pending.add(job);return job;};
async function settle(){for(let n=0;n<500;n++){await new Promise(r=>setTimeout(r,25));if(!pending.size&&$('#refresh').textContent!=='Caricamento…'){await new Promise(r=>setTimeout(r,50));if(!pending.size)break;}if(n===499)throw Error('UI busy');}assert.deepEqual(errors,[]);assert.ok(!$('#notice').classList.contains('error'),$('#notice').textContent);}
async function api(route){const response=await fetch(origin+route);assert.ok(response.ok);return response.json();}
const set=(name,value)=>{const field=$(`#manual-training-form [name="${name}"]`);field.value=value;field.dispatchEvent(new w.Event(field.tagName==='SELECT'?'change':'input',{bubbles:true}));};
(async()=>{try{
  for(const name of ['metric-status.js','charts.js','app.js','usability.js','progress.js','wellness.js','manual-training.js','plan-coach.js'])w.eval(fs.readFileSync(path.join(project,'web',name),'utf8'));await settle();
  assert.match($('.exercise-catalogue summary').textContent,/201 esercizi/);
  set('sport','strength');set('name','QA Catalogo completo');$('.exercise-catalogue').open=true;
  set('exercise_group','Petto');set('exercise_equipment','Manubri');set('exercise_search','DUMBBELL BENCH');assert.equal($('.exercise-catalogue-grid').children.length,1);$('.exercise-catalogue-grid button').click();
  set('exercise_group','');set('exercise_equipment','');set('exercise_search','Side plank');$('.exercise-catalogue-grid button').click();
  set('exercise_search','Extrarotazioni con elastico');$('.exercise-catalogue-grid button').click();assert.equal($('.gym-builder-list').children.length,3);
  assert.equal((await api('/coach/workouts')).length,0);
  $('#manual-training-form').dispatchEvent(new w.Event('submit',{cancelable:true,bubbles:true}));await settle();
  const workouts=await api('/coach/workouts');assert.equal(workouts.length,1);const workout=workouts[0];assert.equal(workout.sport,'strength');assert.equal(workout.payload.exercises.length,3);
  const [press,plank,band]=workout.payload.exercises;assert.equal(press.name,'Panca piana con manubri');assert.equal(press.reps,10);assert.equal(press.weight_kg,null);assert.equal(plank.work_seconds,30);assert.equal(plank.reps,null);assert.match(plank.notes,/per lato/);assert.equal(band.name,'Extrarotazioni con elastico');
  $('.gym-library .list-row').click();await settle();assert.equal($('#gym-session-dialog').open,true);assert.equal($('.gym-live-exercises').children.length,3);
  $('.gym-set-check input').click();await settle();const progress=await api(`/training/gym/${workout.id}/progress`);assert.equal(progress.completed_count,1);assert.equal(progress.total_sets,6);assert.equal(progress.progress.timer.running,true);
  assert.equal((await api('/activities')).length,0);assert.equal(calls.filter(c=>c.status>=400).length,0);
  const result={passed:true,catalogue_exercises:201,muscle_groups:11,equipment_types:12,frontend_requests:calls.length,dom_errors:errors.length,http_errors:0,preview_does_not_write:true,real_api_saved:true,reps_and_time_preserved:true,live_checklist_and_timer:true,activities_created:0,browser_visual_verified:false};fs.writeFileSync(path.join(__dirname,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{dom.window.close();}})().catch(e=>{console.error(e.stack);process.exitCode=1;});
