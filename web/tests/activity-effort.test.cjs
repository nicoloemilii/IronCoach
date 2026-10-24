'use strict';
// Production renderer and activity UI, synthetic JSON only. No browser or IO.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require('jsdom');
const moduleSource = fs.readFileSync(path.join(__dirname, '../activity-effort.js'), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
function comparison() {
  return {status:'available',reasons:[],source:'stored_observation_streams',window:{start_s:0,midpoint_s:1800,end_s:3600,source:'elapsed_seconds'},
    halves:[{key:'first',start_s:0,end_s:1800,duration_seconds:1800,paired_seconds:1620,coverage_pct:90,power_mean_w:200,heart_rate_mean_bpm:140,ratio_w_per_bpm:200/140,power_zero_seconds:15},
      {key:'second',start_s:1800,end_s:3600,duration_seconds:1800,paired_seconds:1620,coverage_pct:90,power_mean_w:196,heart_rate_mean_bpm:145,ratio_w_per_bpm:196/145,power_zero_seconds:30}],
    difference:{power_w:-4,heart_rate_bpm:5},change_pct:-5.37931,
    method:{version:'raw-paired-1',weighting:'trapezoidal',gap_limit_seconds:10,min_coverage_pct:80,min_paired_seconds_per_half:300,zero_power:'included',halves:'equal_elapsed_time',raw_point_limit:250000},
    limits:['Un confronto descrittivo delle misure registrate.']};
}
function setup(t) {
  const errors = []; const console = new VirtualConsole(); console.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM('<!doctype html><body></body>',{runScripts:'outside-only',virtualConsole:console});t.after(()=>dom.window.close());const w=dom.window;
  let requests=0;w.fetch=()=>{requests++;throw new Error('No network in this renderer.');};w.eval(moduleSource);
  const render=value=>{const root=w.IronActivityEffort.render(value);if(root)w.document.body.append(root);assert.deepEqual(errors,[]);return root;};
  return {w,render,requests:()=>requests};
}

test('paired halves show weighted means, exact boundaries, coverage and descriptive deltas',t=>{
  const {render,requests}=setup(t);const root=render({effort_comparison:comparison()});
  assert.match(root.querySelector('.effort-scope').textContent,/Intera registrazione.*0:00 — 1:00:00.*zoom/);
  const halves=root.querySelectorAll('.effort-half');assert.equal(halves.length,2);assert.equal(halves[0].dataset.half,'first');assert.equal(halves[1].dataset.half,'second');
  assert.match(halves[0].textContent,/0:00 — 30:00/);assert.match(halves[1].textContent,/30:00 — 1:00:00/);
  assert.match(halves[0].querySelector('.effort-values').textContent,/200 W.*140 bpm/);assert.match(halves[1].querySelector('.effort-values').textContent,/196 W.*145 bpm/);
  assert.match(halves[0].querySelector('.effort-coverage').textContent,/27:00 su 30:00 · 90%/);
  assert.match(root.querySelector('.effort-change').textContent,/potenza -4 W.*frequenza cardiaca \+5 bpm/);assert.equal(requests(),0);
  assert.equal(root.querySelector('[data-tone]'),null,'The comparison must not assign favorable/unfavorable fitness colors.');
});

test('ratio sign and formula stay in collapsed methods without becoming a score',t=>{
  const {render}=setup(t);const value=comparison();value.change_pct=3.25;value.difference={power_w:4,heart_rate_bpm:-2};
  const root=render({effort_comparison:value});const details=root.querySelector('.effort-method');assert.equal(details.open,false);
  assert.match(details.querySelector('.effort-ratio').textContent,/\+3,3 %/);assert.match(details.textContent,/potenza media pesata ÷ frequenza cardiaca media pesata/);
  assert.match(details.textContent,/100 ×.*seconda metà ÷ rapporto della prima metà − 1/);assert.match(details.textContent,/Non è un punteggio di fitness/);
  assert.match(root.querySelector('.effort-change').textContent,/\+4 W.*-2 bpm/);assert.equal(root.querySelector('[role="meter"], [role="progressbar"]'),null);
});

test('backend weighted means are displayed rather than recomputed from reduced chart points',t=>{
  const {render}=setup(t);const value=comparison();value.halves[0].power_mean_w=126.55;
  const root=render({effort_comparison:value,streams:[{kind:'power',points:[{time_s:0,value:1000},{time_s:1800,value:1000}]}]});
  assert.match(root.querySelector('.effort-half[data-half="first"] .effort-values').textContent,/126,6 W/);assert.doesNotMatch(root.querySelector('.effort-values').textContent,/1.000/);
});

test('insufficient coverage retains observed halves but hides gated comparison values',t=>{
  const {render}=setup(t);const value=comparison();value.status='insufficient_data';value.reasons=['insufficient_coverage'];value.halves[1].coverage_pct=40;
  const root=render({effort_comparison:value});assert.match(root.querySelector('.effort-unavailable').textContent,/copertura.*insufficiente/i);
  assert.equal(root.querySelector('.effort-change'),null);assert.match(root.querySelector('.effort-ratio').textContent,/non disponibile/);
  assert.match(root.querySelector('.effort-half[data-half="second"] .effort-coverage').textContent,/40%/);
  const ratioCells=[...root.querySelectorAll('tbody tr')].map(row=>row.querySelectorAll('td')[4].textContent);assert.deepEqual(ratioCells,['—','—']);
});

test('a missing half stays unknown and cannot become zero minutes or zero watts',t=>{
  const {render}=setup(t);const value=comparison();value.status='insufficient_data';value.reasons=['missing_power'];value.halves[1]={key:'second',start_s:1800,end_s:3600,duration_seconds:1800};
  const root=render({effort_comparison:value});const second=root.querySelector('.effort-half[data-half="second"]');assert.match(second.textContent,/— W.*— bpm/);assert.match(second.textContent,/Dati abbinati: — su 30:00 · —%/);
  assert.doesNotMatch(second.textContent,/\b0 W|0:00 su/);
});

test('measured zero power is retained and never automatically called a stop',t=>{
  const {render}=setup(t);const value=comparison();value.status='insufficient_data';value.reasons=['zero_first_ratio'];value.halves[0].power_mean_w=0;value.halves[0].power_zero_seconds=600;
  const root=render({effort_comparison:value});assert.match(root.querySelector('.effort-half[data-half="first"]').textContent,/0 W/);assert.match(root.querySelector('.effort-half').textContent,/10:00.*Non identifica automaticamente una sosta/);
  assert.match(root.querySelector('.effort-unavailable').textContent,/prima metà è zero/);assert.match(root.querySelector('.effort-method').textContent,/zeri di potenza registrati sono inclusi/);
});

test('truncated or ambiguous input produces a compact unavailable state without misleading half metrics',t=>{
  const {render}=setup(t);const value=comparison();value.status='unavailable';value.reasons=['truncated_input','ambiguous_time'];
  const root=render({effort_comparison:value});assert.equal(root.querySelector('.effort-halves'),null);assert.equal(root.querySelector('table'),null);assert.equal(root.querySelector('.effort-change'),null);
  assert.match(root.textContent,/supera il limite elaborabile/);assert.match(root.textContent,/scala temporale contiene dati ambigui/);
});

test('older detail contracts and unsupported sports do not add irrelevant sections',t=>{
  const {render}=setup(t);assert.equal(render({streams:[]}),null);assert.equal(render(null),null);assert.equal(render({effort_comparison:{status:'not_applicable',reasons:['unsupported_sport']}}),null);
});

test('source, reasons and limits cannot introduce executable markup',t=>{
  const {render}=setup(t);const value=comparison();value.source='<img src=x onerror=bad()>';value.limits=['<svg onload=bad()>'];value.reasons=['<script>bad()</script>'];
  const root=render({effort_comparison:value});assert.equal(root.querySelector('img,svg,script'),null);assert.match(root.textContent,/<img src=x/);assert.match(root.textContent,/<svg onload/);
});

test('nonfinite, boolean and numeric-string inputs never appear as fabricated numerical values',t=>{
  const {render}=setup(t);const value=comparison();value.halves[0].power_mean_w=Infinity;value.halves[0].heart_rate_mean_bpm='140';value.halves[0].paired_seconds=false;value.halves[0].coverage_pct=NaN;value.difference={power_w:false,heart_rate_bpm:Infinity};
  const root=render({effort_comparison:value});assert.match(root.querySelector('.effort-half').textContent,/— W.*— bpm/);assert.doesNotMatch(root.outerHTML,/NaN|Infinity/);assert.match(root.querySelector('.effort-change').textContent,/— W.*— bpm/);
});

test('native details and a captioned table expose method, thresholds and row/column headers',t=>{
  const {w,render}=setup(t);const root=render({effort_comparison:comparison()});const details=root.querySelector('details');details.querySelector('summary').click();assert.equal(details.open,true);
  const table=root.querySelector('table');assert.ok(table.querySelector('caption'));assert.equal(table.querySelectorAll('thead th[scope="col"]').length,7);assert.equal(table.querySelectorAll('tbody th[scope="row"]').length,2);
  assert.equal(root.querySelector('.effort-table-wrap').tabIndex,0);assert.match(details.textContent,/80%.*5:00/);assert.match(details.textContent,/10 s/);assert.match(details.textContent,/Fonte: Serie conservate/);
});

async function appSetup(t) {
  const errors=[];const console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error.message));
  const dom=new JSDOM(fs.readFileSync(path.join(__dirname,'../index.html'),'utf8'),{url:'http://fixture.invalid/app#activities',runScripts:'outside-only',virtualConsole:console});t.after(()=>dom.window.close());const w=dom.window,$=selector=>w.document.querySelector(selector);const calls=[];
  w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');this.dispatchEvent(new w.Event('close'));};w.setInterval=()=>1;w.clearInterval=()=>{};
  const data={detail:{activity_id:91,name:'Rulli sintetici',sport:'VirtualRide',start_time:'2026-10-17T08:00:00Z',summary:{duration_seconds:3600},route:{available:false,points:[]},effort_comparison:comparison(),streams:[{kind:'power',label:'Potenza',unit:'W',points:[{time_s:0,value:200},{time_s:3600,value:196}]},{kind:'heart_rate',label:'Frequenza cardiaca',unit:'bpm',points:[{time_s:0,value:140},{time_s:3600,value:145}]}]},fetch:null};
  w.fetch=async(url,options={})=>{calls.push({url:String(url),method:options.method||'GET'});if(data.fetch){const response=await data.fetch(String(url));if(response)return response;}if(String(url).endsWith('/detail'))return{ok:true,status:200,json:async()=>clone(data.detail)};if(String(url).endsWith('/assessment'))return{ok:true,status:200,json:async()=>({score:null,judgment:'Non valutabile',evidence:[],actions:[],limits:[]})};throw new Error(`Unexpected ${url}`);};
  const app=fs.readFileSync(path.join(__dirname,'../app.js'),'utf8'),marker="page(location.hash.slice(1) || 'today'); action(load);";assert.ok(app.includes(marker));
  w.eval(fs.readFileSync(path.join(__dirname,'../charts.js'),'utf8'));w.eval(fs.readFileSync(path.join(__dirname,'../metric-status.js'),'utf8'));w.eval(app.replace(marker,"page(location.hash.slice(1) || 'today');")+'\nwindow.__effortFixture={showActivity};');w.eval(moduleSource);
  const flush=async()=>{for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(errors,[]);};
  const open=async(id=91)=>{await w.__effortFixture.showActivity({id});await flush();};return{w,$,calls,data,flush,open};
}

