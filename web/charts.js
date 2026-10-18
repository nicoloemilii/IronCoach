'use strict';
(() => {
  const ns='http://www.w3.org/2000/svg';
  const make=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
  const svg=(tag,attrs={},text)=>{const n=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);if(text!=null)n.textContent=text;return n;};
  const num=(n,d=1)=>Number(n).toLocaleString('it-IT',{maximumFractionDigits:d});
  const time=n=>{n=Math.round(n);return n>=3600?`${Math.floor(n/3600)}:${String(Math.floor(n%3600/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`:`${Math.floor(n/60)}:${String(n%60).padStart(2,'0')}`;};
  const tones={power:'#c56124',heart_rate:'#d53964',speed:'#078585',pace:'#6662c2',altitude:'#438450',cadence:'#9463b4',temperature:'#be7d13',distance:'#236fa8'};
  function niceBounds(values,negative=false) {
    const min=Math.min(...values),max=Math.max(...values),span=max-min||Math.max(1,Math.abs(max)*.1),raw=span*1.22/4;
    const power=10**Math.floor(Math.log10(raw)),f=raw/power,step=(f<=1?1:f<=2?2:f<=2.5?2.5:f<=5?5:10)*power;
    let low=Math.floor((min-span*.08)/step)*step;
    if(!negative)low=Math.max(0,low);
    return {low,high:Math.max(low+step,Math.ceil((max+span*.08)/step)*step),step};
  }
  function activity(series,options={}) {
    const factor=series.kind==='speed'?3.6:series.kind==='distance'?.001:1,unit=series.kind==='speed'?'km/h':series.kind==='distance'?'km':series.unit;
    const points=series.points.map(p=>({...p,value:Number.isFinite(p.value)?p.value*factor:null}));
    const valid=points.filter(p=>Number.isFinite(p.time_s)&&Number.isFinite(p.value));
    const root=make('div',null,'interactive-chart');if(!valid.length){root.append(make('p','Serie non disponibile.','empty-state'));return root;}
    const color=tones[series.kind]||'#078585';root.style.setProperty('--chart-color',color);
    const format=v=>series.kind==='pace'?time(v):num(v,Math.abs(v)<10?2:1);
    const first=valid[0].time_s,last=valid.at(-1).time_s,stats=make('div',null,'stream-stats');
    for(const [label,value] of [['Media campioni',valid.reduce((s,p)=>s+p.value,0)/valid.length],['Minimo',Math.min(...valid.map(p=>p.value))],['Massimo',Math.max(...valid.map(p=>p.value))]]){const tile=make('div');tile.append(make('small',label),make('strong',`${format(value)} ${unit}`));stats.append(tile);}root.append(stats);
    const controls=make('div',null,'chart-view-controls'),zoomLabel=make('label','Vista temporale'),zoom=make('select');zoom.name='chart_zoom';
    for(const [v,label]of [['all','Attività completa'],['600','Finestra di 10 minuti'],['1800','Finestra di 30 minuti']]){if(v!=='all'&&last-first<=Number(v))continue;const option=make('option',label);option.value=v;zoom.append(option);}zoomLabel.append(zoom);controls.append(zoomLabel);
    const panLabel=make('label','Inizio finestra'),pan=make('input');pan.type='range';pan.name='chart_start';pan.min=first;pan.max=last;pan.step=1;pan.value=first;panLabel.append(pan);panLabel.hidden=true;controls.append(panLabel);root.append(controls);
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
    const readout=make('p','Muovi il puntatore sul grafico o usa ← e → per leggere i campioni.','chart-readout');readout.setAttribute('role','status');root.append(readout);
    const note=make('p',`${series.sample_count||valid.length} campioni${series.downsampled?' · visualizzazione ridotta, picchi e interruzioni conservati':''}. La media descrive i campioni disponibili. Le fasce sono riferimenti, non un voto della seduta.`,'chart-data-note');root.append(note);
    function draw(){
      readout.textContent='Muovi il puntatore sul grafico o usa ← e → per leggere i campioni.';
      const duration=zoom.value==='all'?last-first:Number(zoom.value),start=zoom.value==='all'?first:Number(pan.value),end=Math.min(last,start+duration);
      panLabel.hidden=zoom.value==='all';pan.max=Math.max(first,last-duration);
      const view=points.filter(p=>p.time_s>=start&&p.time_s<=end),observed=view.filter(p=>Number.isFinite(p.value));
      plot.replaceChildren();if(!observed.length){plot.append(make('p','Nessun campione in questa finestra.','empty-state'));return;}
      const values=observed.map(p=>p.value);if(band&&Number.isFinite(band.lower)&&Number.isFinite(band.upper))values.push(band.lower,band.upper);
      const axis=niceBounds(values,['altitude','temperature'].includes(series.kind)),left=60,right=610,top=24,bottom=212;
      const x=t=>left+(t-start)/(end-start||1)*(right-left),y=v=>bottom-(v-axis.low)/(axis.high-axis.low)*(bottom-top);
      const chart=svg('svg',{viewBox:'0 0 640 260',role:'img',tabindex:0,class:'performance-chart activity-stream-chart','aria-label':`${series.label}, da ${time(start)} a ${time(end)}. Usa le frecce per esplorare.`});chart.append(svg('title',{},`${series.label} · ${unit}`));
      if(band){chart.append(svg('rect',{x:left,y:y(band.upper),width:right-left,height:Math.max(0,y(band.lower)-y(band.upper)),fill:'#d3efdb',class:'reference-band'}));for(const v of [band.lower,band.upper])chart.append(svg('line',{x1:left,x2:right,y1:y(v),y2:y(v),stroke:'#69a77b','stroke-dasharray':'5 4',class:'reference-boundary'}));}
      for(let v=axis.low;v<=axis.high+axis.step/100;v+=axis.step){chart.append(svg('line',{x1:left,x2:right,y1:y(v),y2:y(v),stroke:'#dfe7ec','stroke-dasharray':'3 4'}),svg('text',{x:left-10,y:y(v)+4,'text-anchor':'end',class:'axis-label'},format(v)));}
      chart.append(svg('text',{x:left,y:13,class:'axis-unit'},unit));
      let segment=[];const flush=()=>{if(segment.length){chart.append(svg('polyline',{points:segment.map(p=>`${x(p.time_s)},${y(p.value)}`).join(' '),fill:'none',stroke:color,'stroke-width':2.3,'stroke-linecap':'round','stroke-linejoin':'round'}));if(segment.length===1)chart.append(svg('circle',{cx:x(segment[0].time_s),cy:y(segment[0].value),r:3,fill:color}));}segment=[];};
      for(const p of view){if(!Number.isFinite(p.time_s)||!Number.isFinite(p.value)){flush();continue;}segment.push(p);}flush();
      for(let i=0;i<=4;i++){const t=start+(end-start)*i/4;chart.append(svg('text',{x:x(t),y:239,'text-anchor':'middle',class:'axis-label'},time(t)));}
      const cursor=svg('line',{x1:left,x2:left,y1:top,y2:bottom,stroke:'#79919f','stroke-dasharray':'3 4',visibility:'hidden'}),dot=svg('circle',{r:5,fill:color,stroke:'#fff','stroke-width':2,visibility:'hidden'});chart.append(cursor,dot);
      let selected=0;
      const show=index=>{selected=Math.max(0,Math.min(observed.length-1,index));const p=observed[selected];cursor.setAttribute('x1',x(p.time_s));cursor.setAttribute('x2',x(p.time_s));dot.setAttribute('cx',x(p.time_s));dot.setAttribute('cy',y(p.value));cursor.setAttribute('visibility','visible');dot.setAttribute('visibility','visible');readout.textContent=`${time(p.time_s)} · ${series.label}: ${format(p.value)} ${unit}${band?` · ${p.value<band.lower?'Sotto':p.value>band.upper?'Sopra':'Nella'} fascia`:''}`;};
      chart.addEventListener('pointermove',e=>{const box=chart.getBoundingClientRect();if(!box.width)return;const target=start+(((e.clientX-box.left)/box.width*640-left)/(right-left))*(end-start);let index=0;for(let i=1;i<observed.length;i++)if(Math.abs(observed[i].time_s-target)<Math.abs(observed[index].time_s-target))index=i;show(index);});
      chart.addEventListener('focus',()=>show(selected));chart.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();show(e.key==='Home'?0:e.key==='End'?observed.length-1:selected+(e.key==='ArrowRight'?1:-1));}});plot.append(chart);
      legend.textContent=band?`${band.label||'Fascia obiettivo personale'} · ${format(band.lower)}–${format(band.upper)} ${unit}${band.note?' · '+band.note:''}`:'Fascia obiettivo non impostata. Definiscila in base alla seduta e ai tuoi riferimenti personali.';
      legend.classList.toggle('has-band',!!band);
    }
    zoom.addEventListener('change',()=>{pan.value=first;draw();});pan.addEventListener('input',draw);
    targetForm.addEventListener('submit',e=>{e.preventDefault();const low=Number(lower.value),high=Number(upper.value);if(!lower.value||!upper.value||!Number.isFinite(low)||!Number.isFinite(high)||low>=high){feedback.textContent='Inserisci due valori validi: il primo deve essere inferiore al secondo.';return;}band={lower:low,upper:high,label:'Fascia obiettivo impostata da te'};feedback.textContent='Fascia applicata a questa vista.';draw();});
    clear.addEventListener('click',()=>{band=null;feedback.textContent='Fascia rimossa.';draw();});draw();return root;
  }
  window.IronCoachCharts={activity,niceBounds};
})();
