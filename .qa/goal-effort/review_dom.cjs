'use strict';
// Production assets and HTTP; DOM behavior only, not a browser/layout check.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {createRequire}=require('node:module');
const ROOT=path.resolve(__dirname,'../..'),BASE='http://127.0.0.1:8002';
const {JSDOM,VirtualConsole,requestInterceptor}=createRequire(path.join(ROOT,'web/tests/package.json'))('jsdom');
const requests=[],errors=[],external=[],assets={};let active=0,last=0,dom;
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(test,label){const end=Date.now()+20000;while(!test()){if(Date.now()>end)throw Error(label);await sleep(20);}}
async function idle(){await until(()=>!active&&Date.now()-last>100,'HTTP idle');}
async function request(input,options={}){
 const url=new URL(typeof input==='string'?input:input.url,BASE);assert.equal(url.origin,BASE);
 const item={method:options.method||'GET',path:url.pathname+url.search};requests.push(item);active++;last=Date.now();
 try{const response=await fetch(url,{...options,headers:{Origin:BASE,...Object.fromEntries(new Headers(options.headers||{}))}});item.status=response.status;if(!response.ok)errors.push(`${item.path}: ${response.status}`);if(url.pathname.startsWith('/web/')||url.pathname==='/app')assets[url.pathname]=hash(Buffer.from(await response.clone().arrayBuffer()));return response;}
 finally{active--;last=Date.now();}
}
async function read(p){const r=await request(p);assert.ok(r.ok);return r.json();}
async function run(){
 const before=await read('/_qa/state');assert.equal(before.synthetic,true);
 const console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error.message));
 dom=await JSDOM.fromURL(BASE+'/app#activities',{
  runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:console,
  resources:{interceptors:[requestInterceptor(async resource=>{if(new URL(resource.url).origin!==BASE){external.push(resource.url);throw Error('external resource blocked');}return request(resource.url,{method:resource.method,headers:Object.fromEntries(resource.headers)});})]},
  beforeParse(w){w.Headers=Headers;w.Request=Request;w.Response=Response;w.fetch=(input,options)=>request(input,options);w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');this.dispatchEvent(new w.Event('close'));};w.addEventListener('error',event=>errors.push(event.error?.message||event.message));}
 });
 const w=dom.window,$=s=>w.document.querySelector(s);
 await until(()=>$('#activity-list button'),'Activity diary');await idle();
 const full=[...w.document.querySelectorAll('#activity-list button')].find(row=>row.textContent.includes('completi'));assert.ok(full);full.click();
 await until(()=>$('.activity-effort'),'Effort comparison');await idle();
 assert.match($('.effort-halves').textContent,/150 W.*135 bpm.*100%.*250 W.*145 bpm.*100%/);
 assert.match($('.effort-change').textContent,/\+100 W.*\+10 bpm/);
 const select=$('[name=activity_stream]');const effort=$('.activity-effort');
 for(const kind of ['heart_rate','power','heart_rate','power']){select.value=kind;select.dispatchEvent(new w.Event('change'));}
 assert.equal($('.activity-stream-panel').querySelectorAll('.activity-effort').length,1);assert.equal($('.activity-effort'),effort);
 const zoom=$('[name=chart_zoom]');zoom.value='600';zoom.dispatchEvent(new w.Event('change'));assert.match($('.chart-stats-scope').textContent,/completa.*non cambia con lo zoom/);
 $('.chart-sample-navigation button:last-child').click();assert.match($('.chart-readout').textContent,/0:00.*100 W/);
 $('.effort-method summary').click();assert.equal($('.effort-method').open,true);assert.equal($('.effort-method').querySelectorAll('tbody tr').length,2);
 $('#close-activity').click();const partial=[...w.document.querySelectorAll('#activity-list button')].find(row=>row.textContent.includes('parziale'));partial.click();
 await until(()=>$('.activity-effort[data-status="insufficient_data"]'),'Partial evidence');await idle();
 assert.match($('.effort-unavailable').textContent,/copertura.*insufficiente/);assert.match($('.effort-half[data-half="second"]').textContent,/— W.*— bpm/);assert.equal($('.effort-change'),null);
 const after=await read('/_qa/state');assert.deepEqual(after,before,'Read-only UI must preserve every table');
 for(const [url,digest] of Object.entries(assets)){const local=url==='/app'?'web/index.html':'web/'+url.split('/').at(-1);assert.equal(digest,hash(fs.readFileSync(path.join(ROOT,local))),url);}
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);assert.ok(requests.every(item=>item.method==='GET'));
 const result={scope:'Production HTML/JS in JSDOM plus actual localhost HTTP; not visual/browser QA',synthetic_only:true,personal_data_opened:false,visual_verification:false,requests,assets_verified:Object.keys(assets).length,errors,external_resources:external,all_tables_preserved:true,table_count:Object.keys(before.tables).length,checks:['raw analytical means','exact half windows','coverage and unknowns','stream switches preserve component','native sample reading','zoom scope','method table','partial coverage blocks change','all assets byte exact','all GET and archive unchanged']};
 fs.writeFileSync(path.join(__dirname,'review_dom.json'),JSON.stringify(result,null,2));process.stdout.write(JSON.stringify({requests:requests.length,assets:result.assets_verified,checks:result.checks.length,errors:errors.length,all_tables_preserved:true,visual_verification:false})+'\n');
}
run().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;}).finally(()=>dom?.window.close());
