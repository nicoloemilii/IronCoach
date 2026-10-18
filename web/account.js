'use strict';
(() => {
  const $=s=>document.querySelector(s),make=(tag,text,cls)=>{const e=document.createElement(tag);if(text!=null)e.textContent=text;if(cls)e.className=cls;return e;};
  const nativeFetch=window.fetch.bind(window),loginPage=!!$('#auth-login');let csrf=null,config=null;
  const api=async(path,options={})=>{
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),30000);
    try{
      const response=await window.fetch(path,{...options,signal:controller.signal,headers:{'Content-Type':'application/json',...options.headers}}),data=await response.json();
      if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Controlla i campi e riprova.');return data;
    }catch(error){if(error.name==='AbortError')throw new Error('Il servizio non risponde. Riprova tra poco.');throw error;}
    finally{clearTimeout(timeout);}
  };
  window.fetch=async(input,options={})=>{
    const url=new URL(typeof input==='string'?input:input.url,location.href),same=url.origin===location.origin,method=(options.method||input.method||'GET').toUpperCase();
    if(same&&csrf&&!['GET','HEAD','OPTIONS'].includes(method)){const headers=new Headers(options.headers||input.headers);headers.set('X-CSRF-Token',csrf);options={...options,headers};}
    const result=await nativeFetch(input,options);
    if(same&&result.status===401&&!loginPage&&config?.mode==='hosted')location.assign('/login');
    return result;
  };
  const send=(path,body,method='POST')=>api(path,{method,body:JSON.stringify(body)});
  const link=(label,href)=>{const a=make('a',label);a.href=href;a.target='_blank';a.rel='noopener noreferrer';return a;};
  function legal(root){for(const a of root.querySelectorAll('[data-legal]'))if(config[a.dataset.legal])a.href=config[a.dataset.legal];}
  function bind(form,work,status){form.addEventListener('submit',async event=>{event.preventDefault();const submit=form.querySelector('[type=submit]');if(submit.disabled||!form.reportValidity())return;submit.disabled=true;status.textContent='Attendi…';status.dataset.error='false';try{await work(form);}catch(e){status.textContent=e.message;status.dataset.error='true';}finally{submit.disabled=false;}});}

  function mountAccount(){
    const nav=$('nav'),main=$('#main-content');if(!nav||!main)return;
    const button=make('button');button.type='button';button.dataset.page='account';button.id='account-nav';button.hidden=true;button.append(make('span','Account'));nav.append(button);
    const section=make('section',null,'page');section.id='account';section.hidden=true;
    // Static markup only: all account and configuration values use textContent.
    section.innerHTML='<div class="page-intro"><p class="eyebrow">IL TUO SPAZIO PERSONALE</p><h2>Account e abbonamento.</h2></div><div id="account-content"><p class="caption">Caricamento dell’account…</p></div>';
    main.append(section);
  }
  mountAccount();

  async function renderAccount(){
    const root=$('#account-content');if(!root)return;
    if(config.mode==='local'){root.replaceChildren(make('p','Stai usando la versione personale sul tuo computer. Gli account e gli abbonamenti sono disponibili nella versione ospitata.','card'));return;}
    const me=await api('/account/me');csrf=me.csrf;$('#account-nav').hidden=false;root.replaceChildren();
    const profile=make('article',null,'card account-card');profile.append(make('p','IL TUO ACCOUNT','eyebrow'),make('h3',me.name),make('p',me.email));
    const feedback=make('p','','account-feedback');feedback.setAttribute('role','status');
    const action=(label,work,style='outline')=>{const b=make('button',label,style);b.type='button';b.addEventListener('click',async()=>{b.disabled=true;feedback.textContent='';try{await work();}catch(e){feedback.textContent=e.message;}finally{b.disabled=false;}});return b;};
    profile.append(action('Esci',async()=>{await send('/account/logout',{});location.assign('/login');}),action('Esci da tutti i dispositivi',async()=>{await send('/account/logout?all_sessions=true',{});location.assign('/login');}));root.append(profile);
    const plan=make('article',null,'card account-card');plan.append(make('p','IL TUO ABBONAMENTO','eyebrow'));
    const date=t=>new Date(t*1000).toLocaleDateString('it-IT');
    plan.append(make('h3',me.access.paid?'Abbonamento attivo':me.access.billing_required?(me.access.trial?`Periodo di prova fino al ${date(me.access.trial_until)}`:'Periodo di prova terminato'):'Accesso beta abilitato'));
    if(config.billing_enabled){
      const price=make('p','Caricamento del prezzo…','account-price');plan.append(price);
      api('/billing/price').then(v=>{price.textContent=v.amount_minor==null?'Prezzo disponibile al pagamento':`${new Intl.NumberFormat('it-IT',{style:'currency',currency:v.currency.toUpperCase()}).format(v.amount_minor/100)} / ${v.interval_count>1?v.interval_count+' ':''}${({month:'mese',year:'anno',week:'settimana',day:'giorno'})[v.interval]||v.interval}`;}).catch(e=>{price.textContent=e.message;});
      const redirect=async path=>{const result=await send(path,{});const target=new URL(result.url);if(target.protocol!=='https:'||!['checkout.stripe.com','billing.stripe.com'].includes(target.hostname))throw new Error('Collegamento di pagamento non valido.');location.assign(target.href);};
      if(!me.access.paid)plan.append(action('Attiva abbonamento',()=>redirect('/billing/checkout'),'primary'));
      plan.append(action('Gestisci pagamenti e cancellazione',()=>redirect('/billing/portal')),action('Aggiorna stato dell’abbonamento',renderAccount,'text-button'));
      plan.append(make('p','L’attivazione viene confermata dal servizio di pagamento. Il ritorno a questa pagina non costituisce conferma del pagamento.','caption'));
    }else plan.append(make('p','I pagamenti non sono ancora attivati dal titolare del servizio.','caption'));
    root.append(plan);
    const privacy=make('article',null,'card account-card');privacy.append(make('p','DATI E ACCESSO','eyebrow'),make('h3','Gestisci i tuoi dati'));
    privacy.append(action('Scarica tutti i tuoi dati',async()=>{const value=await api('/account/export');const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'})),a=make('a');a.href=url;a.download='iron-coach-account.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}));
    for(const [key,label]of [['privacy_url','Informativa privacy'],['terms_url','Condizioni del servizio']])if(config[key])privacy.append(link(label,config[key]));
    if(config.support_email)privacy.append(link('Contatta il supporto','mailto:'+config.support_email));
    const change=make('form');change.innerHTML='<h3>Cambia password</h3><label>Password attuale<input name="current_password" type="password" autocomplete="current-password" maxlength="128" required></label><label>Nuova password · almeno 12 caratteri<input name="password" type="password" autocomplete="new-password" minlength="12" maxlength="128" required></label><button type="submit" class="outline">Aggiorna password</button>';
    bind(change,async f=>{await send('/account/password',{current_password:f.elements.current_password.value,password:f.elements.password.value},'PUT');location.assign('/login');},feedback);privacy.append(change);
    const close=make('details',null,'account-delete');close.append(make('summary','Chiudi l’account e richiedi la cancellazione'));
    const remove=make('form');remove.innerHTML='<p class="caption">L’account verrà disattivato e le sessioni revocate. La cancellazione dei dati viene gestita secondo l’informativa. Chiudi prima eventuali abbonamenti attivi.</p><label>Conferma la tua email<input name="email" type="email" autocomplete="email" required></label><label>Password<input name="password" type="password" autocomplete="current-password" minlength="12" maxlength="128" required></label><button type="submit" class="outline">Disattiva e richiedi la cancellazione</button>';
    bind(remove,async f=>{await send('/account/delete',{email:f.elements.email.value,password:f.elements.password.value});location.assign('/login#closed');},feedback);close.append(remove);privacy.append(close);root.append(privacy,feedback);
  }

  async function bootLogin(){
    const status=$('#auth-status');legal(document);
    if(config.mode==='local'){$('.auth-tabs').hidden=true;for(const f of document.querySelectorAll('form'))f.hidden=true;status.textContent='La versione personale non richiede un account.';status.append(make('br'),link('Apri Iron Coach','/app'));return;}
    const fragment=new URLSearchParams(location.hash.slice(1)),resetToken=fragment.get('reset'),verify=fragment.get('verify'),invitation=fragment.get('invite');
    if(location.hash)history.replaceState(null,'','/login');
    function tab(id){for(const form of document.querySelectorAll('.auth-card>form'))form.hidden=form.id!=='auth-'+id;for(const b of document.querySelectorAll('[data-auth-tab]'))b.setAttribute('aria-pressed',String(b.dataset.authTab===id));}
    for(const b of document.querySelectorAll('[data-auth-tab]'))b.addEventListener('click',()=>tab(b.dataset.authTab));
    $('#auth-register [name=invitation]').required=!config.registration_open;
    $('#auth-registration-note').textContent=!config.legal_ready?'La registrazione sarà disponibile quando il titolare avrà pubblicato le informazioni del servizio.':config.registration_open?`Crea il tuo account. ${config.billing_required&&config.trial_days?`Sono previsti ${config.trial_days} giorni di prova.`:''}`:'Questa versione è disponibile su invito. Inserisci il codice ricevuto.';
    $('#auth-register [type=submit]').disabled=!config.legal_ready;
    $('#auth-forgot [type=submit]').disabled=!config.email_enabled;$('#auth-resend').disabled=!config.email_enabled;
    if(config.support_email)$('#auth-support').append(link('Hai bisogno di aiuto? Contatta il supporto','mailto:'+config.support_email));
    bind($('#auth-login'),async f=>{await send('/account/login',{email:f.elements.email.value,password:f.elements.password.value});location.assign('/app');},status);
    bind($('#auth-register'),async f=>{const value=await send('/account/register',{name:f.elements.name.value,email:f.elements.email.value,password:f.elements.password.value,invitation:f.elements.invitation.value,terms_accepted:f.elements.terms_accepted.checked,health_data_consent:f.elements.health_data_consent.checked});$('#auth-login [name=email]').value=f.elements.email.value;f.elements.password.value='';status.textContent=value.message;tab('login');},status);
    bind($('#auth-forgot'),async f=>{status.textContent=(await send('/account/forgot-password',{email:f.elements.email.value})).message;},status);
    bind($('#auth-reset'),async f=>{status.textContent=(await send('/account/reset-password',{token:resetToken,password:f.elements.password.value})).message;f.elements.password.value='';tab('login');},status);
    $('#auth-resend').addEventListener('click',async()=>{try{status.textContent=(await send('/account/resend-verification',{email:$('#auth-login [name=email]').value})).message;}catch(e){status.textContent=e.message;}});
    if(resetToken)tab('reset');
    if(invitation){$('#auth-register [name=invitation]').value=invitation;tab('register');}
    if(verify){try{status.textContent=(await send('/account/verify',{token:verify})).message;}catch(e){status.textContent=e.message;}}
    if(fragment.has('closed'))status.textContent='Account disattivato. La richiesta di cancellazione è stata registrata.';
  }
  const ready=(async()=>{config=await api('/account/config');if(loginPage)await bootLogin();else await renderAccount();})();
  ready.catch(e=>{const target=$('#auth-status')||$('#account-content');if(target)target.textContent='Impossibile caricare l’account. '+e.message;});
  window.IronAccount={ready,get csrf(){return csrf;}};
})();
