'use strict';
// Current profile/diary UI with synthetic JSON. No provider, DB or browser use.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM,VirtualConsole}=require('jsdom');
const read=name=>fs.readFileSync(path.join(__dirname,'..',name),'utf8');
const clone=value=>JSON.parse(JSON.stringify(value));
const record=(id,value,date,source)=>({value,unit:'km',label:'Distanza massima',activity_id:id,activity_name:`Uscita ${id}`,date,source,scope:'whole_activity'});
function fixture(){return{name:'Profilo sintetico',headline:'Attività osservate',date:'2026-10-22',total_activities:3,weekly_minutes:null,weekly_load:null,
  duration_coverage:{known:2,expected:3,complete:false,status:'partial',known_minutes:120},
  totals:{all_time:{activities:3,hours:null,distance_km:null,coverage_summary:{hours:{known:2,expected:3,complete:false,status:'partial',known_value:2},distance_km:{known:1,expected:3,complete:false,status:'partial',known_value:30}}}},
  sports:[{sport:'bike',label:'Bici',activities:1,weekly_minutes:null,duration_coverage:{complete:false},totals:{all_time:{activities:3,hours:null,distance_km:null},days_90:{activities:2,hours:2,distance_km:40},days_28:{activities:1,hours:1,distance_km:20}},
    records:{all_time:{longest_distance_km:record(77,200,'2025-04-03','fit')},days_90:{longest_distance_km:record(88,40,'2026-10-01','intervals')},days_28:{longest_distance_km:record(99,20,'2026-10-18','manual')}}}],
  thresholds:{cycling_ftp_watts:{value:230,source:'measured',measured_on:'2026-09-15'}},restrictions:{reasons:[]},records_note:'Attività intere, non segmenti.',
  volume_comparison:{status:'insufficient_data',current:{start:'2026-09-25',end:'2026-10-22',total_minutes:null,known_minutes:120,known_activities:2,expected_activities:3},previous:{start:'2026-08-28',end:'2026-09-24',total_minutes:240,known_minutes:240,known_activities:4,expected_activities:4},change_pct:null,delta_minutes:null}};}
async function setup(t,value=fixture()){
  const errors=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error.message));const dom=new JSDOM(read('index.html'),{url:'https://fixture.invalid/app',runScripts:'outside-only',virtualConsole:console});t.after(()=>dom.window.close());const w=dom.window,$=selector=>w.document.querySelector(selector);
  for(const page of w.document.querySelectorAll('.page'))page.hidden=page.id!=='athlete';
  const data={profile:value,requests:[],opened:[],hold:null,fail:false};
  const el=(tag,text,cls)=>{const node=w.document.createElement(tag);if(text!=null)node.textContent=text;if(cls)node.className=cls;return node;};
  const button=(text,fn,cls)=>{const node=el('button',text,cls);node.type='button';node.addEventListener('click',fn);return node;};
  const loaded=()=>w.document.dispatchEvent(new w.CustomEvent('iron:loaded'));
  const navigate=id=>{for(const page of w.document.querySelectorAll('.page'))page.hidden=page.id!==id;w.document.dispatchEvent(new w.CustomEvent('iron:pagechange',{detail:{id}}));};
  w.fetch=()=>{throw new Error('No network allowed.');};w.IronCoachUI={el,button,number:(value,digits=1)=>value==null?'—':Number(value).toLocaleString('it-IT',{maximumFractionDigits:digits}),sourceLabel:value=>value,openActivity:id=>data.opened.push(id),action:async fn=>fn(),load:async()=>loaded(),api:async url=>{data.requests.push(url);assert.equal(url,'/athlete/derived-profile');if(data.hold)return data.hold;if(data.fail)throw new Error('Synthetic offline');return clone(data.profile);}};
  const flush=async()=>{for(let i=0;i<10;i++)await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(errors,[]);};w.eval(read('wellness.js'));await flush();return{w,$,data,loaded,navigate,flush};
}

