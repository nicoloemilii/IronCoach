'use strict';
// A bounded diary cache. The archive is filtered and paged by the server.
(() => {
  function create({root,sport,search,fetchPage,renderRow,onItems,onArchiveTotal,debounceMs=250}) {
    const doc=root.ownerDocument,make=(tag,text,cls)=>{const node=doc.createElement(tag);if(text!=null)node.textContent=text;if(cls)node.className=cls;return node;};
    const status=make('div',null,'activity-diary-status'),count=make('p',null,'caption activity-diary-count'),rows=make('div',null,'activity-diary-rows'),controls=make('div',null,'activity-diary-controls');
    status.setAttribute('role','status');count.tabIndex=-1;
    const more=make('button','Mostra altre 50 attività','outline');more.type='button';controls.append(more);root.replaceChildren(status,count,rows,controls);search.maxLength=200;
    let items=[],page=null,displayQuery=null,activeQuery=null,pending=false,promise=null,controller=null,timer=null,version=0,revision=0,renderedRevision=-1,stale=false,needsRefresh=false,message='Apri il diario per caricare le attività.',retryMode=null,lastVisible=false;
    const rowNodes=new Map();
    const query=()=>({sport:sport.value||'',q:search.value||''});
    const key=value=>JSON.stringify(value);
    const visible=()=>!root.closest('.page')?.hidden;
    lastVisible=visible();
    const same=(a,b)=>a&&b&&key(a)===key(b);
    const scope=value=>`${value?.sport?({cycling:'Bici',running:'Corsa',swimming:'Nuoto',strength:'Forza',other:'Altro'}[value.sport]||value.sport):'Tutte le discipline'}${value?.q?` · nome: ${value.q}`:''}`;
    function invalidate() {version++;controller?.abort();controller=null;pending=false;promise=null;}
    function clearTimer(){if(timer!=null){clearTimeout(timer);timer=null;}}
    function render() {
      const changed=displayQuery&&!same(displayQuery,query());
      root.setAttribute('aria-busy',String(pending));root.dataset.stale=String(stale||Boolean(changed));
      status.replaceChildren(make('p',message,'caption'));
      if(retryMode){const retry=make('button',retryMode==='first'?'Ricarica diario':'Riprova','outline');retry.type='button';retry.disabled=pending;retry.addEventListener('click',()=>request(retryMode));status.append(retry);}
      count.textContent=page?`${changed||stale?'Ultimi risultati caricati · ':''}${scope(displayQuery)} · Mostrate ${items.length} di ${page.total} attività${displayQuery.sport||displayQuery.q?' corrispondenti ai filtri':''} · Archivio: ${page.archive_total} attività.`:'Il conteggio dello storico sarà disponibile dopo il caricamento.';
      more.hidden=!page?.has_more||Boolean(changed);more.disabled=pending||stale||Boolean(changed);
      if(!visible())return;
      if(renderedRevision!==revision){rows.replaceChildren();rowNodes.clear();renderedRevision=revision;}
      for(const item of items){const id=String(item.id);if(!rowNodes.has(id)){const row=renderRow(item);row.dataset.activityId=id;rows.append(row);rowNodes.set(id,row);}}
      if(!pending&&page&&!items.length&&!rows.children.length)rows.append(make('p',page.archive_total===0?'Non ci sono attività importate. Puoi aggiungerle da Connessioni.':'Nessuna attività corrisponde ai filtri.','empty-state'));
    }
    function validate(value){
      if(!value||!Array.isArray(value.items)||value.items.length>50||!Number.isSafeInteger(value.total)||value.total<0||!Number.isSafeInteger(value.archive_total)||value.archive_total<value.total||typeof value.has_more!=='boolean'||value.has_more&&typeof value.next_cursor!=='string'||value.items.some(item=>!item||!Number.isSafeInteger(item.id)||item.id<1))throw new Error('Il diario non ha restituito una pagina utilizzabile. Ricarica e riprova.');
    }
    function request(mode='first',{coalesce=false,focusMore=false}={}) {
      const wanted=query();
      if(coalesce&&pending&&same(activeQuery,wanted)&&!page&&promise)return promise;
      if(mode==='more'&&(!page?.next_cursor||!same(displayQuery,wanted)))return Promise.resolve();
      clearTimer();invalidate();const current=version;controller=new AbortController();const signal=controller.signal;activeQuery=wanted;pending=true;retryMode=null;
      const wasFocused=doc.activeElement;const focusedId=wasFocused?.closest('[data-activity-id]')?.dataset.activityId;
      message=mode==='more'?'Caricamento delle attività successive…':items.length?'Aggiornamento del diario… Le ultime attività caricate restano visibili.':'Caricamento delle attività…';render();
      const parameters=new URLSearchParams({limit:'50'});if(wanted.sport)parameters.set('sport',wanted.sport);if(wanted.q)parameters.set('q',wanted.q);if(mode==='more')parameters.set('cursor',page.next_cursor);
      promise=(async()=>{
        try{
          const value=await fetchPage(`/activities/diary?${parameters}`,signal);if(current!==version||signal.aborted)return;validate(value);
          const priorIds=new Set(items.map(item=>item.id));
          const next=mode==='more'?[...items,...value.items.filter(item=>!priorIds.has(item.id))]:value.items;
          const unique=[...new Map(next.map(item=>[item.id,item])).values()];
          if(unique.length>value.total)throw new Error('Lo storico è cambiato durante il caricamento. Ricarica il diario per riallineare i risultati.');
          if(mode==='first'){revision++;}
          items=unique;page=value;displayQuery=wanted;stale=false;needsRefresh=false;message=`${value.has_more?'Puoi caricare le attività successive.':'Tutte le attività corrispondenti ai filtri sono caricate.'} Lo storico può cambiare: usa Aggiorna dati per riallinearlo.`;
          onItems?.([...items]);onArchiveTotal?.(value.archive_total);render();
          if(mode==='more'&&focusMore&&wasFocused===more&&(doc.activeElement===more||doc.activeElement===doc.body)){const added=items.find(item=>!priorIds.has(item.id));if(added)rowNodes.get(String(added.id))?.focus();}
          else if(mode==='first'&&focusedId&&(!doc.activeElement||doc.activeElement===doc.body||doc.activeElement===wasFocused)&&!doc.querySelector('dialog[open]'))(rowNodes.get(focusedId)||count).focus();
        }catch(error){
          if(current!==version||signal.aborted)return;
          stale=Boolean(page);needsRefresh=true;retryMode=mode==='more'&&![409,422].includes(error.status)&&!error.message.includes('riallineare')?'more':'first';
          message=`${items.length?'Aggiornamento non riuscito. Sono conservati gli ultimi risultati caricati.':'Il diario non è disponibile.'} ${error.name==='AbortError'?'Il server non risponde. Riprova tra poco.':error.message||'Riprova tra poco.'}`;
        }finally{if(current===version){pending=false;controller=null;promise=null;render();}}
      })();return promise;
    }
    function filterChanged(delayed=false){clearTimer();invalidate();stale=Boolean(page);needsRefresh=true;retryMode=null;message=items.length?'Sto applicando i nuovi filtri. Sono ancora mostrati i risultati precedenti.':'Ricerca delle attività…';pending=true;render();if(delayed)timer=setTimeout(()=>{timer=null;request('first');},debounceMs);else request('first');}
    function onPageChange(){const now=visible();if(now){if(!page||needsRefresh||!same(displayQuery,query()))request('first',{coalesce:true});else render();}else if(lastVisible&&pending){clearTimer();invalidate();needsRefresh=true;message='Il caricamento riprenderà aprendo il diario.';render();}lastVisible=now;}
    const onSport=()=>filterChanged(false),onSearch=()=>filterChanged(true);
    sport.addEventListener('change',onSport);search.addEventListener('input',onSearch);more.addEventListener('click',()=>request('more',{focusMore:true}));doc.addEventListener('iron:pagechange',onPageChange);
    render();
    return {
      refresh:options=>request('first',options),render,
      reset(){clearTimer();invalidate();items=[];page=null;displayQuery=null;revision++;stale=false;needsRefresh=false;retryMode=null;message='Il diario sarà ricaricato con i dati ripristinati.';onItems?.([]);render();},
      destroy(){clearTimer();invalidate();sport.removeEventListener('change',onSport);search.removeEventListener('input',onSearch);doc.removeEventListener('iron:pagechange',onPageChange);}
    };
  }
  window.IronActivityDiary={create};
})();
