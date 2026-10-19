'use strict';
// Complete local workflows; all values are rendered as text, never markup.
(() => {
  const ui = window.IronCoachUI;
  if (!ui) return;
  const {el, button, number, api, action} = ui;
  const colors = window.IronCoachMetricStatus;
  const json = (method, value) => ({method, body:JSON.stringify(value)});
  const labels = {cycling:'Bici',running:'Corsa',swimming:'Nuoto',strength:'Forza',bike:'Bici',run:'Corsa',swim:'Nuoto'};
  const review = el('article', null, 'card progress-card'); review.id = 'week-reflection';
  review.append(el('p', 'DAL PROGRAMMA AL LAVORO SVOLTO', 'eyebrow'), el('h2', 'Rivedi la tua settimana'));
  const controls = el('div', null, 'action-group'); const dateLabel = el('label', 'Un giorno della settimana');
  const weekDate = el('input'); weekDate.type = 'date'; weekDate.value = ui.context().weekStart; weekDate.id = 'reflection-date'; dateLabel.append(weekDate);
  controls.append(dateLabel, button('Carica settimana', () => action(() => loadReview(weekDate.value)))); review.append(controls);
  const reviewBody = el('div', 'Apri Coach per rivedere le sedute e confermare quelle dei giorni passati.'); reviewBody.id = 'reflection-content'; review.append(reviewBody);
  review.hidden=true; // Review of unresolved calendar entries is available through calendar controls.
  const recovery = el('article', null, 'card progress-card'); recovery.id = 'personal-recovery'; recovery.append(el('p', 'IL TUO RIFERIMENTO PERSONALE', 'eyebrow'), el('h2', 'Come cambia il recupero'));
  const recoveryBody = el('div', 'Apri Salute per confrontare le osservazioni recenti con il tuo storico.'); recoveryBody.id = 'recovery-trends'; recovery.append(recoveryBody);
  document.getElementById('health').prepend(recovery);
  const goalContext = el('article', null, 'card progress-card'); goalContext.id = 'active-race-context'; goalContext.hidden = true; document.getElementById('calendar').prepend(goalContext);
  let reviewVersion = 0; let recoveryVersion = 0; let contextVersion = 0;

  function errorView(root, message, retry) {
    root.replaceChildren(el('p', message, 'muted'), button('Riprova', () => action(retry)));
  }
  async function loadReview(start) {
    if (!start) return;
    const version = ++reviewVersion; reviewBody.setAttribute('aria-busy','true');
    try {
      const value = await api(`/progress/week?start=${encodeURIComponent(start)}`);
      if (version !== reviewVersion) return;
      weekDate.value = value.start;
      reviewBody.replaceChildren(el('p', `${value.start} — ${value.end}`, 'caption'), el('p', value.review));
      const totals = el('div', null, 'metrics');
      for (const [label, amount] of [['Minuti programmati',value.totals.planned_minutes],['Minuti confermati',value.totals.confirmed_actual_minutes],['Sedute da chiarire',value.counts.unconfirmed]]) {
        const card = el('div', null, 'metric'); card.append(el('p',label),el('strong',number(amount))); totals.append(card);
        if (colors) colors.apply(card, amount == null ? colors.unknown() : colors.info('Dati registrati', 'Le quantità descrivono il periodo.'), false);
      }
      reviewBody.append(totals, el('p', value.adherence.description, 'caption'));
      for (const suggestion of value.suggestions || []) reviewBody.append(el('p', suggestion));
      const list = el('div',null,'list');
      for (const session of value.sessions || []) {
        const row = el('article',null,'reflection-session');
        const status = session.resolution?.status === 'rest' ? 'Riposo confermato' : session.resolution ? 'Non svolta, confermata' : ({completed:'Esecuzione registrata',modified:'Esecuzione modificata',unconfirmed:'Da confermare',rest:'Riposo',skipped:'Non svolta',planned:'Programmata'}[session.status] || 'Da verificare');
        row.append(el('h3',session.name),el('p',`${session.date} · ${labels[session.sport] || session.sport} · ${number(session.planned_minutes)} min previsti · ${number(session.actual_minutes)} min confermati`, 'caption'));
        if (colors) {
          const tone = session.resolution?.status === 'rest' ? 'neutral' : ({completed:'good',modified:'caution',unconfirmed:'unknown',rest:'neutral',skipped:'caution',planned:'neutral'}[session.status] || 'unknown');
          colors.apply(row, {tone,label:status}, false);
        } else row.append(el('span',status,'badge'));
        if (session.can_resolve) {
          const reasonLabel = el('label','Motivo o nota (facoltativo)'); const reason = el('input'); reason.type='text'; reason.maxLength=1000; reasonLabel.append(reason); row.append(reasonLabel);
          const actions = el('div',null,'action-group');
          for (const [kind,text] of [['skipped','Non svolta'],['rest','Ho scelto il riposo']]) actions.append(button(text, () => action(async () => {
            await api(`/progress/workouts/${session.id}/resolution`,json('PUT',{status:kind,reason:reason.value.trim() || (kind === 'rest' ? 'Riposo scelto dall’atleta.' : 'Seduta non svolta, confermata dall’atleta.')}));
            await ui.load(); await loadReview(value.start);
          })));
          row.append(actions,el('p','Se ti sei allenato, apri la seduta dal piano e registra il feedback o abbina l’attività.', 'caption'));
        } else if (session.resolution) {
          if (session.resolution.reason) row.append(el('p',session.resolution.reason,'caption'));
          row.append(button('Annulla questa conferma', () => action(async () => {
            await api(`/progress/workouts/${session.id}/resolution`,{method:'DELETE'}); await ui.load(); await loadReview(value.start);
          }), 'text-button'));
        }
        list.append(row);
      }
      if (!(value.sessions || []).length) list.append(el('p','Nessuna seduta programmata in questa settimana. Le attività registrate restano nel diario.', 'empty-state'));
      reviewBody.append(list,button('Apri il calendario',()=>ui.navigate('calendar')));
    } catch (error) { if (version === reviewVersion) errorView(reviewBody,'La revisione settimanale non è disponibile. I dati del piano restano conservati.',()=>loadReview(start)); }
    finally { if (version === reviewVersion) reviewBody.removeAttribute('aria-busy'); }
  }
  async function loadRecovery() {
    const version = ++recoveryVersion;
    try {
      const value = await api('/progress/recovery?days=28'); if (version !== recoveryVersion) return;
      recoveryBody.replaceChildren(); const grid = el('div',null,'progress-grid');
      for (const metric of value.metrics || []) {
        const card = el('article',null,'trend-card');
        card.classList.add('clickable-card');
        card.append(el('h3',metric.label),el('strong',`${number(metric.current,1)} ${metric.unit}`));
        card.append(el('p',metric.counts?.recent >= 3 ? 'Mediana delle ultime 3 letture · stessa fonte' : 'Servono 3 letture recenti per il confronto','caption'));
        if(metric.points?.length)card.append(ui.sparkline(metric,true));
        if(colors)colors.apply(card,colors.trend(metric),false);
        const open=button('Analisi e andamento ↗',()=>ui.openHealthMetric({...metric,latest:metric.points?.at(-1),baseline:metric.baseline}),'metric-open');open.setAttribute('aria-label',`Apri ${metric.label}`);card.append(open);
        if(!metric.counts || metric.counts.recent<3 || metric.counts.baseline<7)card.append(el('p','Storico insufficiente per il confronto personale.','caption'));
        grid.append(card);
      }
      recoveryBody.append(grid);
      if (value.subjective?.length) {
        const notes = el('details'); notes.append(el('summary','Le tue sensazioni recenti'));
        for (const point of value.subjective.slice(-7)) notes.append(el('p',`${point.date}${point.fatigue != null ? ` · Fatica dichiarata ${point.fatigue}/5` : ''}${point.sleep_quality != null ? ` · Sonno ${point.sleep_quality}/5` : ''}${point.notes ? ` · ${point.notes}` : ''}`, 'caption'));
        recoveryBody.append(notes);
      }

    } catch (error) { if (version === recoveryVersion) errorView(recoveryBody,'Il confronto del recupero non è disponibile. I grafici sottostanti restano consultabili.',loadRecovery); }
  }
  async function loadGoalContext() {
    const version=++contextVersion;
    try {
      const week=await api(`/planning/week?start=${encodeURIComponent(ui.context().weekStart)}`); if(version!==contextVersion)return;
      goalContext.hidden=!week.race_goal && !week.goal_warnings?.length; goalContext.replaceChildren();
      if(week.race_goal) {
        goalContext.append(el('p','OBIETTIVO COLLEGATO AL PIANO','eyebrow'),el('h2',week.race_goal.name),el('p',`${week.race_goal.date} · Le caratteristiche della gara orientano la distribuzione delle sedute; la capacità recente limita il volume.`));
        for(const leg of week.race_goal.legs||[]) goalContext.append(el('p',`${labels[leg.sport]||leg.sport} · ${number(leg.distance_km,1)} km · ${number(leg.elevation_gain_m)} m D+ · ${{road:'Strada',trail:'Trail',mixed:'Misto',water:'Acqua'}[leg.terrain]||leg.terrain}`, 'caption'));

        goalContext.append(button('Scollega dal piano',()=>action(async()=>{
          const current=await api('/planning/profile'); await api('/planning/goal',{...json('PUT',{goal_id:null}),headers:{'If-Match':String(current.revision)}}); await ui.load(); await loadGoalContext();
        }),'text-button'));
      }
      for(const warning of week.goal_warnings||[])goalContext.append(el('p',warning,'warning'));
    } catch(error) { if(version===contextVersion){goalContext.hidden=false;errorView(goalContext,'Non è stato possibile leggere l’obiettivo collegato. Aggiorna prima di ricalcolare il piano.',loadGoalContext);} }
  }

  const preparationDialog=el('dialog'); preparationDialog.id='goal-preparation-dialog'; preparationDialog.setAttribute('aria-label','Preparazione operativa della gara');
  const prepTop=el('div',null,'dialog-top'); prepTop.append(button('← Indietro',()=>preparationDialog.close(),'back-button'),el('h2','Preparare la gara'));
  const prepContent=el('div'); preparationDialog.append(prepTop,prepContent); document.body.append(preparationDialog);
  let preparationVersion=0;let currentPreparationId=null;
  async function openPreparation(id,name) {
    const draft=document.getElementById('goal-preparation-form');
    if(draft?.dataset.dirty) {
      if(!preparationDialog.open)preparationDialog.showModal();
      if(String(currentPreparationId)===String(id))return;
      let warning=document.getElementById('preparation-draft-warning');
      if(!warning){warning=el('div',null,'change-preview');warning.id='preparation-draft-warning';prepContent.prepend(warning);}
      warning.replaceChildren(el('p','La preparazione aperta contiene note non salvate. Salvale prima di cambiare gara.'),button('Scarta la bozza e apri l’altra gara',()=>{delete draft.dataset.dirty;openPreparation(id,name);}));
      return;
    }
    currentPreparationId=id;
    const version=++preparationVersion; if(!preparationDialog.open)preparationDialog.showModal(); prepContent.replaceChildren(el('p','Caricamento della preparazione…'));
    try {
      const value=await api(`/goals/${id}/preparation`); if(version!==preparationVersion)return;
      prepContent.replaceChildren(el('h3',name),el('p',value.summary));
      const form=el('form'); form.id='goal-preparation-form'; const inputs=new Map();
      for(const item of value.items||[]) {
        const row=el('fieldset',null,'preparation-item'); row.append(el('legend',item.label),el('p',item.description));
        const doneLabel=el('label',null,'checkbox-label'); const done=el('input'); done.type='checkbox'; done.checked=Boolean(item.done); done.disabled=item.verified===true;
        doneLabel.append(done,el('span',item.verified?'Riscontro disponibile nei dati':'Confermo di averlo preparato o provato')); row.append(doneLabel);
        if(item.self_confirmed&&!item.verified)row.append(el('p','Conferma personale: non è una verifica automatica delle capacità.','caption'));
        if (colors) colors.apply(row, item.verified ? {...colors.coverage(100),label:'Riscontro nei dati'} : item.self_confirmed ? colors.info('Conferma personale') : item.evidence?.status === 'gap' ? colors.coverage(0) : {...colors.unknown(),label:'Da preparare'}, false);
        if (item.confirmation_stale) row.append(el('p','I requisiti della gara sono cambiati: verifica di nuovo questa conferma.','caption'));
        const notesLabel=el('label','Prove, strategia e note'); const notes=el('textarea'); notes.rows=2; notes.maxLength=2000; notes.value=item.notes||''; notesLabel.append(notes); row.append(notesLabel); form.append(row); inputs.set(item.key,{done,notes});
      }
      const submit=el('button','Salva preparazione','primary'); submit.type='submit'; form.append(submit,el('p','Le conferme descrivono ciò che hai provato. Non certificano idoneità o probabilità di completamento.','caption'));
      form.addEventListener('input',()=>form.dataset.dirty='true');
      form.addEventListener('submit',event=>{event.preventDefault();action(async()=>{
        const items=Object.fromEntries([...inputs].map(([key,input])=>[key,{done:input.done.checked,notes:input.notes.value.trim()}]));
        await api(`/goals/${id}/preparation`,json('PUT',{items,...(value.context_signature?{context_signature:value.context_signature}:{})}));delete form.dataset.dirty;await openPreparation(id,name);
      });}); form.append(button('Ricarica i requisiti e sostituisci la bozza',()=>{delete form.dataset.dirty;action(()=>openPreparation(id,name));},'text-button'));prepContent.append(form);
      for(const limit of value.limits||[])prepContent.append(el('p',limit,'caption'));
    }catch(error){if(version===preparationVersion)errorView(prepContent,'La checklist non è disponibile. Nessuna conferma è stata modificata.',()=>openPreparation(id,name));}
  }
  function attachPreparationButtons() {
    for(const card of document.querySelectorAll('#race-goal-list .goal-item')) {
      if(card.querySelector('[data-preparation]'))continue;
      const first=card.querySelector('.action-group button'); const name=card.querySelector('h3')?.textContent;
      // Cards expose the database id without inferring it from displayed text.
      const id=card.dataset.goalId; if(!id||!first)continue;
      const control=button('Preparazione operativa',()=>action(()=>openPreparation(id,name)),'outline');control.dataset.preparation='true';card.querySelector('.action-group').append(control);
    }
  }
  const goalList=document.getElementById('race-goal-list');
  if(goalList)new MutationObserver(attachPreparationButtons).observe(goalList,{childList:true});

  const backup=el('article',null,'card progress-card');backup.id='personal-backup';backup.append(el('p','I TUOI DATI PERSONALI','eyebrow'),el('h2','Backup e ripristino'),el('p','Conserva una copia di attività, salute, piano e obiettivi. Credenziali e dati temporanei Strava sono esclusi.'));
  const exportLink=el('a','Scarica una copia dei dati','outline button-link');exportLink.href='/backups/export';exportLink.download='iron-coach-backup.json';backup.append(exportLink);
  const fileLabel=el('label','Scegli una copia da controllare');const file=el('input');file.id='backup-file';file.type='file';file.accept='.json,application/json';fileLabel.append(file);backup.append(fileLabel);
  const previewBody=el('div');previewBody.id='backup-preview';previewBody.setAttribute('role','status');backup.append(previewBody);document.getElementById('connections').append(backup);
  let previewToken=null;let selectedFile=null;let previewExpires=0;
  const tableNames={activities:'Attività',activity_identities:'Identità delle attività',activity_observations:'Dati delle fonti',daily_metrics:'Dati giornalieri',health_metrics:'Metriche salute',workouts:'Sedute',workout_evaluations:'Valutazioni',athlete_adaptations:'Risposte agli allenamenti',athlete_settings:'Profilo e disponibilità',plan_revisions:'Versioni del piano',athlete_checkins:'Check-in',session_feedback:'Feedback',race_preparation:'Checklist generale',race_goals:'Obiettivi gara',iron_users:'Profilo locale',workout_resolutions:'Conferme delle sedute',goal_preparation_items:'Preparazione delle gare'};
  async function previewFile() {
    previewToken=null;previewBody.replaceChildren(el('p','Controllo della copia in corso…'));
    if(!selectedFile)return;
    if(selectedFile.size>20*1024*1024)throw new Error('La copia supera 20 MB. Scegli un file più piccolo.');
    try {
      const value=await api('/backups/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:selectedFile}); previewToken=value.preview_token;previewExpires=Date.parse(value.expires_at);
      previewBody.replaceChildren(el('h3','Controlla prima di ripristinare'),el('p',`${number(value.total_records)} registrazioni nella copia.`));
      for(const table of value.tables||[])if(table.count)previewBody.append(el('p',`${tableNames[table.name]||'Altri dati personali'}: ${number(table.count)}`,'caption'));
      previewBody.append(el('p','Il ripristino sostituisce i dati personali attuali. Prima della sostituzione viene creata una copia di sicurezza; i collegamenti protetti restano sul computer. I riferimenti locali agli invii Garmin vengono azzerati.'));
      const form=el('form');form.id='restore-form';const confirmLabel=el('label','Scrivi RIPRISTINA per sostituire i dati attuali');const confirm=el('input');confirm.name='confirmation';confirm.autocomplete='off';confirmLabel.append(confirm);const submit=el('button','Ripristina questa copia','primary');submit.type='submit';submit.disabled=true;
      confirm.addEventListener('input',()=>{submit.disabled=confirm.value!=='RIPRISTINA';form.dataset.dirty='true';});form.append(confirmLabel,submit);
      form.addEventListener('submit',event=>{event.preventDefault();action(async()=>{
        if(confirm.value!=='RIPRISTINA'||!previewToken)throw new Error('Controlla la copia e scrivi RIPRISTINA per confermare.');
        if(Date.now()>=previewExpires)throw new Error('L’anteprima è scaduta. Rileggi il file per controllarlo di nuovo.');
        const result=await api('/backups/restore',json('POST',{preview_token:previewToken,confirmation:confirm.value}));previewToken=null;ui.resetAfterRestore();await ui.load();
        previewBody.replaceChildren(el('strong','Ripristino completato'),el('p',result.message));file.value='';selectedFile=null;
      });});previewBody.append(form,button('Rileggi il file',()=>action(previewFile),'text-button'));
    }catch(error){previewBody.replaceChildren(el('p',error.message,'warning'),button('Rileggi il file',()=>action(previewFile)));throw error;}
  }
  file.addEventListener('change',()=>{selectedFile=file.files?.[0];action(previewFile);});

  function refreshVisible() {
    attachPreparationButtons();const page=document.querySelector('.page:not([hidden])')?.id;

    if(page==='health')loadRecovery();if(page==='calendar')loadGoalContext();
  }
  document.addEventListener('iron:loaded',refreshVisible);document.addEventListener('iron:pagechange',refreshVisible);
  document.addEventListener('iron:weekchange',()=>{if(document.querySelector('.page:not([hidden])')?.id==='calendar')loadGoalContext();});
  refreshVisible();
})();
