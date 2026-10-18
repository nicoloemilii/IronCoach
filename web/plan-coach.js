'use strict';
(() => {
  const ui=window.IronCoachUI,root=document.getElementById('plan-coach');if(!ui||!root)return;
  const {el,button,api,action}=ui;
  const day=offset=>{const today=new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Rome',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());const d=new Date(today+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+offset);return d.toISOString().slice(0,10);};
  const card=el('details',null,'plan-coach-card');card.append(el('summary','Parla con il Coach · adatta il piano'));
  card.append(el('p','Puoi chiedere pause di una disciplina, giorni o fasce orarie. Scrivi anche il periodo: ti mostro le date interpretate e le sedute interessate prima di salvare.','muted'));
  const form=el('form');form.id='plan-coach-form';const field=el('label','La tua richiesta'),message=el('textarea');message.name='message';message.rows=2;message.required=true;message.maxLength=1200;message.placeholder='Es. Non voglio fare nuoto fino a gennaio';field.append(message);form.append(field);
  const automatic=el('label',null,'checkbox-label coach-date-mode'),autoDates=el('input');autoDates.type='checkbox';autoDates.name='interpret_dates';autoDates.checked=true;automatic.append(autoDates,el('span','Leggi il periodo dal messaggio, se indicato'));form.append(automatic);
  form.append(el('p','Senza un periodo nel messaggio valgono Dal/Al. Disattiva la lettura automatica per usare sempre queste date.','caption'));
  const dates=el('div',null,'form-grid');for(const [name,label,value]of [['start','Dal',day(0)],['end','Al',day(13)]]){const l=el('label',label),input=el('input');input.name=name;input.type='date';input.value=value;input.min=day(0);input.max=day(730);input.required=true;l.append(input);dates.append(l);}form.append(dates);
  const suggestions=el('div',null,'coach-suggestions');for(const text of ['Niente nuoto fino a gennaio','Niente corsa per due settimane','Niente mattina','Pausa dagli allenamenti'])suggestions.append(button(text,()=>{message.value=text;invalidate();message.focus();},'outline'));form.append(suggestions);
  const submit=el('button','Mostra proposta','primary');submit.type='submit';form.append(submit);card.append(form);
  const conversation=el('div',null,'coach-conversation');conversation.setAttribute('aria-live','polite');card.append(conversation);
  const preferences=el('div',null,'coach-time-rules');card.append(preferences);root.append(card);
  const names={morning:'mattina',afternoon:'pomeriggio',evening:'sera'},days=['lun','mar','mer','gio','ven','sab','dom'];
  let requestVersion=0;
  function invalidate(){requestVersion++;conversation.replaceChildren();}
  form.addEventListener('input',invalidate);
  form.addEventListener('submit',event=>{event.preventDefault();if(!form.reportValidity())return;const payload={message:message.value.trim(),start:form.elements.start.value,end:form.elements.end.value,interpret_dates:autoDates.checked},version=++requestVersion;
    action(async()=>{
      conversation.replaceChildren(el('p','Il Coach sta preparando la proposta…'));
      try{const result=await api('/planning/coach/preview',{method:'POST',body:JSON.stringify(payload)});if(version!==requestVersion)return;
        form.elements.start.value=result.rule.start;form.elements.end.value=result.rule.end;const confirmedPayload={...payload,start:result.rule.start,end:result.rule.end};
        conversation.replaceChildren(el('p',payload.message,'coach-user-message'),el('h3',result.message),el('p',`Giorni interessati: ${result.rule.weekdays.map(i=>days[i]).join(', ')}.`),el('p',result.note,'caption'));
        for(const assumption of result.assumptions||[])conversation.append(el('p',assumption,'coach-date-assumption'));
        if(result.removed_count)conversation.append(el('p',`${result.removed_count===1?'1 seduta verrà tolta':result.removed_count+' sedute verranno tolte'} dal piano attivo. Nessuna attività verrà registrata come svolta o saltata.`,'coach-removal-summary'));
        if(result.conflicts)conversation.append(el('p',`${result.conflicts===1?'1 seduta resta':result.conflicts+' sedute restano'} da chiarire con le nuove disponibilità. Quelle già iniziate o confermate vengono conservate.`,'manual-warning'));
        if(result.changes.length){const list=el('ul');for(const change of result.changes)list.append(el('li',`${change.date} · ${change.name}: ${change.reason||change.window.label}${change.action==='availability'?' · '+change.window.label:''}`));conversation.append(list);}else conversation.append(el('p','Nessuna seduta già pianificata nel periodo; la preferenza guiderà le prossime generazioni.'));
        conversation.append(button('Applica al piano',()=>action(async()=>{if(version!==requestVersion)throw new Error('La richiesta è cambiata: mostra di nuovo la proposta.');await api('/planning/coach/apply',{method:'POST',body:JSON.stringify({...confirmedPayload,preview_signature:result.preview_signature})});conversation.replaceChildren(el('p','Piano aggiornato. Le pause verranno rispettate fino alla scadenza indicata.','coach-saved'));await ui.load();await refreshRules();}),'primary'));
      }catch(error){if(version===requestVersion)conversation.replaceChildren(el('p',error.message,'error'));throw error;}
    });
  });
  let refreshing=false;
  async function refreshRules(){if(refreshing)return;refreshing=true;try{const current=await api('/planning/profile');preferences.replaceChildren();const rules=(current.profile.time_constraints||[]).filter(rule=>rule.end>=day(0));if(!rules.length)return;preferences.append(el('h3','Le tue preferenze temporanee'));
    for(const rule of rules){const line=el('div',null,'coach-time-rule');line.append(el('span',`${rule.start} – ${rule.end} · ${rule.sports?.length?rule.sports.map(k=>({swimming:'Nuoto',cycling:'Bici',running:'Corsa',strength:'Palestra'})[k]).join(', '):'Tutte le discipline'} · ${rule.blocked.length?'evita '+rule.blocked.map(k=>names[k]).join(', '):'pausa completa'} · ${rule.weekdays.map(i=>days[i]).join(', ')}`));line.append(button('Rimuovi',()=>action(async()=>{const fresh=await api('/planning/profile');const rules=fresh.profile.time_constraints.filter(item=>JSON.stringify(item)!==JSON.stringify(rule));await api('/planning/profile',{method:'PUT',headers:{'If-Match':String(fresh.revision)},body:JSON.stringify({...fresh.profile,time_constraints:rules})});invalidate();await ui.load();await refreshRules();conversation.replaceChildren(el('p','Preferenza rimossa. Ricalcola la settimana per riprogrammare eventuali sedute tolte durante la pausa.','coach-saved'));}),'text-button'));preferences.append(line);}
  }catch(error){preferences.replaceChildren(el('p','Preferenze non disponibili.'),button('Riprova',()=>action(refreshRules),'outline'));}finally{refreshing=false;}}
  function refresh(){if(!document.getElementById('calendar').hidden)refreshRules();}
  document.addEventListener('iron:pagechange',refresh);document.addEventListener('iron:loaded',refresh);refresh();
})();
