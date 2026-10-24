'use strict';
// Focused contracts against the real activity UI and real page markup.
// Only the unrelated automatic dashboard load is disabled in this fixture.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM,VirtualConsole}=require('jsdom');
const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
const script=fs.readFileSync(path.join(__dirname,'../app.js'),'utf8');
const colors=fs.readFileSync(path.join(__dirname,'../metric-status.js'),'utf8');
const clone=value=>JSON.parse(JSON.stringify(value));

const activity=(id=91,name='Giro registrato')=>({id,name,sport:'cycling',source:'intervals',start_time:'2026-10-17T08:00:00Z',duration_seconds:3300,distance_meters:27000,elevation_gain_meters:0,load:50});
const rating=()=>({activity_id:91,scope:'recorded_session',score:82,judgment:'Buono',level:4,tone:'good',confidence_percent:80,
  summary:'Seduta ben proporzionata.',explanation:'Seduta ben proporzionata.',evidence:['55 minuti, 50 punti di carico.'],actions:['Conserva il recupero.'],benefits:'Resistenza specifica.',limits:[]});
const detail=()=>({activity_id:91,name:'Giro registrato',sport:'cycling',start_time:'2026-10-17T08:00:00Z',
  summary:{duration_seconds:3300,elapsed_seconds:3500,distance_meters:27000,elevation_gain_meters:0,avg_hr:135,max_hr:160,avg_power:150,normalized_power:155,max_power:400,avg_speed:8,max_speed:12,avg_cadence:82,calories:500,load:50},
  streams:[{kind:'power',label:'Potenza',unit:'W',points:[{time_s:0,value:100},{time_s:60,value:null},{time_s:120,value:250}],sample_count:3,downsampled:false},
    {kind:'heart_rate',label:'Frequenza cardiaca',unit:'bpm',points:[{time_s:0,value:120},{time_s:60,value:135},{time_s:120,value:150}],sample_count:3,downsampled:false}],
  route:{available:true,points:[{lat:42.1,lon:13.1,time_s:0},{lat:42.2,lon:13.2,time_s:60},{lat:42.3,lon:13.1,time_s:120}],sample_count:3,downsampled:false},
  provider_url:'https://intervals.icu/activities/i12345',availability:{status:'complete',message:null,remote_fetched:true},coach_rating:rating()});

async function setup(t,customFetch){
  const errors=[],calls=[];const console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
  const dom=new JSDOM(html,{url:'http://127.0.0.1:8000/app#activities',runScripts:'outside-only',virtualConsole:console});
  const w=dom.window,$=selector=>w.document.querySelector(selector);
  w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
  w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');this.dispatchEvent(new w.Event('close'));};
  w.setInterval=()=>1;w.clearInterval=()=>{};
  const data={detail:detail(),assessment:rating(),detailFails:false};
  w.fetch=async(url,options={})=>{
    const request=String(url);calls.push({path:request,method:options.method||'GET'});
    if(customFetch){const custom=await customFetch(request,options,data);if(custom)return custom;}
    if(/\/detail$/.test(request))return {ok:!data.detailFails,status:data.detailFails?502:200,json:async()=>data.detailFails?{detail:'Provider temporaneamente non disponibile.'}:clone(data.detail)};
    if(/\/assessment$/.test(request))return {ok:true,status:200,json:async()=>clone(data.assessment)};
    throw new Error(`Unexpected focused fixture request: ${request}`);
  };
  const marker="page(location.hash.slice(1) || 'today'); action(load);";
  assert.ok(script.includes(marker),'The fixture must disable only the automatic initial dashboard load.');
  const focused=script.replace(marker,"page(location.hash.slice(1) || 'today');")+'\nwindow.__activityFixture={showActivity,state};';
  w.eval(fs.readFileSync(path.join(__dirname,'../charts.js'),'utf8'));w.eval(fs.readFileSync(path.join(__dirname,'../power-duration.js'),'utf8'));w.eval(fs.readFileSync(path.join(__dirname,'../distance-efforts.js'),'utf8'));w.eval(colors);w.eval(focused);
  t.after(()=>dom.window.close());
  const flush=async()=>{for(let index=0;index<8;index++)await new Promise(resolve=>setImmediate(resolve));assert.equal(errors.length,0,errors.map(error=>error.stack||error.message).join('\n'));};
  const open=async(row=activity())=>{await w.__activityFixture.showActivity(row);await flush();};
  return {w,$,data,calls,flush,open};
}

