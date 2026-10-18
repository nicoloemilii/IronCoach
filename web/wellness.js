'use strict';
(() => {
  const ui=window.IronCoachUI;if(!ui)return;
  const {el,button,api,action,number}=ui;
  const health=document.getElementById('health'),profile=document.getElementById('derived-profile');
  const toolbar=el('div',null,'health-toolbar');toolbar.append(el('div','Il tuo stato fisico','eyebrow'),button('+ Segnala un problema',()=>{form.reset();form.elements.date.value=today();form.elements.severity.value='2';dialog.showModal();},'primary'));health.prepend(toolbar);
  const journal=el('div',null,'symptom-journal');health.insertBefore(journal,health.children[1]);
  const dialog=el('dialog');dialog.id='health-report-dialog';dialog.setAttribute('aria-labelledby','health-report-title');
  const top=el('div',null,'dialog-top');top.append(button('← Indietro',()=>dialog.close(),'back-button'),el('h2','Come stai?'));top.querySelector('h2').id='health-report-title';dialog.append(top);
  const form=el('form');form.id='health-report-form';
  const today=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Rome',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  function field(label,name,type='text',options=null){const wrapper=el('label',label),input=el(options?'select':'input');input.name=name;if(options){for(const [value,text] of options){const option=el('option',text);option.value=value;input.append(option);}}else input.type=type;wrapper.append(input);form.append(wrapper);return input;}
  const date=field('Quando è iniziato?','date','date');date.required=true;date.max=today();
  field('Cosa segnali?','kind','text',[['contracture','Contrattura'],['injury','Infortunio'],['illness','Malessere / malattia'],['cold','Raffreddore']]);
  const location=field('Dove?','body_location');location.maxLength=160;location.placeholder='Es. polpaccio destro';
  field('Quanto ti limita?','severity','text',[[1,'1 · Poco'],[2,'2 · Lieve'],[3,'3 · Moderato'],[4,'4 · Molto'],[5,'5 · Completamente']]);
  const hypothesis=field('Secondo te da cosa dipende?','suspected_cause');hypothesis.maxLength=1200;hypothesis.placeholder='La tua ipotesi';
  const notes=field('Altri dettagli','notes');notes.maxLength=2000;
  const submit=el('button','Salva e analizza','primary');submit.type='submit';form.append(submit);dialog.append(form);document.body.append(dialog);
  form.addEventListener('submit',event=>{event.preventDefault();const values=Object.fromEntries(new FormData(form));values.severity=Number(values.severity);action(async()=>{await api('/health/reports',{method:'POST',body:JSON.stringify(values)});delete form.dataset.dirty;dialog.close();await ui.load();await loadReports();});});
  async function loadReports(){try{const value=await api('/health/reports');journal.replaceChildren();
    for(const report of value.reports||[]){const card=el('article',null,'card symptom-card');card.dataset.tone=report.status==='resolved'?'positive':'caution';card.append(el('h3',`${report.label}${report.body_location?' · '+report.body_location:''}`),el('p',`${report.status==='active'?'In corso':'Risolto'} · Intensità ${report.severity}/5`,'caption'));
      const analysis=report.analysis||{};card.append(el('p',analysis.summary));
      if(analysis.associations?.length){const list=el('ul',null,'coach-findings');for(const item of analysis.associations)list.append(el('li',item.observation));card.append(list);}
      if(report.suspected_cause)card.append(el('p',`La tua ipotesi: ${report.suspected_cause}`));
      if(report.status==='active')card.append(button('Segna risolto',()=>action(async()=>{await api(`/health/reports/${report.id}`,{method:'PATCH',body:JSON.stringify({status:'resolved'})});await ui.load();await loadReports();}),'outline'));
      else card.append(button('Riapri segnalazione',()=>action(async()=>{await api(`/health/reports/${report.id}`,{method:'PATCH',body:JSON.stringify({status:'active'})});await ui.load();await loadReports();}),'outline'));
      card.append(button('Elimina',()=>action(async()=>{await api(`/health/reports/${report.id}`,{method:'DELETE'});await ui.load();await loadReports();}),'text-button'));journal.append(card);
    }
  }catch(error){journal.replaceChildren(el('p','Segnalazioni non disponibili.'),button('Riprova',()=>action(loadReports)));}}
  async function loadProfile(){if(!profile)return;try{const value=await api('/athlete/derived-profile');profile.replaceChildren(el('p','PROFILO AUTOMATICO','eyebrow'),el('h2',value.name||'Il tuo profilo atleta'),el('p',value.headline));
    const grid=el('div',null,'derived-grid');for(const [label,amount] of [['Attività nello storico',value.total_activities],['Media settimanale · ore',value.weekly_minutes/60],['Carico settimanale',value.weekly_load]]){const card=el('div');card.append(el('small',label),el('strong',number(amount,1)));grid.append(card);}profile.append(grid);
    const sports=el('div',null,'derived-sports');for(const sport of value.sports||[])if(sport.activities)sports.append(el('span',`${sport.label} · ${sport.activities} sedute · ${number(sport.weekly_minutes,0)} min/settimana`,'badge'));profile.append(sports);
    if(value.goal)profile.append(el('h3',value.goal.name));
    for(const [title,items] of [['Punti di forza',value.strengths],['Priorità',value.priorities]])if(items?.length){profile.append(el('h3',title));const list=el('ul',null,'coach-findings');for(const item of items)list.append(el('li',item));profile.append(list);}
    for(const reason of value.restrictions?.reasons||[])profile.append(el('p',reason,'error'));
  }catch(error){profile.replaceChildren(el('p','Il profilo automatico non è disponibile.'),button('Riprova',()=>action(loadProfile)));}}
  let refreshing=false;
  async function refresh(){if(refreshing)return;const page=document.querySelector('.page:not([hidden])')?.id;if(!['health','athlete'].includes(page))return;refreshing=true;try{await(page==='health'?loadReports():loadProfile());}finally{refreshing=false;}}
  document.addEventListener('iron:loaded',refresh);document.addEventListener('iron:pagechange',refresh);refresh();
})();
