'use strict';
(() => {
  const ns='http://www.w3.org/2000/svg';
  const make=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
  const svg=(tag,attrs={},text)=>{const n=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);if(text!=null)n.textContent=text;return n;};
  const num=(n,d=1)=>Number(n).toLocaleString('it-IT',Math.abs(n)>=1e7||(n!==0&&Math.abs(n)<.001)?{notation:'scientific',maximumFractionDigits:2}:{maximumFractionDigits:d});
  const time=n=>{if(!Number.isFinite(n))return 'N/D';if(n>3153600000)return `${num(n)} s`;n=Math.round(n);return n>=3600?`${Math.floor(n/3600)}:${String(Math.floor(n%3600/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`:`${Math.floor(n/60)}:${String(n%60).padStart(2,'0')}`;};
  const intervalTime=n=>{if(!Number.isFinite(n)||n>3153600000)return time(n);const milliseconds=Math.round(n*1000),whole=Math.floor(milliseconds/1000),fraction=milliseconds%1000;return time(whole)+(fraction?','+String(fraction).padStart(3,'0').replace(/0+$/,''):'');};
  const tones={power:'#c56124',heart_rate:'#d53964',speed:'#078585',pace:'#6662c2',altitude:'#438450',cadence:'#9463b4',temperature:'#be7d13',distance:'#236fa8'};
  function niceBounds(values,negative=false) {
    const scale=Math.max(...values.map(Math.abs))||1,min=Math.min(...values)/scale,max=Math.max(...values)/scale,span=max-min||Math.max(1,Math.abs(max)*.1),raw=span*1.22/4;
    const power=10**Math.floor(Math.log10(raw)),f=raw/power,step=(f<=1?1:f<=2?2:f<=2.5?2.5:f<=5?5:10)*power;
    let low=Math.floor((min-span*.08)/step)*step;
    if(!negative)low=Math.max(0,low);
    const bounded=value=>Math.max(-Number.MAX_VALUE,Math.min(Number.MAX_VALUE,value));
    return {low:bounded(low*scale),high:bounded(Math.max(low+step,Math.ceil((max+span*.08)/step)*step)*scale),step:Math.max(Number.MIN_VALUE,bounded(step*scale))};
  }
  function activity(series,options={}) {
    const factor=series.kind==='speed'?3.6:series.kind==='distance'?.001:1,unit=series.kind==='speed'?'km/h':series.kind==='distance'?'km':series.unit;
    const points=series.points.map(p=>({...p,value:Number.isFinite(p.value)?p.value*factor:null}));
    const valid=points.filter(p=>Number.isFinite(p.time_s)&&Number.isFinite(p.value));
    const root=make('div',null,'interactive-chart');if(!valid.length){root.append(make('p','Serie non disponibile.','empty-state'));return root;}
    const color=tones[series.kind]||'#078585';root.style.setProperty('--chart-color',color);
    const format=v=>series.kind==='pace'?time(v):num(v,Math.abs(v)<10?2:1);
    const raw=series.stats_scope==='raw_recording'&&series.statistics&&typeof series.statistics==='object'?series.statistics:null;
    const scaled=value=>Number.isFinite(value)&&Number.isFinite(value*factor)?value*factor:null;
    const first=raw&&Number.isFinite(raw.window_start_s)?raw.window_start_s:points[0].time_s,last=raw&&Number.isFinite(raw.window_end_s)?raw.window_end_s:points.at(-1).time_s;
    const stats=make('div',null,'stream-stats'),weighted=raw&&Number.isFinite(raw.time_weighted_mean)&&raw.covered_seconds>0;
    const values=raw?[[weighted?'Media temporale':'Media campioni originali',scaled(weighted?raw.time_weighted_mean:raw.sample_mean)],['Minimo',scaled(raw.min)],['Massimo',scaled(raw.max)]]:[['Media punti visualizzati',valid.reduce((s,p)=>s+p.value/valid.length,0)],['Minimo',Math.min(...valid.map(p=>p.value))],['Massimo',Math.max(...valid.map(p=>p.value))]];
    root.append(make('p',raw?'Riepilogo della registrazione completa · non cambia con lo zoom':'Riepilogo dei punti visualizzati · non cambia con lo zoom','chart-stats-scope'));
    for(const [label,value] of values){const tile=make('div');tile.append(make('small',label),make('strong',Number.isFinite(value)?`${format(value)} ${unit}`:'N/D'));stats.append(tile);}root.append(stats);
    const controls=make('div',null,'chart-view-controls'),zoomLabel=make('label','Vista temporale'),zoom=make('select');zoom.name='chart_zoom';
    for(const [v,label]of [['all','Attività completa'],['600','Finestra di 10 minuti'],['1800','Finestra di 30 minuti']]){if(v!=='all'&&last-first<=Number(v))continue;const option=make('option',label);option.value=v;zoom.append(option);}zoomLabel.append(zoom);controls.append(zoomLabel);
    const panLabel=make('label','Inizio finestra'),pan=make('input');pan.type='range';pan.name='chart_start';pan.min=first;pan.max=last;pan.step=1;pan.value=first;panLabel.append(pan);panLabel.hidden=true;controls.append(panLabel);root.append(controls);
    let selectedInterval=null,intervalOption=null;
    const intervalInfo=make('p','','chart-interval-info');intervalInfo.hidden=true;intervalInfo.setAttribute('role','status');
    const clearInterval=make('button','Rimuovi evidenziazione','text-button');clearInterval.type='button';clearInterval.hidden=true;controls.append(clearInterval);root.append(intervalInfo);
    let band=options.reference&&Number.isFinite(options.reference.lower)&&Number.isFinite(options.reference.upper)&&options.reference.lower<options.reference.upper?options.reference:null;
    const custom=make('details',null,'chart-target-editor');custom.append(make('summary','Imposta una fascia obiettivo personale'));
    const targetForm=make('form',null,'chart-target-form');
    const input=(label,name)=>{const l=make('label',label),i=make('input');i.type='number';i.name=name;i.step='any';i.required=true;l.append(i);targetForm.append(l);return i;};
    const lower=input(`Da · ${unit}`,'target_low'),upper=input(`A · ${unit}`,'target_high');
    if(!['temperature','altitude'].includes(series.kind)){lower.min=0;upper.min=0;}
    const apply=make('button','Mostra fascia','outline');apply.type='submit';const clear=make('button','Rimuovi','text-button');clear.type='button';targetForm.append(apply,clear);custom.append(targetForm);
    const feedback=make('p','','caption');feedback.setAttribute('role','status');custom.append(feedback);
    if(!['distance','altitude','temperature'].includes(series.kind))root.append(custom);
    const legend=make('p','','chart-band-legend');root.append(legend);
    const plot=make('div',null,'stream-plot');root.append(plot);
    const navigation=make('div',null,'chart-sample-navigation'),previous=make('button','← Campione precedente','outline'),next=make('button','Campione successivo →','outline');
    previous.type=next.type='button';navigation.append(previous,next);root.insertBefore(navigation,plot);
    const readout=make('p','Usa i pulsanti, le frecce della tastiera o il puntatore per leggere i campioni.','chart-readout');readout.setAttribute('role','status');readout.setAttribute('aria-live','polite');root.append(readout);
    const coverage=raw&&Number.isFinite(raw.coverage_pct)?` Media temporale sui tratti osservati: copertura ${num(raw.coverage_pct)}%.`:'',scope=raw?' Calcoli sui campioni originali, prima della riduzione grafica.':' Statistiche dei soli punti disponibili in questa visualizzazione.';
    const count=raw?`${series.sample_count??valid.length} posizioni temporali · ${raw.valid_samples??'N/D'} misure valide`:`${series.sample_count??valid.length} campioni`;
    const note=make('p',`${count}${series.downsampled?' · visualizzazione ridotta':''}.${scope}${coverage} Le interruzioni restano vuote. Le fasce sono riferimenti personali, non un voto della seduta.`,'chart-data-note');root.append(note);
    let selectSample=null;
    previous.addEventListener('click',()=>selectSample?.(-1));next.addEventListener('click',()=>selectSample?.(1));
    function draw(){
      readout.textContent='Usa i pulsanti, le frecce della tastiera o il puntatore per leggere i campioni.';
      const intervalView=zoom.value==='effort'&&selectedInterval;
      const duration=intervalView?Math.min(last-first,Math.max(600,(selectedInterval.end_s-selectedInterval.start_s)*1.2)):zoom.value==='all'?last-first:Number(zoom.value);
      const start=intervalView?Math.max(first,Math.min(last-duration,selectedInterval.start_s-(duration-(selectedInterval.end_s-selectedInterval.start_s))/2)):zoom.value==='all'?first:Number(pan.value),end=Math.min(last,start+duration);
      panLabel.hidden=zoom.value==='all'||Boolean(intervalView);pan.max=Math.max(first,last-duration);
      const view=points.filter(p=>p.time_s>=start&&p.time_s<=end),observed=view.filter(p=>Number.isFinite(p.value));
      if(!observed.length&&intervalView){zoom.value='all';intervalInfo.textContent=`${selectedInterval.label} · ${intervalTime(selectedInterval.start_s)} — ${intervalTime(selectedInterval.end_s)}. Nessun campione del grafico ridotto in questa finestra: mostro il periodo sull'attività completa.`;draw();return;}
      selectSample=null;previous.disabled=next.disabled=true;
      plot.replaceChildren();if(!observed.length){plot.append(make('p','Nessun campione in questa finestra.','empty-state'));return;}
      const values=observed.map(p=>p.value);if(band&&Number.isFinite(band.lower)&&Number.isFinite(band.upper))values.push(band.lower,band.upper);
      const axis=niceBounds(values,['altitude','temperature'].includes(series.kind)),left=60,right=610,top=24,bottom=212;
      const axisScale=Math.max(Math.abs(axis.low),Math.abs(axis.high),Number.MIN_VALUE),x=t=>left+(t-start)/(end-start||1)*(right-left),y=v=>bottom-(v/axisScale-axis.low/axisScale)/(axis.high/axisScale-axis.low/axisScale)*(bottom-top);
      const chart=svg('svg',{viewBox:'0 0 640 260',role:'img',tabindex:0,class:'performance-chart activity-stream-chart','aria-label':`${series.label}, da ${time(start)} a ${time(end)}. Usa le frecce per esplorare.`});chart.append(svg('title',{},`${series.label} · ${unit}`));
      if(band){chart.append(svg('rect',{x:left,y:y(band.upper),width:right-left,height:Math.max(0,y(band.lower)-y(band.upper)),fill:'#d3efdb',class:'reference-band'}));for(const v of [band.lower,band.upper])chart.append(svg('line',{x1:left,x2:right,y1:y(v),y2:y(v),stroke:'#69a77b','stroke-dasharray':'5 4',class:'reference-boundary'}));}
      if(selectedInterval&&selectedInterval.end_s>start&&selectedInterval.start_s<end){
        const from=Math.max(start,selectedInterval.start_s),to=Math.min(end,selectedInterval.end_s);
        const area=svg('rect',{x:x(from),y:top,width:Math.max(0,x(to)-x(from)),height:bottom-top,fill:'#c56124','fill-opacity':.14,stroke:'#c56124','stroke-width':1,class:'selected-effort-window'});area.append(svg('title',{},`${selectedInterval.label} · ${intervalTime(selectedInterval.start_s)} — ${intervalTime(selectedInterval.end_s)}`));chart.append(area);
      }
      for(let i=0;i<10;i++){const v=(axis.low/axisScale+axis.step/axisScale*i)*axisScale;if(!Number.isFinite(v)||v>axis.high)break;chart.append(svg('line',{x1:left,x2:right,y1:y(v),y2:y(v),stroke:'#dfe7ec','stroke-dasharray':'3 4'}),svg('text',{x:left-10,y:y(v)+4,'text-anchor':'end',class:'axis-label'},format(v)));}
      chart.append(svg('text',{x:left,y:13,class:'axis-unit'},unit));
      let segment=[];const flush=()=>{if(segment.length){chart.append(svg('polyline',{points:segment.map(p=>`${x(p.time_s)},${y(p.value)}`).join(' '),fill:'none',stroke:color,'stroke-width':2.3,'stroke-linecap':'round','stroke-linejoin':'round'}));if(segment.length===1)chart.append(svg('circle',{cx:x(segment[0].time_s),cy:y(segment[0].value),r:3,fill:color}));}segment=[];};
      for(const p of view){if(p.break_before)flush();if(!Number.isFinite(p.time_s)||!Number.isFinite(p.value)){flush();continue;}segment.push(p);}flush();
      for(let i=0;i<=4;i++){const t=start+(end-start)*(i/4);chart.append(svg('text',{x:x(t),y:239,'text-anchor':'middle',class:'axis-label'},time(t)));}
      const cursor=svg('line',{x1:left,x2:left,y1:top,y2:bottom,stroke:'#79919f','stroke-dasharray':'3 4',visibility:'hidden'}),dot=svg('circle',{r:5,fill:color,stroke:'#fff','stroke-width':2,visibility:'hidden'});chart.append(cursor,dot);
      let selected=-1;
      const show=index=>{selected=Math.max(0,Math.min(observed.length-1,index));const p=observed[selected];previous.disabled=selected===0;next.disabled=selected===observed.length-1;cursor.setAttribute('x1',x(p.time_s));cursor.setAttribute('x2',x(p.time_s));dot.setAttribute('cx',x(p.time_s));dot.setAttribute('cy',y(p.value));cursor.setAttribute('visibility','visible');dot.setAttribute('visibility','visible');readout.textContent=`${time(p.time_s)} · ${series.label}: ${format(p.value)} ${unit}${band?` · ${p.value<band.lower?'Sotto':p.value>band.upper?'Sopra':'Nella'} fascia`:''}`;};
      selectSample=direction=>show(selected<0?(direction<0?observed.length-1:0):selected+direction);previous.disabled=next.disabled=false;
      chart.addEventListener('pointermove',e=>{const box=chart.getBoundingClientRect();if(!box.width)return;const target=start+(((e.clientX-box.left)/box.width*640-left)/(right-left))*(end-start);let index=0;for(let i=1;i<observed.length;i++)if(Math.abs(observed[i].time_s-target)<Math.abs(observed[index].time_s-target))index=i;show(index);});
      chart.addEventListener('focus',()=>show(selected));chart.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();show(e.key==='Home'?0:e.key==='End'?observed.length-1:selected+(e.key==='ArrowRight'?1:-1));}});plot.append(chart);
      legend.textContent=band?`${band.label||'Fascia obiettivo personale'} · ${format(band.lower)}–${format(band.upper)} ${unit}${band.note?' · '+band.note:''}`:'Fascia obiettivo non impostata. Definiscila in base alla seduta e ai tuoi riferimenti personali.';
      legend.classList.toggle('has-band',!!band);
    }
    zoom.addEventListener('change',()=>{pan.value=first;draw();});pan.addEventListener('input',draw);
    root.addEventListener('iron:chartinterval',event=>{
      const value=event.detail;if(!value||!Number.isFinite(value.start_s)||!Number.isFinite(value.end_s)||value.end_s<=value.start_s||value.start_s<first-1e-6||value.end_s>last+1e-6)return;
      selectedInterval={start_s:Math.max(first,value.start_s),end_s:Math.min(last,value.end_s),label:typeof value.label==='string'?value.label:'Sforzo selezionato'};
      if(selectedInterval.end_s<=selectedInterval.start_s)return;
      if(!intervalOption){intervalOption=make('option','Intervallo selezionato con contesto');intervalOption.value='effort';zoom.append(intervalOption);}
      zoom.value='effort';intervalInfo.textContent=`${selectedInterval.label} · ${intervalTime(selectedInterval.start_s)} — ${intervalTime(selectedInterval.end_s)}. La fascia arancione indica questo periodo della registrazione.`;intervalInfo.hidden=false;clearInterval.hidden=false;draw();
    });
    clearInterval.addEventListener('click',()=>{selectedInterval=null;intervalOption?.remove();intervalOption=null;intervalInfo.hidden=true;clearInterval.hidden=true;if(!zoom.value||zoom.value==='effort')zoom.value='all';draw();});
    targetForm.addEventListener('submit',e=>{e.preventDefault();const low=Number(lower.value),high=Number(upper.value);if(!lower.value||!upper.value||!Number.isFinite(low)||!Number.isFinite(high)||low>=high){feedback.textContent='Inserisci due valori validi: il primo deve essere inferiore al secondo.';return;}band={lower:low,upper:high,label:'Fascia obiettivo impostata da te'};feedback.textContent='Fascia applicata a questa vista.';draw();});
    clear.addEventListener('click',()=>{band=null;feedback.textContent='Fascia rimossa.';draw();});draw();return root;
  }
  function quality(detail){
    const value=detail?.stream_quality;if(!value||typeof value!=='object')return null;const issues=[];
    if(value.truncated===true)issues.push('registrazione oltre il limite elaborabile; dati parziali');
    const rejected=[value.invalid_timestamp_count,value.out_of_order_count,value.duplicate_timestamp_count].filter(Number.isFinite).reduce((sum,n)=>sum+n,0);
    if(rejected>0)issues.push(`${rejected} timestamp non utilizzabili`);
    if(Number.isFinite(value.long_gap_count)&&value.long_gap_count>0)issues.push(`${value.long_gap_count} interruzioni nella scala temporale`);
    if(Number.isFinite(value.clipped_timestamp_count)&&value.clipped_timestamp_count>0)issues.push(`${value.clipped_timestamp_count} timestamp oltre il termine verificabile, esclusi`);
    if(Array.isArray(value.duplicate_channel_types)&&value.duplicate_channel_types.length)issues.push('canali duplicati esclusi');
    return issues.length?make('p',`Qualità delle serie · ${issues.join(' · ')}.`,'stream-quality'):null;
  }
  window.IronCoachCharts={activity,niceBounds,quality};
})();
