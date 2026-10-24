'use strict';
(() => {
  const ui=window.IronCoachUI;if(!ui)return;
  const {el,button,api,action,number}=ui;
  const health=document.getElementById('health'),profile=document.getElementById('derived-profile');
  let recordPeriod='all_time';
  const finite=value=>typeof value==='number'&&Number.isFinite(value);
  const sourceName=value=>({fit:'File FIT',garmin:'Garmin',intervals:'Intervals.icu',manual:'Inserimento manuale'}[value]||ui.sourceLabel?.(value)||value||'Fonte non disponibile');
  function coverageSummary(total,key,expected) {
    if(total?.coverage_summary?.[key])return total.coverage_summary[key];
    const raw={hours:'duration_seconds',distance_km:'distance_meters',elevation_m:'elevation_gain_meters',calories:'calories'}[key];
    const known=total?.coverage?.[raw],count=total?.activities??expected;
    return finite(known)&&finite(count)?{known,expected:count,complete:known===count,status:count===0?'empty':known===count?'complete':known?'partial':'unavailable',known_value:total?.[key]}:null;
  }
  function coverageText(coverage) {
    if(!coverage||!finite(coverage.known)||!finite(coverage.expected))return '';
    return `Misurate ${number(coverage.known,0)}/${number(coverage.expected,0)} attività${coverage.complete===false?' · dati parziali':''}`;
  }
  function profileStatus(message,error=false) {
    let status=profile.querySelector('.profile-data-status');
    if(!status){status=el('div',null,'profile-data-status');status.setAttribute('role','status');profile.prepend(status);}
    status.replaceChildren(el('p',message,'caption'));if(error)status.append(button('Riprova',()=>action(loadProfile),'outline'));
  }
  function renderVolumeComparison(value) {
    const comparison=value.volume_comparison;if(!comparison?.current||!comparison.previous)return null;
    const section=el('section',null,'profile-volume-comparison');section.setAttribute('aria-label','Confronto del tempo registrato nelle ultime quattro settimane');
    section.append(el('h3','Il tempo registrato, nelle ultime quattro settimane'),el('p','Durate delle attività registrate e copertura dei dati per ciascun periodo.','caption'));
    const periods=el('div',null,'profile-periods');const max=Math.max(...[comparison.current,comparison.previous].map(period=>finite(period.total_minutes)?period.total_minutes:0),1);
    for(const [key,label]of [['previous','28 giorni precedenti'],['current','Ultimi 28 giorni']]){
      const period=comparison[key],complete=typeof period.complete==='boolean'?period.complete:finite(period.total_minutes)&&finite(period.known_activities)&&period.known_activities===period.expected_activities;
      const minutes=finite(period.total_minutes)?period.total_minutes:period.known_minutes;
      const card=el('div',null,'profile-period');card.dataset.period=key;card.dataset.complete=String(complete);
      card.append(el('h4',label),el('p',`${period.start||'—'} — ${period.end||'—'}`,'caption'),el('strong',`${number(minutes,1)} min${!complete&&finite(minutes)?' misurati · parziale':''}`));
      card.append(el('p',coverageText({known:period.known_activities,expected:period.expected_activities,complete}),'profile-coverage'));
      if(comparison.status==='available'&&finite(period.total_minutes)){
        const track=el('div',null,'profile-duration-track'),bar=el('span',null,'profile-duration-bar');track.setAttribute('aria-hidden','true');bar.style.width=`${Math.max(0,Math.min(100,period.total_minutes/max*100))}%`;track.append(bar);card.append(track);
      }periods.append(card);
    }section.append(periods);
    if(comparison.status==='available'&&finite(comparison.change_pct)&&finite(comparison.delta_minutes))section.append(el('p',`${comparison.change_pct>0?'+':''}${number(comparison.change_pct,1)}% · ${comparison.delta_minutes>0?'+':''}${number(comparison.delta_minutes,1)} min rispetto ai 28 giorni precedenti. È un confronto del tempo registrato.`, 'profile-volume-change'));
    else section.append(el('p',comparison.status==='no_baseline'?'Il periodo precedente non contiene una durata positiva completa per calcolare la variazione.':'La variazione non è calcolata: in almeno un periodo mancano durate utilizzabili.','caption'));
    return section;
  }
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
  const requests={health:{version:0,pending:false,running:null},athlete:{version:0,pending:false,running:null}};
  const visiblePage=()=>document.querySelector('.page:not([hidden])')?.id;
  const current=(page,version)=>visiblePage()===page&&requests[page].version===version;
  function schedule(page,read){
    if(visiblePage()!==page)return Promise.resolve();
    const state=requests[page];state.version++;state.pending=true;
    if(!state.running)state.running=(async()=>{
      try{
        // Coalesce repeated events into the latest read, independently per page.
        while(state.pending&&visiblePage()===page){state.pending=false;const version=state.version;await read(version);}
      }finally{state.pending=false;state.running=null;}
    })();
    return state.running;
  }
  function loadReports(){return schedule('health',async version=>{try{const value=await api('/health/reports');if(!current('health',version))return;journal.replaceChildren();
    for(const report of value.reports||[]){const card=el('article',null,'card symptom-card');card.dataset.tone=report.status==='resolved'?'positive':'caution';card.append(el('h3',`${report.label}${report.body_location?' · '+report.body_location:''}`),el('p',`${report.status==='active'?'In corso':'Risolto'} · Intensità ${report.severity}/5`,'caption'));
      const analysis=report.analysis||{};card.append(el('p',analysis.summary));
      if(analysis.associations?.length){const list=el('ul',null,'coach-findings');for(const item of analysis.associations)list.append(el('li',item.observation));card.append(list);}
      if(report.suspected_cause)card.append(el('p',`La tua ipotesi: ${report.suspected_cause}`));
      if(report.status==='active')card.append(button('Segna risolto',()=>action(async()=>{await api(`/health/reports/${report.id}`,{method:'PATCH',body:JSON.stringify({status:'resolved'})});await ui.load();await loadReports();}),'outline'));
      else card.append(button('Riapri segnalazione',()=>action(async()=>{await api(`/health/reports/${report.id}`,{method:'PATCH',body:JSON.stringify({status:'active'})});await ui.load();await loadReports();}),'outline'));
      card.append(button('Elimina',()=>action(async()=>{await api(`/health/reports/${report.id}`,{method:'DELETE'});await ui.load();await loadReports();}),'text-button'));journal.append(card);
    }
  }catch(error){if(current('health',version))journal.replaceChildren(el('p','Segnalazioni non disponibili.'),button('Riprova',()=>action(loadReports)));}});}
  function loadProfile(){if(!profile)return Promise.resolve();return schedule('athlete',async version=>{try{
    profile.setAttribute('aria-busy','true');profileStatus(profile.dataset.loaded==='true'?'Aggiornamento del profilo in corso. Sono ancora visibili gli ultimi dati caricati.':'Caricamento del profilo…');
    const value=await api('/athlete/derived-profile');if(!current('athlete',version))return;
    const focused=document.activeElement,restoreFocus=profile.contains(focused)?{period:focused.dataset.period,activityId:focused.dataset.activityId,label:focused.getAttribute('aria-label')}:null;
    profile.replaceChildren(el('p','PROFILO AUTOMATICO','eyebrow'),el('h2',value.name||'Il tuo profilo atleta'),el('p',value.headline));
    profile.dataset.loaded='true';profile.dataset.stale='false';
    profileStatus(value.date?`Dati al ${value.date}.`:'Profilo aggiornato con i dati disponibili.');
    const totals=value.totals?.all_time;
    const grid=el('div',null,'derived-grid');
    for(const [label,amount,key] of [['Attività nello storico',value.total_activities,null],['Ore nello storico',totals?.hours,'hours'],['Distanza nello storico · km',totals?.distance_km,'distance_km'],['Media settimanale · ore',value.weekly_minutes==null?null:value.weekly_minutes/60,null],['Carico settimanale',value.weekly_load,null]]){
      if(amount===undefined&&label!=='Carico settimanale')continue;
      const coverage=key?coverageSummary(totals,key,value.total_activities):label==='Media settimanale · ore'?value.duration_coverage:label==='Carico settimanale'?coverageSummary(value.totals?.days_28,'load'):null;
      const partial=coverage?.complete===false;
      const shown=key&&amount==null&&finite(coverage?.known_value)?coverage.known_value:amount;
      const card=el('div');card.append(el('small',`${label}${partial&&finite(shown)?' · parziale':''}`),el('strong',number(shown,1)));
      if(coverageText(coverage))card.append(el('p',coverageText(coverage),'profile-coverage'));grid.append(card);
    }
    profile.append(grid);
    const volume=renderVolumeComparison(value);if(volume)profile.append(volume);
    if(value.body_mass&&ui.appendBodyMass){const bmi=el('section',null,'profile-bmi-card');ui.appendBodyMass(bmi,value.body_mass);profile.append(bmi);}
    const sports=el('div',null,'derived-sports');for(const sport of value.sports||[])if(sport.activities)sports.append(el('span',`${sport.label} · ${sport.activities} sedute · ${number(sport.weekly_minutes,0)} min/settimana${sport.duration_coverage?.complete===false?' · durate parziali':''}`,'badge'));profile.append(sports);
    if(value.goal){const goal=el('section',null,'profile-goal');goal.append(el('p','OBIETTIVO ATTIVO','eyebrow'),el('h3',value.goal.name));
      const names={cycling:'Bici',running:'Corsa',swimming:'Nuoto'};
      for(const leg of value.goal.legs||[])goal.append(el('p',`${names[leg.sport]||leg.sport} · ${number(leg.distance_km,1)} km${leg.elevation_gain_m?' · '+number(leg.elevation_gain_m,0)+' m D+':''}`));profile.append(goal);
    }
    for(const [title,items] of [['Punti di forza',value.strengths],['Priorità',value.priorities]])if(items?.length){profile.append(el('h3',title));const list=el('ul',null,'coach-findings');for(const item of items)list.append(el('li',item));profile.append(list);}
    for(const reason of value.restrictions?.reasons||[])profile.append(el('p',reason,'error'));
    const metrics=value.health?.metrics||[];
    if(metrics.length){const section=el('section',null,'profile-health');section.append(el('h3','Misure e condizione'),el('p','Ultimi valori registrati','caption'));const cards=el('div',null,'profile-health-grid');
      for(const item of metrics){const card=el('article',null,'profile-health-card');card.dataset.kind=item.kind;card.append(el('small',item.label),el('strong',`${number(item.latest.value,1)} ${item.unit}`));
        if(item.kind==='weight_kg'&&value.health.weight?.change_28d_kg!=null){const change=value.health.weight.change_28d_kg;card.append(el('span',`${change>0?'+':''}${number(change,1)} kg nelle ultime 4 settimane`,'caption'));}
        if(window.IronCoachMetricStatus){const status=window.IronCoachMetricStatus.metric(item,metrics);window.IronCoachMetricStatus.apply(card,status,false);}
        if(ui.openHealthMetric){const open=button('Andamento ↗',()=>{const from=new Date(today()+'T12:00:00Z');from.setUTCDate(from.getUTCDate()-27);ui.openHealthMetric({...item,points:(item.points||[]).filter(point=>point.date>=from.toISOString().slice(0,10))});},'metric-open');open.setAttribute('aria-label',`Apri andamento ${item.label}`);card.append(open);}cards.append(card);
      }section.append(cards);profile.append(section);
    }
    const thresholds=Object.entries(value.thresholds||{});
    if(thresholds.length){const section=el('section',null,'profile-thresholds');section.append(el('h3','Soglie di riferimento'));const labels={cycling_ftp_watts:['FTP bici','W'],running_threshold_pace_sec_per_km:['Soglia corsa','s/km'],swimming_css_sec_per_100m:['CSS nuoto','s/100 m']};const cards=el('div',null,'derived-grid');
      for(const [key,threshold]of thresholds){const [label,unit]=labels[key]||[key,''];const card=el('div');card.append(el('small',label),el('strong',`${number(threshold.value,1)} ${unit}`),el('span',threshold.source==='measured'?'Test misurato':threshold.source==='estimated'?'Valore stimato':'Provenienza non indicata','caption'));if(threshold.measured_on)card.append(el('p',`Riferimento del ${threshold.measured_on}`,'caption'));cards.append(card);}section.append(cards);profile.append(section);
    }
    const observedSports=(value.sports||[]).filter(sport=>sport.totals?.all_time?.activities>0&&sport.records);
    if(observedSports.length){const section=el('section',null,'profile-records');section.append(el('h3','Le tue migliori attività registrate'));const tabs=el('div',null,'profile-record-tabs');tabs.setAttribute('role','group');tabs.setAttribute('aria-label','Periodo dei record');const content=el('div',null,'profile-record-content');
      function drawRecords(period){recordPeriod=period;content.replaceChildren();for(const control of tabs.children)control.setAttribute('aria-pressed',String(control.dataset.period===period));
        for(const sport of observedSports){const records=sport.records[period]||{},sportTotals=sport.totals[period]||{};const card=el('article',null,'profile-record-sport');card.dataset.sport=sport.sport||sport.key||sport.label.toLowerCase();card.append(el('h4',sport.label),el('p',`${number(sportTotals.activities,0)} attività · ${number(sportTotals.hours,1)} ore · ${number(sportTotals.distance_km,1)} km`,'caption'));
          for(const key of ['hours','distance_km']){const coverage=coverageSummary(sportTotals,key);if(coverage?.complete===false)card.append(el('p',`${key==='hours'?'Durata':'Distanza'}: ${number(coverage.known_value,1)} ${key==='hours'?'ore':'km'} misurati · ${coverageText(coverage)}`,'profile-coverage'));}
          const tiles=el('div',null,'profile-record-grid');
          for(const [key,record]of Object.entries(records)){if(!record)continue;const tile=el('div',null,'profile-record-tile');tile.dataset.record=key;tile.append(el('small',record.label),el('strong',`${number(record.value,key==='longest_distance_km'?2:1)} ${record.unit||''}`));
            const provenance=el('p',null,'record-provenance');const date=el('time',record.date||'Data non disponibile');if(record.date)date.dateTime=record.date;provenance.append(date,el('span',` · ${sourceName(record.source)}`));tile.append(provenance);
            if(record.candidate_coverage&&record.candidate_coverage.known<record.candidate_coverage.expected)tile.append(el('p',`Misura disponibile in ${number(record.candidate_coverage.known,0)}/${number(record.candidate_coverage.expected,0)} attività.`,'profile-coverage'));
            if(ui.openActivity){const open=button(record.activity_name,()=>ui.openActivity(record.activity_id),'text-button');open.dataset.activityId=String(record.activity_id);open.setAttribute('aria-label',`Apri attività del record: ${record.activity_name}`);tile.append(open);}else tile.append(el('span',record.activity_name,'caption'));tiles.append(tile);
          }if(!tiles.children.length)tiles.append(el('p','Nessun record documentato in questo periodo.','empty-state'));card.append(tiles);content.append(card);
        }
      }
      const periods=[['all_time','Tutto lo storico'],['days_90','Ultimi 90 giorni']];if(observedSports.some(sport=>Object.hasOwn(sport.records,'days_28')))periods.push(['days_28','Ultimi 28 giorni']);
      for(const [period,label]of periods){const control=button(label,()=>drawRecords(period),'outline');control.dataset.period=period;tabs.append(control);}section.append(tabs,content);section.append(el('p',value.records_note,'caption'));profile.append(section);drawRecords(periods.some(([key])=>key===recordPeriod)?recordPeriod:'all_time');
    }
    if(restoreFocus&&!document.querySelector('dialog[open]')){
      const target=[...profile.querySelectorAll('button')].find(button=>restoreFocus.period?button.dataset.period===restoreFocus.period:restoreFocus.activityId?button.dataset.activityId===restoreFocus.activityId:restoreFocus.label&&button.getAttribute('aria-label')===restoreFocus.label);target?.focus();
    }
  }catch(error){if(current('athlete',version)){profile.dataset.stale='true';if(profile.dataset.loaded==='true')profileStatus('Aggiornamento non riuscito. Sono conservati gli ultimi dati caricati; potrebbero non essere aggiornati.',true);else profile.replaceChildren(el('p','Il profilo automatico non è disponibile.'),button('Riprova',()=>action(loadProfile)));}}
    finally{if(current('athlete',version))profile.removeAttribute('aria-busy');}});}
  function refresh(){const page=visiblePage();if(page!=='athlete')profile?.removeAttribute('aria-busy');return page==='health'?loadReports():page==='athlete'?loadProfile():Promise.resolve();}
  document.addEventListener('iron:loaded',refresh);document.addEventListener('iron:pagechange',refresh);refresh();
})();
