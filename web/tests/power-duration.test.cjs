'use strict';
// Production renderer with synthetic backend values only; no browser or IO.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM,VirtualConsole}=require('jsdom');
const source=fs.readFileSync(path.join(__dirname,'../power-duration.js'),'utf8');
const durations=[5,15,30,60,300,600,1200,3600];
function efforts(){return{status:'available',metric:'power',unit:'W',source:'stored_observation_streams',scope:'activity_valid_segments',durations:durations.map((duration_seconds,index)=>({duration_seconds,status:duration_seconds<=1200?'available':'insufficient_duration',mean_power_w:duration_seconds<=1200?450-index*40:null,start_s:duration_seconds<=1200?10.25+index:null,end_s:duration_seconds<=1200?10.25+index+duration_seconds:null,segment_start_s:0,segment_end_s:2400,covered_seconds:duration_seconds<=1200?duration_seconds:null,coverage_pct:duration_seconds<=1200?100:null})),coverage:{covered_seconds:2400,window_seconds:3000,coverage_pct:80,segments_count:2,longest_segment_seconds:2100},method:{version:'raw-power-duration-1',weighting:'trapezoidal',interpolation:'linear',search:'continuous_window',gap_limit_seconds:10,zero_power:'included'},reasons:[],limits:['Non sono collegate interruzioni della registrazione.']};}
function setup(t){const errors=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error.message));const dom=new JSDOM('<!doctype html><body></body>',{runScripts:'outside-only',virtualConsole:console});t.after(()=>dom.window.close());const w=dom.window;let requests=0;w.fetch=()=>{requests++;throw Error('No network permitted in renderer');};w.eval(source);return{w,requests:()=>requests,render:(value,options)=>{const result=w.IronPowerDuration.render(value,options);if(result)w.document.body.append(result);assert.deepEqual(errors,[]);return result;}};}

test('this-activity curve shows zero-based watts, all duration labels and honest global coverage',t=>{
 const {render,requests}=setup(t),root=render({best_efforts:efforts()});assert.equal(root.getAttribute('aria-label'),'Migliore potenza per durata di questa attività');assert.match(root.querySelector('.power-duration-scope').textContent,/questa attività.*intera registrazione/);assert.match(root.querySelector('.power-duration-coverage').textContent,/Copertura parziale: 40:00 su 50:00 · 80%.*Segmenti validi: 2.*35:00/);assert.equal(root.querySelector('.power-duration-coverage').dataset.partial,'true');
 const svg=root.querySelector('svg');assert.equal(svg.getAttribute('role'),'img');assert.match(svg.getAttribute('aria-label'),/watt da zero.*scala logaritmica/);assert.equal(root.querySelector('.power-duration-baseline').getAttribute('y1'),'184');assert.equal(root.querySelectorAll('.power-duration-point').length,7);for(const label of ['5 s','15 s','30 s','1 min','5 min','10 min','20 min','60 min'])assert.ok([...svg.querySelectorAll('text')].some(node=>node.textContent===label));assert.ok([...svg.querySelectorAll('text')].some(node=>node.textContent==='0'));assert.equal(requests(),0);
});

test('default readout stays local and emits no chart callback until an explicit selection',t=>{
 const {render}=setup(t),selections=[],value=efforts(),original=JSON.stringify(value),root=render({best_efforts:value},{onSelect:item=>selections.push(item)});assert.equal(selections.length,0);assert.match(root.querySelector('.power-duration-readout').textContent,/5 s · 450 W.*0:10,3 — 0:15,3.*100%/);assert.equal(root.querySelector('[data-duration-seconds="5"]').getAttribute('aria-pressed'),'true');const button=root.querySelector('[data-duration-seconds="300"]');button.click();assert.equal(selections.length,1);assert.equal(selections[0].duration_seconds,300);assert.equal(selections[0].start_s,14.25);assert.equal(selections[0].end_s,314.25);assert.equal(button.getAttribute('aria-pressed'),'true');assert.equal(root.querySelector('[data-duration-seconds="5"]').getAttribute('aria-pressed'),'false');assert.equal(root.querySelectorAll('circle[data-selected="true"]').length,1);assert.match(root.querySelector('.power-duration-readout').textContent,/5 min · 290 W/);selections[0].start_s=999;assert.equal(JSON.stringify(value),original,'Callback data must not mutate backend JSON.');
});

