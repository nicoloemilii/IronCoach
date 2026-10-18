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
      if(value && typeof value.name==='string'){for(const [key,val]of Object.entries({name:value.name,sets:value.sets||2,reps:value.reps||10,seconds:value.work_seconds||30,weight:value.weight_kg??'',rest:value.rest_seconds??60,notes:value.notes||'',mode:value.work_seconds?'time':'reps'})){row.querySelector(`[name="exercise_${key}"]`).value=val;}updateMode();}
      return row;
    }
    const catalogue=[
      {name:'Squat a corpo libero',group:'Gambe',equipment:'Corpo libero',reps:10,notes:'Usa una profondità che controlli e mantieni il movimento fluido.'},
      {name:'Affondi indietro',group:'Gambe',equipment:'Corpo libero',reps:8,notes:'Ripetizioni per lato; usa un appoggio stabile se necessario.'},
      {name:'Ponte glutei',group:'Gambe',equipment:'Tappetino',reps:12,notes:'Sali in controllo e mantieni il bacino stabile.'},
      {name:'Calf raise',group:'Gambe',equipment:'Corpo libero',reps:12,notes:'Solleva e abbassa i talloni senza rimbalzi.'},
      {name:'Rematore con manubri',group:'Parte alta',equipment:'Manubri',reps:10,notes:'Scegli il carico prima di salvare la scheda.'},
      {name:'Push-up su appoggio',group:'Parte alta',equipment:'Appoggio stabile',reps:8,notes:'Scegli un’altezza che consenta un movimento controllato.'},
      {name:'Plank',group:'Core',equipment:'Tappetino',work_seconds:25,notes:'Mantieni una posizione che riesci a controllare.'},
      {name:'Dead bug',group:'Core',equipment:'Tappetino',reps:8,notes:'Ripetizioni per lato; muovi lentamente braccio e gamba opposti.'},
      {name:'Bird dog',group:'Core',equipment:'Tappetino',reps:8,notes:'Ripetizioni per lato, senza ruotare il bacino.'}
    ];
    const libraryPicker=el('details',null,'exercise-catalogue');libraryPicker.append(el('summary','Sfoglia gli esercizi'));
    const catalogueSearch=inputField(libraryPicker,'Cerca esercizio, gruppo o attrezzo','exercise_search','search',{placeholder:'Es. core, gambe, manubri'}),catalogueRows=el('div',null,'exercise-catalogue-grid');libraryPicker.append(catalogueRows);
    function drawCatalogue(){const query=catalogueSearch.value.toLocaleLowerCase('it-IT');catalogueRows.replaceChildren();for(const exercise of catalogue.filter(e=>`${e.name} ${e.group} ${e.equipment}`.toLocaleLowerCase('it-IT').includes(query))){const item=el('article');item.append(el('strong',exercise.name),el('small',`${exercise.group} · ${exercise.equipment}`),button('Aggiungi',()=>{addExercise(exercise);form.dataset.dirty='true';},'outline'));catalogueRows.append(item);}if(!catalogueRows.children.length)catalogueRows.append(el('p','Nessuna corrispondenza. Puoi aggiungere un esercizio personalizzato.','caption'));}
    catalogueSearch.addEventListener('input',drawCatalogue);drawCatalogue();gym.append(libraryPicker,button('+ Aggiungi esercizio',()=>addExercise(),'outline'));form.append(gym);
    function prepareGym(title,exercises,minutes=30){sport.value='strength';sport.dispatchEvent(new Event('change'));name.value=title;duration.value=minutes;rpe.value=4;date.value=today();purpose.value='Forza di supporto';exerciseList.replaceChildren();for(const exercise of exercises)addExercise(exercise);form.dataset.dirty='true';card.scrollIntoView?.({behavior:'smooth',block:'start'});name.focus();}

    sport.addEventListener('change', () => { const strength = sport.value === 'strength'; gym.hidden = !strength; gym.disabled = !strength; aerobic.hidden = strength; aerobic.disabled = strength; if (strength && !exerciseList.children.length) addExercise(); });
    const status = el('p', '', 'manual-training-status'); status.setAttribute('role', 'status');
    const submit = el('button', 'Salva nel piano', 'primary'); submit.type = 'submit'; form.append(status, submit); card.append(form); container.append(card);
    form.addEventListener('input', () => { form.dataset.dirty = 'true'; });
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