test('opening an activity loads its recorded summary, real route, selectable charts and Coach score',async t=>{
  const {$,calls,open}=await setup(t);await open();
  assert.equal($('#activity-dialog').open,true);
  assert.ok(calls.some(call=>call.path==='/activities/91/detail'));
  assert.ok(calls.some(call=>call.path==='/activities/91/assessment'));
  assert.ok($('#activity-detail .activity-overview'));
  assert.match($('#activity-detail .activity-stat-grid').textContent,/27/);
  assert.match($('#activity-detail .activity-stat-grid').textContent,/135/);
  assert.ok($('#activity-detail .activity-route'));
  assert.ok($('#activity-detail .activity-stream-panel select[name="activity_stream"]'));
  assert.ok($('#activity-detail .activity-stream-chart'));
  assert.match($('#activity-detail .activity-coach').textContent,/82/);
  assert.match($('#activity-detail .activity-coach').textContent,/Conserva il recupero/);
});

test('switching series preserves a personal target in the current activity view',async t=>{
  const {w,$,open,flush}=await setup(t);await open();const form=$('.chart-target-form');form.elements.target_low.value='120';form.elements.target_high.value='180';form.dispatchEvent(new w.Event('submit',{cancelable:true}));assert.ok($('.reference-band'));
  const select=$('[name=activity_stream]');select.value='heart_rate';select.dispatchEvent(new w.Event('change'));await flush();assert.equal($('.reference-band'),null);select.value='power';select.dispatchEvent(new w.Event('change'));assert.ok($('.reference-band'));assert.match($('.chart-band-legend').textContent,/120–180/);
});

test('switching streams changes the actual chart and keeps missing samples finite and unfilled',async t=>{
  const {w,$,open,flush}=await setup(t);await open();
  const select=$('[name="activity_stream"]');
  assert.deepEqual([...select.options].map(option=>option.value).sort(),['heart_rate','power']);
  assert.equal($('.activity-stream-chart').querySelectorAll('polyline').length,2,'A missing sample breaks the line instead of becoming zero or joining unrelated segments.');
  const original=$('.activity-stream-chart').outerHTML;
  select.value='heart_rate';select.dispatchEvent(new w.Event('change',{bubbles:true}));await flush();
  assert.notEqual($('.activity-stream-chart').outerHTML,original);
  assert.doesNotMatch($('.activity-stream-chart').outerHTML,/NaN|Infinity/);
  assert.equal($('.activity-stream-chart').getAttribute('role'),'img');
});

for(const [kind,label,unit,values] of [['altitude','Altitudine','m',[-15,-10,5]],['temperature','Temperatura','°C',[-8,-5,-2]]]) test(`${kind} charts retain negative observed values inside the plot`,async t=>{
  const {$,data,open}=await setup(t);
  data.detail.streams=[{kind,label,unit,points:values.map((value,index)=>({time_s:index*60,value}))}];
  await open();
  const chart=$('.activity-stream-chart');
  assert.match(chart.textContent,/-/,'The value axis includes negative observations.');
  const coordinates=chart.querySelector('polyline').getAttribute('points').split(' ').map(point=>point.split(',').map(Number));
  assert.ok(coordinates.every(([x,y])=>x>=57&&x<=620&&y>=24&&y<=212),'No negative observation is clipped outside the chart.');
});