test('duration buttons support keyboard navigation and skip unavailable durations',t=>{
 const {w,render}=setup(t),selected=[],root=render({best_efforts:efforts()},{onSelect:item=>selected.push(item.duration_seconds)}),first=root.querySelector('[data-duration-seconds="5"]');assert.equal(first.tagName,'BUTTON');assert.equal(first.type,'button');first.focus();first.dispatchEvent(new w.KeyboardEvent('keydown',{key:'End',bubbles:true,cancelable:true}));assert.equal(w.document.activeElement.dataset.durationSeconds,'1200');assert.deepEqual(selected,[1200]);w.document.activeElement.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true,cancelable:true}));assert.equal(w.document.activeElement,first);assert.equal(selected.at(-1),5);first.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true,cancelable:true}));assert.equal(w.document.activeElement.dataset.durationSeconds,'1200');w.document.activeElement.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Home',bubbles:true,cancelable:true}));assert.equal(w.document.activeElement,first);
});

test('unavailable durations are disabled and never imply zero-watt efforts or bridge a missing point',t=>{
 const {render}=setup(t),value=efforts();value.durations[2]={duration_seconds:30,status:'numeric_limit',mean_power_w:null};const root=render({best_efforts:value}),missing=root.querySelector('[data-duration-seconds="30"]'),long=root.querySelector('[data-duration-seconds="3600"]');assert.equal(missing.disabled,true);assert.equal(long.disabled,true);assert.match(missing.textContent,/N\/D/);assert.match(missing.getAttribute('aria-label'),/Limite numerico/);assert.match(long.getAttribute('aria-label'),/troppo breve/);assert.equal(root.querySelectorAll('.power-duration-point').length,6);assert.equal(root.querySelectorAll('.power-duration-line').length,4,'The line must break across the missing 30s point.');const row=root.querySelectorAll('tbody tr')[2];assert.equal(row.querySelector('td').textContent,'—');assert.doesNotMatch(row.textContent,/0 W/);
});

test('recorded zero watts remain available at the baseline',t=>{
 const {render}=setup(t),value=efforts();for(const item of value.durations)if(item.status==='available')item.mean_power_w=0;const root=render({best_efforts:value});assert.equal(root.querySelector('[data-duration-seconds="5"]').disabled,false);assert.match(root.querySelector('.power-duration-readout').textContent,/0 W di media/);assert.ok([...root.querySelectorAll('circle')].every(node=>node.getAttribute('cy')==='184'));assert.doesNotMatch(root.outerHTML,/NaN|Infinity/);
});

test('extreme finite and tiny positive values use compact scientific labels without corrupting SVG geometry',t=>{
 const {render}=setup(t);for(const amount of [1e308,1e-8]){const value=efforts();for(const item of value.durations)if(item.status==='available')item.mean_power_w=amount;const root=render({best_efforts:value});assert.doesNotMatch(root.outerHTML,/NaN|Infinity/);assert.match(root.querySelector('.power-duration-readout').textContent,amount===1e308?/1E308 W/:/1E-8 W/);assert.ok([...root.querySelectorAll('svg text')].every(node=>node.textContent.length<45));assert.ok([...root.querySelectorAll('svg circle')].every(node=>Number.isFinite(Number(node.getAttribute('cy')))));assert.ok([...root.querySelectorAll('svg text')].some(node=>node.textContent==='0'),'Zero baseline stays literal zero.');}
});

test('fractional watt axes have distinct readable tick labels',t=>{
 const {render}=setup(t),value=efforts();for(const item of value.durations)if(item.status==='available')item.mean_power_w=.1;
 const root=render({best_efforts:value}),ticks=[...root.querySelectorAll('svg text[text-anchor="end"]')].map(node=>node.textContent);
 assert.equal(new Set(ticks).size,5);assert.ok(ticks.includes('0,05'));assert.ok(ticks.includes('0,1'));assert.doesNotMatch(root.outerHTML,/NaN|Infinity/);
});

test('backend maxima are authoritative instead of reduced plot points',t=>{
 const {render}=setup(t),value=efforts();value.durations[0].mean_power_w=126.55;const root=render({best_efforts:value,streams:[{kind:'power',points:[{time_s:0,value:1000},{time_s:3600,value:1000}]}]});assert.match(root.querySelector('[data-duration-seconds="5"]').textContent,/126,6 W/);assert.doesNotMatch(root.querySelector('.power-duration-readout').textContent,/1.000/);
});

