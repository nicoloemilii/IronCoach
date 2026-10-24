'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM,VirtualConsole}=require('../../web/tests/node_modules/jsdom');
const project=path.resolve(__dirname,'../..'),origin='http://127.0.0.1:8777',calls=[],errors=[],pending=new Set();
const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.stack));
const dom=new JSDOM(fs.readFileSync(path.join(project,'web/index.html'),'utf8'),{url:origin+'/app#today',runScripts:'outside-only',virtualConsole:vc});const w=dom.window,$=s=>w.document.querySelector(s);
w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
w.fetch=(relative,options={})=>{const url=new URL(relative,origin);assert.equal(url.origin,origin);assert.ok(!url.pathname.startsWith('/sync'));const job=fetch(url,options).then(response=>{calls.push({path:url.pathname,method:options.method||'GET',status:response.status});return response;}).finally(()=>pending.delete(job));pending.add(job);return job;};
async function settle(){for(let n=0;n<400;n++){await new Promise(r=>setTimeout(r,25));if(!pending.size&&$('#refresh').textContent!=='Caricamento…'){await new Promise(r=>setTimeout(r,50));if(!pending.size)break;}if(n===399)throw Error('UI busy');}assert.deepEqual(errors,[]);assert.ok(!$('#notice').classList.contains('error'),$('#notice').textContent);}
const fill=(name,value)=>{const field=$(`#athlete-form [name=${name}]`);field.value=value;field.dispatchEvent(new w.Event('input',{bubbles:true}));};
const save=()=>$('#athlete-form').dispatchEvent(new w.Event('submit',{cancelable:true,bubbles:true}));
(async()=>{try{
 for(const name of ['metric-status.js','charts.js','app.js','usability.js','progress.js','wellness.js','manual-training.js','plan-coach.js'])w.eval(fs.readFileSync(path.join(project,'web',name),'utf8'));await settle();
 assert.match($('.bmi-summary').textContent,/Da completare/);$('.bmi-profile-link').click();await settle();assert.equal(w.document.activeElement.name,'height_cm');
 fill('height_cm','175');fill('birth_date','1990-01-01');fill('sex','female');save();await settle();assert.match($('#metrics .bmi-value').textContent,/22,86/);assert.match($('.profile-bmi-card .bmi-value').textContent,/22,86/);
 fill('sex','male');save();await settle();assert.match($('#metrics .bmi-value').textContent,/22,86/);
 w.IronCoachUI.navigate('today');$('#metrics [data-metric-kind=weight_kg] .metric-open').click();await settle();assert.match($('#metric-detail').textContent,/175 cm/);assert.match($('#metric-detail').textContent,/Maschio/);$('#close-metric').click();assert.equal($('#metric-dialog').open,false);
 const response=await fetch(origin+'/activities');assert.equal((await response.json()).length,0);assert.equal(calls.filter(c=>c.status>=400).length,0);
 const result={passed:true,frontend_requests:calls.length,dom_errors:errors.length,http_errors:calls.filter(c=>c.status>=400).length,missing_height_action:true,profile_save_updates_home_and_profile:true,bmi_independent_of_adult_sex:true,activities_created:0};fs.writeFileSync(path.join(__dirname,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{dom.window.close();}})().catch(e=>{console.error(e.stack);process.exitCode=1;});
