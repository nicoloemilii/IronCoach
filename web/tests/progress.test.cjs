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
  const data={resolved:false,loads:0,resets:0,failedRecovery:false,failedRestore:false,preparation:{},currentPage:'today',openedHealth:[],openedWorkouts:[],weekStart:'2026-10-12'};
  const el=(tag,text,css)=>{const n=w.document.createElement(tag);if(text!=null)n.textContent=text;if(css)n.className=css;return n;};
  const button=(text,callback,css)=>{const n=el('button',text,css);n.type='button';n.addEventListener('click',callback);return n;};
  const action=async fn=>{try{await fn();}catch(e){$('#notice').hidden=false;$('#notice').textContent=e.message;}};
  const api=async(url,options={})=>{
    const method=options.method||'GET';const payload=typeof options.body==='string'?JSON.parse(options.body):options.body;calls.push({url,method,payload});
    if(url.startsWith('/progress/week?')) { if(data.weekRequest) return data.weekRequest(url); const start=url.split('=')[1];return {start,end:start==='2026-10-12'?'2026-10-18':'2026-10-25',counts:{unconfirmed:data.resolved?0:1},totals:{planned_minutes:60,confirmed_actual_minutes:null},adherence:{description:'Solo esecuzioni confermate.'},review:'Una seduta da rivedere.',suggestions:['Conferma i giorni passati.'],sessions:[{id:71,date:'2026-10-15',sport:'cycling',name:'Bici facile',planned_minutes:60,actual_minutes:null,status:data.resolved?'rest':'unconfirmed',can_resolve:!data.resolved,resolution:data.resolved?{status:'rest',reason:'Riposo scelto.'}:null}]};}
    if(url==='/progress/workouts/71/resolution'){data.resolved=method==='PUT';return {status:data.resolved?'skipped':'planned'};}
    if(url==='/progress/recovery?days=28'){if(data.failedRecovery)throw new Error('Test offline');return {summary:'Un confronto disponibile.',metrics:data.recoveryMetrics||[{kind:'hrv_ms',label:'HRV',unit:'ms',current:null,baseline:null,recent_delta:null,status:'insufficient_data',stale:false,source:'intervals',points:[],explanation:'Servono più letture della stessa fonte.'}],subjective:[],limits:['Trend descrittivi.']};}
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
  w.IronCoachUI={el,button,api,action,number:v=>v==null?'—':String(v),sourceLabel:v=>v==='intervals'?'Intervals.icu':v,sparkline:()=>el('p','Grafico'),openHealthMetric:item=>data.openedHealth.push(item),openWorkoutFromReview:id=>data.openedWorkouts.push(id),context:()=>({weekStart:data.weekStart,revision:8}),load:async()=>{data.loads++;},navigate,resetAfterRestore:()=>{data.resets++;}};
  const flush=async()=>{for(let i=0;i<12;i++)await new Promise(r=>setImmediate(r));assert.equal(errors.length,0,errors.map(e=>e.message).join('\n'));};
  const goal=id=>{const card=el('article',null,'goal-item');card.dataset.goalId=id;card.append(el('h3',`Gara ${id}`));const actions=el('div',null,'action-group');actions.append(button('Vedi preparazione',()=>{}));card.append(actions);$('#race-goal-list').append(card);return card;};
  w.eval(colorsScript);w.eval(script);await flush();return {w,$,calls,data,flush,navigate,goal};
}

test('unconfirmed legacy feedback can be opened even when a skip resolution is blocked',async t=>{
 const {data,navigate,$,flush,calls}=await setup(t);
 data.weekRequest=async()=>({start:'2026-10-12',end:'2026-10-18',counts:{unconfirmed:1},totals:{planned_minutes:60,confirmed_actual_minutes:null},adherence:{description:'Solo esecuzioni confermate.'},review:'Una seduta da chiarire.',sessions:[{id:71,date:'2026-10-15',sport:'cycling',name:'Evidenza da verificare',planned_minutes:60,actual_minutes:null,status:'unconfirmed',can_resolve:false,evidence_issue:'future_activity'}]});
 navigate('calendar');await flush();
 const open=[...$('#reflection-content').querySelectorAll('button')].find(button=>button.textContent==='Apri seduta e conferma');
 assert.ok(open);open.click();await flush();assert.deepEqual(data.openedWorkouts,[71]);
 assert.equal(calls.some(call=>call.url.endsWith('/resolution')),false);
});

