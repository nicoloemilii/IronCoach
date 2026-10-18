'use strict';
(() => {
  // Absolute deadlines survive an inactive tab; no per-second network writes.
  class GymRestClock {
    constructor(timer = {}, now = () => Date.now()) { this.now = now; this.restore(timer); }
    restore(timer = {}) { this.remaining = timer.remaining_seconds || 0; this.ends = timer.running && timer.ends_at ? Date.parse(timer.ends_at) : null; }
    seconds() { return this.ends == null ? this.remaining : Math.max(0, Math.ceil((this.ends - this.now()) / 1000)); }
    start(seconds) { this.remaining = Math.max(0, Math.min(600, seconds)); this.ends = this.remaining ? this.now() + this.remaining * 1000 : null; }
    pause() { this.remaining = this.seconds(); this.ends = null; }
    resume() { this.start(this.seconds()); }
    reset(seconds = 0) { this.remaining = seconds; this.ends = null; }
    snapshot() { const seconds = this.seconds(); return { remaining_seconds: seconds, running: this.ends != null && seconds > 0, ends_at: this.ends != null && seconds > 0 ? new Date(this.ends).toISOString() : null }; }
  }
  const mounted = new WeakSet();
  let activeController = null;
  function mount(container, ui = window.IronCoachUI) {
    if (!container || !ui || mounted.has(container)) return;
    mounted.add(container);
    const { el, button, api, action, number } = ui;
    const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Rome', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
    const labels = { cycling: 'Bici', running: 'Corsa', swimming: 'Nuoto', strength: 'Palestra' };
    function inputField(parent, label, name, type = 'text', config = {}) {
      const wrapper = el('label', label), input = el('input'); input.type = type; input.name = name;
      Object.assign(input, config); if (config.value != null) input.defaultValue = String(config.value); wrapper.append(input); parent.append(wrapper); return input;
    }
    function choice(parent, label, name, options) {
      const wrapper = el('label', label), select = el('select'); select.name = name;
      for (const [value, text] of options) { const option = el('option', text); option.value = value; select.append(option); }
      wrapper.append(select); parent.append(wrapper); return select;
    }
    const card = el('article', null, 'card manual-training-card');
    card.append(el('p', 'IL TUO ALLENAMENTO', 'eyebrow'), el('h2', 'Pianifica una seduta'));
    const form = el('form'); form.id = 'manual-training-form';
    const grid = el('div', null, 'form-grid');
    const date = inputField(grid, 'Giorno', 'date', 'date', { required: true, value: today(), min: today() });
    const sport = choice(grid, 'Disciplina', 'sport', Object.entries(labels));
    const name = inputField(grid, 'Nome della seduta', 'name', 'text', { required: true, maxLength: 160, placeholder: 'Es. Fondo facile o forza gambe' });
    const duration = inputField(grid, 'Durata · minuti', 'duration_min', 'number', { required: true, min: 5, max: 600, step: 1, value: 45 });
    const rpe = inputField(grid, 'Sforzo previsto · RPE 1–10', 'target_rpe', 'number', { required: true, min: 1, max: 10, step: 1, value: 4 });
    const purpose = inputField(grid, 'Obiettivo della seduta', 'purpose', 'text', { maxLength: 1500, placeholder: 'Cosa vuoi allenare?' });
    form.append(grid);
    const aerobic = el('fieldset', null, 'manual-phases');
    aerobic.append(el('legend', 'Fasi della seduta · facoltative'));
    const phaseList = el('div', null, 'manual-phase-list'); aerobic.append(phaseList);
    function addPhase() {
      if (phaseList.children.length >= 40) return;
      const row = el('div', null, 'manual-phase-row form-grid');
      inputField(row, 'Nome fase', 'phase_name', 'text', { required: true, maxLength: 100, placeholder: 'Es. Riscaldamento' });
      inputField(row, 'Minuti', 'phase_duration', 'number', { required: true, min: 1, max: 600, step: 1, value: 10 });
      inputField(row, 'RPE', 'phase_rpe', 'number', { required: true, min: 1, max: 10, step: 1, value: 3 });
      inputField(row, 'Istruzioni', 'phase_instruction', 'text', { maxLength: 1000 });
      const remove = button('Rimuovi fase', () => { row.remove(); form.dataset.dirty = 'true'; }, 'text-button');
      remove.setAttribute('aria-label', 'Rimuovi questa fase'); row.append(remove); phaseList.append(row);
    }
    aerobic.append(button('+ Aggiungi fase', addPhase, 'outline')); form.append(aerobic);
    const gym = el('fieldset', null, 'manual-gym'); gym.hidden = true; gym.disabled = true;
    gym.append(el('legend', 'La tua scheda palestra'));
    const exerciseList = el('div', null, 'gym-builder-list'); gym.append(exerciseList);
    function addExercise(value = {}) {
      if (exerciseList.children.length >= 30) return;
      const row = el('div', null, 'gym-exercise-builder');
      const fields = el('div', null, 'form-grid');
      inputField(fields, 'Esercizio', 'exercise_name', 'text', { required: true, maxLength: 120, placeholder: 'Es. Squat' });
      inputField(fields, 'Serie', 'exercise_sets', 'number', { required: true, min: 1, max: 20, step: 1, value: 3 });
      const mode = choice(fields, 'Esecuzione', 'exercise_mode', [['reps', 'Ripetizioni'], ['time', 'A tempo']]);
      const reps = inputField(fields, 'Ripetizioni per serie', 'exercise_reps', 'number', { required: true, min: 1, max: 100, step: 1, value: 10 });
      const seconds = inputField(fields, 'Secondi per serie', 'exercise_seconds', 'number', { required: true, min: 5, max: 600, step: 1, value: 30 });
      function updateMode() { const timed = mode.value === 'time'; reps.disabled = timed; reps.parentElement.hidden = timed; seconds.disabled = !timed; seconds.parentElement.hidden = !timed; }
      mode.addEventListener('change', updateMode); updateMode();
      inputField(fields, 'Carico · kg (facoltativo)', 'exercise_weight', 'number', { min: 0, max: 1000, step: .5, placeholder: 'Corpo libero' });
      inputField(fields, 'Recupero tra le serie · secondi', 'exercise_rest', 'number', { required: true, min: 0, max: 600, step: 1, value: 90 });
      inputField(fields, 'Indicazioni', 'exercise_notes', 'text', { maxLength: 1000, placeholder: 'Es. movimento controllato' });
      row.append(fields, button('Sposta su',()=>{if(row.previousElementSibling)exerciseList.insertBefore(row,row.previousElementSibling);form.dataset.dirty='true';},'text-button'),button('Sposta giù',()=>{if(row.nextElementSibling)exerciseList.insertBefore(row.nextElementSibling,row);form.dataset.dirty='true';},'text-button'), button('Rimuovi esercizio', () => { row.remove(); form.dataset.dirty = 'true'; }, 'text-button')); exerciseList.append(row);
      row.addEventListener('input',()=>{row.dataset.edited='true';});row.addEventListener('change',()=>{row.dataset.edited='true';});
      if(value && typeof value.name==='string'){for(const [key,val]of Object.entries({name:value.name,sets:value.sets||2,reps:value.reps||10,seconds:value.work_seconds||30,weight:value.weight_kg??'',rest:value.rest_seconds??60,notes:value.notes||'',mode:value.work_seconds?'time':'reps'})){row.querySelector(`[name="exercise_${key}"]`).value=val;}updateMode();}
      return row;
    }
    const catalogue=[
      {name:'Squat a corpo libero',group:'Gambe',equipment:'Corpo libero',reps:10,notes:'Usa una profondità che controlli e mantieni il movimento fluido.'},
      {name:'Affondi indietro',group:'Gambe',equipment:'Corpo libero',reps:8,notes:'Ripetizioni per lato; usa un appoggio stabile se necessario.'},
      {name:'Ponte glutei',group:'Glutei',equipment:'Corpo libero',reps:12,notes:'Sali in controllo e mantieni il bacino stabile.'},
      {name:'Calf raise',group:'Polpacci',equipment:'Corpo libero',reps:12,notes:'Solleva e abbassa i talloni senza rimbalzi.'},
      {name:'Rematore con manubri',group:'Dorso',equipment:'Manubri',reps:10,notes:'Scegli il carico prima di salvare la scheda.'},
      {name:'Push-up su appoggio',group:'Petto',equipment:'Corpo libero',reps:8,notes:'Scegli un’altezza che consenta un movimento controllato.'},
      {name:'Plank',group:'Core',equipment:'Corpo libero',work_seconds:25,notes:'Mantieni una posizione che riesci a controllare.'},
      {name:'Dead bug',group:'Core',equipment:'Corpo libero',reps:8,notes:'Ripetizioni per lato; muovi lentamente braccio e gamba opposti.'},
      {name:'Bird dog',group:'Core',equipment:'Corpo libero',reps:8,notes:'Ripetizioni per lato, senza ruotare il bacino.'},
      // Repetition/time values are editable draft fields; loads are always left unset.
      ...[
        ['Gambe','Corpo libero',[
          ['Squat sumo','sumo squat'],['Squat su panca','box squat'],['Affondi in avanti','forward lunge','side'],['Affondi laterali','lateral lunge','side'],['Split squat','affondo statico','side'],['Squat isometrico al muro','wall sit','time']]],
        ['Gambe','Bilanciere',[
          ['Back squat','squat bilanciere posteriore'],['Front squat','squat frontale'],['Stacco rumeno con bilanciere','romanian deadlift rdl femorali'],['Good morning','hip hinge femorali'],['Affondi con bilanciere','barbell lunge','side']]],
        ['Gambe','Manubri',[
          ['Goblet squat con manubrio','accosciata'],['Squat con manubri','dumbbell squat'],['Affondi camminati con manubri','walking lunge','side'],['Affondi indietro con manubri','reverse lunge','side'],['Split squat bulgaro con manubri','bulgarian split squat','side'],['Stacco rumeno con manubri','romanian deadlift rdl femorali'],['Stacco rumeno a una gamba','single leg rdl','side'],['Step-up con manubri','salita gradino','side']]],
        ['Gambe','Macchine',[
          ['Leg press a 45°','pressa gambe'],['Leg press orizzontale','pressa seduta'],['Leg press a una gamba','pressa monopodalica','side'],['Squat al multipower','smith machine'],['Hack squat','hack machine'],['Leg extension','quadricipiti'],['Leg curl seduto','femorali'],['Leg curl sdraiato','lying leg curl femorali'],['Leg curl in piedi','standing leg curl','side'],['Adductor machine','adduttori interno coscia'],['Abductor machine','abduttori esterno coscia']]],
        ['Gambe','Kettlebell',[
          ['Goblet squat con kettlebell','accosciata'],['Stacco con kettlebell','kettlebell deadlift'],['Affondi con kettlebell','kettlebell lunge','side']]],
        ['Gambe','Step',[
          ['Step-up','salita gradino','side'],['Step-down','discesa gradino','side'],['Step-up laterale','salita laterale','side']]],
        ['Gambe','Fitball',[
          ['Leg curl su fitball','swiss ball hamstring curl'],['Squat al muro con fitball','wall squat']]],
        ['Glutei','Corpo libero',[
          ['Ponte glutei a una gamba','single leg glute bridge','side'],['Frog pump','ponte a farfalla'],['Slanci posteriori in quadrupedia','donkey kick','side'],['Abduzioni sul fianco','side lying leg raise','side'],['Fire hydrant','abduzione in quadrupedia','side']]],
        ['Glutei','Bilanciere',[
          ['Hip thrust con bilanciere','spinta anca'],['Glute bridge con bilanciere','ponte glutei carico'],['Stacco sumo con bilanciere','sumo deadlift']]],
        ['Glutei','Manubri',[
          ['Hip thrust con manubrio','spinta anca panca'],['Glute bridge con manubrio','ponte glutei carico']]],
        ['Glutei','Cavi',[
          ['Kickback al cavo','slancio posteriore glutei','side'],['Pull-through al cavo','estensione anca'],['Abduzione anca al cavo','slancio laterale','side']]],
        ['Glutei','Elastici',[
          ['Camminata laterale con miniband','lateral band walk','side'],['Monster walk','camminata elastico'],['Clamshell con elastico','conchiglia anca','side'],['Ponte glutei con miniband','banded bridge']]],
        ['Glutei','Macchine',[
          ['Glute machine','macchina glutei','side'],['Hip thrust alla macchina','glute drive']]],
        ['Polpacci','Corpo libero',[
          ['Calf raise a una gamba','single leg calf','side'],['Tibialis raise al muro','tibiale anteriore']]],
        ['Polpacci','Manubri',[
          ['Calf raise con manubri','standing calf'],['Calf raise seduto con manubrio','soleo']]],
        ['Polpacci','Macchine',[
          ['Calf raise alla macchina in piedi','standing calf machine'],['Calf raise alla macchina seduto','seated calf soleo'],['Calf press alla pressa','polpacci leg press']]],
        ['Petto','Corpo libero',[
          ['Push-up','piegamenti flessioni'],['Push-up sulle ginocchia','knee push up'],['Push-up con piedi rialzati','decline push up']]],
        ['Petto','Bilanciere',[
          ['Panca piana con bilanciere','bench press'],['Panca inclinata con bilanciere','incline bench press'],['Floor press con bilanciere','distensioni a terra']]],
        ['Petto','Manubri',[
          ['Panca piana con manubri','dumbbell bench press'],['Panca inclinata con manubri','incline press'],['Floor press con manubri','distensioni a terra'],['Croci su panca piana','dumbbell fly'],['Croci su panca inclinata','incline fly'],['Pullover con manubrio','dumbbell pullover']]],
        ['Petto','Cavi',[
          ['Croci ai cavi alti','cable crossover'],['Croci ai cavi bassi','low cable fly'],['Chest press ai cavi','spinte petto']]],
        ['Petto','Macchine',[
          ['Chest press alla macchina','spinte petto seduto'],['Chest press inclinata','incline machine press'],['Pec deck','pectoral machine butterfly croci']]],
        ['Dorso','Sbarra e parallele',[
          ['Trazioni presa prona','pull up'],['Trazioni presa supina','chin up'],['Trazioni scapolari','scapular pull up']]],
        ['Dorso','Macchine',[
          ['Trazioni assistite alla macchina','assisted pull up'],['Lat machine presa larga','lat pulldown'],['Lat machine presa neutra','neutral pulldown'],['Lat machine presa supina','reverse pulldown'],['Rematore alla macchina','row machine'],['Pullover alla macchina','pullover machine'],['Back extension alla macchina','estensioni lombari']]],
        ['Dorso','Cavi',[
          ['Pulley basso','seated cable row'],['Rematore al cavo a un braccio','single arm row','side'],['Pulldown a braccia tese','straight arm pulldown'],['Rematore al cavo alto','high cable row']]],
        ['Dorso','Bilanciere',[
          ['Rematore con bilanciere','bent over row'],['Rematore landmine','t bar row'],['Stacco da terra','deadlift']]],
        ['Dorso','Manubri',[
          ['Rematore a un braccio su panca','one arm dumbbell row','side'],['Rematore su panca inclinata','chest supported row']]],
        ['Dorso','TRX',[
          ['Rematore al TRX','suspension row'],['Rematore alto al TRX','high row']]],
        ['Dorso','Elastici',[
          ['Rematore con elastico','band row'],['Pulldown con elastico','band lat pull'],['Pull-apart con elastico','band pull apart']]],
        ['Spalle','Manubri',[
          ['Shoulder press con manubri','lento avanti distensioni'],['Arnold press','rotazione distensioni'],['Alzate laterali con manubri','lateral raise'],['Alzate frontali con manubri','front raise'],['Alzate posteriori su panca','reverse fly deltoidi posteriori']]],
        ['Spalle','Bilanciere',[
          ['Military press','overhead press lento avanti'],['Landmine press a un braccio','spinte angolate','side']]],
        ['Spalle','Cavi',[
          ['Alzate laterali al cavo','cable lateral raise','side'],['Face pull','tirate al viso deltoidi posteriori'],['Croci inverse ai cavi','reverse cable fly'],['Extrarotazioni al cavo','rotazione esterna spalla','side']]],
        ['Spalle','Macchine',[
          ['Shoulder press alla macchina','spinte spalle'],['Reverse pec deck','croci inverse posteriori'],['Alzate laterali alla macchina','lateral raise machine']]],
        ['Spalle','Elastici',[
          ['Extrarotazioni con elastico','rotazione esterna spalla','side'],['Face pull con elastico','band face pull']]],
        ['Spalle','Corpo libero',[
          ['Pike push-up','piegamenti spalle'],['Scapular push-up','piegamenti scapolari']]],
        ['Bicipiti','Manubri',[
          ['Curl alternato con manubri','dumbbell curl','side'],['Hammer curl','curl a martello'],['Curl su panca inclinata','incline curl'],['Curl concentrato','concentration curl','side'],['Curl spider con manubri','spider curl']]],
        ['Bicipiti','Bilanciere',[
          ['Curl con bilanciere dritto','barbell curl'],['Curl con bilanciere EZ','ez bar curl'],['Curl inverso con bilanciere','reverse curl avambracci']]],
        ['Bicipiti','Cavi',[
          ['Curl al cavo basso','cable curl'],['Curl con corda al cavo','rope hammer curl'],['Curl al cavo a un braccio','single arm curl','side']]],
        ['Bicipiti','Macchine',[
          ['Curl Scott alla macchina','preacher curl panca scott'],['Curl alla macchina','biceps machine']]],
        ['Bicipiti','Elastici',[
          ['Curl con elastico','band biceps curl']]],
        ['Tricipiti','Cavi',[
          ['Pushdown con corda','rope triceps extension'],['Pushdown con barra','straight bar push down'],['Estensioni sopra la testa al cavo','overhead triceps extension'],['Estensioni al cavo a un braccio','single arm pushdown','side']]],
        ['Tricipiti','Manubri',[
          ['French press con manubri','lying triceps extension'],['Estensioni sopra la testa con manubrio','overhead extension'],['Kickback con manubrio','triceps kick back','side']]],
        ['Tricipiti','Bilanciere',[
          ['French press con bilanciere EZ','skull crusher'],['Panca presa stretta','close grip bench press']]],
        ['Tricipiti','Sbarra e parallele',[
          ['Dip alle parallele','parallel bar dip']]],
        ['Tricipiti','Macchine',[
          ['Dip assistiti alla macchina','assisted dip'],['Triceps extension alla macchina','estensioni tricipiti']]],
        ['Tricipiti','Corpo libero',[
          ['Push-up presa stretta','close grip push up']]],
        ['Core','Corpo libero',[
          ['Side plank','plank laterale','time-side'],['Plank su braccia tese','high plank','time'],['Plank con tocco delle spalle','shoulder tap','side'],['Crunch','addominali'],['Crunch inverso','reverse crunch'],['Bicycle crunch','addominali bicicletta','side'],['Hollow hold','tenuta addominale','time'],['Sollevamento gambe da terra','leg raise'],['Mountain climber','scalatore','time'],['Heel touch','tocco talloni','side'],['Bear plank','plank orso','time']]],
        ['Core','Cavi',[
          ['Pallof press al cavo','anti rotazione','side'],['Woodchop al cavo','taglialegna','side'],['Crunch al cavo','cable crunch']]],
        ['Core','Elastici',[
          ['Pallof press con elastico','anti rotazione','side']]],
        ['Core','Fitball',[
          ['Plank su fitball','stabilita','time'],['Crunch su fitball','swiss ball crunch'],['Stir the pot','cerchi su fitball','time']]],
        ['Core','TRX',[
          ['Plank al TRX','suspension plank','time'],['Knee tuck al TRX','raccolta ginocchia']]],
        ['Core','Sbarra e parallele',[
          ['Sollevamento ginocchia alla sbarra','hanging knee raise'],['Sollevamento ginocchia alle parallele','captain chair']]],
        ['Core','Macchine',[
          ['Crunch alla macchina','abdominal machine']]],
        ['Total body','Manubri',[
          ['Farmer carry con manubri','camminata del contadino','time'],['Suitcase carry','camminata unilaterale','time-side'],['Thruster con manubri','squat e distensione']]],
        ['Total body','Kettlebell',[
          ['Kettlebell swing','slancio'],['Farmer carry con kettlebell','camminata del contadino','time']]],
        ['Total body','Palla medica',[
          ['Slam con palla medica','medicine ball slam'],['Squat con palla medica','medicine ball squat']]],
        ['Total body','Corpo libero',[
          ['Burpee senza salto','step back burpee'],['Bear crawl','camminata orso','time'],['Inchworm','camminata con le mani']]],
        ['Mobilità','Corpo libero',[
          ['Cat-cow','gatto mucca colonna'],['Rotazioni toraciche in quadrupedia','thread the needle','side'],['Mobilità anca 90/90','hip switch','side'],['Mobilità caviglia al muro','knee to wall','side'],['Affondo con rotazione toracica','world greatest stretch','side'],['Wall slide','scivolamenti al muro spalle'],['Circonduzioni delle spalle','shoulder circles'],['Rock-back adduttori','adductor rockback','side']]]
      ].flatMap(([group,equipment,entries])=>entries.map(([name,aliases,mode])=>({name,aliases,group,equipment,sets:2,rest_seconds:60,
        ...(mode?.startsWith('time')?{work_seconds:30}:{reps:10}),
        notes:mode?.includes('side')?(mode.startsWith('time')?'Durata indicata per lato.':'Ripetizioni indicate per lato.'):'',
      })))
    ];
    const libraryPicker=el('details',null,'exercise-catalogue');libraryPicker.append(el('summary',`Sfoglia i ${catalogue.length} esercizi`));
    libraryPicker.append(el('p','Scegli gli esercizi e personalizza serie, ripetizioni o durata, recuperi e carichi. I valori iniziali sono una bozza modificabile.','caption'));
    const filters=el('div',null,'exercise-catalogue-filters');
    const catalogueSearch=inputField(filters,'Cerca esercizio o nome inglese','exercise_search','search',{placeholder:'Es. panca manubri, leg press, core'});
    const options=key=>[...new Set(catalogue.map(e=>e[key]))].sort((a,b)=>a.localeCompare(b,'it'));
    const muscle=choice(filters,'Gruppo muscolare','exercise_group',[['','Tutti i gruppi'],...options('group').map(v=>[v,v])]);
    const equipment=choice(filters,'Attrezzatura','exercise_equipment',[['','Tutte le attrezzature'],...options('equipment').map(v=>[v,v])]);libraryPicker.append(filters);
    const toolbar=el('div',null,'exercise-catalogue-toolbar'),catalogueCount=el('p','','caption');catalogueCount.setAttribute('role','status');
    toolbar.append(catalogueCount,button('Azzera filtri',()=>{catalogueSearch.value='';muscle.value='';equipment.value='';drawCatalogue(true);catalogueSearch.focus();},'text-button'));libraryPicker.append(toolbar);
    const catalogueRows=el('div',null,'exercise-catalogue-grid'),catalogueFeedback=el('p','','exercise-catalogue-feedback');catalogueFeedback.setAttribute('role','status');
    let visibleExercises=24;
    const moreExercises=button('Mostra altri 24 esercizi',()=>{visibleExercises+=24;drawCatalogue();},'outline');
    libraryPicker.append(catalogueRows,moreExercises,catalogueFeedback);
    const normalize=text=>text.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase('it-IT').replace(/[^a-z0-9]+/g,' ').trim();
    function drawCatalogue(reset=false){
      if(reset)visibleExercises=24;
      const words=normalize(catalogueSearch.value).split(' ').filter(Boolean);
      const found=catalogue.filter(e=>(!muscle.value||e.group===muscle.value)&&(!equipment.value||e.equipment===equipment.value)&&words.every(word=>normalize(`${e.name} ${e.aliases||''} ${e.group} ${e.equipment}`).includes(word)));
      const shown=found.slice(0,visibleExercises);catalogueRows.replaceChildren();
      for(const exercise of shown){
        const item=el('article');item.append(el('strong',exercise.name),el('small',`${exercise.group} · ${exercise.equipment}`));
        if(exercise.work_seconds)item.append(el('span','A tempo','exercise-mode-tag'));
        const added=el('small','','exercise-added');
        const add=button('Aggiungi',()=>{
          const blank=exerciseList.children.length===1?exerciseList.firstElementChild:null;
          if(blank&&!blank.dataset.edited&&!blank.querySelector('[name=exercise_name]').value.trim())blank.remove();
          if(!addExercise(exercise)){added.textContent='Limite di 30 esercizi raggiunto.';catalogueFeedback.textContent='Hai raggiunto 30 esercizi. Rimuovine uno dalla scheda prima di aggiungerne altri.';return;}
          form.dataset.dirty='true';added.textContent='Aggiunto alla scheda';catalogueFeedback.textContent=`${exercise.name} aggiunto alla scheda · ${exerciseList.children.length}/30 esercizi.`;
        },'outline');add.setAttribute('aria-label',`Aggiungi ${exercise.name} alla scheda`);item.append(add,added);catalogueRows.append(item);
      }
      catalogueCount.textContent=`${found.length} esercizi trovati su ${catalogue.length} · ${shown.length} visibili`;
      moreExercises.hidden=shown.length>=found.length;
      if(!found.length)catalogueRows.append(el('p','Nessuna corrispondenza. Azzera i filtri oppure aggiungi un esercizio personalizzato.','empty-state'));
    }
    catalogueSearch.addEventListener('input',()=>drawCatalogue(true));muscle.addEventListener('change',()=>drawCatalogue(true));equipment.addEventListener('change',()=>drawCatalogue(true));drawCatalogue();
    gym.insertBefore(libraryPicker,exerciseList);gym.append(button('+ Aggiungi esercizio',()=>{if(!addExercise())catalogueFeedback.textContent='Hai raggiunto il limite di 30 esercizi per scheda.';},'outline'));form.append(gym);
    function prepareGym(title,exercises,minutes=30){sport.value='strength';sport.dispatchEvent(new Event('change'));name.value=title;duration.value=minutes;rpe.value=4;date.value=today();purpose.value='Forza di supporto';exerciseList.replaceChildren();for(const exercise of exercises)addExercise(exercise);form.dataset.dirty='true';card.scrollIntoView?.({behavior:'smooth',block:'start'});name.focus();}

    sport.addEventListener('change', () => { const strength = sport.value === 'strength'; gym.hidden = !strength; gym.disabled = !strength; aerobic.hidden = strength; aerobic.disabled = strength; if (strength && !exerciseList.children.length) addExercise(); });
    const status = el('p', '', 'manual-training-status'); status.setAttribute('role', 'status');
    const submit = el('button', 'Salva nel piano', 'primary'); submit.type = 'submit'; form.append(status, submit); card.append(form); container.append(card);
    form.addEventListener('input', event => { if(!event.target.closest('.exercise-catalogue'))form.dataset.dirty = 'true'; });
    form.addEventListener('submit', event => {
      event.preventDefault(); if (!form.reportValidity()) return;
      // Capture before the shared action disables form controls.
      const value = { date: date.value, sport: sport.value, name: name.value.trim(), duration_min: Number(duration.value), target_rpe: Number(rpe.value), purpose: purpose.value.trim(), steps: [], exercises: [] };
      if (sport.value === 'strength') {
        value.exercises = [...exerciseList.children].map(row => {
          const get = key => row.querySelector(`[name="exercise_${key}"]`).value;
          return { name: get('name').trim(), sets: Number(get('sets')), reps: get('mode') === 'reps' ? Number(get('reps')) : null,
            work_seconds: get('mode') === 'time' ? Number(get('seconds')) : null, weight_kg: get('weight') === '' ? null : Number(get('weight')), rest_seconds: Number(get('rest')), notes: get('notes').trim() };
        });
        if (!value.exercises.length) { status.textContent = 'Aggiungi almeno un esercizio alla scheda.'; return; }
      } else {
        value.steps = [...phaseList.children].map(row => ({ name: row.querySelector('[name="phase_name"]').value.trim(), duration_min: Number(row.querySelector('[name="phase_duration"]').value), target_rpe: Number(row.querySelector('[name="phase_rpe"]').value), instruction: row.querySelector('[name="phase_instruction"]').value.trim() }));
        if (value.steps.length && value.steps.reduce((sum, step) => sum + step.duration_min, 0) !== value.duration_min) { status.textContent = 'La somma dei minuti delle fasi deve corrispondere alla durata della seduta.'; return; }
      }
      action(async () => {
        try {
          const result = await api('/training/manual', { method: 'POST', body: JSON.stringify(value) });
          delete form.dataset.dirty;
          form.reset(); date.value = today(); sport.dispatchEvent(new Event('change')); phaseList.replaceChildren(); exerciseList.replaceChildren();
          drawCatalogue(true);catalogueFeedback.textContent='';
          status.replaceChildren(el('strong', 'Seduta salvata nel piano.'));
          for (const warning of result.warnings || []) status.append(el('span', warning, 'manual-warning'));
          await ui.load(); await refreshLibrary();
          if (value.sport === 'strength') status.append(button('Segui la scheda →', () => action(() => openGym(result.id)), 'outline'));
          else if (ui.openWorkout) status.append(button('Apri la seduta →', () => ui.openWorkout(result.payload, result), 'outline'));
        } catch (error) { status.textContent = error.message; throw error; }
      });
    });

    const library = el('article', null, 'card gym-library'); library.append(el('p', 'SERIE, RECUPERI, PROGRESSI', 'eyebrow'), el('h2', 'Le tue schede palestra'));
    library.append(el('p','Crea una base, personalizza esercizi e carichi, poi salvala nel giorno scelto. Puoi riutilizzare le tue schede senza perdere l’avanzamento delle precedenti.','muted'));
    const templates=el('div',null,'gym-template-grid');
    for(const [title,indices]of [['Gambe e core',[0,2,6]],['Parte alta e stabilità',[4,5,8]],['Core a corpo libero',[6,7,8]]]){const tile=el('article');tile.append(el('span','BOZZA MODIFICABILE','eyebrow'),el('h3',title),el('p',indices.map(i=>catalogue[i].name).join(' · ')),button('Usa come base',()=>prepareGym(title,indices.map(i=>catalogue[i])),'outline'));templates.append(tile);}library.append(templates,button('+ Crea scheda vuota',()=>prepareGym('La mia scheda',[]),'primary'));
    const librarySearch=inputField(library,'Cerca tra le tue schede','gym_search','search',{placeholder:'Nome o esercizio'});
    const routines = el('div', null, 'list'); routines.setAttribute('aria-live', 'polite'); library.append(routines); container.append(library);
    let loading = false, libraryEntries=[];
    function drawLibrary(){routines.replaceChildren();const query=librarySearch.value.toLocaleLowerCase('it-IT');const entries=libraryEntries.filter(row=>`${row.name} ${row.payload.exercises.map(e=>e.name).join(' ')}`.toLocaleLowerCase('it-IT').includes(query));
      if(!entries.length)routines.append(el('p',libraryEntries.length?'Nessuna scheda corrisponde alla ricerca.':'Le tue schede salvate appariranno qui. Inizia da una base o crea la tua.','empty-state'));
      for(const row of entries){const wrapper=el('article',null,'gym-library-routine'),item=button('',()=>action(()=>openGym(row.id)),'list-row');const summary=el('span');const sets=row.payload.exercises.reduce((n,e)=>n+e.sets,0);summary.append(el('strong',row.name),el('small',`${row.date} · ${row.payload.exercises.length} esercizi · ${sets} serie · ${number(row.duration_min)} min`));item.append(summary,el('span',row.date>today()?'Anteprima →':'Segui la scheda →','badge'));wrapper.append(item);const details=el('details');details.append(el('summary','Esercizi e recuperi'));const list=el('ul');for(const e of row.payload.exercises)list.append(el('li',`${e.name} · ${e.sets} × ${e.reps||e.work_seconds+' s'}${e.weight_kg!=null?' · '+e.weight_kg+' kg':''} · recupero ${e.rest_seconds} s`));details.append(list);wrapper.append(details,button('Riutilizza in una nuova seduta',()=>prepareGym(row.name,row.payload.exercises,row.duration_min),'text-button'));routines.append(wrapper);}
    }
    librarySearch.addEventListener('input',drawLibrary);
    async function refreshLibrary() {
      if (loading) return; loading = true;
      try {
        const workouts = await api('/coach/workouts?limit=500'); routines.replaceChildren();
        libraryEntries=workouts.filter(row=>row.sport==='strength'&&row.payload?.exercises?.length&&!['superseded','skipped','cancelled'].includes(row.status));drawLibrary();
      } catch (error) { routines.replaceChildren(el('p', 'Schede non disponibili.'), button('Riprova', () => action(refreshLibrary), 'outline')); }
      finally { loading = false; }
    }
    const dialog = el('dialog', null, 'gym-session-dialog'); dialog.id = 'gym-session-dialog'; dialog.setAttribute('aria-labelledby', 'gym-session-title');
    document.body.append(dialog); let session = null, clock = new GymRestClock(), clockTick = null, saving = false;
    const top = el('div', null, 'dialog-top'); const heading = el('h2', 'La tua scheda'); heading.id = 'gym-session-title';
    top.append(button('← Indietro', () => dialog.close(), 'back-button'), heading);
    const content = el('div', null, 'gym-session-content'); dialog.append(top, content);
    function tick() {
      const output = dialog.querySelector('.gym-timer-display'); if (!output) return;
      const seconds = clock.seconds(); output.textContent = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
      if (clock.ends != null && seconds === 0) { clock.reset(); const state = dialog.querySelector('.gym-timer-state'); if (state) state.textContent = 'Timer terminato. Procedi quando sei pronto.'; }
    }
    dialog.addEventListener('close', () => { if (clockTick != null) clearInterval(clockTick); clockTick = null; });
    async function persist(change = {}) {
      if (saving || !session) return; saving = true;
      const original = JSON.parse(JSON.stringify(session.progress)); const next = { ...session.progress, ...change, timer: clock.snapshot() };
      content.querySelectorAll('button, input, textarea').forEach(node => { node.disabled = true; });
      try {
        session = await api(`/training/gym/${session.workout_id}/progress`, { method: 'PUT', body: JSON.stringify({ expected_revision: session.revision, ...next }) });
        clock.restore(session.progress.timer); renderSession();
      } catch (error) {
        session.progress = original; clock.restore(original.timer); renderSession();
        const failure = el('p', error.message, 'error'); failure.setAttribute('role', 'alert');
        failure.append(button('Ricarica progresso', () => action(() => openGym(session.workout_id)), 'outline')); content.prepend(failure); throw error;
      } finally { saving = false; }
    }
    function renderSession() {
      const exercises = session.exercises, active = session.progress.active_exercise, selected = exercises[active];
      const future = session.date > today(); heading.textContent = session.name;
      content.replaceChildren(el('p',`${session.completed_count} / ${session.total_sets} serie completate`,'gym-progress-summary'));const progress=el('progress');progress.max=session.total_sets;progress.value=session.completed_count;progress.setAttribute('aria-label','Serie completate nella scheda');content.append(progress);
      if (future) content.append(el('p', 'La scheda è programmata per un giorno futuro. Puoi consultarla e seguirla dal giorno indicato.', 'caption'));
      const timer = el('section', null, 'gym-timer'); timer.setAttribute('aria-label', 'Timer della scheda');
      timer.append(el('p', selected.name, 'eyebrow')); const output = el('output', '', 'gym-timer-display'); output.setAttribute('aria-label', 'Secondi rimanenti'); timer.append(output);
      const timerStatus = el('p', session.finished ? 'Scheda completata.' : 'Avvia il recupero dopo ogni serie.', 'gym-timer-state'); timerStatus.setAttribute('role', 'status'); timer.append(timerStatus);
      const controls = el('div', null, 'action-group');
      controls.append(button(`Recupero · ${selected.rest_seconds} s`, () => action(async () => { clock.start(selected.rest_seconds); await persist(); }), 'primary'),
        button(clock.ends != null ? 'Pausa' : 'Riprendi', () => action(async () => { if (clock.ends != null) clock.pause(); else clock.resume(); await persist(); }), 'outline'),
        button('Azzera', () => action(async () => { clock.reset(); await persist(); }), 'text-button'));
      if (selected.work_seconds) controls.append(button(`Timer esercizio · ${selected.work_seconds} s`, () => action(async () => { clock.start(selected.work_seconds); await persist(); }), 'outline'));
      const next=exercises.findIndex((exercise,index)=>Array.from({length:exercise.sets},(_,set)=>session.progress.completed_sets.some(p=>p.exercise_index===index&&p.set_index===set)).some(done=>!done));if(next>=0&&next!==active)controls.append(button('Vai al prossimo esercizio da completare',()=>action(async()=>{clock.pause();await persist({active_exercise:next});}),'outline'));
      timer.append(controls); content.append(timer);
      const list = el('div', null, 'gym-live-exercises');
      for (const [index, exercise] of exercises.entries()) {
        const row = el('article', null, `gym-live-exercise${index === active ? ' active' : ''}`);
        const summary = el('div', null, 'gym-exercise-heading'); summary.append(el('h3', `${index + 1}. ${exercise.name}`), el('span', `${exercise.sets} × ${exercise.reps || `${exercise.work_seconds} s`}${exercise.weight_kg != null ? ` · ${number(exercise.weight_kg, 1)} kg` : ''}`, 'badge'));
        row.append(summary); if (exercise.notes) row.append(el('p', exercise.notes));
        const sets = el('div', null, 'gym-set-list');
        for (let set = 0; set < exercise.sets; set++) {
          const label = el('label', null, 'gym-set-check'); const check = el('input'); check.type = 'checkbox';
          check.checked = session.progress.completed_sets.some(item => item.exercise_index === index && item.set_index === set);
          check.setAttribute('aria-label', `${exercise.name}, serie ${set + 1}`);
          check.addEventListener('change', () => {
            const checked = check.checked;
            action(async () => {
              const completed = session.progress.completed_sets.filter(item => item.exercise_index !== index || item.set_index !== set);
              if (checked) { completed.push({ exercise_index: index, set_index: set }); if (completed.length === session.total_sets) clock.reset(); else clock.start(exercise.rest_seconds); }
              else clock.pause();
              await persist({ completed_sets: completed, active_exercise: index });
            });
          });
          label.append(check, el('span', `Serie ${set + 1}`)); sets.append(label);
        }
        row.append(sets); if (index !== active) row.append(button('Seleziona esercizio', () => action(async () => { clock.pause(); await persist({ active_exercise: index }); }), 'text-button')); list.append(row);
      }
      content.append(list);
      const notes = inputField(content, 'Note sulla scheda', 'gym_notes', 'text', { value: session.progress.notes || '', maxLength: 2000 });
      content.append(button('Salva nota', () => { const note = notes.value; action(() => persist({ notes: note })); }, 'outline'));
      if (session.finished) content.append(el('p', 'Tutte le serie sono state segnate. Il progresso non crea un’attività sportiva nello storico.', 'gym-finished'));
      if (future) content.querySelectorAll('button, input').forEach(node => { node.disabled = true; delete node.dataset.busyDisabled; });
      tick();
    }
    async function openGym(workoutId) {
      const value = await api(`/training/gym/${workoutId}/progress`); session = value; clock.restore(value.progress.timer); renderSession();
      if (!dialog.open) dialog.showModal(); if (clockTick != null) clearInterval(clockTick); clockTick = setInterval(tick, 250);
    }
    function refresh() { if (!document.getElementById('training')?.hidden) refreshLibrary(); }
    document.addEventListener('iron:loaded', refresh); document.addEventListener('iron:pagechange', refresh); refresh();
    activeController = { openGym, refreshLibrary };
    return activeController;
  }
  window.IronManualTraining = { mount, GymRestClock, openGym: workoutId => activeController?.openGym(workoutId) };
  const container = document.getElementById('manual-training'); if (container) mount(container);
})();
