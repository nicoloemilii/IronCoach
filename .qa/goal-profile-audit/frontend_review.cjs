'use strict';
// Read-only production-source reproduction in JSDOM, synthetic data only.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {createRequire}=require('node:module');const ROOT=path.resolve(__dirname,'../..');
const {JSDOM,VirtualConsole}=createRequire(path.join(ROOT,'web/tests/package.json'))('jsdom');
const read=name=>fs.readFileSync(path.join(ROOT,name),'utf8');
const hash=name=>crypto.createHash('sha256').update(fs.readFileSync(path.join(ROOT,name))).digest('hex');
const errors=[],calls=[],openedActivities=[];const result={scope:'Read-only synthetic production-source DOM audit; no browser, provider or personal data',source_sha256:{'web/wellness.js':hash('web/wellness.js'),'web/app.js':hash('web/app.js')}};
const profile={name:'QA synthetic profile',headline:'Dati sintetici per audit frontend',date:'2026-10-22',generated_at:'2026-10-22T10:00:00Z',total_activities:3,weekly_minutes:60,weekly_load:null,
  totals:{all_time:{activities:3,hours:2,distance_km:30,coverage:{duration_seconds:2,distance_meters:1,elevation_gain_meters:0,calories:0}}},
  sports:[{sport:'bike',label:'Bici',activities:1,weekly_minutes:60,totals:{all_time:{activities:3,hours:2,distance_km:30},days_90:{activities:1,hours:1,distance_km:20}},
    records:{all_time:{longest_distance_km:{value:200,unit:'km',label:'Distanza massima',activity_id:77,activity_name:'Giro storico sintetico',date:'2025-04-03',source:'fit',scope:'whole_activity'}},
      days_90:{longest_distance_km:{value:20,unit:'km',label:'Distanza massima',activity_id:88,activity_name:'Giro recente sintetico',date:'2026-10-18',source:'intervals',scope:'whole_activity'}}}}],
  thresholds:{cycling_ftp_watts:{value:230,source:'measured',measured_on:'2026-09-15'}},records_note:'Record sulle attività intere, non su segmenti.',restrictions:{reasons:[]}};
