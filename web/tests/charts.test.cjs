'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
function setup(t,series,reference){const dom=new JSDOM('<!doctype html><body></body>',{runScripts:'outside-only'});t.after(()=>dom.window.close());const w=dom.window;w.eval(fs.readFileSync(path.join(__dirname,'../charts.js'),'utf8'));const chart=w.IronCoachCharts.activity(series,{reference});w.document.body.append(chart);return {w,chart,$:s=>chart.querySelector(s)};}
const series=()=>({kind:'heart_rate',label:'Frequenza cardiaca',unit:'bpm',points:[{time_s:0,value:100},{time_s:600,value:null},{time_s:1200,value:160},{time_s:1800,value:140}],sample_count:4});
test('activity chart exposes observed stats and keyboard reads actual samples across missing data',t=>{const {w,$}=setup(t,series());assert.match($('.stream-stats').textContent,/133,3/);assert.equal($('.activity-stream-chart').querySelectorAll('polyline').length,2);const svg=$('svg');svg.dispatchEvent(new w.Event('focus'));assert.match($('.chart-readout').textContent,/0:00.*100/);svg.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));assert.match($('.chart-readout').textContent,/20:00.*160/);assert.equal($('.reference-band'),null);});
test('personal target band requires valid ordered values and updates the accessible legend',t=>{const {w,$}=setup(t,series());const form=$('.chart-target-form');form.elements.target_low.value='150';form.elements.target_high.value='120';form.dispatchEvent(new w.Event('submit',{cancelable:true}));assert.equal($('.reference-band'),null);assert.match($('.chart-target-editor').textContent,/inferiore/);form.elements.target_low.value='120';form.elements.target_high.value='150';form.dispatchEvent(new w.Event('submit',{cancelable:true}));assert.ok($('.reference-band'));assert.match($('.chart-band-legend').textContent,/120–120|120–150/);});
test('time windows limit visible samples and pan without joining a missing observation',t=>{const {w,$}=setup(t,series());const zoom=$('[name=chart_zoom]');zoom.value='600';zoom.dispatchEvent(new w.Event('change'));assert.equal($('svg').querySelectorAll('polyline').length,1);const pan=$('[name=chart_start]');pan.value='1200';pan.dispatchEvent(new w.Event('input'));const chart=$('svg');chart.dispatchEvent(new w.KeyboardEvent('keydown',{key:'End'}));assert.match($('.chart-readout').textContent,/30:00.*140/);assert.equal(chart.querySelector('polyline').getAttribute('points').split(' ').length,2);});
test('unavailable or invalid reference does not fabricate an optimal area',t=>{const {$}=setup(t,series(),{lower:200,upper:100,label:'bad'});assert.equal($('.reference-band'),null);assert.match($('.chart-band-legend').textContent,/non impostata/);});
test('recorded power reference is visible and retains provenance instead of a universal optimal claim',t=>{const {$}=setup(t,{...series(),kind:'power',label:'Potenza',unit:'W'},{lower:120,upper:165,label:'Z2 potenza',note:'FTP attuale stimata'});assert.ok($('.reference-band'));assert.match($('.chart-band-legend').textContent,/FTP attuale stimata/);assert.doesNotMatch($('svg').outerHTML,/NaN|Infinity/);});