test('strict unknown totals retain labeled measured subtotals and never imply complete coverage',async t=>{
  const {$}=await setup(t);const grid=$('.derived-grid');assert.match(grid.textContent,/Ore nello storico · parziale2Misurate 2\/3 attività/);assert.match(grid.textContent,/Distanza nello storico · km · parziale30Misurate 1\/3 attività/);
  assert.match(grid.textContent,/Media settimanale · ore—/);assert.doesNotMatch(grid.textContent,/Media settimanale · ore0/);assert.match($('.derived-sports').textContent,/— min\/settimana.*durate parziali/);
});
test('complete sample counts cannot override a backend aggregation failure',async t=>{
  const value=fixture();value.volume_comparison.current={...value.volume_comparison.current,known_activities:3,expected_activities:3,total_minutes:null,known_minutes:null,complete:false,coverage_status:'unavailable'};
  const {$}=await setup(t,value);const period=$('.profile-period[data-period="current"]');
  assert.equal(period.dataset.complete,'false');assert.match(period.textContent,/— min/);assert.doesNotMatch(period.textContent,/Dati completi/);assert.equal($('.profile-volume-change'),null);
});

test('missing duration in a whole period stays unknown while measured zero remains zero',async t=>{
  const value=fixture();value.volume_comparison.current={...value.volume_comparison.current,known_minutes:null,known_activities:0};
  const {$}=await setup(t,value);assert.match($('.profile-period[data-period="current"]').textContent,/— min.*Misurate 0\/3 attività/);assert.doesNotMatch($('.profile-period[data-period="current"]').textContent,/\b0 min/);
  assert.equal($('.profile-volume-change'),null);assert.equal($('.profile-duration-bar'),null);
});

test('complete measured periods expose descriptive dated bars and change without assigning fitness colors',async t=>{
  const value=fixture();value.volume_comparison={status:'available',current:{start:'2026-09-25',end:'2026-10-22',total_minutes:0,known_minutes:0,known_activities:3,expected_activities:3},previous:{start:'2026-08-28',end:'2026-09-24',total_minutes:240,known_minutes:240,known_activities:4,expected_activities:4},change_pct:-100,delta_minutes:-240};
  const {$}=await setup(t,value);const section=$('.profile-volume-comparison');assert.match(section.textContent,/2026-09-25 — 2026-10-22/);assert.match(section.textContent,/2026-08-28 — 2026-09-24/);assert.match($('.profile-volume-change').textContent,/-100% · -240 min/);
  assert.equal($('.profile-period[data-period="current"] .profile-duration-bar').style.width,'0%');assert.equal(section.querySelector('[data-tone]'),null);
});

test('record period survives refresh and navigation and restores focused period control',async t=>{
  const {$,data,loaded,navigate,flush}=await setup(t);const recent=$('[data-period="days_28"]');recent.click();recent.focus();assert.match($('.profile-record-grid').textContent,/20 km/);
  loaded();await flush();assert.equal($('.profile-record-tabs [aria-pressed="true"]').dataset.period,'days_28');assert.equal($.call(null,':focus').dataset.period,'days_28');assert.match($('.profile-record-grid').textContent,/20 km/);
  navigate('coach');await flush();navigate('athlete');await flush();assert.equal($('.profile-record-tabs [aria-pressed="true"]').dataset.period,'days_28');assert.equal(data.requests.length,3);
});

test('records expose exact observed date and source and keep the original activity action',async t=>{
  const {$,data}=await setup(t);const tile=$('.profile-record-tile');assert.match(tile.querySelector('.record-provenance').textContent,/2025-04-03 · File FIT/);assert.equal(tile.querySelector('time').dateTime,'2025-04-03');tile.querySelector('button').click();assert.deepEqual(data.opened,[77]);assert.match($('.profile-thresholds').textContent,/Riferimento del 2026-09-15/);
});

test('pending and failed refresh retains usable records with an explicit local stale status and retry',async t=>{
  const {$,data,loaded,flush}=await setup(t);let reject;data.hold=new Promise((_,no)=>{reject=no;});loaded();await flush();assert.equal($('#derived-profile').getAttribute('aria-busy'),'true');assert.ok($('.profile-record-tile'));assert.match($('.profile-data-status').textContent,/Aggiornamento.*ultimi dati caricati/);
  reject(new Error('Synthetic offline'));data.hold=null;await flush();assert.equal($('#derived-profile').hasAttribute('aria-busy'),false);assert.equal($('#derived-profile').dataset.stale,'true');assert.ok($('.profile-record-tile'));assert.match($('.profile-data-status').textContent,/Aggiornamento non riuscito.*potrebbero non essere aggiornati/);
  $('.profile-data-status button').click();await flush();assert.equal($('#derived-profile').dataset.stale,'false');assert.equal($('.profile-data-status button'),null);
});

