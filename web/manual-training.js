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
    function addExercise() {
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
      row.append(fields, button('Rimuovi esercizio', () => { row.remove(); form.dataset.dirty = 'true'; }, 'text-button')); exerciseList.append(row);
    }
    gym.append(button('+ Aggiungi esercizio', addExercise, 'outline')); form.append(gym);
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
    const routines = el('div', null, 'list'); routines.setAttribute('aria-live', 'polite'); library.append(routines); container.append(library);
    let loading = false;
    async function refreshLibrary() {
      if (loading) return; loading = true;
      try {
        const workouts = await api('/coach/workouts?limit=500'); routines.replaceChildren();
        const entries = workouts.filter(row => row.sport === 'strength' && row.payload?.exercises?.length && !['superseded', 'skipped', 'cancelled'].includes(row.status));
        if (!entries.length) routines.append(el('p', 'Crea la prima scheda scegliendo Palestra qui sopra.', 'empty-state'));
        for (const row of entries) {
          const item = button('', () => action(() => openGym(row.id)), 'list-row');
          const summary = el('span'); summary.append(el('strong', row.name), el('small', `${row.date} · ${row.payload.exercises.length} esercizi · ${number(row.duration_min)} min`));
          item.append(summary, el('span', 'Segui la scheda →', 'caption')); routines.append(item);
        }
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
      content.replaceChildren(el('p', `${session.completed_count} / ${session.total_sets} serie completate`, 'gym-progress-summary'));
      if (future) content.append(el('p', 'La scheda è programmata per un giorno futuro. Puoi consultarla e seguirla dal giorno indicato.', 'caption'));
      const timer = el('section', null, 'gym-timer'); timer.setAttribute('aria-label', 'Timer della scheda');
      timer.append(el('p', selected.name, 'eyebrow')); const output = el('output', '', 'gym-timer-display'); output.setAttribute('aria-label', 'Secondi rimanenti'); timer.append(output);
      const timerStatus = el('p', session.finished ? 'Scheda completata.' : 'Avvia il recupero dopo ogni serie.', 'gym-timer-state'); timerStatus.setAttribute('role', 'status'); timer.append(timerStatus);
      const controls = el('div', null, 'action-group');
      controls.append(button(`Recupero · ${selected.rest_seconds} s`, () => action(async () => { clock.start(selected.rest_seconds); await persist(); }), 'primary'),
        button(clock.ends != null ? 'Pausa' : 'Riprendi', () => action(async () => { if (clock.ends != null) clock.pause(); else clock.resume(); await persist(); }), 'outline'),
        button('Azzera', () => action(async () => { clock.reset(); await persist(); }), 'text-button'));
      if (selected.work_seconds) controls.append(button(`Timer esercizio · ${selected.work_seconds} s`, () => action(async () => { clock.start(selected.work_seconds); await persist(); }), 'outline'));
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