test('full raw statistics take precedence over display points and retain explicit scope when zooming',t=>{
  const input={...series(),stats_scope:'raw_recording',statistics:{sample_mean:126.55,time_weighted_mean:128.25,min:0,max:200,covered_seconds:1440,coverage_pct:80,window_start_s:0,window_end_s:2000}};
  const {w,$}=setup(t,input);assert.match($('.stream-stats').textContent,/Media temporale128,3/);assert.match($('.stream-stats').textContent,/Minimo0/);assert.match($('.chart-data-note').textContent,/originali.*80%/);
  const before=$('.stream-stats').textContent;const zoom=$('[name=chart_zoom]');zoom.value='600';zoom.dispatchEvent(new w.Event('change'));
  assert.equal($('.stream-stats').textContent,before);assert.match($('.chart-stats-scope').textContent,/registrazione completa.*non cambia con lo zoom/);
});
test('native buttons read actual samples and stay bounded across graph windows',t=>{
  const {w,$}=setup(t,series()),previous=$('.chart-sample-navigation button:first-child'),next=$('.chart-sample-navigation button:last-child');
  next.click();assert.match($('.chart-readout').textContent,/0:00.*100/);assert.equal(previous.disabled,true);
  next.click();assert.match($('.chart-readout').textContent,/20:00.*160/);next.click();assert.equal(next.disabled,true);previous.click();assert.match($('.chart-readout').textContent,/20:00.*160/);
  const zoom=$('[name=chart_zoom]');zoom.value='600';zoom.dispatchEvent(new w.Event('change'));next.click();assert.match($('.chart-readout').textContent,/0:00.*100/);assert.equal(next.disabled,true);
  assert.ok($('.chart-sample-navigation').compareDocumentPosition($('.stream-plot'))&w.Node.DOCUMENT_POSITION_FOLLOWING);
});
test('missing raw statistics stay unavailable and do not fall back to reduced display-point averages',t=>{
  const {$}=setup(t,{...series(),stats_scope:'raw_recording',statistics:{sample_mean:null,time_weighted_mean:null,min:null,max:null,covered_seconds:0,coverage_pct:0,window_start_s:0,window_end_s:1800}});
  assert.equal($('.stream-stats').querySelectorAll('strong')[0].textContent,'N/D');assert.doesNotMatch($('.stream-stats').textContent,/133,3/);
});
test('raw speed statistics are converted to the displayed unit only once',t=>{
  const {$}=setup(t,{...series(),kind:'speed',unit:'m/s',stats_scope:'raw_recording',statistics:{sample_mean:5,time_weighted_mean:6,min:0,max:10,covered_seconds:1800,coverage_pct:100,window_start_s:0,window_end_s:1800}});
  assert.match($('.stream-stats').textContent,/21,6 km\/h/);assert.match($('.stream-stats').textContent,/36 km\/h/);
});
test('extreme finite input cannot create infinite axes or unbounded tick generation',t=>{
  const {w,$}=setup(t,{kind:'power',label:'Potenza',unit:'W',points:[{time_s:0,value:1.7e308},{time_s:10,value:1.6e308}]});
  assert.doesNotMatch($('svg').outerHTML,/NaN|Infinity/);assert.ok($('svg').querySelectorAll('line').length<20);
  assert.ok(Object.values(w.IronCoachCharts.niceBounds([-1.7e308,1.7e308],true)).every(Number.isFinite));
});
test('omitted gap markers still interrupt a reduced graph at the following retained sample',t=>{
  const {$}=setup(t,{kind:'power',label:'Potenza',unit:'W',points:[{time_s:0,value:100},{time_s:1,value:120},{time_s:20,value:140,break_before:true},{time_s:21,value:130}]});
  assert.equal($('svg').querySelectorAll('polyline').length,2);
});
test('a fully recorded zero-power channel has a finite readable chart and explicit zero statistics',t=>{
  const {$}=setup(t,{kind:'power',label:'Potenza',unit:'W',points:[{time_s:0,value:0},{time_s:10,value:0}]});
  assert.doesNotMatch($('svg').outerHTML,/NaN|Infinity/);assert.match($('.stream-stats').textContent,/Minimo0 W.*Massimo0 W/);
  $('.chart-sample-navigation button:last-child').click();assert.match($('.chart-readout').textContent,/0 W/);
});

test('a selected effort highlights its time bounds, keeps raw summary and survives stream-view choices',t=>{
  const input={...series(),kind:'power',label:'Potenza',unit:'W',stats_scope:'raw_recording',statistics:{sample_mean:150,time_weighted_mean:140,min:0,max:300,covered_seconds:1800,coverage_pct:100,window_start_s:0,window_end_s:1800}};
  const {w,chart,$}=setup(t,input,{lower:120,upper:180,label:'Riferimento personale'}),summary=$('.stream-stats').textContent;
  chart.dispatchEvent(new w.CustomEvent('iron:chartinterval',{detail:{start_s:1195.25,end_s:1200.25,label:'Migliore media 5 s'}}));
  assert.equal($('[name=chart_zoom]').value,'effort');assert.ok($('.selected-effort-window'));assert.ok($('.reference-band'));assert.match($('.chart-interval-info').textContent,/Migliore media 5 s/);assert.equal($('.stream-stats').textContent,summary);
  const zoom=$('[name=chart_zoom]');zoom.value='all';zoom.dispatchEvent(new w.Event('change'));assert.ok($('.selected-effort-window'));
  [...chart.querySelectorAll('button')].find(button=>button.textContent==='Rimuovi evidenziazione').click();assert.equal($('.selected-effort-window'),null);assert.equal($('.chart-interval-info').hidden,true);assert.equal(zoom.querySelector('[value=effort]'),null);assert.equal($('.stream-stats').textContent,summary);
});

test('malformed effort windows are ignored without corrupting axes or an existing selection',t=>{
  const {w,chart,$}=setup(t,series());
  chart.dispatchEvent(new w.CustomEvent('iron:chartinterval',{detail:{start_s:0,end_s:30,label:'Valido'}}));
  for(const value of [{start_s:-1,end_s:30},{start_s:0,end_s:Infinity},{start_s:0,end_s:2000},{start_s:30,end_s:30},{start_s:'0',end_s:30}])chart.dispatchEvent(new w.CustomEvent('iron:chartinterval',{detail:value}));
  assert.match($('.chart-interval-info').textContent,/Valido/);assert.ok($('.selected-effort-window'));assert.doesNotMatch($('svg').outerHTML,/NaN|Infinity/);
});

test('a raw effort outside reduced display samples stays visible on the whole chart without inventing points',t=>{
  const input={kind:'power',label:'Potenza',unit:'W',points:[{time_s:0,value:100},{time_s:1800,value:200}],downsampled:true};
  const {w,chart,$}=setup(t,input);
  chart.dispatchEvent(new w.CustomEvent('iron:chartinterval',{detail:{start_s:900.25,end_s:905.25,label:'Migliore media 5 s'}}));
  assert.equal($('[name=chart_zoom]').value,'all');assert.ok($('.selected-effort-window'));assert.match($('.chart-interval-info').textContent,/15:00,25.*15:05,25.*ridotto/);
  assert.equal($('svg polyline').getAttribute('points').split(' ').length,2);assert.doesNotMatch($('svg').outerHTML,/NaN|Infinity/);
});
