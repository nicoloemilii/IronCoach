'use strict';
// Source-only synthetic audit. No real backend, provider or personal DB access.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {createRequire}=require('node:module');const ROOT=path.resolve(__dirname,'../..');
const {JSDOM,VirtualConsole}=createRequire(path.join(ROOT,'web/tests/package.json'))('jsdom');
const source=fs.readFileSync(path.join(ROOT,'web/app.js'),'utf8'),html=fs.readFileSync(path.join(ROOT,'web/index.html'),'utf8');
const result={scope:'Synthetic production-source JSDOM audit; no HTTP/backend/provider or visual browser',source_sha256:crypto.createHash('sha256').update(source).digest('hex')};
const rows=Array.from({length:10000},(_,index)=>({id:index+1,name:`Attività numero ${index+1}`,source:'fit',sport:'cycling',sport_category:'cycling',start_time:'2026-10-18T08:00:00Z',duration_seconds:3600,distance_meters:30000,load:null}));
const errors=[];const doms=[];
function create(){const console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error.message));const dom=new JSDOM(html,{url:'https://fixture.invalid/app',runScripts:'outside-only',virtualConsole:console});doms.push(dom);return dom.window;}
async function ticks(){for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(errors,[]);}
async function run(){
 const w=create(),calls=[];let release,rendered=0;
 for(const name of ['renderIntervals','renderReview','fillCheckin','fillRacePreparation','renderHealth','renderCoach','renderGoals','renderStrava','renderStravaActivities','renderDuplicates','updateFeedbackAvailability','updateIntervalsControls','updateStravaControls'])w[name]=()=>{};
 w.render=()=>{rendered++;};w.intervalsConnection={};
 w.fetch=async url=>{calls.push(String(url));if(String(url)==='/activities')return new Promise(resolve=>{release=()=>resolve({ok:true,status:200,json:async()=>rows});});const data=String(url)==='/coach/today'?{date:'2026-10-22',training_paused:false}:String(url)==='/planning/profile'?{profile:{},revision:1}:String(url).startsWith('/planning/week?')?{can_undo:false}:String(url)==='/dashboard/home'?{date:'2026-10-22'}:{};return{ok:true,status:200,json:async()=>data};};
 w.eval(source.slice(0,source.indexOf('function setPlanTab('))+'\nwindow.startupAudit={state,load,action};');
 const loading=w.startupAudit.action(w.startupAudit.load);await ticks();
 result.startup_while_diary_pending={visible_page:'today',request_count:calls.length,all_history_requested:calls.includes('/activities'),home_data_already_received:!!w.startupAudit.state.home,main_render_calls:rendered,busy:w.startupAudit.state.busy,refresh_disabled:w.document.querySelector('#refresh').disabled,navigation_still_enabled:!w.document.querySelector('nav [data-page]').disabled};
 assert.equal(rendered,0);assert.equal(w.startupAudit.state.busy,true);release();await loading;await ticks();result.startup_after_diary={main_render_calls:rendered,busy:w.startupAudit.state.busy,all_rows_retained:w.startupAudit.state.activities.length,serialized_payload_bytes:Buffer.byteLength(JSON.stringify(rows))};

 const d=create(),$=selector=>d.document.querySelector(selector),detailCalls=[];d.setInterval=()=>1;d.clearInterval=()=>{};
 d.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};d.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');this.dispatchEvent(new d.Event('close'));};
 d.fetch=async url=>{detailCalls.push(String(url));if(String(url).endsWith('/detail'))return{ok:true,status:200,json:async()=>({activity_id:90001,name:'Record fuori pagina',sport:'cycling',start_time:'2026-01-01T08:00:00Z',summary:{duration_seconds:3600,distance_meters:30000,load:null},streams:[],route:{available:false,points:[]},availability:{message:'Synthetic summary only.'}})};if(String(url).endsWith('/assessment'))return{ok:true,status:200,json:async()=>({score:null,judgment:'Non valutabile',evidence:[],actions:[],limits:[]})};throw Error(`Unwanted request ${url}`);};
 const marker="page(location.hash.slice(1) || 'today'); action(load);";assert.ok(source.includes(marker));d.eval(fs.readFileSync(path.join(ROOT,'web/metric-status.js'),'utf8'));d.eval(source.replace(marker,"page(location.hash.slice(1) || 'today');")+'\nwindow.diaryAudit={state,renderActivities};');
 let nameReads=0;const observedRows=rows.map(row=>{const copy={...row};Object.defineProperty(copy,'name',{enumerable:true,get(){nameReads++;return row.name;}});return copy;});d.diaryAudit.state.activities=observedRows;d.diaryAudit.renderActivities();
 result.client_search=[];
 for(const query of ['Attività','Attività numero','Attività numero 9']){nameReads=0;$('#activity-search').value=query;const start=performance.now();$('#activity-search').dispatchEvent(new d.Event('input',{bubbles:true}));result.client_search.push({query,name_property_reads:nameReads,history_rows:rows.length,rendered_rows:$('#activity-list').querySelectorAll('.list-row').length,synthetic_dom_elapsed_ms:Math.round((performance.now()-start)*100)/100});assert.ok(nameReads>=rows.length);}
 $('#activity-search').value='';$('#activity-search').dispatchEvent(new d.Event('input',{bubbles:true}));const first=$('#activity-list .list-row');const more=[...$('#activity-list').querySelectorAll('button')].find(button=>button.textContent==='Mostra altre 50 attività');assert.ok(more);more.click();
 result.client_paging={history_rows:rows.length,rendered_after_more:$('#activity-list').querySelectorAll('.list-row').length,first_row_preserved:$('#activity-list .list-row')===first,http_calls_for_search_and_paging:detailCalls.length};
 assert.equal(result.client_paging.rendered_after_more,100);assert.equal(result.client_paging.first_row_preserved,false);
 await d.IronCoachUI.openActivity(90001);await ticks();result.outside_loaded_history={requested_id:90001,in_client_cache:d.diaryAudit.state.activities.some(row=>row.id===90001),calls:detailCalls,dialog_open:$('#activity-dialog').open,title:$('.activity-overview h2').textContent};assert.equal(result.outside_loaded_history.in_client_cache,false);assert.deepEqual(detailCalls,['/activities/90001/detail','/activities/90001/assessment']);assert.equal(result.outside_loaded_history.title,'Record fuori pagina');
 result.dom_errors=errors;result.real_http_requests=0;result.personal_data_opened=false;result.ok=true;fs.writeFileSync(path.join(__dirname,'frontend_audit.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result,null,2));
}
run().catch(error=>{console.error(error.stack);process.exitCode=1;}).finally(()=>doms.forEach(dom=>dom.window.close()));
