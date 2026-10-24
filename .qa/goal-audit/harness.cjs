'use strict';
// Actual HTTP + synthetic DOM. No browser automation and no production data.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM,VirtualConsole}=require('../../web/tests/node_modules/jsdom');
const project=path.resolve(__dirname,'../..'),origin='http://127.0.0.1:8777';
const seed=JSON.parse(fs.readFileSync(path.join(__dirname,'seed-manifest.json'),'utf8'));
const calls=[],errors=[],pending=new Set(),vc=new VirtualConsole();
vc.on('jsdomError',error=>errors.push(error.stack||error.message));
const dom=new JSDOM(fs.readFileSync(path.join(project,'web/index.html'),'utf8'),{url:origin+'/app#today',runScripts:'outside-only',virtualConsole:vc});
const w=dom.window,$=selector=>w.document.querySelector(selector);
w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
w.HTMLElement.prototype.scrollIntoView=function(){};
w.fetch=(relative,options={})=>{
  const url=new URL(typeof relative==='string'?relative:relative.url,origin);
  assert.equal(url.origin,origin);assert.ok(!url.pathname.startsWith('/sync'),'No provider sync is allowed.');
  const job=fetch(url,options).then(response=>{
    calls.push({path:url.pathname,query:url.search,method:options.method||'GET',status:response.status});return response;
  }).finally(()=>pending.delete(job));pending.add(job);return job;
};
async function settle(){
  for(let n=0;n<600;n++){
    await new Promise(resolve=>setTimeout(resolve,20));
    if(!pending.size&&$('#refresh').textContent!=='Caricamento…'){
      await new Promise(resolve=>setTimeout(resolve,70));if(!pending.size)break;
    }
    if(n===599)throw Error('UI did not settle.');
  }
  assert.deepEqual(errors,[]);assert.ok(!$('#notice').classList.contains('error'),$('#notice').textContent);
}
async function api(route,options={}){
  const response=await fetch(origin+route,options);assert.ok(response.ok,route+' HTTP '+response.status);return response.json();
}
const offset=(day,n)=>new Date(new Date(day+'T12:00:00Z').valueOf()+n*86400000).toISOString().slice(0,10);
const set=(selector,value,type='input')=>{const field=$(selector);assert.ok(field,selector);field.value=value;field.dispatchEvent(new w.Event(type,{bubbles:true}));};
const submit=async selector=>{$(selector).dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await settle();};
const navigate=async id=>{const control=$(`nav [data-page="${id}"]`);assert.ok(control,id);control.click();await settle();assert.equal($(`#${id}`).hidden,false);assert.equal($('nav [aria-current="page"]').dataset.page,id);};