test('Coach omits the unresolved-calendar review while calendar contains its collapsed summary',async t=>{
 const {$,calls,flush,navigate}=await setup(t);navigate('coach');await flush();assert.equal($('#coach #week-reflection'),null);assert.equal(calls.some(c=>c.url.startsWith('/progress/week')),false);
 assert.ok($('#calendar #week-reflection')); assert.equal($('#week-reflection details').open,false);
});

test('calendar review opens any past session without mutating it and keeps manual resolution explicit',async t=>{
 const {$,calls,data,flush,navigate}=await setup(t);navigate('calendar');await flush();
 assert.match($('#reflection-summary').textContent,/1 seduta da confermare/); assert.equal($('#week-reflection details').open,false);
 const buttons=[...$('#reflection-content').querySelectorAll('button')];buttons.find(b=>b.textContent==='Apri seduta e conferma').click();await flush();
 assert.deepEqual(data.openedWorkouts,[71]);assert.equal(calls.some(c=>c.method!=='GET'),false);
 buttons.find(b=>b.textContent==='Ho scelto il riposo').click();await flush();
 assert.deepEqual(calls.find(c=>c.url==='/progress/workouts/71/resolution').payload,{status:'rest',reason:'Riposo scelto dall’atleta.'});
 assert.match($('#reflection-summary').textContent,/0 sedute da confermare/);
 [...$('#reflection-content').querySelectorAll('button')].find(b=>b.textContent==='Annulla questa conferma').click();await flush();
 assert.equal(calls.filter(c=>c.method==='DELETE').length,1);assert.match($('#reflection-summary').textContent,/1 seduta da confermare/);
});

test('review ignores stale week, date and page responses',async t=>{
 const {w,$,data,flush,navigate}=await setup(t);const pending=[];
 const value=start=>({start,end:start,counts:{unconfirmed:1},totals:{},adherence:{description:'Conferme'},sessions:[],review:`Revisione ${start}`});
 data.weekRequest=url=>new Promise(resolve=>pending.push({start:url.split('=')[1],resolve}));
 navigate('calendar');await flush();assert.equal(pending.length,1);
 data.weekStart='2026-10-19';w.document.dispatchEvent(new w.CustomEvent('iron:weekchange'));await flush();assert.equal(pending.length,2);
 pending[1].resolve(value('2026-10-19'));await flush();pending[0].resolve(value('2026-10-12'));await flush();
 assert.equal($('#reflection-date').value,'2026-10-19');assert.match($('#reflection-content').textContent,/Revisione 2026-10-19/);
 data.weekStart='2026-10-26';w.document.dispatchEvent(new w.CustomEvent('iron:weekchange'));await flush();
 $('#reflection-date').value='2026-11-02';$('#reflection-date').dispatchEvent(new w.Event('change'));pending[2].resolve(value('2026-10-26'));await flush();
 assert.equal($('#reflection-date').value,'2026-11-02');assert.match($('#reflection-summary').textContent,/Periodo da caricare/);
 [...$('#week-reflection').querySelectorAll('button')].find(b=>b.textContent==='Carica settimana').click();await flush();navigate('health');pending[3].resolve(value('2026-11-02'));await flush();
 assert.doesNotMatch($('#reflection-content').textContent,/Revisione 2026-11-02/);
});

test('recovery keeps absent values and offers retry after an unavailable endpoint',async t=>{
  const {w,$,data,flush,navigate}=await setup(t);data.failedRecovery=true;navigate('health');await flush();assert.match($('#recovery-trends').textContent,/non è disponibile/);
  data.failedRecovery=false;$('#recovery-trends button').click();await flush();assert.match($('#recovery-trends').textContent,/— ms/);assert.doesNotMatch($('#recovery-trends').textContent,/0 ms/);
  assert.match($('#recovery-trends').textContent,/Servono 3 letture recenti/);
});