test('late responses from a hidden profile do not replace visible content or discard a chosen period',async t=>{
  const {$,data,loaded,navigate,flush}=await setup(t);$('[data-period="days_90"]').click();let resolve;data.hold=new Promise(yes=>{resolve=yes;});loaded();await flush();navigate('coach');resolve({...fixture(),name:'Hidden obsolete response'});data.hold=null;await flush();assert.doesNotMatch($('#derived-profile').textContent,/Hidden obsolete response/);navigate('athlete');await flush();assert.equal($('.profile-record-tabs [aria-pressed="true"]').dataset.period,'days_90');
});

test('record dates, source labels and names remain inert text',async t=>{
  const value=fixture();Object.assign(value.sports[0].records.all_time.longest_distance_km,{activity_name:'<img src=x onerror=bad()>',date:'<svg onload=bad()>',source:'<script>bad()</script>'});
  const {$}=await setup(t,value);assert.equal($('.profile-record-tile').querySelector('img,svg,script'),null);assert.match($('.record-provenance').textContent,/<svg onload/);assert.match($('.record-provenance').textContent,/<script>/);
});

test('older profile JSON remains usable without additive coverage, comparison or28-day records',async t=>{
  const value=fixture();delete value.volume_comparison;delete value.duration_coverage;delete value.totals.all_time.coverage_summary;delete value.sports[0].records.days_28;value.weekly_minutes=480;value.totals.all_time.hours=30;
  const {$}=await setup(t,value);assert.equal($('.profile-volume-comparison'),null);assert.equal($('[data-period="days_28"]'),null);assert.match($('.derived-grid').textContent,/Media settimanale · ore8/);assert.ok($('.profile-record-tile'));
});

test('diary canonical filters and labels use the same normalized aliases and valid server category',async t=>{
  const console=new VirtualConsole(),errors=[];console.on('jsdomError',error=>errors.push(error.message));const dom=new JSDOM(read('index.html'),{url:'https://fixture.invalid/app#activities',runScripts:'outside-only',virtualConsole:console});t.after(()=>dom.window.close());const w=dom.window,$=selector=>w.document.querySelector(selector);w.setInterval=()=>1;w.clearInterval=()=>{};
  const app=read('app.js'),marker="page(location.hash.slice(1) || 'today'); action(load);";assert.ok(app.includes(marker));w.fetch=()=>{throw new Error('No network allowed.');};w.eval(app.replace(marker,"page(location.hash.slice(1) || 'today');")+'\nwindow.profileDiaryFixture={state,renderActivities};');
  const types=[['VirtualRide','cycling','Bici'],['Ride','cycling','Bici'],['TrailRun','running','Corsa'],['WeightTraining','strength','Forza'],['StrengthTraining','strength','Forza'],['Crossfit','strength','Forza'],['gym','strength','Forza'],['weightlifting','strength','Forza'],['palestra','strength','Forza']];
  for(const [sport,filter,label]of types){w.profileDiaryFixture.state.activities=[{id:1,sport,name:'QA activity',start_time:'2026-10-18T08:00:00Z',duration_seconds:null,distance_meters:null,source:'fit'}];$('#activity-sport').value=filter;w.profileDiaryFixture.renderActivities();assert.equal($('#activity-list').querySelectorAll('.list-row').length,1,sport);assert.match($('#activity-list .list-row small').textContent,new RegExp(` · ${label} ·`));assert.match($('#activity-list .badge').textContent,/— min · — km/);}
  w.profileDiaryFixture.state.activities=[{id:1,sport:'provider-new-name',sport_category:'swimming',name:'QA server category',start_time:'2026-10-18T08:00:00Z',source:'fit'}];$('#activity-sport').value='swimming';w.profileDiaryFixture.renderActivities();assert.match($('#activity-list .list-row small').textContent,/ · Nuoto ·/);
  assert.deepEqual(errors,[]);
});
