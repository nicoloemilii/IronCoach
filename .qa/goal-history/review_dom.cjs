'use strict';
// Real production HTML/assets and HTTP, against synthetic localhost only.
// JSDOM checks behavior and semantics. It does not inspect a visual browser.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {createRequire}=require('node:module');const ROOT=path.resolve(__dirname,'../..'),BASE='http://127.0.0.1:8003';
const {JSDOM,VirtualConsole,requestInterceptor}=createRequire(path.join(ROOT,'web/tests/package.json'))('jsdom');
const requests=[],httpErrors=[],domErrors=[],external=[],assets={},checks=[],cancellations=[];let active=0,last=0,dom,holdNextProfile=false,releaseProfile;
const hash=value=>crypto.createHash('sha256').update(value).digest('hex');
const sourceFiles=['web/index.html',...Array.from(fs.readFileSync(path.join(ROOT,'web/index.html'),'utf8').matchAll(/(?:src|href)="(\/web\/[^"?]+)"/g),match=>match[1].slice(1))];
const hashes=()=>Object.fromEntries([...new Set(sourceFiles)].map(name=>[name,hash(fs.readFileSync(path.join(ROOT,name)))]));
const sourceBefore=hashes();const result={scope:'Production HTML and all scripts in JSDOM with actual local HTTP. Functional DOM integration, not visual/browser QA.',synthetic_only:true,personal_data_accessed:false,browser_used:false,visual_verification:false,source_sha256:sourceBefore,checks};
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(predicate,label){const end=Date.now()+20000;while(!predicate()){if(Date.now()>end)throw new Error(`Timed out: ${label}`);await sleep(20);}}
async function idle(){await until(()=>active===0&&Date.now()-last>120,'all local HTTP requests settled');}
async function request(input,options={}){
 const url=new URL(typeof input==='string'?input:input.url,BASE);assert.equal(url.origin,BASE,'Only the dedicated synthetic localhost server is allowed.');
 const method=options.method||'GET';assert.equal(method,'GET','This integration must never mutate application data.');
 const record={method,path:url.pathname+url.search};requests.push(record);active++;last=Date.now();
 try{
  const response=await fetch(url,{...options,headers:{Origin:BASE,...Object.fromEntries(new Headers(options.headers||{}))}});record.status=response.status;if(!response.ok)httpErrors.push(`${record.path}: ${response.status}`);
  if(url.pathname==='/app'||url.pathname.startsWith('/web/'))assets[url.pathname]=hash(Buffer.from(await response.clone().arrayBuffer()));
  if(holdNextProfile&&url.pathname==='/athlete/derived-profile'){holdNextProfile=false;record.response_held_for_loading_check=true;await new Promise(resolve=>{releaseProfile=resolve;});}
  return response;
 }catch(error){if(error.name==='AbortError'&&options.signal?.aborted===true){record.cancelled=true;cancellations.push({path:record.path,reason:'Client AbortController cancelled an obsolete page read.'});}else httpErrors.push(`${record.path}: ${error.message}`);throw error;}
 finally{active--;last=Date.now();}
}
async function read(url){const response=await request(url);assert.ok(response.ok,url);return response.json();}
async function run(){
 const before=await read('/_qa/state');assert.equal(before.synthetic,true);assert.equal(before.fixture,'goal-history-v1');assert.equal(before.provider_configured,false);assert.deepEqual(before.provider_tripwire_calls,[]);
 const [profile,diary]=await Promise.all([read('/athlete/derived-profile'),read('/activities')]);
 assert.equal(profile.weekly_minutes,null);assert.equal(profile.duration_coverage.status,'partial');assert.equal(profile.volume_comparison.status,'insufficient_data');assert.equal(profile.volume_comparison.current.total_minutes,null);assert.equal(profile.volume_comparison.previous.total_minutes,120);
 const canonical=diary.find(row=>row.id===1),conflicting=diary.find(row=>row.id===3);assert.equal(canonical.normalized_power,250);assert.equal(canonical.load,null);assert.equal(canonical.load_evidence.reason,'canonical_identity');assert.equal(conflicting.load,null);assert.equal(conflicting.load_evidence.conflict,true);
 checks.push('Actual diary API preserves null recorded load despite NP; canonical provider identity and load conflicts are explicit.');
 const console=new VirtualConsole();console.on('jsdomError',error=>domErrors.push(error.message));
 dom=await JSDOM.fromURL(BASE+'/app#athlete',{
  runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:console,
  resources:{interceptors:[requestInterceptor(async resource=>{if(new URL(resource.url).origin!==BASE){external.push(resource.url);throw new Error('Nonlocal resource blocked.');}return request(resource.url,{method:resource.method,headers:Object.fromEntries(resource.headers)});})]},
  beforeParse(w){w.Headers=Headers;w.Request=Request;w.Response=Response;w.fetch=(input,options={})=>request(input,options);w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');this.dispatchEvent(new w.Event('close'));};w.addEventListener('error',event=>domErrors.push(event.error?.message||event.message));}
 });
 const w=dom.window,$=selector=>w.document.querySelector(selector);
 const bikeTile=()=>$('.profile-record-sport[data-sport="bike"] .profile-record-tile[data-record="longest_distance_km"]');
 const selectPeriod=async key=>{const button=$(`.profile-record-tabs [data-period="${key}"]`);assert.ok(button,key);await until(()=>!button.disabled,'record period enabled');button.click();await idle();assert.equal($('.profile-record-tabs [aria-pressed="true"]').dataset.period,key);};
 async function click(node,label){assert.ok(node,label);await until(()=>node.isConnected&&!node.disabled,label);node.click();await idle();}
 async function navigate(page){await click($(`nav [data-page="${page}"]`),`navigate ${page}`);await until(()=>$('#'+page).hidden===false,`visible ${page}`);await idle();}
 await until(()=>w.IronCoachUI&&w.ironViewSnapshot&&bikeTile(),'full production profile');await idle();
 const meanCard=[...$('.derived-grid').children].find(card=>card.querySelector('small')?.textContent==='Media settimanale · ore');assert.ok(meanCard);assert.equal(meanCard.querySelector('strong').textContent,'—');
 assert.match($('.derived-grid').textContent,/parziale/);assert.match($('.derived-grid').textContent,/Misurate 5\/6 attività/);
 assert.match($('.profile-period[data-period="current"]').textContent,/135 min misurati · parziale/);assert.match($('.profile-period[data-period="previous"]').textContent,/120 min/);assert.equal($('.profile-volume-change'),null);assert.equal($('.profile-duration-bar'),null);
 checks.push('Partial profile retains measured subtotals with coverage and preserves unknown weekly mean; no unsupported change or full comparison bars.');
 const sports=profile.sports.find(sport=>sport.sport==='bike');const historical=sports.records.all_time.longest_distance_km;
 assert.equal(bikeTile().querySelector('strong').textContent,'200 km');assert.equal(bikeTile().querySelector('time').dateTime,historical.date);assert.match(bikeTile().querySelector('.record-provenance').textContent,/File FIT/);
 await selectPeriod('days_90');assert.equal(bikeTile().querySelector('strong').textContent,'100 km');await selectPeriod('days_28');assert.equal(bikeTile().querySelector('strong').textContent,'30 km');
 const focused=$('.profile-record-tabs [data-period="days_28"]');focused.focus();w.document.dispatchEvent(new w.CustomEvent('iron:loaded'));await idle();
 assert.equal($('.profile-record-tabs [aria-pressed="true"]').dataset.period,'days_28');assert.equal(w.document.activeElement.dataset.period,'days_28');
 await w.IronCoachUI.load();await idle();assert.equal($('.profile-record-tabs [aria-pressed="true"]').dataset.period,'days_28');assert.equal(w.document.activeElement.dataset.period,'days_28');
 checks.push('All-time/90-day/28-day records show actual 200/100/30 km; dates/source and chosen period/focus survive background and global GET refresh.');
 holdNextProfile=true;releaseProfile=null;w.document.dispatchEvent(new w.CustomEvent('iron:loaded'));await until(()=>typeof releaseProfile==='function','actual profile response held');
 assert.equal($('#derived-profile').getAttribute('aria-busy'),'true');assert.ok(bikeTile());assert.equal(bikeTile().querySelector('strong').textContent,'30 km');assert.match($('.profile-data-status').textContent,/Aggiornamento.*ultimi dati caricati/);releaseProfile();await idle();assert.equal($('#derived-profile').hasAttribute('aria-busy'),false);
 await navigate('activities');await navigate('athlete');assert.equal($('.profile-record-tabs [aria-pressed="true"]').dataset.period,'days_28');
 checks.push('Pending real HTTP profile response keeps records visible and marks loading; navigation preserves selected record period.');
 await selectPeriod('all_time');await click(bikeTile().querySelector('button'),'open historical record activity');await until(()=>$('.activity-overview h2')?.textContent.includes('record storico'),'correct record detail');await idle();assert.equal($('.activity-overview h2').textContent,diary.find(row=>row.id===historical.activity_id).name);await click($('#close-activity'),'close record detail with back control');
 checks.push('Record action opens its actual historical activity, including records outside recent client display pages.');
 await navigate('activities');
 for(const [category,label,count]of [['cycling','Bici',4],['running','Corsa',1],['strength','Forza',3]]){const select=$('#activity-sport');select.value=category;select.dispatchEvent(new w.Event('change',{bubbles:true}));await idle();const rows=[...w.document.querySelectorAll('#activity-list .list-row')];assert.equal(rows.length,count,category);assert.ok(rows.every(row=>row.querySelector('small').textContent.includes(` · ${label} ·`)),category);}
 checks.push('Ride/VirtualRide, TrailRun, Crossfit/StrengthTraining/Weightlifting use consistent canonical filters and localized diary labels.');
 $('#activity-sport').value='cycling';$('#activity-sport').dispatchEvent(new w.Event('change',{bubbles:true}));await idle();
 const canonicalButton=[...w.document.querySelectorAll('#activity-list .list-row')].find(row=>row.textContent.includes('identità canonica'));await click(canonicalButton,'open canonical identity detail');await until(()=>$('.activity-stream-chart'),'stored raw canonical stream');await idle();
 const detail=await read('/activities/1/detail');assert.equal(detail.summary.avg_power,150);assert.equal(detail.summary.load,null);assert.equal(detail.availability.source_resolution.reason,'canonical_identity');assert.equal(detail.availability.source_resolution.ambiguous_identity,false);assert.equal(detail.availability.remote_fetched,false);assert.equal(detail.availability.remote_attempted,false);assert.match(detail.provider_url,/\/qa-canonical$/);
 assert.equal(detail.streams.find(stream=>stream.kind==='power').statistics.sample_mean,150);assert.doesNotMatch($('.activity-stream-panel .stream-stats').textContent,/999/);await click($('#close-activity'),'close canonical detail');
 const conflict=await read('/activities/3/detail');assert.equal(conflict.summary.load,null);assert.equal(conflict.availability.source_resolution.ambiguous_identity,true);assert.equal(conflict.provider_url,null);assert.equal(conflict.streams.length,0);
 checks.push('Activity detail selects canonical stored payload over newer alias, does not revive null load, and withholds ambiguous conflict detail without contacting providers.');
 const after=await read('/_qa/state');assert.deepEqual(after,before,'Every synthetic table and provider-call guard must remain unchanged.');
 result.source_after_sha256=hashes();assert.deepEqual(result.source_after_sha256,sourceBefore,'Source stayed unchanged during the integration run.');
 for(const [url,digest]of Object.entries(assets)){const local=url==='/app'?'web/index.html':url.slice(1);assert.equal(digest,sourceBefore[local],`${url}: HTTP bytes exactly match production source`);}
 assert.equal(Object.keys(assets).length,new Set(sourceFiles).size,'All HTML, JavaScript and CSS assets from the actual index were loaded.');
 assert.deepEqual(httpErrors,[]);assert.deepEqual(domErrors,[]);assert.deepEqual(external,[]);assert.ok(requests.every(request=>request.method==='GET'));
 result.asset_sha256=assets;result.assets_verified=Object.keys(assets).length;result.all_tables_preserved=true;result.table_count=Object.keys(before.tables).length;result.provider_calls=after.provider_tripwire_calls;result.ok=true;
}
run().catch(error=>{result.ok=false;result.error=error.stack;result.notice=dom?.window.document.querySelector('#notice')?.textContent;process.exitCode=1;}).finally(()=>{
 result.requests=requests;result.request_count=requests.length;result.http_errors=httpErrors;result.expected_client_cancellations=cancellations;result.dom_errors=domErrors;result.external_resources=external;fs.writeFileSync(path.join(__dirname,'review_dom.json'),JSON.stringify(result,null,2)+'\n');dom?.window.close();
 console.log(JSON.stringify({ok:result.ok,checks:checks.length,requests:requests.length,assets:result.assets_verified,tables:result.table_count,http_errors:httpErrors,dom_errors:domErrors,error:result.error,visual_verification:false},null,2));
});
