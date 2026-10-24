'use strict';
// Bounded diary contracts with production source and synthetic JSON only.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM,VirtualConsole}=require('jsdom');const read=name=>fs.readFileSync(path.join(__dirname,'..',name),'utf8');
const source=read('activity-diary.js');const item=id=>({id,name:`Attività ${id}`,sport:'cycling',sport_category:'cycling',source:'fit',start_time:'2026-10-18T08:00:00Z',duration_seconds:null,distance_meters:null});
const page=(ids,{total=200,archive=500,cursor='next',more=true}={})=>({items:ids.map(item),total,archive_total:archive,limit:50,has_more:more,next_cursor:more?cursor:null,snapshot:{as_of_utc:'2026-10-22T10:00:00Z',max_id:500}});
const hold=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};};
async function setup(t){
 const errors=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error.message));const dom=new JSDOM('<!doctype html><section id="activities" class="page"><select id="sport"><option value="">Tutto</option><option value="cycling">Bici</option><option value="other">Altro</option></select><input id="search"><div id="root"></div></section>',{runScripts:'outside-only'});t.after(()=>dom.window.close());const w=dom.window,$=selector=>w.document.querySelector(selector),calls=[],updates=[],archives=[];
 const data={response:page([1,2]),fetch:null};w.eval(source);
 const diary=w.IronActivityDiary.create({root:$('#root'),sport:$('#sport'),search:$('#search'),debounceMs:15,fetchPage:async(url,signal)=>{calls.push({url,signal});return data.fetch?data.fetch(url,signal):data.response;},renderRow:value=>{const row=w.document.createElement('button');row.type='button';row.className='list-row';row.textContent=value.name;return row;},onItems:items=>updates.push(Array.from(items,value=>value.id)),onArchiveTotal:value=>archives.push(value)});
 const flush=async()=>{for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(errors,[]);};const wait=async()=>{await new Promise(resolve=>setTimeout(resolve,25));await flush();};
 const filter=(text,sport='')=>{$('#sport').value=sport;$('#search').value=text;$('#search').dispatchEvent(new w.Event('input',{bubbles:true}));};
 return{w,$,data,calls,updates,archives,diary,flush,wait,filter};
}

test('bounded first page reports server matches and archive counts, not just cached rows',async t=>{
 const {$,diary,calls,archives}=await setup(t);await diary.refresh();assert.equal(new URL(calls[0].url,'https://fixture.invalid').searchParams.get('limit'),'50');assert.equal(calls[0].url.startsWith('/activities/diary?'),true);assert.match($('.activity-diary-count').textContent,/Mostrate 2 di 200 attività.*Archivio: 500/);assert.deepEqual(archives,[500]);
});

test('search is debounced and sent literally with Unicode and wildcard characters',async t=>{
 const {filter,wait,calls}=await setup(t);filter('À');filter('ÀLBA_');filter('ÀLBA_100%');await wait();assert.equal(calls.length,1);assert.equal(new URL(calls[0].url,'https://fixture.invalid').searchParams.get('q'),'ÀLBA_100%');
});

test('filter changes abort old reads and generation checks reject late successes or errors',async t=>{
 const {diary,data,filter,wait,flush,calls,$,updates}=await setup(t);const old=hold(),latest=hold();data.fetch=()=>calls.length===1?old.promise:latest.promise;const initial=diary.refresh();filter('nuovo');assert.equal(calls[0].signal.aborted,true);await wait();latest.resolve(page([9],{more:false,total:1}));await flush();old.resolve(page([1]));await initial;assert.deepEqual(updates.at(-1),[9]);assert.match($('.list-row').textContent,/9/);assert.doesNotMatch($('.activity-diary-status').textContent,/non riuscito/);
});

test('load more appends unique rows without rebuilding earlier rows and focuses the new entry',async t=>{
 const {$,diary,data,flush}=await setup(t);await diary.refresh();const first=$('.list-row'),more=$('.activity-diary-controls button');data.response=page([2,3],{more:false,total:3});more.focus();more.click();await flush();assert.equal($('.list-row'),first);assert.equal($('.activity-diary-rows').children.length,3);assert.equal($('.list-row[data-activity-id="3"]'),$.call(null,':focus'));assert.match($('.activity-diary-count').textContent,/Mostrate 3 di 3/);
});

test('a later page cannot append after the filters have changed',async t=>{
 const {diary,data,$,flush,filter,wait}=await setup(t);await diary.refresh();const previous=hold();data.fetch=url=>new URL(url,'https://fixture.invalid').searchParams.has('cursor')?previous.promise:page([99],{more:false,total:1});$('.activity-diary-controls button').click();await flush();filter('nuovo');await wait();previous.resolve(page([3,4]));await flush();assert.equal($('.activity-diary-rows').children.length,1);assert.match($('.list-row').textContent,/99/);
});