test('actual activity UI mounts the comparison once and keeps it outside cached stream chart views',async t=>{
  const {w,$,calls,open,flush}=await appSetup(t);await open();
  const panel=$('.activity-stream-panel'),effort=panel.querySelector('.activity-effort');assert.ok(effort);assert.equal(effort.previousElementSibling.querySelector('.interactive-chart')!=null,true);
  const select=panel.querySelector('[name="activity_stream"]');for(const kind of ['heart_rate','power','heart_rate','power']){select.value=kind;select.dispatchEvent(new w.Event('change'));await flush();}
  assert.equal(panel.querySelectorAll('.activity-effort').length,1);assert.equal(panel.querySelector('.activity-effort'),effort);assert.equal(calls.length,2);assert.ok(calls.every(call=>call.method==='GET'));
});

test('actual activity UI remains compatible with details lacking the new comparison',async t=>{
  const {$,data,open}=await appSetup(t);delete data.detail.effort_comparison;await open();assert.ok($('.activity-stream-chart'));assert.equal($('.activity-effort'),null);
});

test('a late response from another activity cannot replace the comparison currently open',async t=>{
  const {w,$,data,open,flush}=await appSetup(t);let release;const held=new Promise(resolve=>{release=resolve;});
  data.fetch=async url=>{if(url==='/activities/91/detail'){await held;return{ok:true,status:200,json:async()=>clone(data.detail)};}if(url==='/activities/92/detail'){const next=clone(data.detail);next.name='Seconda attività';next.effort_comparison.difference.power_w=42;return{ok:true,status:200,json:async()=>next};}};
  const first=w.__effortFixture.showActivity({id:91});await flush();await open(92);release();await first;await flush();
  assert.match($('.activity-overview h2').textContent,/Seconda attività/);assert.match($('.effort-change').textContent,/\+42 W/);assert.equal($('#activity-detail').querySelectorAll('.activity-effort').length,1);
});
