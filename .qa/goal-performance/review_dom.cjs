'use strict';
// Production HTML/JS and actual HTTP on a dedicated synthetic server. No visual QA.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {createRequire}=require('node:module'),ROOT=path.resolve(__dirname,'../..'),BASE='http://127.0.0.1:8004';
const {JSDOM,VirtualConsole,requestInterceptor}=createRequire(path.join(ROOT,'web/tests/package.json'))('jsdom');
const requests=[],httpErrors=[],domErrors=[],external=[],cancellations=[],injections=[],assets={},checks=[];
let dom,active=0,last=0,holdDiary=true,releaseDiary,failDiary=false;
const hash=value=>crypto.createHash('sha256').update(value).digest('hex');
const sourceFiles=['web/index.html',...Array.from(fs.readFileSync(path.join(ROOT,'web/index.html'),'utf8').matchAll(/(?:src|href)="(\/web\/[^"?]+)"/g),match=>match[1].slice(1))];
const hashes=()=>Object.fromEntries([...new Set(sourceFiles)].map(name=>[name,hash(fs.readFileSync(path.join(ROOT,name)))]));
const sourceBefore=hashes(),result={scope:'Production source in JSDOM with actual local HTTP. Functional integration, not a visual browser.',synthetic_only:true,personal_data_accessed:false,browser_used:false,visual_verification:false,source_sha256:sourceBefore,checks};
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(predicate,label){const end=Date.now()+25000;while(!predicate()){if(Date.now()>end)throw Error('Timed out: '+label);await sleep(20);}}
async function idle(){await until(()=>active===0&&Date.now()-last>150,'all HTTP settled');}
async function request(input,options={}){
 const url=new URL(typeof input==='string'?input:input.url,BASE),method=options.method||'GET';
 assert.equal(url.origin,BASE,'Synthetic localhost only');assert.equal(method,'GET','GET only');assert.notEqual(url.pathname,'/activities','The browser must never download the legacy full archive');
 const record={method,path:url.pathname+url.search};requests.push(record);active++;last=Date.now();
 try{
  const response=await fetch(url,{...options,headers:{Origin:BASE,...Object.fromEntries(new Headers(options.headers||{}))}});record.status=response.status;
  if(!response.ok)httpErrors.push({path:record.path,status:response.status});
  if(url.pathname==='/app'||url.pathname.startsWith('/web/'))assets[url.pathname]=hash(Buffer.from(await response.clone().arrayBuffer()));
  if(url.pathname==='/activities/diary'&&holdDiary){holdDiary=false;record.response_held=true;await new Promise(resolve=>releaseDiary=resolve);}
  if(url.pathname==='/activities/diary'&&failDiary){failDiary=false;record.injected_status=503;injections.push(record.path);return new Response(JSON.stringify({detail:'Errore sintetico di rete'}),{status:503,headers:{'Content-Type':'application/json'}});}
  return response;
 }catch(error){if(error.name==='AbortError'&&options.signal?.aborted){record.cancelled=true;cancellations.push(record.path);}else httpErrors.push({path:record.path,error:error.message});throw error;}
 finally{active--;last=Date.now();}
}
async function read(path){const response=await request(path);assert.ok(response.ok);return response.json();}
async function run(){
 const before=await read('/_qa/state'),fixture=await read('/_qa/fixture');assert.equal(before.synthetic,true);assert.equal(before.fixture,'goal-performance-diary-v1');
 const console=new VirtualConsole();console.on('jsdomError',error=>domErrors.push(error.message));
 dom=await JSDOM.fromURL(BASE+'/app#today',{runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:console,
  resources:{interceptors:[requestInterceptor(async resource=>{if(new URL(resource.url).origin!==BASE){external.push(resource.url);throw Error('Nonlocal resource blocked');}return request(resource.url,{method:resource.method,headers:Object.fromEntries(resource.headers)});})]},
  beforeParse(w){w.Headers=Headers;w.Request=Request;w.Response=Response;w.fetch=(input,options={})=>request(input,options);w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');this.dispatchEvent(new w.Event('close'));};w.addEventListener('error',event=>domErrors.push(event.error?.message||event.message));}
 });
 const w=dom.window,$=selector=>w.document.querySelector(selector),rows=()=>[...w.document.querySelectorAll('#activity-list .list-row')],ids=()=>rows().map(node=>Number(node.dataset.activityId));
 await until(()=>typeof releaseDiary==='function'&&w.IronCoachUI&&w.ironViewSnapshot&&$('#refresh').disabled===false,'Home rendered independently of held diary');
 assert.equal($('#today').hidden,false);assert.equal($('#activity-list').getAttribute('aria-busy'),'true');
 checks.push('Home and main controls finish loading while the first bounded diary response is still held.');
 releaseDiary();await idle();
 async function navigate(page){$(`nav [data-page="${page}"]`).click();await until(()=>!$('#'+page).hidden,'navigate '+page);await idle();}
 async function query(text,sport=''){
  const started=requests.length;$('#activity-sport').value=sport;$('#activity-search').value=text;$('#activity-search').dispatchEvent(new w.Event('input',{bubbles:true}));
  await until(()=>requests.slice(started).some(record=>{const url=new URL(record.path,BASE);return url.pathname==='/activities/diary'&&(url.searchParams.get('q')||'')===text&&(url.searchParams.get('sport')||'')===sport;}),'new query '+text);await idle();
 }
 await navigate('activities');assert.equal(rows().length,50);const first=rows()[0],firstIds=ids();
 assert.match($('.activity-diary-count').textContent,/Mostrate 50 di 147/);
 const more=$('.activity-diary-controls button');more.focus();more.click();await idle();assert.equal(rows()[0],first);assert.equal(rows().length,100);assert.equal(w.document.activeElement,rows()[50]);
 more.click();await idle();assert.equal(rows().length,147);assert.equal(new Set(ids()).size,147);assert.equal(more.hidden,true);
 const expected=[...fixture.rows].sort((a,b)=>b.start_time.localeCompare(a.start_time)||b.id-a.id).map(row=>row.id);assert.deepEqual(ids(),expected);assert.deepEqual(firstIds,expected.slice(0,50));
 checks.push('Three actual cursor pages return 50/50/47 rows in date/id order; previous nodes and keyboard focus are retained, with no duplicates.');
 await query('straße');assert.deepEqual(ids().sort((a,b)=>a-b),[143,146]);
 await query('CAFÉ');assert.deepEqual(ids(),[147]);
 await query('%');assert.deepEqual(ids(),[145]);
 await query('_');assert.deepEqual(ids(),[145]);
 for(const category of ['cycling','running','swimming','strength','other']){await query('',category);while(!more.hidden){more.click();await idle();}assert.equal(rows().length,fixture.rows.filter(row=>row.category===category).length);}
 checks.push('Actual SQL filters handle Unicode casefold, literal percent/underscore and all five shared sport categories.');
 await query('straße');assert.equal(rows().length,2);
 await w.IronCoachUI.openActivity(147);await idle();assert.equal($('#activity-dialog').open,true);assert.match($('.activity-overview h2').textContent,/Café/);assert.ok($('.activity-stream-chart'));assert.equal($('#close-activity').closest('.dialog-top').firstElementChild.id,'close-activity');$('#close-activity').click();await idle();
 checks.push('A record outside the loaded two-row cache opens by ID with its actual stored raw chart; the back control is first in the dialog header.');
 failDiary=true;const preserved=rows()[0];await query('nessun-risultato');assert.equal(rows()[0],preserved);assert.equal($('#activity-list').dataset.stale,'true');assert.match($('.activity-diary-count').textContent,/Ultimi risultati caricati.*straße/);assert.match($('.activity-diary-status').textContent,/Errore sintetico/);
 $('.activity-diary-status button').click();await idle();assert.equal(rows().length,0);assert.match($('#activity-list .empty-state').textContent,/corrisponde ai filtri/);assert.equal(w.ironViewSnapshot.hasActivities,true);
 checks.push('An injected transient failure retains old results and their original scope, marks them stale and retries the current bounded filter; empty filtered results preserve archive presence.');
 await query('CAFÉ','cycling');await w.IronCoachUI.load();await idle();assert.equal($('#activity-search').value,'CAFÉ');assert.equal($('#activity-sport').value,'cycling');assert.deepEqual(ids(),[147]);
 checks.push('Global refresh retains the current query/category, uses a first bounded page and never falls back to the full archive.');
 holdDiary=true;releaseDiary=null;const pending=w.IronCoachUI.load();await until(()=>typeof releaseDiary==='function','hold obsolete response');
 const oldRelease=releaseDiary;$('#activity-sport').value='';$('#activity-search').value='straße';$('#activity-search').dispatchEvent(new w.Event('input',{bubbles:true}));
 await until(()=>$('#activity-list').getAttribute('aria-busy')==='false'&&rows().length===2,'newer filter settled before old response');
 const current=ids();oldRelease();await pending;await idle();assert.deepEqual(ids(),current);assert.deepEqual(current.sort((a,b)=>a-b),[143,146]);
 checks.push('A delayed obsolete real response cannot overwrite a newer successful filter result.');
 const after=await read('/_qa/state');assert.deepEqual(after,before,'All synthetic data and provider counters stay unchanged');
 assert.deepEqual(hashes(),sourceBefore,'No source change during integration');
 for(const [url,digest]of Object.entries(assets)){const local=url==='/app'?'web/index.html':url.slice(1);assert.equal(digest,sourceBefore[local]);}
 assert.equal(Object.keys(assets).length,new Set(sourceFiles).size);assert.deepEqual(httpErrors,[]);assert.deepEqual(domErrors,[]);assert.deepEqual(external,[]);
 result.ok=true;result.assets_verified=Object.keys(assets).length;result.table_count=Object.keys(before.tables).length;result.all_tables_preserved=true;result.fixture=fixture.fixture;
}
run().catch(error=>{result.ok=false;result.error=error.stack;result.notice=dom?.window.document.querySelector('#notice')?.textContent;process.exitCode=1;}).finally(()=>{
 result.requests=requests;result.request_count=requests.length;result.actual_http_errors=httpErrors;result.intentional_failures=injections;result.expected_cancellations=cancellations;result.dom_errors=domErrors;result.external_resources=external;result.asset_sha256=assets;
 fs.writeFileSync(path.join(__dirname,'review_dom.json'),JSON.stringify(result,null,2)+'\n');dom?.window.close();console.log(JSON.stringify({ok:result.ok,checks:checks.length,requests:requests.length,assets:result.assets_verified,tables:result.table_count,error:result.error,http_errors:httpErrors,dom_errors:domErrors,visual_verification:false},null,2));
});
