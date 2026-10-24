'use strict';
// Production renderer with synthetic backend results. No provider, DB or visual browser.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM,VirtualConsole}=require('jsdom');
const source=fs.readFileSync(path.join(__dirname,'../distance-efforts.js'),'utf8');
function fixture(){return{status:'available',metric:'distance',source:'stored_observation_streams',distances:[1000,1609.344,5000,10000,21097.5,42195].map((distance_m,index)=>({distance_m,status:index<4?'available':'insufficient_distance',elapsed_seconds:index<4?distance_m/4:null,start_s:12.345,end_s:12.345+distance_m/4,coverage_pct:100})),coverage:{covered_seconds:3600,window_seconds:4000,coverage_pct:90,segments_count:2,longest_segment_m:12000},method:{gap_limit_seconds:10},limits:['Confini ricavati dai campioni originali.'],reasons:[]};}
function pool(){return{status:'available',metric:'pool_distance',source:'stored_fit_lengths',groups:[{pool_length_m:25,stroke:'freestyle',distances:[{distance_m:100,distance_value:100,distance_unit:'m',status:'available',elapsed_seconds:92,start_s:10,end_s:102},{distance_m:200,distance_value:200,distance_unit:'m',status:'insufficient_distance'}]},{pool_length_m:22.86,stroke:'backstroke',distances:[{distance_m:91.44,distance_value:100,distance_unit:'yd',status:'available',elapsed_seconds:105,start_s:100,end_s:205}]}]};}
function setup(t){const errors=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error.message));const dom=new JSDOM('<!doctype html><body></body>',{runScripts:'outside-only',virtualConsole:console});t.after(()=>{assert.deepEqual(errors,[]);dom.window.close();});const w=dom.window;let requests=0;w.fetch=()=>{requests++;throw Error('No network permitted');};w.eval(source);return{w,requests:()=>requests,render:(value,options)=>{const result=w.IronDistanceEfforts.render(value,options);if(result)w.document.body.append(result);return result;}};}

test('running uses backend distances and elapsed times, labels interpolation and coverage honestly',t=>{
 const {render,requests}=setup(t),root=render(fixture());assert.equal(root.dataset.metric,'running_distance');assert.match(root.textContent,/questa attività.*Tempo trascorso, pause comprese/s);assert.match(root.querySelector('.distance-efforts-coverage').textContent,/Copertura parziale: 1:00:00 su 1:06:40.*90%.*2.*12.000 m/);assert.match(root.querySelector('.distance-efforts-method').textContent,/interpolati linearmente.*tempo trascorso.*pause.*azzeramenti/s);assert.match(root.textContent,/non sono passaggi cronometrici ufficiali/);assert.equal(root.querySelectorAll('tbody tr').length,6);assert.equal(root.querySelectorAll('.distance-efforts-point').length,4);assert.equal(root.querySelector('.distance-efforts-baseline').getAttribute('y1'),'184');assert.match(root.querySelector('svg').getAttribute('aria-label'),/tempo da zero.*scala logaritmica/);assert.equal(requests(),0);
});

test('selection is explicit, preserves precise boundaries and cannot mutate the input',t=>{
 const {render}=setup(t),value=fixture(),before=JSON.stringify(value),selected=[],root=render(value,{onSelect:row=>selected.push(row)});assert.equal(selected.length,0);root.querySelector('button[data-distance-meters="5000"]').click();assert.equal(selected.length,1);assert.equal(selected[0].distance_meters,5000);assert.equal(selected[0].metric,'running_distance');assert.equal(selected[0].start_s,12.345);assert.equal(selected[0].end_s,1262.345);assert.match(root.querySelector('.distance-efforts-readout').textContent,/5 km · 20:50.*0:12,3.*4:10 \/ km/);selected[0].start_s=99;assert.equal(JSON.stringify(value),before);assert.equal(root.querySelectorAll('button[aria-pressed="true"]').length,1);
});

