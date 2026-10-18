const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM,VirtualConsole}=require('jsdom');
const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
const script=fs.readFileSync(path.join(__dirname,'../progress.js'),'utf8');
const colorsScript=fs.readFileSync(path.join(__dirname,'../metric-status.js'),'utf8');

async function setup(t){
  const errors=[];const console=new VirtualConsole();console.on('jsdomError',e=>errors.push(e));
  const dom=new JSDOM(html,{url:'http://127.0.0.1/app',runScripts:'outside-only',virtualConsole:console});const w=dom.window;const $=s=>w.document.querySelector(s);const calls=[];
  w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
  t.after(()=>dom.window.close());
  const data={resolved:false,loads:0,resets:0,failedRecovery:false,failedRestore:false,preparation:{},currentPage:'today'};
  const el=(tag,text,css)=>{const n=w.document.createElement(tag);if(text!=null)n.textContent=text;if(css)n.className=css;return n;};
  const button=(text,callback,css)=>{const n=el('button',text,css);n.type='button';n.addEventListener('click',callback);return n;};
  const action=async fn=>{try{await fn();}catch(e){$('#notice').hidden=false;$('#notice').textContent=e.message;}};
  const api=async(url,options={})=>{
    const method=options.method||'GET';const payload=typeof options.body==='string'?JSON.parse(options.body):options.body;calls.push({url,method,payload});
    if(url.startsWith('/progress/week?'))return {start:'2026-10-12',end:'2026-10-18',counts:{unconfirmed:data.resolved?0:1},totals:{planned_minutes:60,confirmed_actual_minutes:null},adherence:{description:'Solo esecuzioni confermate.'},review:'Una seduta da rivedere.',suggestions:['Conferma i giorni passati.'],sessions:[{id:71,date:'2026-10-15',sport:'cycling',name:'Bici facile',planned_minutes:60,actual_minutes:null,status:data.resolved?'rest':'unconfirmed',can_resolve:!data.resolved,resolution:data.resolved?{status:'rest',reason:'Riposo scelto.'}:null}]};
    if(url==='/progress/workouts/71/resolution'){data.resolved=method==='PUT';return {status:data.resolved?'skipped':'planned'};}
    if(url==='/progress/recovery?days=28'){if(data.failedRecovery)throw new Error('Test offline');return {summary:'Un confronto disponibile.',metrics:[{kind:'hrv_ms',label:'HRV',unit:'ms',current:null,baseline:null,recent_delta:null,status:'insufficient_data',stale:false,source:'intervals',points:[],explanation:'Servono più letture della stessa fonte.'}],subjective:[],limits:['Trend descrittivi.']};}
    if(url.startsWith('/planning/week?'))return {race_goal:{id:4,name:'Gara trail',date:'2027-06-01',legs:[{sport:'running',distance_km:21,elevation_gain_m:1000,terrain:'trail'}],constraints:['Il volume non aumenta automaticamente.']},goal_warnings:[]};
    if(url==='/planning/profile')return {profile:{},revision:8};
    if(url==='/planning/goal')return {profile:{race_goal_id:null},revision:9};
    if(/^\/goals\/\d+\/preparation$/.test(url)){
      if(method==='PUT')data.preparation=payload.items;
      return {goal_id:Number(url.split('/')[2]),summary:'Prove specifiche da preparare.',items:[{key:'equipment',label:'Attrezzatura',description:'Prova prima della gara.',done:data.preparation.equipment?.done||false,notes:data.preparation.equipment?.notes||'',verified:false,self_confirmed:!!data.preparation.equipment?.done,evidence:{source:'self_reported'}}],limits:['Nessuna idoneità certificata.']};
    }
    if(url==='/backups/preview')return {preview_token:'safe-token',expires_at:new Date(Date.now()+600000).toISOString(),total_records:3,tables:[{name:'activities',count:2},{name:'athlete_settings',count:1}]};
    if(url==='/backups/restore'){if(data.failedRestore)throw new Error('I dati sono cambiati dopo l’anteprima. Carica nuovamente il backup.');return {restored:true,message:'Dati personali ripristinati.'};}
    throw new Error(`Unexpected fixture: ${method} ${url}`);
  };
  const navigate=id=>{data.currentPage=id;for(const p of w.document.querySelectorAll('.page'))p.hidden=p.id!==id;w.document.dispatchEvent(new w.CustomEvent('iron:pagechange',{detail:{id,userInitiated:true}}));};
  w.IronCoachUI={el,button,api,action,number:v=>v==null?'—':String(v),sourceLabel:v=>v==='intervals'?'Intervals.icu':v,sparkline:()=>el('p','Grafico'),context:()=>({weekStart:'2026-10-12',revision:8}),load:async()=>{data.loads++;},navigate,resetAfterRestore:()=>{data.resets++;}};
  const flush=async()=>{for(let i=0;i<12;i++)await new Promise(r=>setImmediate(r));assert.equal(errors.length,0,errors.map(e=>e.message).join('\n'));};
  const goal=id=>{const card=el('article',null,'goal-item');card.dataset.goalId=id;card.append(el('h3',`Gara ${id}`));const actions=el('div',null,'action-group');actions.append(button('Vedi preparazione',()=>{}));card.append(actions);$('#race-goal-list').append(card);return card;};
  w.eval(colorsScript);w.eval(script);await flush();return {w,$,calls,data,flush,navigate,goal};
}