const clone=value=>JSON.parse(JSON.stringify(value));
let held=null,fail=false;
function createDOM(){const console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error.message));return new JSDOM(read('web/index.html'),{url:'https://fixture.invalid/app',runScripts:'outside-only',virtualConsole:console});}
async function flush(){for(let i=0;i<10;i++)await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(errors,[]);}
async function run(){
  const dom=createDOM(),w=dom.window,$=selector=>w.document.querySelector(selector);
  try{
    for(const page of w.document.querySelectorAll('.page'))page.hidden=page.id!=='athlete';
    const make=(tag,text,cls)=>{const node=w.document.createElement(tag);if(text!=null)node.textContent=text;if(cls)node.className=cls;return node;};
    const button=(text,fn,cls)=>{const node=make('button',text,cls);node.type='button';node.addEventListener('click',fn);return node;};
    w.fetch=()=>{throw new Error('No HTTP permitted.');};
    w.IronCoachUI={el:make,button,number:(value,digits=1)=>value==null?'—':Number(value).toLocaleString('it-IT',{maximumFractionDigits:digits}),action:async fn=>fn(),load:async()=>{},openActivity:id=>openedActivities.push(id),
      api:async url=>{calls.push(url);assert.equal(url,'/athlete/derived-profile');if(held)return held.promise;if(fail)throw new Error('Synthetic unavailable endpoint');return clone(profile);}};
    w.eval(read('web/wellness.js'));await flush();
    const firstTile=$('.profile-record-tile');result.records={displayed:firstTile.textContent,backend_date:profile.sports[0].records.all_time.longest_distance_km.date,
      date_visible:firstTile.textContent.includes('2025-04-03'),source_visible:firstTile.textContent.includes('fit'),threshold_date_visible:$('.profile-thresholds').textContent.includes('2026-09-15')};
    firstTile.querySelector('button').click();result.records.opened_activity_ids=[...openedActivities];assert.deepEqual(openedActivities,[77]);
    const ninety=$('.profile-record-tabs [data-period="days_90"]');ninety.click();ninety.focus();result.period={before_refresh:$('.profile-record-tabs [aria-pressed="true"]').dataset.period,before_text:$('.profile-record-grid').textContent};
    w.document.dispatchEvent(new w.CustomEvent('iron:loaded'));await flush();
    result.period.after_refresh=$('.profile-record-tabs [aria-pressed="true"]').dataset.period;result.period.after_text=$('.profile-record-grid').textContent;result.period.focus_after_refresh=w.document.activeElement.tagName;
    result.partial_totals={text:$('.derived-grid').textContent,backend_coverage:profile.totals.all_time.coverage,visible_partial_label:/parzial|copertura|1 su 3/i.test($('.derived-grid').textContent)};
    held={};held.promise=new Promise((resolve,reject)=>{held.resolve=resolve;held.reject=reject;});w.document.dispatchEvent(new w.CustomEvent('iron:loaded'));await flush();
    result.loading={old_records_remain:!!$('.profile-record-tile'),aria_busy:$('#derived-profile').getAttribute('aria-busy'),visible_pending_label:/Caricamento|Aggiornamento in corso/i.test($('#derived-profile').textContent)};
    fail=true;held.reject(new Error('Synthetic unavailable endpoint'));held=null;await flush();
    result.after_failure={text:$('#derived-profile').textContent,has_previous_records:!!$('.profile-record-tile'),has_retry:[...$('#derived-profile').querySelectorAll('button')].some(button=>button.textContent==='Riprova')};
    result.empty_loading_profile_is_announced=!!$('#derived-profile').getAttribute('aria-live');
  }finally{w.close();}
  const diary=createDOM(),d=diary.window,$d=selector=>d.document.querySelector(selector);
  try{
    const app=read('web/app.js');const prefix=app.slice(0,app.indexOf('async function api('));const renderer=app.slice(app.indexOf('function renderActivities()'),app.indexOf('function renderReview('));
    const sourceLabels=app.slice(app.indexOf('const sources ='),app.indexOf('function listText('));
    d.eval(`${prefix}\n${sourceLabels}\n${renderer}\nwindow.diaryAudit={state,renderActivities};`);
    d.diaryAudit.state.activities=[{id:1,sport:'VirtualRide',name:'Rulli sintetici',start_time:'2026-10-18T08:00:00Z',source:'intervals',duration_seconds:3600,distance_meters:30000},
      {id:2,sport:'TrailRun',name:'Corsa sintetica',start_time:'2026-10-18T10:00:00Z',source:'fit',duration_seconds:1800,distance_meters:5000},
      {id:3,sport:'WeightTraining',name:'Pesi sintetici',start_time:'2026-10-18T18:00:00Z',source:'fit',duration_seconds:null,distance_meters:null}];
    result.diary_labels=[];
    for(const key of ['cycling','running','strength']){$d('#activity-sport').value=key;d.diaryAudit.renderActivities();const rows=[...d.document.querySelectorAll('#activity-list .list-row')];assert.equal(rows.length,1);result.diary_labels.push({filter:key,displayed:rows[0].querySelector('small').textContent,missing_duration_kept:rows[0].querySelector('.badge').textContent});}
    result.diary_filters=[...$d('#activities').querySelectorAll('input,select')].map(element=>({id:element.id,type:element.type,name:element.name}));
  }finally{d.close();}
  result.calls=calls;result.http_requests=0;result.mutations=0;result.dom_errors=errors;result.ok=true;
  fs.writeFileSync(path.join(__dirname,'frontend_review.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result,null,2));
}
run().catch(error=>{console.error(error.stack);process.exitCode=1;});