test('zero, nonfinite and malformed times are unavailable and absent points break the curve',t=>{
 const {render}=setup(t),value=fixture();value.distances[1].elapsed_seconds=0;value.distances[2].elapsed_seconds=Infinity;value.distances[3].elapsed_seconds=false;const root=render(value);assert.equal(root.querySelectorAll('button:not(:disabled)').length,1);assert.equal(root.querySelectorAll('.distance-efforts-line').length,0);assert.match(root.querySelector('button[data-distance-meters="5000"]').textContent,/N\/D/);assert.doesNotMatch(root.outerHTML,/NaN|Infinity/);assert.equal(root.querySelector('tbody tr:nth-child(3) td').textContent,'—');
});

test('keyboard navigation skips unavailable distances and keeps selection in one activity',t=>{
 const {w,render}=setup(t),selected=[],first=render(fixture(),{onSelect:row=>selected.push(row.distance_meters)}),second=render(fixture()),button=first.querySelector('button');button.focus();button.dispatchEvent(new w.KeyboardEvent('keydown',{key:'End',bubbles:true,cancelable:true}));assert.equal(w.document.activeElement.dataset.distanceMeters,'10000');assert.deepEqual(selected,[10000]);w.document.activeElement.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true,cancelable:true}));assert.equal(w.document.activeElement,button);assert.equal(second.querySelector('button[aria-pressed="true"]').dataset.distanceMeters,'1000');
});

test('pool comparison separates length and stroke, preserves yards and declares pauses as segment breaks',t=>{
 const {w,render}=setup(t),calls=[],root=render(pool(),{onSelect:row=>calls.push(row)});assert.equal(root.dataset.metric,'pool_distance');assert.match(root.querySelector('.distance-efforts-group').textContent,/25 metri.*Stile libero/);assert.match(root.querySelector('.distance-efforts-method').textContent,/vasche intere consecutive.*pause registrate.*interrompono.*Non interpolano.*non mescolano/s);const select=root.querySelector('select');select.value='1';select.dispatchEvent(new w.Event('change'));assert.equal(calls.length,0,'Changing comparison groups must not select the main activity chart');assert.match(root.querySelector('.distance-efforts-group').textContent,/25 yard.*Dorso/);assert.match(root.querySelector('button').textContent,/100 yd.*1:45/);root.querySelector('button').click();assert.equal(calls[0].pool_length_m,22.86);assert.equal(calls[0].stroke,'backstroke');assert.equal(calls[0].distance_meters,91.44);assert.equal(calls[0].start_s,100);assert.equal(calls[0].end_s,205);assert.equal(root.querySelectorAll('tbody tr').length,1);
});

test('missing pool lengths stay unavailable and no time is inferred from a summary',t=>{
 const {render}=setup(t),root=render({status:'unavailable',metric:'pool_distance',groups:[],reasons:['missing_lengths'],distance:1000,moving_time:1200,average_speed:1});assert.match(root.textContent,/Tempi in piscina non disponibili.*Mancano i tempi e le distanze delle singole vasche/s);assert.equal(root.querySelector('svg,button,table'),null);
});

test('unsafe text stays plain text and unknown reason codes cannot create markup',t=>{
 const {render}=setup(t),value=fixture();value.status='unavailable';value.source='<img src=x onerror=bad()>';value.reasons=['<script>bad()</script>'];value.limits=['<svg onload=bad()>'];const root=render(value);assert.equal(root.querySelector('img,script,svg'),null);assert.match(root.textContent,/<img src=x/);assert.match(root.textContent,/<svg onload/);assert.match(root.textContent,/I dati disponibili non permettono/);
});

test('missing and inapplicable containers render nothing; renderer script precedes app',t=>{
 const {render}=setup(t);assert.equal(render(null),null);assert.equal(render({status:'not_applicable'}),null);const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');assert.equal((html.match(/src="\/web\/distance-efforts\.js"/g)||[]).length,1);assert.ok(html.indexOf('/web/distance-efforts.js')<html.indexOf('/web/app.js'));
});
