'use strict';
(() => {
  const ui=window.IronCoachUI,root=document.getElementById('plan-coach');if(!ui||!root)return;
  const {el,button,api,action}=ui;
  const day=offset=>{const today=new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Rome',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());const d=new Date(today+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+offset);return d.toISOString().slice(0,10);};
  const card=el('details',null,'plan-coach-card');card.append(el('summary','Parla con il Coach · adatta giorni e fasce orarie'));
  card.append(el('p','Dimmi quando preferisci non allenarti. Ti mostro il cambiamento prima di salvarlo. Supporta disponibilità e pause; per le singole sedute usa i comandi del calendario. Il periodo è quello indicato nei campi Dal/Al.','muted'));
  const form=el('form');form.id='plan-coach-form';const field=el('label','La tua richiesta'),message=el('textarea');message.name='message';message.rows=2;message.required=true;message.maxLength=1200;message.placeholder='Es. Non voglio allenarmi la mattina nei giorni feriali';field.append(message);form.append(field);
  const dates=el('div',null,'form-grid');for(const [name,label,value]of [['start','Dal',day(0)],['end','Al',day(13)]]){const l=el('label',label),input=el('input');input.name=name;input.type='date';input.value=value;input.min=day(0);input.max=day(730);input.required=true;l.append(input);dates.append(l);}form.append(dates);
  const suggestions=el('div',null,'coach-suggestions');for(const text of ['Niente mattina','Solo pomeriggio','Pausa dagli allenamenti'])suggestions.append(button(text,()=>{message.value=text;invalidate();message.focus();},'outline'));form.append(suggestions);
  const submit=el('button','Mostra proposta','primary');submit.type='submit';form.append(submit);card.append(form);
  const conversation=el('div',null,'coach-conversation');conversation.setAttribute('aria-live','polite');card.append(conversation);
  const preferences=el('div',null,'coach-time-rules');card.append(preferences);root.append(card);
  const names={morning:'mattina',afternoon:'pomeriggio',evening:'sera'},days=['lun','mar','mer','gio','ven','sab','dom'];
  let requestVersion=0;
  function invalidate(){requestVersion++;conversation.replaceChildren();}
  form.addEventListener('input',invalidate);
  form.addEventListener('submit',event=>{event.preventDefault();if(!form.reportValidity())return;const payload={message:message.value.trim(),start:form.elements.start.value,end:form.elements.end.value},version=++requestVersion;
    action(async()=>{
      conversation.replaceChildren(el('p','Il Coach sta preparando la proposta…'));
      try{const result=await api('/planning/coach/preview',{method:'POST',body:JSON.stringify(payload)});if(version!==requestVersion)return;
        conversation.replaceChildren(el('p',payload.message,'coach-user-message'),el('h3',result.message),el('p',`Giorni interessati: ${result.rule.weekdays.map(i=>days[i]).join(', ')}.`),el('p',result.note,'caption'));
        if(result.conflicts)conversation.append(el('p',`${result.conflicts} sedute ricadono in giorni senza disponibilità. Restano da spostare o saltare dal calendario.`,'manual-warning'));
        if(result.changes.length){const list=el('ul');for(const change of result.changes)list.append(el('li',`${change.date} · ${change.name}: ${change.window.label}`));conversation.append(list);}else conversation.append(el('p','Nessuna seduta già pianificata nel periodo; la preferenza guiderà le prossime generazioni.'));
        conversation.append(button('Applica questa disponibilità',()=>action(async()=>{if(version!==requestVersion)throw new Error('La richiesta è cambiata: mostra di nuovo la proposta.');await api('/planning/coach/apply',{method:'POST',body:JSON.stringify({...payload,preview_signature:result.preview_signature})});conversation.replaceChildren(el('p','Disponibilità aggiornata. Il calendario mostra le fasce consentite e gli eventuali conflitti.','coach-saved'));await ui.load();await refreshRules();}),'primary'));
      }catch(error){if(version===requestVersion)conversation.replaceChildren(el('p',error.message,'error'));throw error;}
    });
  });
  let refreshing=false;
  async function refreshRules(){if(refreshing)return;refreshing=true;try{const current=await api('/planning/profile');preferences.replaceChildren();const rules=(current.profile.time_constraints||[]).filter(rule=>rule.end>=day(0));if(!rules.length)return;preferences.append(el('h3','Le tue disponibilità temporanee'));
    for(const rule of rules){const line=el('div',null,'coach-time-rule');line.append(el('span',`${rule.start} – ${rule.end} · evita ${rule.blocked.map(k=>names[k]).join(', ')} · ${rule.weekdays.map(i=>days[i]).join(', ')}`));line.append(button('Rimuovi',()=>action(async()=>{const fresh=await api('/planning/profile');const rules=fresh.profile.time_constraints.filter(item=>JSON.stringify(item)!==JSON.stringify(rule));await api('/planning/profile',{method:'PUT',headers:{'If-Match':String(fresh.revision)},body:JSON.stringify({...fresh.profile,time_constraints:rules})});invalidate();await ui.load();await refreshRules();}),'text-button'));preferences.append(line);}
  }catch(error){preferences.replaceChildren(el('p','Preferenze non disponibili.'),button('Riprova',()=>action(refreshRules),'outline'));}finally{refreshing=false;}}
  function refresh(){if(!document.getElementById('calendar').hidden)refreshRules();}
  document.addEventListener('iron:pagechange',refresh);document.addEventListener('iron:loaded',refresh);refresh();
})();