test('recovery labels the three-reading median instead of presenting it as the latest measurement',async t=>{
  const {$,data,flush,navigate}=await setup(t);
  data.recoveryMetrics=[{kind:'resting_hr',label:'FC a riposo',unit:'bpm',current:53,baseline:52,recent_delta:1,status:'stable',stale:false,source:'intervals',counts:{recent:3,baseline:12},points:[{date:'2026-10-15',value:52},{date:'2026-10-16',value:53},{date:'2026-10-17',value:56}],explanation:'Confronto tra mediane della stessa fonte.'}];
  navigate('health');await flush();
  const card=$('#recovery-trends .trend-card');
  assert.equal(card.querySelector('strong').textContent,'53 bpm');
  assert.match(card.textContent,/Mediana delle ultime 3 letture · stessa fonte/);
  assert.doesNotMatch(card.textContent,/Ultima lettura|56 bpm/);
});

test('recovery detail separates the median from the last usable measure and retains a newer zero',async t=>{
  const {$,data,flush,navigate}=await setup(t);
  data.recoveryMetrics=[{kind:'hrv_ms',label:'HRV',unit:'ms',current:50,baseline:48,recent_delta:2,status:'stale',stale:true,source:'intervals',counts:{recent:3,baseline:12},latest_observation_date:'2026-10-19',latest_usable_date:'2026-10-16',recent_dates:['2026-10-14','2026-10-15','2026-10-16'],points:[{date:'2026-10-14',value:48,source:'intervals'},{date:'2026-10-15',value:50,source:'intervals'},{date:'2026-10-16',value:55,source:'intervals'},{date:'2026-10-19',value:0,source:'intervals'}],explanation:'Mediana delle tre letture recenti 50 ms.'}];
  navigate('health');await flush();
  const card=$('#recovery-trends .trend-card');
  assert.equal(card.querySelector('strong').textContent,'50 ms');
  assert.match(card.textContent,/Ultima misura utilizzabile: 2026-10-16 · Intervals.icu/);
  assert.match(card.textContent,/2026-10-19 esclusa dal calcolo: non aggiorna il trend/);
  assert.equal(card.dataset.tone,'unknown');
  card.querySelector('.metric-open').click();
  const detail=data.openedHealth[0];
  assert.equal(detail.latest.date,'2026-10-16');assert.equal(detail.latest.value,55);
  assert.equal(detail.recovery_trend.current,50);assert.equal(detail.recovery_trend.stale,true);
  assert.equal(detail.points.at(-1).value,0);
});

test('recovery detail preserves the usable source when a later invalid reading has a different source',async t=>{
  const {$,data,flush,navigate}=await setup(t);
  data.recoveryMetrics=[{kind:'hrv_ms',label:'HRV',unit:'ms',current:53,baseline:50,recent_delta:3,status:'above_baseline',stale:false,source:'intervals',counts:{recent:3,baseline:7},latest_observation_date:'2026-10-19',latest_usable_date:'2026-10-18',points:[{date:'2026-10-18',value:55,source:'intervals'},{date:'2026-10-19',value:0,source:'manual'}]}];
  navigate('health');await flush();$('#recovery-trends .metric-open').click();
  const detail=data.openedHealth[0];
  assert.equal(detail.latest.source,'intervals');assert.equal(detail.latest.date,'2026-10-18');
  assert.equal(detail.recovery_trend.source,'intervals');
  assert.equal(detail.points.at(-1).source,'manual');assert.equal(detail.points.at(-1).value,0);
});

test('recovery with only an unusable observation never substitutes it as a current measure',async t=>{
  const {$,data,flush,navigate}=await setup(t);
  data.recoveryMetrics=[{kind:'resting_hr_bpm',label:'FC a riposo',unit:'bpm',current:null,baseline:null,stale:false,status:'insufficient_data',source:'intervals',counts:{recent:0,baseline:0},latest_observation_date:'2026-10-19',latest_usable_date:null,points:[{date:'2026-10-19',value:0,source:'intervals'}]}];
  navigate('health');await flush();
  const card=$('#recovery-trends .trend-card');assert.equal(card.querySelector('strong').textContent,'— bpm');
  assert.match(card.textContent,/Nessuna misura utilizzabile/);assert.equal(card.dataset.tone,'unknown');
  card.querySelector('.metric-open').click();assert.equal(data.openedHealth[0].latest,null);
  assert.equal(data.openedHealth[0].points[0].value,0);
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
