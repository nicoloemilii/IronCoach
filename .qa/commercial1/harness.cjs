'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM,VirtualConsole}=require('../../web/tests/node_modules/jsdom');
const root=path.resolve(__dirname,'../..'),origin='https://product.test',transport='http://127.0.0.1:8777',calls=[],errors=[],pending=new Set();let cookie='';
async function http(relative,options={}){
  const url=new URL(relative,origin);assert.equal(url.origin,origin);assert.ok(!url.pathname.startsWith('/sync'));
  const headers=new Headers(options.headers);headers.set('Host','product.test');if(cookie)headers.set('Cookie',cookie);if(options.method&&options.method!=='GET')headers.set('Origin',origin);
  const response=await new Promise((resolve,reject)=>{
    const req=require('node:http').request(transport+url.pathname+url.search,{method:options.method||'GET',headers:Object.fromEntries(headers)},res=>{
      const chunks=[];res.on('data',chunk=>chunks.push(chunk));res.on('end',()=>{const received=new Headers();for(let i=0;i<res.rawHeaders.length;i+=2)received.append(res.rawHeaders[i],res.rawHeaders[i+1]);resolve(new Response(res.statusCode===204?null:Buffer.concat(chunks),{status:res.statusCode,headers:received}));});
    });req.on('error',reject);if(options.body)req.write(options.body);req.end();
  });const set=response.headers.getSetCookie?.()[0];if(set)cookie=set.split(';')[0];
  calls.push({path:url.pathname,status:response.status,method:options.method||'GET'});return response;
}
async function api(path,method='GET',body){const response=await http(path,{method,headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});const value=await response.json();assert.ok(response.ok,JSON.stringify({path,status:response.status,value}));return value;}
const invitations=JSON.parse(fs.readFileSync(path.join(__dirname,'invitations.json'),'utf8'));
async function signup(email){await api('/account/register','POST',{name:email.includes('qa-a')?'QA A':'QA B',email,password:'valid-test-password-2026',invitation:invitations[email],terms_accepted:true,health_data_consent:true});return api('/account/login','POST',{email,password:'valid-test-password-2026'});}
const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.stack));
const dom=new JSDOM(fs.readFileSync(path.join(root,'web/index.html'),'utf8'),{url:origin+'/app#training',runScripts:'outside-only',virtualConsole:vc});const w=dom.window,$=s=>w.document.querySelector(s);w.Headers=Headers;
w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
w.fetch=(path,options={})=>{const job=http(path,options).finally(()=>pending.delete(job));pending.add(job);return job;};
async function settle(){for(let n=0;n<500;n++){await new Promise(r=>setTimeout(r,25));if(!pending.size&&$('#refresh').textContent!=='Caricamento…'){await new Promise(r=>setTimeout(r,60));if(!pending.size)break;}if(n===499)throw Error('UI busy');}assert.deepEqual(errors,[]);assert.ok(!$('#notice').classList.contains('error'),$('#notice').textContent);}
(async()=>{try{
  await signup('qa-a@product.test');const firstCookie=cookie;
  await Promise.all(Array.from({length:32},(_,i)=>api(['/coach/workouts','/planning/profile','/health/metrics','/goals','/experience/summary','/experience/race-preparation','/athlete/profile','/integrations/intervals/status'][i%8])));
  for(const name of ['account.js','metric-status.js','charts.js','app.js','usability.js','progress.js','wellness.js','manual-training.js','plan-coach.js'])w.eval(fs.readFileSync(path.join(root,'web',name),'utf8'));await w.IronAccount.ready;await settle();
  assert.equal($('#account-nav').hidden,false);assert.match($('#account-content').textContent,/QA A/);assert.match($('.exercise-catalogue summary').textContent,/201 esercizi/);
  const form=$('#manual-training-form');form.elements.sport.value='strength';form.elements.sport.dispatchEvent(new w.Event('change'));form.elements.name.value='QA scheda privata A';form.elements.exercise_search.value='Side plank';form.elements.exercise_search.dispatchEvent(new w.Event('input',{bubbles:true}));$('.exercise-catalogue-grid button').click();form.dispatchEvent(new w.Event('submit',{cancelable:true,bubbles:true}));await settle();
  const saved=await api('/coach/workouts');assert.equal(saved.length,1);assert.equal(saved[0].payload.exercises[0].name,'Side plank');
  $('.gym-library .list-row').click();await settle();$('.gym-set-check input').click();await settle();assert.equal((await api(`/training/gym/${saved[0].id}/progress`)).completed_count,1);$('#gym-session-dialog').close();
  const exported=await api('/account/export');assert.equal(exported.training.data.workouts.length,1);assert.ok(!JSON.stringify(exported).includes('password_hash'));
  await signup('qa-b@product.test');assert.equal((await api('/coach/workouts')).length,0);assert.equal((await api('/account/export')).training.data.workouts.length,0);
  const stale=await w.fetch('/training/manual',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})});assert.equal(stale.status,403);
  cookie=firstCookie;assert.equal((await api('/coach/workouts')).length,1);assert.equal((await api('/activities')).length,0);
  assert.equal(calls.filter(c=>c.status>=400&&!(c.path==='/training/manual'&&c.status===403)).length,0);
  const result={passed:true,frontend_requests:calls.length,unexpected_http_errors:0,expected_stale_session_rejection:1,dom_errors:errors.length,hosted_accounts:2,cross_account_isolation:true,real_ui_csrf:true,gym_save_and_live_progress:true,export_excludes_credentials:true,activities_created:0,browser_visual_verified:false};fs.writeFileSync(path.join(__dirname,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{dom.window.close();}})().catch(e=>{console.error(e.stack);process.exitCode=1;});
