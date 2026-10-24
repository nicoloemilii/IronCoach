'use strict';
// Present backend best windows from this recording; never recompute reduced plots.
(() => {
  const make=(tag,text,cls)=>{const node=document.createElement(tag);if(text!=null)node.textContent=text;if(cls)node.className=cls;return node;};
  const finite=value=>typeof value==='number'&&Number.isFinite(value);
  const number=(value,digits=1)=>finite(value)?value.toLocaleString('it-IT',Math.abs(value)>=1e7||(value!==0&&Math.abs(value)<.001)?{notation:'scientific',maximumFractionDigits:2}:{maximumFractionDigits:digits}):'—';
  const duration=value=>value<60?`${number(value)} s`:`${number(value/60)} min`;
  const clock=value=>{
    if(!finite(value)||value<0||value>Number.MAX_SAFE_INTEGER/10)return '—';
    const tenths=Math.round(value*10),whole=Math.floor(tenths/10),fraction=tenths%10,hours=Math.floor(whole/3600),minutes=Math.floor(whole%3600/60),seconds=String(whole%60).padStart(2,'0');
    return `${hours?`${hours}:${String(minutes).padStart(2,'0')}`:minutes}:${seconds}${fraction?`,${fraction}`:''}`;
  };
  const sourceLabels={stored_observation_streams:'Serie conservate nell’osservazione della fonte',intervals_remote_streams:'Serie ricevute da Intervals.icu'};
  const reasons={
    missing_power:'Non è disponibile una serie di potenza utilizzabile.',ambiguous_power:'La serie di potenza contiene misure ambigue.',
    missing_time:'Manca una scala temporale utilizzabile.',ambiguous_time:'La scala temporale contiene misure ambigue.',
    truncated_input:'La registrazione supera il limite elaborabile: le migliori finestre non sono presentate come complete.',
    no_valid_segments:'Non ci sono segmenti consecutivi utilizzabili per il calcolo.',insufficient_duration:'I segmenti validi sono troppo brevi per le durate richieste.',
    invalid_window:'L’intervallo della registrazione non è utilizzabile.',numeric_limit:'I valori superano i limiti del calcolo numerico.'
  };
  const expected=[5,15,30,60,300,600,1200,3600];
  const usable=item=>item?.status==='available'&&finite(item.mean_power_w)&&item.mean_power_w>=0&&finite(item.start_s)&&item.start_s>=0&&finite(item.end_s)&&item.end_s>item.start_s;
  const unavailable=item=>item?.status==='insufficient_duration'?'Segmento valido troppo breve':item?.status==='numeric_limit'?'Limite numerico':'Dato non disponibile';
  function render(detail,{onSelect}={}){
    const value=detail?.best_efforts;
    if(!value||typeof value!=='object'||value.status==='not_applicable')return null;
    const root=make('section',null,'power-duration');root.dataset.status=value.status||'unavailable';root.setAttribute('aria-label','Migliore potenza per durata di questa attività');
    const heading=make('div',null,'power-duration-heading');heading.append(make('h3','La tua potenza migliore, per durata'),make('p','Migliori finestre di questa attività · intera registrazione. Ogni punto indica la potenza media più alta registrata per quella durata.','power-duration-scope'));root.append(heading);
    const coverage=value.coverage||{},partial=finite(coverage.coverage_pct)&&coverage.coverage_pct<100;
    const coverageText=make('p',`${partial?'Copertura parziale':'Copertura dei dati'}: ${clock(coverage.covered_seconds)} su ${clock(coverage.window_seconds)} · ${number(coverage.coverage_pct)}%. Segmenti validi: ${number(coverage.segments_count,0)} · il più lungo: ${clock(coverage.longest_segment_seconds)}.`,'power-duration-coverage');coverageText.dataset.partial=String(partial);root.append(coverageText);
    const given=Array.isArray(value.durations)?value.durations:[],items=expected.map(seconds=>({...(given.find(item=>item?.duration_seconds===seconds)||{}),duration_seconds:seconds}));
    const available=value.status==='available'?items.filter(usable):[];
    let selected=available[0]||null;
    const readout=make('p',null,'power-duration-readout');readout.setAttribute('role','status');readout.setAttribute('aria-live','polite');readout.setAttribute('aria-atomic','true');
    const buttons=new Map(),points=new Map();
    function update(){
      for(const [seconds,control]of buttons)control.setAttribute('aria-pressed',String(selected?.duration_seconds===seconds));
      for(const [seconds,point]of points)point.dataset.selected=String(selected?.duration_seconds===seconds);
      readout.textContent=selected?`${duration(selected.duration_seconds)} · ${number(selected.mean_power_w)} W di media. Intervallo ${clock(selected.start_s)} — ${clock(selected.end_s)} dall’inizio · copertura della finestra: ${number(selected.coverage_pct)}%.`:'Nessuna finestra disponibile per le durate richieste.';
    }
    function select(item){selected=item;update();if(typeof onSelect==='function')onSelect({...item});}
    if(available.length){
      const wrapper=make('div',null,'power-duration-plot');wrapper.tabIndex=0;wrapper.setAttribute('role','region');wrapper.setAttribute('aria-label','Grafico potenza e durata; valori disponibili anche nei pulsanti e nella tabella');
      const ns='http://www.w3.org/2000/svg',svgNode=(tag,attrs={},text)=>{const node=document.createElementNS(ns,tag);for(const [key,value]of Object.entries(attrs))node.setAttribute(key,String(value));if(text!=null)node.textContent=text;return node;};
      const svg=svgNode('svg',{viewBox:'0 0 720 236',role:'img','aria-label':'Migliore potenza media registrata in questa attività. Asse verticale in watt da zero; durate su scala logaritmica.'});
      svg.append(svgNode('title',{},'Potenza media massima per durata, in questa attività'));
      const left=64,right=690,top=24,bottom=184,max=Math.max(...available.map(item=>item.mean_power_w)),magnitude=Math.pow(10,Math.floor(Math.log10(max||1))),rounded=Math.ceil(max/magnitude/2)*2*magnitude,ceiling=finite(rounded)&&rounded>0?rounded:max||1;
      const x=seconds=>left+(Math.log(seconds)-Math.log(5))/(Math.log(3600)-Math.log(5))*(right-left),y=watts=>bottom-watts/ceiling*(bottom-top);
      const axisDigits=Math.max(0,Math.min(6,Math.ceil(-Math.log10(ceiling/4))+1));
      for(let index=0;index<=4;index++){const watts=ceiling*(index/4),yy=y(watts);svg.append(svgNode('line',{x1:left,x2:right,y1:yy,y2:yy,class:index?'power-duration-grid':'power-duration-baseline'}),svgNode('text',{x:left-10,y:yy+4,'text-anchor':'end',class:'power-duration-tick'},number(watts,axisDigits)));}
      svg.append(svgNode('text',{x:18,y:15,class:'power-duration-axis'},'W'));
      for(const item of items)svg.append(svgNode('text',{x:x(item.duration_seconds),y:207,'text-anchor':'middle',class:'power-duration-tick'},duration(item.duration_seconds)));
      svg.append(svgNode('text',{x:(left+right)/2,y:230,'text-anchor':'middle',class:'power-duration-axis'},'Durata · scala logaritmica'));
      let previous=null;
      for(const item of items){if(!available.includes(item)){previous=null;continue;}const xx=x(item.duration_seconds),yy=y(item.mean_power_w);if(previous)svg.append(svgNode('line',{x1:previous.x,y1:previous.y,x2:xx,y2:yy,class:'power-duration-line'}));const point=svgNode('circle',{cx:xx,cy:yy,r:5,class:'power-duration-point'});point.append(svgNode('title',{},`${duration(item.duration_seconds)}: ${number(item.mean_power_w)} W`));svg.append(point);points.set(item.duration_seconds,point);previous={x:xx,y:yy};}
      wrapper.append(svg);root.append(wrapper);
    }else{
      const message=make('div',null,'power-duration-unavailable');message.append(make('p','Migliori potenze non disponibili con questi dati.'));
      const codes=[...new Set(Array.isArray(value.reasons)?value.reasons.filter(code=>typeof code==='string'):[])];
      for(const code of codes.length?codes:['unknown'])message.append(make('p',reasons[code]||'I dati disponibili non permettono di calcolare le finestre richieste.','caption'));root.append(message);
    }
    if(value.status==='available'||value.status==='insufficient_data'){
      const choices=make('div',null,'power-duration-choices');choices.setAttribute('role','group');choices.setAttribute('aria-label','Scegli la durata della migliore finestra');
      for(const item of items){const valid=available.includes(item),control=make('button',null,'power-duration-choice');control.type='button';control.disabled=!valid;control.dataset.durationSeconds=String(item.duration_seconds);control.setAttribute('aria-pressed','false');control.setAttribute('aria-label',`${duration(item.duration_seconds)}: ${valid?`${number(item.mean_power_w)} watt. Mostra l’intervallo nel grafico`:unavailable(item)}`);control.append(make('span',duration(item.duration_seconds)),make('strong',valid?`${number(item.mean_power_w)} W`:'N/D'));if(!valid)control.title=unavailable(item);else control.addEventListener('click',()=>select(item));choices.append(control);buttons.set(item.duration_seconds,control);}
      choices.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)||!event.target.matches('button')||!available.length)return;event.preventDefault();const controls=[...buttons.values()].filter(button=>!button.disabled),index=controls.indexOf(event.target);const next=event.key==='Home'?controls[0]:event.key==='End'?controls.at(-1):controls[(index+(event.key==='ArrowRight'?1:-1)+controls.length)%controls.length];next.focus();next.click();});
      root.append(choices,readout);if(available.length)root.append(make('p','Seleziona una durata per evidenziare il suo intervallo nel grafico della registrazione. La curva resta riferita all’intera attività quando cambi lo zoom.','power-duration-scope'));
    }
    const method=make('details',null,'power-duration-method');method.append(make('summary','Valori, copertura e metodo'));
    if(value.status==='available'||value.status==='insufficient_data'){
      const scroll=make('div',null,'power-duration-table');scroll.tabIndex=0;scroll.setAttribute('role','region');scroll.setAttribute('aria-label','Tabella delle migliori finestre di potenza');const table=make('table');table.append(make('caption','Migliori finestre registrate di questa attività'));const head=make('thead'),row=make('tr');for(const label of ['Durata','Potenza media','Intervallo dall’inizio','Copertura finestra','Disponibilità']){const cell=make('th',label);cell.scope='col';row.append(cell);}head.append(row);table.append(head);const body=make('tbody');
      for(const item of items){const valid=available.includes(item),line=make('tr'),name=make('th',duration(item.duration_seconds));name.scope='row';line.append(name);for(const text of [valid?`${number(item.mean_power_w)} W`:'—',valid?`${clock(item.start_s)} — ${clock(item.end_s)}`:'—',valid?`${number(item.coverage_pct)}%`:'—',valid?'Disponibile':unavailable(item)])line.append(make('td',text));body.append(line);}table.append(body);scroll.append(table);method.append(scroll);
    }
    method.append(make('p','Il massimo riguarda solo questa attività: non è un record personale di tutto lo storico. Non stima FTP o forma fisica.','caption'),make('p','La potenza media è calcolata dal server sui campioni originali con integrazione trapezoidale e interpolazione lineare. La ricerca scorre finestre continue entro segmenti consecutivi validi, senza collegare i vuoti.','caption'),make('p','Gli zeri di potenza registrati sono inclusi. Campioni mancanti, non validi e intervalli troppo lunghi sono esclusi: non diventano zero. Le finestre disponibili sono interamente coperte dai dati utilizzabili.','caption'));
    if(finite(value.method?.gap_limit_seconds))method.append(make('p',`Intervallo massimo collegato tra campioni: ${number(value.method.gap_limit_seconds)} s.`,'caption'));
    method.append(make('p',`Fonte: ${sourceLabels[value.source]||(typeof value.source==='string'?value.source:'non disponibile')}.`,'caption'));
    for(const limit of Array.isArray(value.limits)?value.limits:[])if(typeof limit==='string')method.append(make('p',limit,'caption'));
    root.append(method);update();return root;
  }
  window.IronPowerDuration={render};
})();
