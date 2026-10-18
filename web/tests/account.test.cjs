'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM,VirtualConsole}=require('jsdom');
async function setup(t,{login=false,fragment='',mode='hosted',billing=false,failure=false}={}){
  const errors=[],calls=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(fs.readFileSync(path.join(__dirname,login?'../login.html':'../index.html'),'utf8'),{url:'https://product.test/'+(login?'login':'app')+fragment,runScripts:'outside-only',virtualConsole:vc});t.after(()=>dom.window.close());const w=dom.window,$=s=>w.document.querySelector(s);w.Headers=Headers;
  const cfg={mode,legal_ready:true,registration_open:false,email_enabled:true,billing_enabled:billing,billing_required:false,trial_days:14,legal_version:'v1',privacy_url:'https://product.test/privacy',terms_url:'https://product.test/terms',support_email:'support@product.test'};
  w.fetch=async(input,options={})=>{const url=String(input),body=options.body?JSON.parse(options.body):null;calls.push({url,options,body});let status=200,data;
    if(url==='/account/config')data=cfg;
    else if(url==='/account/me')data={mode,authenticated:true,name:'<img src=x onerror=alert(1)>',email:'athlete@example.test',csrf:'private-csrf',access:{paid:false,billing_required:false,trial:true,trial_until:Date.now()/1000+86400}};
    else if(url==='/account/register')data={status:'ready',message:'Account creato. Puoi accedere.'};
    else if(url==='/account/verify')data={message:'Indirizzo confermato.'};
    else if(url==='/account/reset-password')data={message:'Password aggiornata.'};
    else if(url==='/account/login'){status=401;data={detail:'Email o password non validi.'};}
    else if(url==='/billing/price')data={amount_minor:1990,currency:'eur',interval:'month',interval_count:1};
    else if(url==='/billing/checkout')data={url:'https://attacker.test/fake-checkout'};
    else data={ok:true};
    return {ok:status<400,status,json:async()=>data};
  };
  w.eval(fs.readFileSync(path.join(__dirname,'../account.js'),'utf8'));await w.IronAccount.ready;
  const flush=async()=>{for(let i=0;i<10;i++)await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(errors,[]);};await flush();
  const submit=form=>form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));return {w,$,calls,cfg,flush,submit};
}

test('hosted account renders untrusted names as text and sends CSRF only to the same origin',async t=>{
  const {w,$,calls}=await setup(t);assert.equal($('#account-nav').hidden,false);assert.match($('#account-content').textContent,/<img/);assert.equal($('#account-content img'),null);
  await w.fetch('/private',{method:'PUT',body:'{}'});assert.equal(new Headers(calls.at(-1).options.headers).get('x-csrf-token'),'private-csrf');
  await w.fetch('https://external.test/api',{method:'POST',body:'{}'});assert.equal(new Headers(calls.at(-1).options.headers).has('x-csrf-token'),false);
  assert.equal(w.localStorage.length,0);assert.equal(w.sessionStorage.length,0);
});

test('local mode retains the personal application without account navigation or login requirement',async t=>{
  const {$,calls}=await setup(t,{mode:'local'});assert.equal($('#account-nav').hidden,true);assert.match($('#account-content').textContent,/versione personale/);assert.equal(calls.length,1);
});

test('invitation fragment preselects registration, clears address fragment and persists explicit consent',async t=>{
  const {$,w,calls,submit,flush}=await setup(t,{login:true,fragment:'#invite=private-invitation'});const form=$('#auth-register');assert.equal(form.hidden,false);assert.equal(w.location.hash,'');assert.equal(form.elements.invitation.value,'private-invitation');assert.match($('[data-legal=privacy_url]').href,/product.test\/privacy/);
  form.elements.name.value='Atleta';form.elements.email.value='athlete@example.test';form.elements.password.value='valid-test-password-2026';form.elements.terms_accepted.checked=true;form.elements.health_data_consent.checked=true;submit(form);await flush();
  const write=calls.find(c=>c.url==='/account/register');assert.ok(write.body.terms_accepted&&write.body.health_data_consent);assert.equal(write.body.invitation,'private-invitation');assert.equal(form.elements.password.value,'');assert.equal($('#auth-login').hidden,false);
});

test('failed login retains the draft, offers the server reason and enables retry',async t=>{
  const {$,submit,flush}=await setup(t,{login:true});const form=$('#auth-login');form.elements.email.value='athlete@example.test';form.elements.password.value='valid-test-password-2026';submit(form);await flush();assert.match($('#auth-status').textContent,/Email o password/);assert.equal(form.elements.email.value,'athlete@example.test');assert.equal(form.querySelector('[type=submit]').disabled,false);
});

test('verification and reset tokens remain transient and reset requires explicit submission',async t=>{
  const first=await setup(t,{login:true,fragment:'#verify=private-verification'});assert.equal(first.calls.find(c=>c.url==='/account/verify').body.token,'private-verification');assert.equal(first.w.location.hash,'');
  const {$,calls,w,submit,flush}=await setup(t,{login:true,fragment:'#reset=private-reset'});assert.equal($('#auth-reset').hidden,false);assert.equal(calls.some(c=>c.url==='/account/reset-password'),false);$('#auth-reset [name=password]').value='replacement-test-password';submit($('#auth-reset'));await flush();assert.equal(calls.find(c=>c.url==='/account/reset-password').body.token,'private-reset');assert.equal(w.localStorage.length,0);assert.equal(w.sessionStorage.length,0);
});

test('billing displays the server price and refuses a foreign checkout redirect',async t=>{
  const {$,flush,calls}=await setup(t,{billing:true});assert.match($('.account-price').textContent,/19,90/);const checkout=[...$('#account-content').querySelectorAll('button')].find(b=>b.textContent==='Attiva abbonamento');checkout.click();await flush();assert.match($('.account-feedback').textContent,/Collegamento di pagamento non valido/);assert.equal(new Headers(calls.find(c=>c.url==='/billing/checkout').options.headers).get('x-csrf-token'),'private-csrf');
});