test('page errors retain old results and retry only the bounded failed cursor',async t=>{
 const {diary,data,$,flush,calls}=await setup(t);await diary.refresh();const first=$('.list-row');data.fetch=async()=>{throw new Error('Offline di prova');};$('.activity-diary-controls button').click();await flush();assert.equal($('.list-row'),first);assert.match($('.activity-diary-status').textContent,/conservati gli ultimi risultati.*Offline/);data.fetch=null;data.response=page([3],{more:false,total:3});$('.activity-diary-status button').click();await flush();assert.equal($('.activity-diary-rows').children.length,3);assert.equal(new URL(calls.at(-1).url,'https://fixture.invalid').searchParams.get('cursor'),'next');assert.ok(calls.every(call=>call.url.startsWith('/activities/diary?')));
});

test('failed new filters clearly retain the previous scope and retry the current query',async t=>{
 const {diary,data,$,filter,wait,flush,calls}=await setup(t);await diary.refresh();data.fetch=async()=>{throw new Error('Offline');};filter('specifica','other');await wait();assert.match($('.activity-diary-count').textContent,/Ultimi risultati caricati · Tutte le discipline/);assert.equal($('.activity-diary-controls button').hidden,true);assert.equal($('.activity-diary-rows').children.length,2);data.fetch=null;data.response=page([77],{more:false,total:1});$('.activity-diary-status button').click();await flush();const url=new URL(calls.at(-1).url,'https://fixture.invalid');assert.equal(url.searchParams.get('sport'),'other');assert.equal(url.searchParams.get('q'),'specifica');assert.equal(url.searchParams.has('cursor'),false);
});

test('global refresh preserves filters and resets previously appended pages to the first page',async t=>{
 const {diary,data,$,filter,wait,calls,flush}=await setup(t);filter('giro','cycling');await wait();data.response=page([3],{more:false,total:3});$('.activity-diary-controls button').click();await flush();data.response=page([4]);await diary.refresh();const url=new URL(calls.at(-1).url,'https://fixture.invalid');assert.equal(url.searchParams.get('sport'),'cycling');assert.equal(url.searchParams.get('q'),'giro');assert.equal(url.searchParams.has('cursor'),false);assert.equal($('.activity-diary-rows').children.length,1);
});

test('empty filtered results keep archive presence distinct from an empty archive',async t=>{
 const {diary,data,$,archives}=await setup(t);data.response=page([],{total:0,archive:200,more:false});await diary.refresh();assert.match($('.empty-state').textContent,/corrisponde ai filtri/);assert.deepEqual(archives,[200]);data.response=page([],{total:0,archive:0,more:false});await diary.refresh();assert.match($('.empty-state').textContent,/Non ci sono attività importate/);
});

test('cursor invalidation offers a first-page reload and never falls back to the full archive',async t=>{
 const {diary,data,$,flush,calls}=await setup(t);await diary.refresh();data.fetch=async()=>{const error=new Error('Posizione non valida');error.status=422;throw error;};$('.activity-diary-controls button').click();await flush();assert.equal($('.activity-diary-status button').textContent,'Ricarica diario');data.fetch=null;$('.activity-diary-status button').click();await flush();assert.equal(new URL(calls.at(-1).url,'https://fixture.invalid').searchParams.has('cursor'),false);assert.ok(calls.every(call=>call.url.startsWith('/activities/diary?')));
});

test('navigation cancels obsolete page reads and reopens with a fresh bounded query',async t=>{
 const {w,diary,data,$,calls,flush}=await setup(t);const previous=hold();data.fetch=()=>previous.promise;const pending=diary.refresh();$('#activities').hidden=true;w.document.dispatchEvent(new w.CustomEvent('iron:pagechange'));assert.equal(calls[0].signal.aborted,true);previous.reject(new Error('Obsolete failure'));await pending;data.fetch=null;$('#activities').hidden=false;w.document.dispatchEvent(new w.CustomEvent('iron:pagechange'));await flush();assert.equal(calls.length,2);assert.equal($('#root').getAttribute('aria-busy'),'false');assert.doesNotMatch($('.activity-diary-status').textContent,/Obsolete/);
});

test('initial same-query reads can coalesce while a deliberate refresh invalidates old results',async t=>{
 const {diary,data,calls}=await setup(t);const pending=hold();data.fetch=()=>pending.promise;const first=diary.refresh();const coalesced=diary.refresh({coalesce:true});assert.equal(first,coalesced);assert.equal(calls.length,1);data.fetch=null;await diary.refresh();assert.equal(calls[0].signal.aborted,true);pending.resolve(page([99]));await first;
});