test('missing GPS and stream data never produce a fabricated route or empty chart',async t=>{
  const {$,data,open}=await setup(t);
  data.detail.route={available:false,points:[],sample_count:0};data.detail.streams=[];
  data.detail.summary.avg_hr=null;data.detail.summary.avg_power=null;
  data.detail.availability={status:'summary_only',message:'Serie temporali non disponibili.'};
  await open();
  assert.equal($('.activity-route'),null);assert.equal($('.activity-stream-chart'),null);
  assert.ok($('.activity-stat-grid'));
  assert.match($('#activity-detail').textContent,/non disponibili|non disponibile/);
  assert.doesNotMatch($('#activity-detail').textContent,/NaN|Infinity/);
});

test('a provider detail failure preserves local activity values and the available Coach assessment',async t=>{
  const {$,data,open}=await setup(t);data.detailFails=true;await open();
  assert.ok($('.activity-stat-grid'));
  assert.match($('.activity-stat-grid').textContent,/27/);
  assert.match($('.activity-coach').textContent,/82/);
  assert.equal($('.activity-route'),null);
  assert.equal($('#activity-dialog').open,true);
});

test('late results from another activity cannot overwrite the activity currently open',async t=>{
  let release;const held=new Promise(resolve=>release=resolve);
  const {w,$,open,flush}=await setup(t,async path=>{
    if(path==='/activities/91/detail'){await held;return {ok:true,status:200,json:async()=>detail()};}
    if(path==='/activities/92/detail'){const next=detail();next.activity_id=92;next.name='Seconda attività';return {ok:true,status:200,json:async()=>next};}
  });
  const first=w.__activityFixture.showActivity(activity());
  await flush();await open(activity(92,'Seconda attività'));
  assert.match($('#activity-detail').textContent,/Seconda attività/);
  release();await first;await flush();
  assert.match($('#activity-detail').textContent,/Seconda attività/);
  assert.doesNotMatch($('#activity-detail .activity-overview').textContent,/Giro registrato/);
});

test('activity titles are inert text and the back arrow closes the activity view',async t=>{
  const {$,data,open}=await setup(t);const name='<img src=x onerror=alert(1)> Giro';data.detail.name=name;
  await open(activity(91,name));
  assert.equal($('#activity-detail img, #activity-detail script'),null);
  assert.match($('#activity-detail').textContent,/<img/);
  assert.match($('#close-activity').getAttribute('aria-label'),/Torna/);
  $('#close-activity').click();assert.equal($('#activity-dialog').open,false);
});

test('selecting a best-power duration opens its actual power interval without changing full-activity statistics',async t=>{
  const {w,$,data,open,flush}=await setup(t);
  data.detail.streams.reverse();
  data.detail.best_efforts={status:'available',metric:'power',unit:'W',source:'stored_observation_streams',
    coverage:{covered_seconds:120,window_seconds:120,coverage_pct:100,segments_count:1,longest_segment_seconds:120},
    durations:[{duration_seconds:5,status:'available',mean_power_w:90,start_s:2,end_s:7,coverage_pct:100,covered_seconds:5}],
    method:{gap_limit_seconds:10},reasons:[],limits:[]};
  await open();assert.equal($('[name=activity_stream]').value,'heart_rate');assert.equal($('.selected-effort-window'),null);
  $('.power-duration-choice[data-duration-seconds="5"]').click();await flush();
  assert.equal($('[name=activity_stream]').value,'power');assert.ok($('.selected-effort-window'));assert.match($('.chart-interval-info').textContent,/0:02.*0:07/);
  const summary=$('.stream-stats').textContent;
  const select=$('[name=activity_stream]');select.value='heart_rate';select.dispatchEvent(new w.Event('change'));select.value='power';select.dispatchEvent(new w.Event('change'));
  assert.ok($('.selected-effort-window'));assert.equal($('.stream-stats').textContent,summary);
  $('#close-activity').click();data.detail.activity_id=92;data.detail.name='Altra attività';data.detail.best_efforts.durations[0].start_s=10;data.detail.best_efforts.durations[0].end_s=15;
  await open(activity(92,'Altra attività'));assert.equal($('.selected-effort-window'),null);assert.equal($('[name=activity_stream]').value,'heart_rate');
});