(async()=>{try{
  const before=(await api('/backups/export')).data;
  const modules=['account.js','metric-status.js','charts.js','app.js','usability.js','progress.js','wellness.js','manual-training.js','plan-coach.js','training-insights.js'];
  for(const name of modules)w.eval(fs.readFileSync(path.join(project,'web',name),'utf8'));
  await settle();assert.equal($('#insights-date').value,seed.today);
  assert.equal(calls.filter(call=>call.path==='/dashboard/training-insights').length,0);
  const visited=['today'];
  for(const id of ['health','activities','coach','calendar','training']){await navigate(id);visited.push(id);}
  assert.equal(calls.filter(call=>call.path==='/dashboard/training-insights').length,0,'Insights must remain lazy before opening athlete profile.');
  await navigate('athlete');visited.push('athlete');
  assert.equal($('#insights-content').hidden,false,$('#insights-status').textContent);
  assert.equal($('.rhythm-grid').children.length,28);assert.equal($('.insight-week-chart').children.length,8);
  const response=await api('/dashboard/training-insights?as_of='+seed.today);
  assert.equal(response.volume.current.activities,seed.current_insights_activities);
  assert.equal(response.volume.previous.activities,seed.previous_insights_activities);
  assert.equal(response.volume.current.load,seed.current_load);
  assert.equal(response.coverage.current.load.known,seed.known_current_loads);
  assert.equal(response.continuity.days.length,28);assert.equal(response.weeks.length,8);
  assert.ok(response.sports.some(sport=>sport.key==='cycling'));assert.ok(response.sports.some(sport=>sport.key==='swimming'));
  assert.equal(response.quality.current_possible_duplicates,1);assert.ok($('.insight-quality-note'));
  assert.equal(response.volume.minutes_change_pct,null,'Missing duration must prevent a full-volume percent.');
  $('[data-metric="load"]').click();
  assert.equal($('[data-metric="load"]').getAttribute('aria-pressed'),'true');
  assert.ok([...$('.insight-week-chart').children].some(bar=>bar.querySelector('.partial')));
  $('.rhythm-grid button').focus();$('.rhythm-grid button').click();assert.equal($('.rhythm-grid button').getAttribute('aria-pressed'),'true');
  const historical=offset(seed.today,-20);set('#insights-date',historical);await submit('#insights-period');
  assert.equal($('#insights-content').hidden,false);assert.equal($('.rhythm-grid').children.length,28);
  assert.ok(calls.some(call=>call.path==='/dashboard/training-insights'&&call.query==='?as_of='+historical));
  assert.equal((await api('/dashboard/training-insights?as_of='+historical)).period.includes_partial_day,false);
  await navigate('connections');visited.push('connections');
  const burstRoutes=['/health','/activities','/dashboard/home','/dashboard/training-insights','/planning/profile',
    '/planning/week','/progress/recovery?days=28','/coach/overview','/health/metrics','/athlete/derived-profile'];
  const burst=[];let next=0;
  await Promise.all(Array.from({length:8},async()=>{while(next<100){const index=next++,route=burstRoutes[index%burstRoutes.length],started=performance.now();
    const read=await fetch(origin+route);await read.arrayBuffer();burst.push({route,status:read.status,ms:performance.now()-started});}}));
  assert.ok(burst.every(read=>read.status===200),'Concurrent reads must all succeed.');
  const durations=burst.map(read=>read.ms).sort((a,b)=>a-b),p50=Math.round(durations[Math.ceil(durations.length*.5)-1]),p95=Math.round(durations[Math.ceil(durations.length*.95)-1]);
  fs.writeFileSync(path.join(__dirname,'http-read-burst.json'),JSON.stringify({requests:100,concurrency:8,errors:0,p50_ms:p50,p95_ms:p95,reads:burst},null,2));
  const afterRead=(await api('/backups/export')).data;
  assert.deepEqual(afterRead,before,'Navigation and insights reads must not modify the personal dataset.');
  assert.ok(calls.every(call=>call.method==='GET'),'All initial navigation should be read-only.');
  $('.skip-link').click();assert.equal(w.location.hash,'#connections');assert.equal(w.document.activeElement,$('#main-content'));

  // The new explicit scan adds review candidates, preserving every activity.
  await navigate('activities');
  const summary=await api('/activities/duplicates/summary');assert.equal(summary.possible_pairs,1);assert.equal(summary.pending_candidates,0);
  assert.equal($('#duplicate-card').hidden,true);$('#history-quality button').click();await settle();
  assert.equal($('#duplicate-card').hidden,false);assert.equal(w.document.activeElement,$('#duplicate-card'));
  const beforeCoach=(await api('/backups/export')).data;
  assert.equal(beforeCoach.duplicate_candidates.length,1);
  for(const [table,rows] of Object.entries(before))if(table!=='duplicate_candidates')assert.deepEqual(beforeCoach[table],rows,'Duplicate scan changed '+table);
  assert.equal((await api('/activities')).length,seed.activity_count);
  assert.equal((await api('/activities/duplicates/summary')).pending_candidates,1);

  // Review the natural-language pause using real HTTP before any apply call.
  await navigate('calendar');$('.plan-coach-card').open=true;
  set('#plan-coach-form [name="message"]','non voglio fare nuoto per due settimane');await submit('#plan-coach-form');
  assert.match($('.coach-conversation').textContent,/1 seduta verrà tolta/);
  assert.equal((await api('/coach/workouts')).find(row=>row.id===seed.workout_ids['QA Nuoto futuro']).status,'planned');
  assert.deepEqual((await api('/backups/export')).data,beforeCoach,'Coach preview must not persist profile or workout changes.');
  const apply=[...$('.coach-conversation').querySelectorAll('button')].find(button=>button.textContent==='Applica al piano');assert.ok(apply);apply.click();await settle();
  const afterApply=await api('/coach/workouts');
  assert.equal(afterApply.some(row=>row.id===seed.workout_ids['QA Nuoto futuro']),false,'Superseded workouts leave the active workout list.');
  assert.equal((await api('/backups/export')).data.workouts.find(row=>row.id===seed.workout_ids['QA Nuoto futuro']).status,'superseded');
  assert.equal(afterApply.find(row=>row.id===seed.workout_ids['QA Bici futura']).status,'planned');
  assert.equal(afterApply.find(row=>row.id===seed.workout_ids['QA Corsa futura']).status,'planned');
  assert.equal(afterApply.find(row=>row.id===seed.workout_ids['QA Nuoto completato']).status,'completed');
  assert.equal((await api('/activities')).length,seed.activity_count);
  $('.coach-time-rule button').click();await settle();assert.equal((await api('/planning/profile')).profile.time_constraints.length,0);

  // Create and follow a synthetic gym plan without creating an Activity.
  await navigate('training');
  set('#manual-training-form [name="sport"]','strength','change');set('#manual-training-form [name="name"]','QA Scheda integrata');
  $('.exercise-catalogue').open=true;
  set('#manual-training-form [name="exercise_search"]','DUMBBELL BENCH');
  assert.equal($('.exercise-catalogue-grid').children.length,1);$('.exercise-catalogue-grid button').click();
  set('#manual-training-form [name="exercise_search"]','Side plank');$('.exercise-catalogue-grid button').click();
  assert.equal($('.gym-builder-list').children.length,2);await submit('#manual-training-form');
  const gym=(await api('/coach/workouts')).find(row=>row.name==='QA Scheda integrata');assert.ok(gym);assert.equal(gym.payload.exercises.length,2);
  const gymButton=[...$('.gym-library').querySelectorAll('.list-row')].find(button=>button.textContent.includes('QA Scheda integrata'));assert.ok(gymButton);gymButton.click();await settle();
  assert.equal($('#gym-session-dialog').open,true);$('.gym-set-check input').click();await settle();
  const progress=await api(`/training/gym/${gym.id}/progress`);assert.equal(progress.completed_count,1);assert.equal(progress.progress.timer.running,true);
  assert.equal((await api('/activities')).length,seed.activity_count);
  w.IronCoachUI.navigate('health');await settle();assert.equal($('#gym-session-dialog').open,false);
  assert.equal(calls.filter(call=>call.status>=400).length,0);assert.deepEqual(errors,[]);
  const result={passed:true,frontend_modules:modules.length,navigation_pages:visited,frontend_requests:calls.length,dom_errors:0,http_errors:0,
    real_http:true,read_only_navigation_preserved_dataset:true,insights_lazy:true,insights_actual_schema:true,insights_days:28,insights_weeks:8,
    missing_load_stays_unknown:true,historical_period:true,load_controls:true,coach_preview_readonly:true,coach_only_future_swim_removed:true,
    other_disciplines_and_completed_preserved:true,manual_gym_saved:true,live_set_and_timer:true,activities_created:0,
    concurrent_reads:{requests:100,concurrency:8,errors:0,p50_ms:p50,p95_ms:p95,dataset_unchanged:true},
    duplicate_scan_explicit:true,duplicate_candidates_created:1,duplicate_scan_preserved_activities:true,
    provider_calls:0,browser_visual_verified:false};
  fs.writeFileSync(path.join(__dirname,'http-result.json'),JSON.stringify(result,null,2));
  fs.writeFileSync(path.join(__dirname,'http-requests.json'),JSON.stringify(calls,null,2));console.log(JSON.stringify(result));
}finally{w.close();}})().catch(error=>{console.error(error.stack);process.exitCode=1;});