test('insufficient duration presents an honest compact empty state and disabled native choices',t=>{
 const {render}=setup(t),value=efforts();value.status='insufficient_data';value.reasons=['insufficient_duration'];for(const item of value.durations){item.status='insufficient_duration';item.mean_power_w=null;item.start_s=null;item.end_s=null;}const root=render({best_efforts:value});assert.equal(root.querySelector('svg'),null);assert.match(root.querySelector('.power-duration-unavailable').textContent,/segmenti validi.*troppo brevi/);assert.equal(root.querySelectorAll('button:disabled').length,8);assert.match(root.querySelector('.power-duration-readout').textContent,/Nessuna finestra/);assert.doesNotMatch(root.querySelector('tbody').textContent,/0 W/);
});

test('missing old contracts and not-applicable power do not add empty sections to every activity',t=>{
 const {render}=setup(t);assert.equal(render(null),null);assert.equal(render({}),null);assert.equal(render({best_efforts:{status:'not_applicable',reasons:['missing_power']}}),null);
});

test('unavailable or truncated data cannot display stale per-duration watts',t=>{
 const {render}=setup(t),value=efforts();value.status='unavailable';value.reasons=['truncated_input','ambiguous_time'];const root=render({best_efforts:value});assert.equal(root.querySelector('svg,table,button'),null);assert.match(root.textContent,/supera il limite elaborabile/);assert.match(root.textContent,/scala temporale.*ambigue/);assert.doesNotMatch(root.textContent,/450 W/);
});

test('method and captioned table distinguish activity windows, valid segments, gaps and zeros',t=>{
 const {render}=setup(t),root=render({best_efforts:efforts()}),method=root.querySelector('details');assert.equal(method.open,false);method.querySelector('summary').click();assert.equal(method.open,true);const table=method.querySelector('table');assert.match(table.querySelector('caption').textContent,/di questa attività/);assert.equal(table.querySelectorAll('thead th[scope="col"]').length,5);assert.equal(table.querySelectorAll('tbody th[scope="row"]').length,8);assert.equal(root.querySelector('.power-duration-table').tabIndex,0);assert.match(method.textContent,/campioni originali.*integrazione trapezoidale e interpolazione lineare/);assert.match(method.textContent,/finestre continue entro segmenti consecutivi validi, senza collegare i vuoti/);assert.match(method.textContent,/zeri di potenza registrati sono inclusi.*non diventano zero/);assert.match(method.textContent,/non è un record personale.*Non stima FTP o forma fisica/);assert.match(method.textContent,/10 s.*Fonte: Serie conservate/);assert.equal(root.querySelector('[data-tone], [role="meter"], [role="progressbar"]'),null);
});

test('nonfinite and malformed numerical fields stay unavailable instead of being coerced to zero',t=>{
 const {render}=setup(t),value=efforts();value.durations[0].mean_power_w=Infinity;value.durations[1].mean_power_w='150';value.durations[2].mean_power_w=false;value.coverage={covered_seconds:null,window_seconds:null,coverage_pct:null,segments_count:null,longest_segment_seconds:null};const root=render({best_efforts:value});for(const seconds of [5,15,30])assert.equal(root.querySelector(`[data-duration-seconds="${seconds}"]`).disabled,true);assert.match(root.querySelector('.power-duration-coverage').textContent,/— su — · —%/);assert.doesNotMatch(root.outerHTML,/NaN|Infinity/);
});

test('imported source and limit text stays inert and unknown reasons have an honest fallback',t=>{
 const {render}=setup(t),value=efforts();value.source='<img src=x onerror=bad()>';value.limits=['<svg onload=bad()>'];value.status='unavailable';value.reasons=['<script>bad()</script>'];const root=render({best_efforts:value});assert.equal(root.querySelector('img,svg,script'),null);assert.match(root.textContent,/<img src=x/);assert.match(root.textContent,/<svg onload/);assert.match(root.textContent,/dati disponibili non permettono/);
});

test('each render owns its selection without leaking into another activity',t=>{
 const {render}=setup(t),calls=[],first=render({best_efforts:efforts()},{onSelect:item=>calls.push(item.duration_seconds)}),next=efforts();next.durations[0].mean_power_w=610;const second=render({best_efforts:next});first.querySelector('[data-duration-seconds="600"]').click();assert.match(first.querySelector('.power-duration-readout').textContent,/10 min/);assert.match(second.querySelector('.power-duration-readout').textContent,/5 s · 610 W/);assert.equal(second.querySelector('[data-duration-seconds="5"]').getAttribute('aria-pressed'),'true');assert.deepEqual(calls,[600]);
});

test('production HTML loads the renderer once before app initialization',()=>{
 const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');assert.equal((html.match(/src="\/web\/power-duration\.js"/g)||[]).length,1);assert.ok(html.indexOf('/web/power-duration.js')<html.indexOf('/web/app.js'));
});