test('a timeout is a visible retryable error, not a silent user cancellation',async t=>{
 const {w,diary,data,$}=await setup(t);data.fetch=async()=>{throw new w.DOMException('Timeout','AbortError');};await diary.refresh();assert.match($('.activity-diary-status').textContent,/server non risponde/);assert.ok($('.activity-diary-status button'));
});

test('module output renders imported text inertly and rejects oversized payloads',async t=>{
 const {diary,data,$}=await setup(t);data.response=page([1]);data.response.items[0].name='<img src=x onerror=bad()>';await diary.refresh();assert.equal($('#root img'),null);assert.match($('.list-row').textContent,/<img/);data.response=page(Array.from({length:51},(_,i)=>i+1));await diary.refresh();assert.match($('.activity-diary-status').textContent,/pagina utilizzabile/);assert.equal($('.activity-diary-rows').children.length,1);
});

test('production initialization avoids the legacy full archive and does not block Home on a pending bounded diary',async t=>{
 const html=read('index.html');assert.ok(html.indexOf('/web/activity-diary.js')<html.indexOf('/web/app.js'),'Factory must load before app initialization.');const errors=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error.message));const dom=new JSDOM(html,{url:'https://fixture.invalid/app#today',runScripts:'outside-only',virtualConsole:console});t.after(()=>dom.window.close());const w=dom.window,$=selector=>w.document.querySelector(selector),calls=[];let diaryRead=hold();w.setInterval=()=>1;w.clearInterval=()=>{};w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');this.dispatchEvent(new w.Event('close'));};
 w.fetch=async(url,options={})=>{calls.push({url:String(url),signal:options.signal});if(String(url).startsWith('/activities/diary?'))return diaryRead.promise;if(String(url)==='/activities')throw Error('Unbounded legacy archive requested');let value={};if(url==='/coach/today')value={date:'2026-10-22',training_paused:false};if(url==='/planning/profile')value={profile:{},revision:1};if(String(url).startsWith('/planning/week?'))value={can_undo:false};if(url==='/dashboard/home')value={date:'2026-10-22'};return{ok:true,status:200,json:async()=>value};};
 w.eval(source);const app=read('app.js'),marker="page(location.hash.slice(1) || 'today'); action(load);";assert.ok(app.includes(marker));
 const stubs="render=()=>{window.homeRendered=(window.homeRendered||0)+1;renderActivities();};"+['renderIntervals','renderReview','fillCheckin','fillRacePreparation','renderHealth','renderCoach','renderGoals','renderStrava','renderStravaActivities','renderDuplicates'].map(name=>`${name}=()=>{};`).join('');
 w.eval(app.replace(marker,"page(location.hash.slice(1) || 'today');")+`\n${stubs}\nwindow.diaryAppTest={state,load,action};`);
 await w.diaryAppTest.action(w.diaryAppTest.load);assert.equal(w.homeRendered,1);assert.equal(w.diaryAppTest.state.busy,false);assert.equal(calls.some(call=>call.url==='/activities'),false);assert.equal(calls.filter(call=>call.url.startsWith('/activities/diary?')).length,1);assert.equal(w.diaryAppTest.state.activities.length,0);
 diaryRead.resolve({ok:true,status:200,json:async()=>page([],{total:0,archive:100,more:false})});for(let i=0;i<10;i++)await new Promise(resolve=>setImmediate(resolve));assert.equal(w.ironViewSnapshot.hasActivities,true,'Archive presence must not be inferred from an empty filtered cache.');assert.equal(w.diaryAppTest.state.activityArchiveTotal,100);
 diaryRead=hold();await w.diaryAppTest.action(w.diaryAppTest.load);assert.equal($('#activity-list .activity-diary-controls button').disabled,true,'Global action cleanup must preserve the independent diary pending state.');const previous=calls.filter(call=>call.url.startsWith('/activities/diary?')).at(-1);$('#activity-search').value='Nuovo';$('#activity-search').dispatchEvent(new w.Event('input',{bubbles:true}));assert.equal(previous.signal.aborted,true,'The production api wrapper must forward caller cancellation to fetch.');await new Promise(resolve=>setTimeout(resolve,280));assert.equal(new URL(calls.filter(call=>call.url.startsWith('/activities/diary?')).at(-1).url,'https://fixture.invalid').searchParams.get('q'),'Nuovo');diaryRead.resolve({ok:true,status:200,json:async()=>page([9],{total:1,archive:100,more:false})});for(let i=0;i<10;i++)await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(Array.from(w.diaryAppTest.state.activities,value=>value.id),[9]);assert.equal(calls.some(call=>call.url==='/activities'),false);assert.deepEqual(errors,[]);
});