test('Coach omits the redundant unresolved-calendar review',async t=>{
 const {$,calls,flush,navigate}=await setup(t);navigate('coach');await flush();assert.equal($('#week-reflection'),null);assert.equal(calls.some(c=>c.url.startsWith('/progress/week')),false);
});

test('recovery keeps absent values and offers retry after an unavailable endpoint',async t=>{
  const {w,$,data,flush,navigate}=await setup(t);data.failedRecovery=true;navigate('health');await flush();assert.match($('#recovery-trends').textContent,/non è disponibile/);
  data.failedRecovery=false;$('#recovery-trends button').click();await flush();assert.match($('#recovery-trends').textContent,/— ms/);assert.doesNotMatch($('#recovery-trends').textContent,/0 ms/);
});

test('active goal shows actual race characteristics and detaches through revision-checked API',async t=>{
  const {w,$,calls,flush,navigate}=await setup(t);navigate('calendar');await flush();assert.equal($('#active-race-context').hidden,false);assert.match($('#active-race-context').textContent,/1000 m D\+/);
  $('#active-race-context button').click();await flush();assert.deepEqual(calls.find(c=>c.url==='/planning/goal').payload,{goal_id:null});
});

test('preparation saves personal notes without interpreting markup and preserves unsaved drafts',async t=>{
  const {w,$,calls,data,flush,goal}=await setup(t);const card=goal(4);await flush();card.querySelector('[data-preparation]').click();await flush();
  const form=$('#goal-preparation-form');form.querySelector('input').checked=true;form.querySelector('textarea').value='<img src=x onerror=alert(1)>';form.dispatchEvent(new w.Event('input',{bubbles:true}));
  $('#goal-preparation-dialog .dialog-top button').click();card.querySelector('[data-preparation]').click();await flush();assert.equal($('#goal-preparation-form textarea').value,'<img src=x onerror=alert(1)>');
  $('#goal-preparation-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await flush();assert.equal(data.preparation.equipment.done,true);assert.equal($('#goal-preparation-dialog img'),null);assert.match($('#goal-preparation-dialog').textContent,/Conferma personale/);
});

test('switching preparation to another goal requires an explicit draft discard',async t=>{
  const {w,$,calls,flush,goal}=await setup(t);const first=goal(4),second=goal(5);await flush();first.querySelector('[data-preparation]').click();await flush();
  $('#goal-preparation-form textarea').value='Conservami';$('#goal-preparation-form').dispatchEvent(new w.Event('input',{bubbles:true}));$('#goal-preparation-dialog .dialog-top button').click();second.querySelector('[data-preparation]').click();await flush();
  assert.equal(calls.some(c=>c.url==='/goals/5/preparation'),false);assert.equal($('#goal-preparation-form textarea').value,'Conservami');
  $('#preparation-draft-warning button').click();await flush();assert.ok(calls.some(c=>c.url==='/goals/5/preparation'));
});

test('restore requires file preview and exact confirmation before replacing data',async t=>{
  const {w,$,calls,data,flush}=await setup(t);const file=new w.File(['{}'],'backup.json',{type:'application/json'});Object.defineProperty($('#backup-file'),'files',{value:[file],configurable:true});$('#backup-file').dispatchEvent(new w.Event('change'));await flush();
  assert.equal(calls.find(c=>c.url==='/backups/preview').payload,file);assert.match($('#backup-preview').textContent,/Attività: 2/);assert.equal($('#restore-form button').disabled,true);
  const confirm=$('#restore-form input');confirm.value='RIPRISTINA';confirm.dispatchEvent(new w.Event('input'));assert.equal($('#restore-form button').disabled,false);$('#restore-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await flush();
  assert.deepEqual(calls.find(c=>c.url==='/backups/restore').payload,{preview_token:'safe-token',confirmation:'RIPRISTINA'});assert.equal(data.resets,1);assert.match($('#backup-preview').textContent,/Ripristino completato/);
});

test('a stale restore never reports success and keeps a way to reread the file',async t=>{
  const {w,$,data,flush}=await setup(t);data.failedRestore=true;Object.defineProperty($('#backup-file'),'files',{value:[new w.File(['{}'],'backup.json')],configurable:true});$('#backup-file').dispatchEvent(new w.Event('change'));await flush();
  const confirm=$('#restore-form input');confirm.value='RIPRISTINA';confirm.dispatchEvent(new w.Event('input'));$('#restore-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await flush();assert.equal(data.resets,0);assert.doesNotMatch($('#backup-preview').textContent,/Ripristino completato/);assert.match($('#notice').textContent,/dati sono cambiati/);assert.match($('#backup-preview').textContent,/Rileggi il file/);
});