test('an unavailable raw power curve explains its reason even when no chart can be drawn',async t=>{
  const {$,data,open}=await setup(t);data.detail.streams=[];data.detail.route=null;
  data.detail.best_efforts={status:'unavailable',metric:'power',unit:'W',reasons:['ambiguous_power'],durations:[],coverage:{},method:{}};
  await open();assert.ok($('.power-duration'));assert.match($('.power-duration-unavailable').textContent,/ambigue/);assert.equal($('.selected-effort-window'),null);
});

const distanceResult=()=>({status:'available',metric:'running_distance',source:'stored_observation_streams',distances:[{distance_m:1000,distance_value:1000,distance_unit:'m',status:'available',elapsed_seconds:250,start_s:10,end_s:260}],coverage:{covered_seconds:600,window_seconds:600,coverage_pct:100,segments_count:1,longest_segment_m:2400},method:{gap_limit_seconds:10},reasons:[],limits:[]});

for(const kind of ['distance','pace','speed'])test(`distance result selects the recorded ${kind} interval only after a click`,async t=>{
  const {w,$,data,open,calls}=await setup(t);data.detail.sport='running';data.detail.distance_efforts=distanceResult();
  data.detail.streams=[data.detail.streams[1],{kind,label:kind,unit:kind==='distance'?'m':kind==='pace'?'s/km':'m/s',points:[{time_s:0,value:0},{time_s:300,value:1200},{time_s:600,value:2400}]}];
  await open();assert.equal($('[name=activity_stream]').value,'heart_rate');assert.equal($('.selected-effort-window'),null);
  const original=JSON.stringify(data.detail),summary=$('.activity-stat-grid').textContent,requests=calls.length;
  $('.distance-efforts-choice').click();assert.equal($('[name=activity_stream]').value,kind);assert.ok($('.selected-effort-window'));assert.match($('.chart-interval-info').textContent,/1.000 m.*0:10.*4:20/);
  const select=$('[name=activity_stream]');select.value='heart_rate';select.dispatchEvent(new w.Event('change'));select.value=kind;select.dispatchEvent(new w.Event('change'));assert.ok($('.selected-effort-window'));
  assert.equal($('.activity-stat-grid').textContent,summary);assert.equal(JSON.stringify(data.detail),original);assert.equal(calls.length,requests);
  $('#close-activity').click();data.detail.activity_id=92;await open(activity(92,'Seconda corsa'));assert.equal($('.selected-effort-window'),null);
});

test('pool FIT results render without chart streams and never promise a missing chart highlight',async t=>{
  const {$,data,open,calls}=await setup(t);data.detail.sport='swimming';data.detail.streams=[];data.detail.pool_efforts={status:'available',metric:'pool_distance',source:'stored_fit_lengths',method:{pauses:'split'},groups:[{pool_length_m:25,stroke:'freestyle',distances:[{distance_m:100,distance_value:100,distance_unit:'m',status:'available',elapsed_seconds:95,start_s:20,end_s:115}]}]};
  await open();const root=$('.distance-efforts');assert.equal(root.dataset.metric,'pool_distance');assert.match(root.textContent,/vasche intere consecutive.*senza pause registrate/s);assert.doesNotMatch(root.textContent,/pause comprese|evidenziare il suo intervallo/);assert.equal($('.activity-stream-chart'),null);
  const requests=calls.length;root.querySelector('button').click();assert.match($('.distance-efforts-readout').textContent,/100 m · 1:35.*0:20 — 1:55/);assert.equal(calls.length,requests);
  assert.equal($('#close-activity').parentElement.firstElementChild,$('#close-activity'));
});

test('missing distance source stays explicit next to unrelated recorded series',async t=>{
  const {$,data,open}=await setup(t);data.detail.sport='running';data.detail.streams=[data.detail.streams[1]];data.detail.distance_efforts={status:'unavailable',metric:'running_distance',source:null,reasons:['missing_distance'],distances:[],coverage:{}};
  await open();assert.match($('.distance-efforts-unavailable').textContent,/Manca una serie di distanza cumulativa/);assert.equal($('.distance-efforts svg'),null);assert.equal($('.distance-efforts button'),null);assert.equal($('[name=activity_stream]').value,'heart_rate');
});
