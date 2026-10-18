'use strict';
let homeDay = null;
let coachReturnPage='today';
const $ = selector => document.querySelector(selector);
const state = { today: null, home: null, health: null, healthRange: '', coach: null, goals: [], strava: null, profile: null, settings: null, settingsRevision: null, week: null, weekStart: monday(), workouts: [], activities: [], activityLimit: 50, busy: false, selected: null, garmin: null, failedReads: new Set() };
const sports = { cycling: 'Bici', running: 'Corsa', swimming: 'Nuoto', bike: 'Bici', run: 'Corsa', swim: 'Nuoto', strength: 'Forza' };
const statuses = { planned: 'Pianificata', completed: 'Completata', skipped: 'Saltata', cancelled: 'Sostituita', superseded: 'Sostituita' };
const stepNames = { warmup: 'Riscaldamento', cooldown: 'Defaticamento', endurance: 'Resistenza', easy: 'Facile', interval: 'Ripetuta', recovery: 'Recupero', aerobic: 'Aerobico', tempo: 'Ritmo sostenuto', technique: 'Tecnica e controllo', strength: 'Forza' };
const titles = { today: 'Il tuo allenamento, in chiaro.', health: 'Ascolta il tuo recupero.', coach: 'Il giudizio richiede evidenze.', calendar: 'Obiettivo e piano.', training: 'Allenati con una direzione.', activities: 'Ogni passo conta.', athlete: 'Il punto di partenza sei tu.', goal: 'Dai forma ai tuoi obiettivi.', connections: 'Connetti il tuo percorso.' };
const number = (value, digits = 0) => value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString('it-IT', { maximumFractionDigits: digits });
const scaled = (value, divisor) => value == null ? null : value / divisor;
function dateString(date) { return date.toISOString().slice(0, 10); }
function athleteDate(value = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Rome', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(value);
  const get = key => parts.find(p => p.type === key).value; return `${get('year')}-${get('month')}-${get('day')}`;
}
function activityDate(value) { return athleteDate(new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : value + 'Z')); }
function monday(value = athleteDate()) { const d = new Date(value + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() - (d.getUTCDay() + 6) % 7); return dateString(d); }
function offsetDate(value, days) { const d = new Date(value + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + days); return dateString(d); }
function dayLabel(value) { return new Intl.DateTimeFormat('it-IT', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(value + 'T12:00:00Z')); }
function el(tag, text, className) { const node = document.createElement(tag); if (text != null) node.textContent = text; if (className) node.className = className; return node; }
function button(text, callback, className = 'outline') {
  const node = el('button', text, className); node.type = 'button';
  if (state.busy) { node.disabled = true; node.dataset.busyDisabled = 'true'; }
  node.addEventListener('click', callback); return node;
}
function notice(message, error = false) {
  $('#notice').textContent = message; $('#notice').className = error ? 'error' : ''; $('#notice').hidden = !message;
  const dialog = document.querySelector('dialog[open]');
  document.querySelectorAll('.dialog-notice').forEach(node => node.remove());
  if (dialog && message) {
    const local = el('p', message, `dialog-notice ${error ? 'error' : ''}`); local.setAttribute('role', error ? 'alert' : 'status');
    dialog.querySelector('.dialog-top').after(local);
    if (error) { local.tabIndex = -1; local.focus(); }
  }
}
async function api(path, options = {}) {
  const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(path, { ...options, signal: controller.signal, headers: { 'Content-Type': 'application/json', ...options.headers } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : data.detail?.message || (Array.isArray(data.detail) ? data.detail.map(v => `${v.loc?.slice(1).join('.')}: ${v.msg}`).join('; ') : `Richiesta non completata (${response.status}).`);
      const error = new Error(detail); error.status = response.status; error.path = path; error.detail = data.detail; throw error;
    }
    if (data.error) throw new Error(String(data.error));
    return data;
  } finally { clearTimeout(timeout); }
}
const json = (method, payload) => ({ method, body: JSON.stringify(payload) });
async function saveProfile(payload, revision) {
  if (revision == null || revision === '') throw new Error('Carica il profilo aggiornato prima di salvarlo. Le modifiche inserite restano nel modulo.');
  const result = await api('/planning/profile', { ...json('PUT', payload), headers: { 'If-Match': String(revision) } });
  state.settings = result.profile; state.settingsRevision = result.revision; return result;
}
function showActionError(error) {
  const message = error.name === 'AbortError' ? 'Il server non risponde. Riprova tra poco.' : error instanceof TypeError ? 'Connessione al server locale non disponibile. Verifica che Iron Coach sia avviato e riprova.' : error.message;
  notice(message, true);
  if ([409, 412].includes(error.status) && error.path === '/planning/profile') {
    $('#notice').append(el('p', 'Le tue modifiche restano nel modulo. Puoi copiarle oppure ricaricare i valori salvati nell’altra finestra.'));
    $('#notice').append(button('Ricarica il profilo e sostituisci la bozza', () => action(async () => {
      const current = await api('/planning/profile'); state.settings = current.profile; state.settingsRevision = current.revision;
      for (const id of ['athlete-form', 'goal-form']) { delete $(`#${id}`).dataset.dirty; delete $(`#${id}`).dataset.thresholdDirty; }
      render(); notice('Profilo aggiornato caricato. Controlla i valori prima di modificarli.');
    })));
  }
}
async function action(work) {
  if (state.busy) return;
  state.busy = true; notice(''); $('#refresh').textContent = 'Caricamento…';
  const reading = work === load || work === loadWeek || work === refreshAndSync;
  const controls = [...document.querySelectorAll(reading ? 'button:not([data-page])' : 'button:not([data-page]), input, select, textarea')];
  const original = controls.map(n => n.disabled); controls.forEach(n => n.disabled = true);
  try { await work(); }
  catch (error) { showActionError(error); }
  finally {
    state.busy = false; controls.forEach((n, i) => n.disabled = original[i]); $('#refresh').textContent = '↻ Aggiorna dati';
    document.querySelectorAll('[data-busy-disabled]').forEach(node => { node.disabled = false; delete node.dataset.busyDisabled; });
    document.querySelectorAll('[data-sport], #goal-form button, #checkin button').forEach(n => n.disabled = !state.today);
    for (const id of ['today-save', 'today-shorter', 'today-skip']) $(`#${id}`).disabled = !state.today;
    $('#today-save').disabled = !state.today || state.today.training_paused;
    document.querySelectorAll('[data-sport]').forEach(node => node.disabled = !state.today || state.today.training_paused || (Array.isArray(state.today.allowed_sports) && !state.today.allowed_sports.includes(node.dataset.sport)));
    $('#send-garmin').disabled = !state.selected?.id;
    $('#undo-week').disabled = !state.week?.can_undo;
    updateIntervalsControls();
    updateStravaControls();
    $('#home-next').disabled = (state.home?.date || athleteDate()) >= athleteDate();
    $('#home-prev').disabled = (state.home?.date || athleteDate()) <= '1970-01-01';
  }
}
async function loadWeek() { state.week = await api(`/planning/week?start=${state.weekStart}`); renderWeek(); document.dispatchEvent(new CustomEvent('iron:weekchange')); }
async function load() {
  const tasks = [
    ['/coach/today', value => state.today = value], ['/athlete/profile', value => state.profile = value],
    ['/coach/workouts?limit=200', value => state.workouts = value], ['/activities', value => state.activities = value],
    ['/planning/profile', value => { state.settings = value.profile; state.settingsRevision = value.revision; }],
    [`/planning/week?start=${state.weekStart}`, value => state.week = value],
    ['/integrations/garmin/status', value => state.garmin = value],
    ['/integrations/intervals/status', renderIntervals],
    [homeDay ? `/experience/summary?end=${homeDay}` : '/experience/summary', renderReview], ['/experience/checkin', value => fillCheckin(value.checkin)],
    ['/experience/race-preparation', fillRacePreparation],
    [homeDay ? `/dashboard/home?day=${homeDay}` : '/dashboard/home', value => state.home = value], [`/health/metrics${state.healthRange}`, value => { state.health = value; renderHealth(value); }],
    ['/coach/overview', value => { state.coach = value; renderCoach(value); }], ['/goals', renderGoals],
    ['/integrations/strava/status', renderStrava], ['/integrations/strava/activities', renderStravaActivities],
    ['/activities/duplicates/pending', renderDuplicates]
  ];
  const results = await Promise.allSettled(tasks.map(async ([path, accept]) => accept(await api(path))));
  results.forEach((result, index) => {
    const path = tasks[index][0].split('?')[0];
    if (result.status === 'rejected') state.failedReads.add(path); else state.failedReads.delete(path);
  });
  render();
  const labels = { '/coach/today': 'Consiglio di oggi', '/athlete/profile': 'Profilo', '/coach/workouts': 'Allenamenti', '/activities': 'Attività', '/planning/profile': 'Impostazioni del profilo', '/planning/week': 'Calendario', '/integrations/garmin/status': 'Garmin', '/integrations/intervals/status': 'Intervals.icu', '/experience/summary': 'Riepilogo', '/experience/checkin': 'Check-in', '/experience/race-preparation': 'Checklist gara', '/dashboard/home': 'Home', '/health/metrics': 'Salute', '/coach/overview': 'Coach', '/goals': 'Obiettivi', '/integrations/strava/status': 'Collegamento Strava', '/integrations/strava/activities': 'Vista Strava', '/activities/duplicates/pending': 'Verifica duplicati' };
  const failures = results.map((r, i) => r.status === 'rejected' ? labels[tasks[i][0].split('?')[0]] || 'Dati' : null).filter(Boolean);
  let status = $('#data-status');
  if (!status) { status = el('div', null, 'card connection-note'); status.id = 'data-status'; status.setAttribute('role', 'status'); $('#notice').after(status); }
  status.hidden = !failures.length; status.replaceChildren();
  if (failures.length) {
    $('#home-updated').textContent = 'Aggiornamento parziale: verifica le connessioni';
    status.append(el('strong', 'Alcuni dati non sono stati aggiornati'), el('p', `${failures.join(', ')}. I dati già caricati restano visibili; potrebbero non essere aggiornati.`), button('Riprova caricamento', () => action(load)));
  }
  const loaded = path => results[tasks.findIndex(([p]) => p === path)]?.status === 'fulfilled';
  const previous = window.ironViewSnapshot || {};
  const hasGoals = Boolean(state.goals.length || (state.settings?.event_name && state.settings?.event_date));
  window.ironViewSnapshot = { hasProfile: loaded('/planning/profile') ? state.settingsRevision > 0 : previous.hasProfile,
    hasActivities: loaded('/activities') ? state.activities.length > 0 : previous.hasActivities,
    intervalsConfigured: loaded('/integrations/intervals/status') ? Boolean(intervalsConnection?.configured) : previous.intervalsConfigured,
    hasGoals: hasGoals || (loaded('/goals') && loaded('/planning/profile') ? false : previous.hasGoals) };
  document.dispatchEvent(new CustomEvent('iron:loaded', { detail: window.ironViewSnapshot }));
}
function setPlanTab(tab) {
  const target=tab==='goal'?'goal':'calendar';
  $('#plan-calendar-panel').hidden=target!=='calendar';$('#goal').hidden=target!=='goal';
  document.querySelectorAll('[data-plan-tab]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.planTab===target)));
}
function mergePlanSections() {
  const calendar=$('#calendar'),goal=$('#goal'),panel=el('div');panel.id='plan-calendar-panel';
  for(const child of [...calendar.children])if(!child.classList.contains('page-intro'))panel.append(child);
  const tabs=el('div',null,'plan-tabs');tabs.setAttribute('role','group');tabs.setAttribute('aria-label','Obiettivo e piano');
  for(const [id,label] of [['calendar','Il piano'],['goal','La gara e gli obiettivi']]){const tab=button(label,()=>setPlanTab(id),'outline');tab.dataset.planTab=id;tabs.append(tab);}
  goal.classList.remove('page');calendar.append(tabs,panel,goal);setPlanTab('calendar');
}
function page(id, userInitiated = false) {
  if(id==='goal'){id='calendar';setPlanTab('goal');}
  if (!Object.hasOwn(titles, id)) return;
  const previousPage=document.querySelector('.page:not([hidden])')?.id;
  const changed=previousPage!==id;
  if(id==='coach' && previousPage && previousPage!=='coach')coachReturnPage=previousPage;
  $('#coach-back').hidden=id!=='coach';
  document.querySelectorAll('.page').forEach(n => n.hidden = n.id !== id);
  document.querySelectorAll('nav [data-page]').forEach(n => n.classList.toggle('active', n.dataset.page === id));
  $('#page-title').textContent = titles[id]; if (location.hash !== `#${id}`) location.hash = id;
  updateHeaderDate();
  document.dispatchEvent(new CustomEvent('iron:pagechange', { detail: { id, userInitiated: changed && userInitiated } }));
}
document.querySelectorAll('[data-page]').forEach(n => n.addEventListener('click', () => page(n.dataset.page, true)));
$('#coach-back').addEventListener('click',()=>page(coachReturnPage,true));
window.addEventListener('hashchange', () => page(location.hash.slice(1), true));
function metric(label, value, caption) { const node = el('article', null, 'metric'); node.append(el('p', label), el('strong', value), el('small', caption)); return node; }
function render() {
  const day = state.today;
  if (day) {
    const remaining = day.event.days_to_event;
    $('#daily-goal').textContent = `${day.event.name || 'Imposta la tua prossima gara'}${remaining == null ? '' : remaining >= 0 ? ` · ${remaining} giorni al via` : ' · Gara conclusa'}`;
    $('#metrics').replaceChildren(metric('Fitness · CTL', number(day.fitness.ctl, 1), 'Carico cronico stimato'), metric('Fatica · ATL', number(day.fitness.atl, 1), 'Carico recente stimato'), metric('Bilancio · TSB', number(day.fitness.tsb, 1), 'Differenza tra fitness e fatica'), metric('Recupero', day.readiness.readiness == null ? 'N/D' : `${number(day.readiness.readiness)}/100`, `Confidenza: ${number(day.readiness.confidence)}% · ${day.date}`));
    $('#coach-reason').textContent = day.reason;
    const scheduled = !day.training_paused && day.coach_context?.session_policy?.mode !== 'easy' && (day.scheduled_workouts || []).find(w => w.status === 'planned');
    const proposal = scheduled?.payload || day.workout;
    $('#coach-title').textContent = day.training_paused ? 'Oggi: recupero e rivalutazione' : `Oggi: ${sports[proposal.sport] || proposal.sport} · ${number(proposal.duration_min)} minuti`;
    const brief=$('#daily-brief'); brief.replaceChildren();
    for(const [title,detail] of [
      ['Obiettivo della seduta',proposal.purpose || proposal.goal_focus?.explanation || 'Continuità aerobica senza aggiungere intensità non prevista.'],
      ['Come eseguirla',proposal.expected_rpe ? `Sforzo previsto RPE ${proposal.expected_rpe}/10. Rispetta riscaldamento e defaticamento; apri le fasi per le intensità.` : 'Apri le fasi della seduta e segui i riferimenti disponibili; non inventare soglie mancanti.'],
      ['Decisione pratica',day.training_paused ? 'Non salvare un nuovo allenamento: rivaluta prima i segnali che hanno sospeso la proposta.' : day.coach_context?.actions?.[0] || (day.readiness.readiness==null ? 'Il recupero di oggi non è confermato: mantieni uno stimolo facile e riduci la seduta se le sensazioni peggiorano.' : 'Mantieni il lavoro previsto; se hai meno tempo, controlla l’impatto prima di accorciarlo.')]
    ]) { const item=el('div',null,'daily-brief-item');item.append(el('h3',title),el('p',detail));brief.append(item); }
    const preview = button('', () => showWorkout(proposal, scheduled || null), 'workout-preview');
    preview.append(el('strong', day.training_paused ? 'Recupero e rivalutazione' : `${sports[proposal.sport] || proposal.sport} · ${number(proposal.duration_min)} minuti`), el('span', scheduled ? 'Apri la seduta del tuo calendario →' : 'Scopri le fasi della proposta →'));
    $('#today-save').textContent = !scheduled && day.coach_context?.session_policy?.mode === 'easy' && (day.scheduled_workouts||[]).some(w=>w.status==='planned') ? 'Rivedi la seduta programmata' : scheduled ? 'Apri seduta programmata' : 'Salva la seduta';
    if (!scheduled && day.coach_context?.session_policy?.mode === 'easy' && (day.scheduled_workouts||[]).some(w=>w.status==='planned')) $('#coach-reason').textContent += ' La seduta in calendario va rivista: la proposta ridotta la sostituisce, non si aggiunge.';
    if (scheduled) $('#coach-reason').textContent += ' È già presente una seduta nel calendario: confrontala con il check-in e adattala se necessario.';
    $('#today-workout').replaceChildren(preview);
  }
  $('#workouts').replaceChildren();
  const active = state.workouts.filter(w => !['cancelled', 'superseded'].includes(w.status));
  if (!active.length) $('#workouts').append(el('p', 'Nessuna seduta salvata. Crea il piano settimanale o scegli una disciplina.', 'muted'));
  for (const workout of active) $('#workouts').append(workoutCard(workout));
  renderActivities(); renderWeek(); fillAthlete(); renderProviders(); if (state.home) renderHome(state.home); if (state.coach) renderCoach(state.coach);
  if ((state.profile || state.settings) && !$('#goal-form').dataset.dirty) {
    const form = $('#goal-form');
    form.dataset.revision = state.settingsRevision ?? '';
    form.elements.event.value = state.settings?.event_name || state.profile.goals?.event || '';
    form.elements.event_date.value = state.settings?.event_date || state.profile.goals?.event_date || '';
    form.elements.target_time.value = state.settings?.target_time || state.profile.goals?.target_time || '';
  }
  markRetainedMetrics();
}
function markRetainedMetrics() {
  const colors = window.IronCoachMetricStatus; if (!colors) return;
  const selectors = {
    '/dashboard/home': '#metrics > [data-tone], #home-week > [data-tone], .coach-flash[data-tone]',
    '/coach/today': '#metrics > [data-metric-kind="readiness"][data-depends-coach]',
    '/health/metrics': '#health-metrics > [data-tone]',
    '/coach/overview': '#coach-overview[data-tone]'
  };
  for (const [path, selector] of Object.entries(selectors)) if (state.failedReads.has(path)) {
    for (const card of document.querySelectorAll(selector)) colors.apply(card, {tone:'unknown',label:'Aggiornamento non riuscito',reason:'È mostrato l’ultimo valore caricato. Riprova l’aggiornamento prima di interpretarlo.'});
  }
}
function workoutCard(workout) {
  const row = button('', () => showWorkout(workout.payload, workout), 'list-row'); const detail = el('div');
  detail.append(el('strong', `${sports[workout.sport] || workout.sport} · ${number(workout.duration_min)} min`), el('small', `${workout.date} · ${workout.payload.purpose || workout.name}`));
    row.append(detail, el('span', statuses[workout.status] || workout.status, 'badge')); return row;
}
function renderActivities() {
  const root = $('#activity-list'); root.replaceChildren();
  if (!state.activities.length) $('#activity-list').append(el('p', 'Non ci sono attività importate. Puoi importare un archivio JSON da Connessioni.', 'muted'));
  const filter = $('#activity-sport').value; const search = $('#activity-search').value.toLocaleLowerCase('it-IT');
  const aliases = { bike: 'cycling', ride: 'cycling', virtualride: 'cycling', gravelride: 'cycling', mountainbikeride: 'cycling', run: 'running', trailrun: 'running', trailrunning: 'running', virtualrun: 'running', swim: 'swimming', openwaterswim: 'swimming', weighttraining: 'strength', weight_training: 'strength', biking: 'cycling', indoorcycling: 'cycling', indoor_cycling: 'cycling', mountain_bike: 'cycling', virtual_ride: 'cycling', trail_run: 'running', open_water_swim: 'swimming' };
  const selected = state.activities.filter(a => (!filter || (filter==='other' ? !['cycling','running','swimming','strength'].includes(aliases[(a.sport||'').toLowerCase()] || (a.sport||'').toLowerCase()) : (aliases[(a.sport||'').toLowerCase()] || (a.sport||'').toLowerCase()) === filter)) && (!search || (a.name || '').toLocaleLowerCase('it-IT').includes(search)));
  if (state.activities.length && !selected.length) $('#activity-list').append(el('p', 'Nessuna attività corrisponde ai filtri.', 'empty-state'));
  if (selected.length) root.append(el('p', `${Math.min(selected.length, state.activityLimit)} di ${selected.length} attività${filter || search ? ' corrispondenti ai filtri' : ' nello storico'}`, 'caption'));
  for (const activity of selected.slice(0, state.activityLimit)) {
    const row = button('', () => action(() => showActivity(activity)), 'list-row'); const detail = el('div');
    detail.append(el('strong', activity.name || sports[activity.sport] || activity.sport), el('small', `${activityDate(activity.start_time)} · ${sports[aliases[activity.sport] || activity.sport] || activity.sport} · Fonte: ${sourceLabel(activity.source)}`));
    const rating=activity.coach_rating;if(rating){const vote=el('strong',rating.score==null?'N/D':`${number(rating.score)}/100`,'activity-rating');vote.dataset.tone=rating.score==null?'unknown':window.IronCoachMetricStatus.five(rating.score,'','').tone;detail.append(vote);}
    row.append(detail, el('span', `${number(scaled(activity.duration_seconds, 60))} min · ${number(scaled(activity.distance_meters, 1000), 1)} km`, 'badge')); root.append(row);
  }
  if (selected.length > state.activityLimit) root.append(button('Mostra altre 50 attività', () => {
    const position = state.activityLimit; state.activityLimit += 50; renderActivities();
    root.querySelectorAll('.list-row')[position]?.focus();
  }));
}
function renderReview(summary) {
  const root=$('#weekly-review'),all=Object.values(summary.sports||{}),sessions=all.reduce((n,s)=>n+s.sessions,0),minutes=all.reduce((n,s)=>n+s.minutes,0);
  root.replaceChildren(el('strong',`${sessions} attività registrate · ${number(minutes/60,1)} ore`));
  const text=all.filter(s=>s.sessions>0).map(s=>`${s.label} ${s.sessions}`).join(' · ');
  root.append(el('span',text||'Nessuna attività registrata','brief-sports'),el('small',summary.start&&summary.end?`${dayLabel(summary.start)} – ${dayLabel(summary.end)}`:'Ultimi sette giorni'));
}
function renderWeek() {
  $('#week-label').textContent = `${dayLabel(state.weekStart)} — ${dayLabel(offsetDate(state.weekStart, 6))}`;
  const week = state.week; $('#week-days').replaceChildren();
  if (!week) { $('#week-days').append(el('p', 'Il piano non è stato caricato.', 'muted')); return; }
  const total = (week.days || []).reduce((sum, day) => sum + (day.planned_minutes || 0), 0);
  $('#week-summary').replaceChildren(el('strong', `${number(total / 60, 1)} ore pianificate`), el('span', `Versione ${week.version || 0} · Fase: ${week.phase || 'base'}`));

  $('#generate-week').textContent = week.version ? 'Ricalcola settimana' : 'Genera settimana'; $('#undo-week').disabled = !week.can_undo;
  if (week.needs_review) $('#week-summary').append(el('p', 'Sono cambiati profilo, recupero o feedback: ricalcola la settimana per rivalutare le sedute future.', 'warning'));
  if (week.allocation?.focus) $('#week-summary').append(el('p', week.allocation.focus, 'muted'));
  if (week.brick_status?.reason) $('#week-summary').append(el('p', week.brick_status.reason, 'muted'));
  for (const day of week.days || []) {
    const cell = el('article', null, 'calendar-day'); cell.classList.toggle('today', day.date === state.today?.date); cell.append(el('h3', dayLabel(day.date)), el('small', `${day.available_minutes} min disponibili`, 'muted'));
    if (day.recorded_minutes > 0) cell.append(el('small', `${number(day.recorded_minutes)} min già registrati`, 'badge'));
    if (day.transition_minutes > 0) cell.append(el('small', `${number(day.transition_minutes)} min per la transizione`, 'muted'));
    const sessions = (day.sessions || []).filter(w => !['cancelled', 'superseded'].includes(w.status));
    if (!sessions.length) cell.append(el('p', day.recorded_minutes > 0 ? 'Nessuna seduta aggiuntiva' : 'Recupero / libero', 'rest-day'));
    for (const workout of sessions) { const card = workoutCard(workout); card.classList.add('calendar-session', `sport-${workout.sport}`); cell.append(card); if(workout.payload.time_window)cell.append(el('small',workout.payload.time_window.label,workout.payload.time_window.conflict?'warning':'time-window')); if (workout.payload.brick) cell.append(el('small', `Combinato · fase ${workout.payload.brick.sequence}`, 'badge')); if (workout.status === 'planned') cell.append(button('Sposta / accorcia / salta', () => editSession(workout), 'text-button')); }
    $('#week-days').append(cell);
  }
  $('#plan-outlook').replaceChildren(el('h3', 'Orizzonte di preparazione'));
  const blocks = week.block_preview || [];
  for (const block of Array.isArray(blocks) ? blocks : []) $('#plan-outlook').append(el('p', typeof block === 'string' ? block : `${block.start || block.week || ''} · ${block.focus || block.phase || ''} · ${number(block.indicative_minutes)} min indicativi. ${block.note || ''}`));
}
function editSession(workout, preferred = 'move') {
  $('#session-change-preview')?.remove();
  const form = $('#session-edit-form'); form.dataset.id = workout.id;
  form.elements.date.value = workout.date; form.elements.duration_min.value = workout.duration_min;
  const shorten = form.elements.action.querySelector('[value="shorten"]'); shorten.disabled = workout.duration_min <= 15;
  form.elements.duration_min.min = '15'; form.elements.duration_min.max = String(Math.max(15, Math.ceil(workout.duration_min) - 1));
  form.elements.action.value = preferred === 'shorten' && shorten.disabled ? 'skip' : preferred;
  if(form.elements.action.value === 'shorten') form.elements.duration_min.value = Math.max(15,Math.min(workout.duration_min-1,state.today?.coach_context?.session_policy?.max_duration_min || Math.floor(workout.duration_min*.75)));
  form.elements.reason.value = ''; updateSessionEditFields(); $('#session-edit-dialog').showModal();
}
function updateSessionEditFields() {
  const fields = $('#session-edit-form').elements;
  fields.date.disabled = fields.action.value !== 'move'; fields.date.required = !fields.date.disabled;
  fields.duration_min.disabled = fields.action.value !== 'shorten'; fields.duration_min.required = !fields.duration_min.disabled;
}
$('#session-edit-form').elements.action.addEventListener('change', updateSessionEditFields);
$('#session-edit-form').addEventListener('submit', event => {
  event.preventDefault(); const form = event.currentTarget;
  const payload = { action: form.elements.action.value, reason: form.elements.reason.value.trim() };
  if (payload.action === 'move') payload.date = form.elements.date.value;
  if (payload.action === 'shorten') payload.duration_min = Number(form.elements.duration_min.value);
  action(async () => {
    const identifier = form.dataset.id;
    const preview = await api(`/planning/workouts/${identifier}/preview`, json('POST', payload));
    let root = $('#session-change-preview');
    if (!root) { root = el('div', null, 'change-preview'); root.id = 'session-change-preview'; form.append(root); }
    root.replaceChildren(el('h3', 'Prima di confermare'), el('p', `${number(preview.impact.before_minutes)} → ${number(preview.impact.after_minutes)} minuti nella settimana (${preview.impact.delta_minutes > 0 ? '+' : ''}${number(preview.impact.delta_minutes)} min).`));
    for (const change of preview.affected_workouts || []) root.append(el('p', `${change.before.name}: ${change.before.date}, ${number(change.before.duration_min)} min → ${change.after.date}, ${number(change.after.duration_min)} min · ${statuses[change.after.status] || change.after.status}`, 'caption'));
    for (const reason of preview.rationale || []) root.append(el('p', reason, 'caption'));
    const snapshot = JSON.stringify(payload);
    root.append(button('Conferma questa modifica', () => action(async () => {
      const current = { action: form.elements.action.value, reason: form.elements.reason.value.trim() };
      if (current.action === 'move') current.date = form.elements.date.value;
      if (current.action === 'shorten') current.duration_min = Number(form.elements.duration_min.value);
      if (JSON.stringify(current) !== snapshot || form.dataset.id !== identifier) throw new Error('Hai cambiato i valori. Mostra di nuovo l’impatto prima di confermare.');
      await api(`/planning/workouts/${identifier}`, {...json('PATCH', payload),headers:preview.preview_signature ? {'X-Plan-Preview':preview.preview_signature} : {}}); delete form.dataset.dirty; $('#session-edit-dialog').close(); root.remove(); await load(); notice('Piano aggiornato. Puoi annullare dal calendario.');
    }), 'primary'));
    root.tabIndex = -1; root.focus();
  });
});
$('#session-edit-form').querySelector('button.primary').textContent = 'Mostra impatto';
$('#session-edit-form').addEventListener('input', () => $('#session-change-preview')?.remove());
$('#week-prev').addEventListener('click', () => action(async () => { state.weekStart = offsetDate(state.weekStart, -7); await loadWeek(); }));
$('#week-next').addEventListener('click', () => action(async () => { state.weekStart = offsetDate(state.weekStart, 7); await loadWeek(); }));
$('#generate-week').addEventListener('click', () => action(async () => { await api('/planning/week', json('POST', { start: state.weekStart, regenerate: Boolean(state.week?.version) })); await load(); notice('Settimana aggiornata. Le sedute completate rimangono nello storico.'); }));
$('#undo-week').addEventListener('click', () => action(async () => { await api('/planning/undo', json('POST', { start: state.weekStart })); await load(); notice('Ultima modifica annullata.'); }));

function fillAthlete() {
  const profile = state.settings; const form = $('#athlete-form'); if (!profile || form.dataset.dirty) return;
  form.dataset.revision = state.settingsRevision ?? '';
  for (const key of ['experience', 'race_distance', 'goal_priority', 'weekly_minutes', 'limitations', 'height_cm', 'birth_date', 'sex']) if (form.elements[key]) form.elements[key].value = profile[key] ?? '';
  form.elements.pool_access.checked = Boolean(profile.pool_access);
  form.elements.recent_weekly_minutes.value = profile.recent_weekly_minutes ?? '';
  for (const sport of ['cycling', 'running', 'swimming', 'strength']) form.elements[`exclude_${sport}`].checked = (profile.excluded_sports || []).includes(sport);
  for (let i = 0; i < 7; i++) form.elements[`day_${i}`].value = profile.availability?.[i] || 0;
  for (const [input, key] of [['ftp_watts', 'cycling_ftp_watts'], ['running_threshold', 'running_threshold_pace_sec_per_km'], ['css', 'swimming_css_sec_per_100m']]) form.elements[input].value = profile.thresholds?.[key]?.value ?? '';
  for (const [input, key] of [['ftp_watts', 'cycling_ftp_watts'], ['running_threshold', 'running_threshold_pace_sec_per_km'], ['css', 'swimming_css_sec_per_100m']]) {
    form.elements[`${input}_source`].value = profile.thresholds?.[key]?.source === 'measured' ? 'measured' : 'estimated';
    form.elements[`${input}_date`].value = profile.thresholds?.[key]?.measured_on || '';
  }
}
$('#athlete-form').addEventListener('submit', event => {
  event.preventDefault(); const form = event.currentTarget;
  const payload = { ...state.settings, experience: form.elements.experience.value, race_distance: form.elements.race_distance.value, goal_priority: form.elements.goal_priority.value, weekly_minutes: Number(form.elements.weekly_minutes.value), availability: Array.from({ length: 7 }, (_, i) => Number(form.elements[`day_${i}`].value)), pool_access: form.elements.pool_access.checked, limitations: form.elements.limitations.value.trim(), thresholds: {} };
  payload.height_cm = form.elements.height_cm.value === '' ? null : Number(form.elements.height_cm.value);
  payload.birth_date = form.elements.birth_date.value || null; payload.sex = form.elements.sex.value || null;
  payload.recent_weekly_minutes = form.elements.recent_weekly_minutes.value === '' ? null : Number(form.elements.recent_weekly_minutes.value);
  payload.excluded_sports = ['cycling', 'running', 'swimming', 'strength'].filter(sport => form.elements[`exclude_${sport}`].checked);
  for (const [input, key] of [['ftp_watts', 'cycling_ftp_watts'], ['running_threshold', 'running_threshold_pace_sec_per_km'], ['css', 'swimming_css_sec_per_100m']]) {
    const value = form.elements[input].value;
    payload.thresholds[key] = { value: value === '' ? null : Number(value), source: value === '' ? 'unknown' : form.elements[`${input}_source`].value, measured_on: value === '' ? null : form.elements[`${input}_date`].value || null };
  }
  action(async () => { await saveProfile(payload, form.dataset.revision); delete form.dataset.dirty; delete form.dataset.thresholdDirty; await load(); notice('Profilo salvato. Dal calendario puoi ricalcolare il piano con le nuove disponibilità.'); });
});
$('#goal-form').addEventListener('submit', event => {
  event.preventDefault(); const form = event.currentTarget; const eventName = form.elements.event.value.trim();
  if (!eventName) { notice('Inserisci il nome della gara.', true); return; }
  action(async () => { const current = await api('/planning/profile'); await saveProfile({ ...current.profile, event_name: eventName, event_date: form.elements.event_date.value, target_time: form.elements.target_time.value.trim() || null }, form.dataset.revision); delete form.dataset.dirty; await load(); notice('Obiettivo salvato. Ricalcola il calendario per applicare la nuova fase.'); });
});
function fillCheckin(checkin) {
  const form = $('#checkin'); if (form.dataset.dirty) return;
  form.reset(); if (!checkin) return;
  for (const [key, value] of Object.entries(checkin)) if (form.elements[key]) { if (form.elements[key].type === 'checkbox') form.elements[key].checked = Boolean(value); else form.elements[key].value = value ?? ''; }
}
$('#checkin').addEventListener('submit', event => {
  event.preventDefault(); const form = event.currentTarget; const payload = { pain: form.elements.pain.checked, notes: form.elements.notes.value.trim() };
  for (const key of ['sleep_hours', 'stress_score', 'fatigue', 'soreness', 'motivation', 'sleep_quality']) payload[key] = form.elements[key].value === '' ? null : Number(form.elements[key].value);
  action(async () => { await api('/experience/checkin', json('PUT', payload)); delete form.dataset.dirty; await load(); notice('Check-in salvato. Il consiglio di oggi è aggiornato; puoi ricalcolare il calendario.'); });
});
function fillRacePreparation(values) { const form = $('#race-prep-form'); if (form.dataset.dirty) return; for (const [key, value] of Object.entries(values)) if (form.elements[key]) { if (form.elements[key].type === 'checkbox') form.elements[key].checked = Boolean(value); else form.elements[key].value = value; } }
$('#race-prep-form').addEventListener('submit', event => {
  event.preventDefault(); const form = event.currentTarget; const payload = { notes: form.elements.notes.value.trim() };
  for (const key of ['nutrition', 'hydration', 'equipment', 'pacing']) payload[key] = form.elements[key].checked;
  action(async () => { await api('/experience/race-preparation', json('PUT', payload)); delete form.dataset.dirty; notice('Preparazione gara salvata.'); });
});
for (const form of document.querySelectorAll('form')) form.addEventListener('input', event => { form.dataset.dirty = 'true'; if (['ftp_watts', 'running_threshold', 'css', 'threshold_source', 'threshold_date'].includes(event.target.name)) form.dataset.thresholdDirty = 'true'; });
window.addEventListener('beforeunload', event => {
  const dirty = [...document.querySelectorAll('form[data-dirty]')].some(form => !['health-range', 'intervals-form'].includes(form.id) && !form.hidden && (form.id !== 'session-edit-form' || $('#session-edit-dialog').open));
  if (dirty) { event.preventDefault(); event.returnValue = ''; }
});
$('#close-session-edit').addEventListener('click', () => $('#session-edit-dialog').close());
$('#cancel-session-edit').addEventListener('click', () => $('#session-edit-dialog').close());

let timer = { steps: [], index: 0, remaining: 0, deadline: 0, running: false };
function stopTimer() { timer.running = false; timer.deadline = 0; }
function resetTimer(plan) { stopTimer(); timer.steps = plan.steps || []; timer.index = 0; timer.remaining = (timer.steps[0]?.duration_min || 0) * 60; renderTimer(); }
function renderTimer() {
  if (timer.running) timer.remaining = Math.max(0, Math.ceil((timer.deadline - Date.now()) / 1000));
  if (timer.running && timer.remaining === 0) stopTimer();
  $('#timer-display').textContent = `${String(Math.floor(timer.remaining / 60)).padStart(2, '0')}:${String(Math.floor(timer.remaining % 60)).padStart(2, '0')}`;
  $('#timer-step-name').textContent = timer.steps[timer.index] ? `${timer.index + 1}/${timer.steps.length} · ${timer.steps[timer.index].name || stepNames[timer.steps[timer.index].type] || timer.steps[timer.index].type}` : 'Seduta terminata: aggiungi il tuo feedback.';
}
setInterval(() => { if ($('#workout-dialog').open) renderTimer(); }, 500);
$('#timer-start').addEventListener('click', () => { if (timer.steps[timer.index] && !timer.running && timer.remaining > 0) { timer.deadline = Date.now() + timer.remaining * 1000; timer.running = true; renderTimer(); } });
$('#timer-pause').addEventListener('click', () => { renderTimer(); stopTimer(); });
$('#timer-next').addEventListener('click', () => { stopTimer(); timer.index++; timer.remaining = (timer.steps[timer.index]?.duration_min || 0) * 60; renderTimer(); });
$('#timer-reset').addEventListener('click', () => { if (state.selected) resetTimer(state.selected.plan); });
$('#workout-dialog').addEventListener('close', stopTimer);
async function showWorkout(plan, workout = null) {
  if (state.busy && state.selected?.id !== workout?.id && $('#workout-dialog').open) return;
  if ($('#feedback-form').dataset.dirty) {
    if (state.selected?.id === workout?.id) { if (!$('#workout-dialog').open) $('#workout-dialog').showModal(); return; }
    if (!$('#workout-dialog').open) $('#workout-dialog').showModal();
    notice('Hai un feedback non salvato. Salvalo oppure scartalo prima di aprire un’altra seduta.', true);
    const message = $('#workout-dialog .dialog-notice');
    message?.append(button('Scarta il feedback e apri la nuova seduta', () => {
      delete $('#feedback-form').dataset.dirty; showWorkout(plan, workout);
    }));
    return;
  }
  state.selected = { plan, id: workout?.id, workout }; const selected = state.selected; const root = $('#workout-detail'); root.replaceChildren();
  root.append(el('h2', `${sports[plan.sport] || plan.sport} · ${number(plan.duration_min)} minuti`), el('p', plan.purpose || plan.reason), el('p', plan.reason, 'muted'));
  const steps = el('ol', null, 'steps');
  for (const step of plan.steps || []) {
    const row = el('li'); const text = el('div');
    const target = step.target_power_w != null ? ` · ${number(step.target_power_w)} W` : step.target_pace_sec_per_km ? ` · ${number(step.target_pace_sec_per_km)} sec/km` : step.target_rpe != null ? ` · RPE ${step.target_rpe}/10` : '';
    text.append(el('strong', step.name || stepNames[step.type] || step.type), el('small', `${number(step.duration_min)} minuti${target}`));
    if (step.instruction) text.append(el('p', step.instruction, 'muted')); row.append(text); steps.append(row);
  }
  root.append(steps); resetTimer(plan);
  let extraActions = $('#session-actions');
  if (!extraActions) { extraActions = el('div', null, 'action-group'); extraActions.id = 'session-actions'; $('#workout-actions').prepend(extraActions); }
  extraActions.replaceChildren();
  if(workout?.id && plan.exercises?.length) extraActions.append(button('Segui scheda palestra →',()=>action(async()=>{$('#workout-dialog').close();await window.IronManualTraining.openGym(workout.id);}), 'primary'));
  if (!workout && !state.today?.training_paused && plan.steps?.length) extraActions.append(button('Salva questa proposta per oggi', () => action(() => saveToday(plan.sport)), 'primary'));
  else if (workout?.status === 'planned') extraActions.append(button('Ho meno tempo / sposta / salta', () => { $('#workout-dialog').close(); editSession(workout, 'shorten'); }));
  $('#feedback-form').hidden = !workout || ['skipped', 'cancelled', 'superseded'].includes(workout.status);
  $('#feedback-form').reset(); delete $('#feedback-form').dataset.dirty; $('#feedback-result')?.remove(); document.querySelectorAll('.match-suggestion, .dialog-notice').forEach(n => n.remove());
  $('#garmin-workout-status').textContent = workout ? 'Verifica dell’invio…' : 'Salva la seduta per preparare l’invio a Garmin.';
  $('#send-garmin').disabled = !workout; $('#export-workout').hidden = !workout;
  if (workout) $('#export-workout').href = `/workouts/${workout.id}/export.json`;
  $('#export-fit').hidden = !workout || !['cycling', 'running'].includes(plan.sport);
  if (workout) $('#export-fit').href = `/workouts/${workout.id}/export.fit`;
  const select = $('#feedback-activity'); select.replaceChildren(el('option', 'Solo feedback manuale')); select.firstChild.value = '';
  if (workout) for (const activity of state.activities.filter(a => (sports[a.sport] || a.sport) === (sports[workout.sport] || workout.sport) && Math.abs(new Date(a.start_time) - new Date(workout.date)) <= 3 * 86400000)) {
    const option = el('option', `${activityDate(activity.start_time)} · ${activity.name || sports[activity.sport]} · ${number(activity.duration_seconds / 60)} min`); option.value = activity.id; select.append(option);
  }
  if (!$('#workout-dialog').open) $('#workout-dialog').showModal();
  if (workout) {
    try {
      const feedbackVersion = selected.feedbackVersion || 0;
      const [delivery, review] = await Promise.all([api(`/workouts/${workout.id}/garmin/status`), api(`/experience/workouts/${workout.id}/review`)]);
      if (state.selected !== selected) return;
      $('#garmin-workout-status').textContent = delivery.message || delivery.delivery?.message || 'Invio reale da attivare con accesso ufficiale Garmin.';
      if ((selected.feedbackVersion || 0) !== feedbackVersion) return;
      if (review.feedback) { if (!$('#feedback-form').dataset.dirty) for (const key of ['rpe', 'completion_percent', 'feeling', 'notes', 'activity_id']) if ($('#feedback-form').elements[key]) $('#feedback-form').elements[key].value = review.feedback[key] ?? ''; renderFeedback(review.feedback); }
      else if (review.suggested_match && !$('#feedback-form').dataset.dirty) { select.value = String(review.suggested_match.activity_id); $('#feedback-form').prepend(el('p', `Attività candidata · confidenza ${number(review.suggested_match.confidence)}%. Controlla l’abbinamento prima di salvare.`, 'match-suggestion')); }
    } catch (error) { if (state.selected === selected) $('#garmin-workout-status').textContent = error.message; }
  }
}
function renderFeedback(feedback) {
  $('#feedback-result')?.remove(); const node = el('div', null, 'feedback-result'); node.id = 'feedback-result';
  node.append(el('strong', 'Programmato / eseguito'), el('p', `${number(feedback.planned_duration_min)} / ${number(feedback.actual.duration_min)} minuti · Completamento ${number(feedback.evaluation.completion)}%`), el('p', feedback.source === 'activity' ? 'Durata misurata dall’attività associata.' : 'Durata ricavata dal completamento dichiarato.'), el('small', 'Confronto intensità per intervallo non disponibile senza dati delle singole fasi.')); $('#feedback-form').append(node);
}
$('#feedback-form').addEventListener('submit', event => {
  event.preventDefault(); const form = event.currentTarget; if (!state.selected?.id) return;
  const id = state.selected.id; const payload = { rpe: Number(form.elements.rpe.value), completion_percent: Number(form.elements.completion_percent.value), feeling: form.elements.feeling.value, notes: form.elements.notes.value.trim(), activity_id: form.elements.activity_id.value ? Number(form.elements.activity_id.value) : null };
  action(async () => { const result = await api(`/experience/workouts/${id}/feedback`, json('PUT', payload)); if (state.selected?.id === id) state.selected.feedbackVersion = (state.selected.feedbackVersion || 0) + 1; delete form.dataset.dirty; renderFeedback(result); await load(); notice('Feedback salvato. Il coach considera la risposta nelle prossime decisioni.'); });
});
$('#send-garmin').addEventListener('click', () => {
  if (!state.selected?.id) return; const id = state.selected.id;
  action(async () => { const result = await api(`/workouts/${id}/garmin/send`, json('POST', {})); $('#garmin-workout-status').textContent = result.message; notice(result.message, result.status !== 'published'); });
});
async function saveToday(sport = state.today?.workout.sport) {
  const existing=state.workouts.find(w=>w.date===state.today?.date && w.status==='planned');
  if(existing && state.today?.coach_context?.session_policy?.mode === 'easy') {$('#workout-dialog').close();editSession(existing,'shorten');return;}
  if (!sport) return; const workout = await api('/coach/workouts', json('POST', { sport })); await load();
  await showWorkout(workout, { ...workout, payload: workout, date: state.today.date, status: 'planned' }); notice('Seduta salvata.');
}
$('#today-save').addEventListener('click', () => {
  if(state.today?.coach_context?.session_policy?.mode === 'easy') {
    const existing=state.workouts.find(w=>w.date===state.today.date && w.status==='planned');
    if(existing) {editSession(existing,'shorten');return;}
  }
  const scheduled = state.today?.coach_context?.session_policy?.mode !== 'easy' && (state.today?.scheduled_workouts || []).find(w => w.status === 'planned');
  if (scheduled) showWorkout(scheduled.payload, scheduled); else action(() => saveToday());
});
for (const [id, preferred] of [['today-shorter', 'shorten'], ['today-skip', 'skip']]) $(`#${id}`).addEventListener('click', () => {
  const workout = state.workouts.find(w => w.date === state.today?.date && w.status === 'planned');
  if (workout) editSession(workout, preferred); else { page('calendar'); notice('Genera la settimana o salva la proposta per modificare una seduta.'); }
});
document.querySelectorAll('[data-sport]').forEach(n => n.addEventListener('click', () => action(() => saveToday(n.dataset.sport))));
async function refreshAndSync() {
  try {
  $('#refresh').textContent = 'Sincronizzazione…';
  const started = await api('/sync/refresh', json('POST', {}));
  const results = await Promise.all((started.runs || []).map(async job => {
    const deadline = Date.now() + 120000;
    while (Date.now() < deadline) {
      const run = await api(`/sync/runs/${job.run_id}`);
      if (!['running','queued','pending'].includes(run.status)) return run;
      await new Promise(resolve => setTimeout(resolve,1000));
    }
    return {status:'running',result:{message:'La sincronizzazione prosegue sul server. Aggiorna nuovamente tra poco.'}};
  }));
  await load();
  const errors = [...(started.messages || []), ...results.filter(run=>!['success','completed'].includes(run.status)).map(run=>run.result?.message || run.result?.errors?.[0]?.error || 'Sincronizzazione parziale: verifica Connessioni.')];
  notice(errors.length ? errors.join(' ') : results.length ? 'Sincronizzazione completata. Dati e valutazioni aggiornati.' : 'Dati locali aggiornati. Collega una sorgente in Connessioni per sincronizzare.', Boolean(errors.length));
  } catch (error) { await load(); throw error; }
}
$('#refresh').addEventListener('click', () => action(refreshAndSync));
$('#close-dialog').addEventListener('click', () => $('#workout-dialog').close());

function renderProviders() {
  const root = $('#providers'); root.replaceChildren();
  for (const [name, detail, status] of [['Garmin Connect', 'Allenamenti strutturati e dati: connettore ufficiale da attivare.', 'Accesso ufficiale richiesto'], ['Apple Salute', 'Integrazione nativa nel percorso iOS futuro.', 'Da integrare']]) {
    const row = el('div', null, 'provider'); const text = el('div'); text.append(el('strong', name), el('small', detail)); row.append(text, el('span', status, 'badge')); root.append(row);
  }
  root.append(el('p', state.garmin?.message || 'L’invio a Garmin Connect non è ancora attivo. Nessun dato è stato inviato al dispositivo.', 'muted'));
  root.append(el('p', 'Dispositivi di riferimento: Forerunner 955 ed Edge 530. Il file FIT bici/corsa è esportabile; il caricamento e l’esecuzione su questi dispositivi restano da collaudare. Nessuna copia USB automatica.', 'muted'));
}
function addImportTools() {
  const card = el('article', null, 'card connection-note'); card.append(el('h3', 'Importa i tuoi dati'), el('p', 'Archivio JSON: fonte e attività con data comprensiva di fuso orario. I duplicati vengono riconosciuti per identità e confronto dei dati.', 'muted'));
  const input = el('input'); input.type = 'file'; input.accept = '.json,application/json'; input.id = 'activity-import'; input.setAttribute('aria-label', 'Archivio attività JSON');
  input.addEventListener('change', () => {
    const file = input.files[0]; if (!file) return; if (file.size > 5 * 1024 * 1024) { notice('Il file supera 5 MB.', true); return; }
    action(async () => { const payload = JSON.parse(await file.text()); const result = await api('/experience/import-activities', json('POST', payload)); await load(); notice(`${result.created} attività importate, ${result.matched} già presenti o abbinate. Fonte: ${result.source}.`); input.value = ''; });
  });
  const example = el('details'); example.append(el('summary', 'Formato JSON di esempio')); example.append(el('pre', JSON.stringify({ source: 'manual', activities: [{ source_id: 'attivita-1', sport: 'cycling', name: 'Bici aerobica', start_time: '2026-10-17T08:00:00+02:00', duration_seconds: 3600, distance_meters: 25000 }] }, null, 2)));
  card.append(input, example);
  const fitLabel = el('label', 'Importa attività Garmin · file FIT');
  const fitInput = el('input'); fitInput.type = 'file'; fitInput.accept = '.fit'; fitInput.id = 'fit-import';
  fitLabel.append(fitInput); card.append(fitLabel);
  fitInput.addEventListener('change', () => {
    const file = fitInput.files[0]; if (!file) return;
    if (file.size > 20 * 1024 * 1024) { notice('Il file FIT supera 20 MB.', true); return; }
    action(async () => { const result = await api('/experience/import-fit', { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file }); await load(); notice(`${result.created} attività FIT importate, ${result.matched} già presenti. Sessioni non supportate: ${result.sessions_ignored}.`); fitInput.value = ''; });
  });
  $('#connections').append(card);
}
let intervalsConnection = null;
function updateIntervalsControls() {
  for (const id of ['intervals-sync', 'intervals-verify', 'intervals-disconnect']) if ($(`#${id}`)) $(`#${id}`).disabled = state.busy || !intervalsConnection?.configured;
}
function renderIntervals(value) {
  intervalsConnection = value;
  const root = $('#intervals-status'); root.replaceChildren(el('strong', value.connected ? `Collegato · ${value.athlete_name || value.athlete_id}` : 'Da collegare'), el('p', value.message));
  if (value.last_success_at) root.append(el('small', `Ultima sincronizzazione completa: ${new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', dateStyle: 'short', timeStyle: 'short' }).format(new Date(value.last_success_at))}`));
  if (value.last_result) {
    const result = value.last_result;
    root.append(el('p', result.status === 'error' ? result.message : `${result.new_activities} nuove attività · ${result.already_synced_or_matched} già presenti · ${result.errors?.length || 0} non importate`, 'muted'));
  }
  updateIntervalsControls();
}
async function syncIntervals() {
  const days = Number($('#intervals-days').value);
  const accepted = await api(`/sync/intervals?days=${days}&resources=both&background=true`, json('POST', {}));
  if (accepted.run_id != null) { watchSync(accepted.run_id, 'intervals'); return; }
  const result = accepted;
  await load();
  const message = `${result.new_activities} nuove attività, ${result.already_synced_or_matched} già presenti, ${result.errors.length} non importate.`;
  notice(message, result.errors.length > 0);
  $('#intervals-result').replaceChildren(el('p', message));
  for (const error of result.errors.slice(0, 20)) $('#intervals-result').append(el('p', `${error.id}: ${error.error}`, 'muted'));
}
function addIntervalsConnection() {
  const card = el('article', null, 'card connection-note'); card.id = 'intervals-connection';
  card.append(el('p', 'LE TUE ATTIVITÀ, SEMPRE NEL PIANO', 'eyebrow'), el('h2', 'Collega Intervals.icu'));
  const status = el('div', 'Caricamento stato…'); status.id = 'intervals-status'; status.setAttribute('role', 'status'); card.append(status);
  const guide = el('p', 'Genera una chiave personale in Intervals.icu → Settings → Developer Settings. La chiave viene inviata al server locale, verificata con Intervals.icu e salvata protetta per il tuo utente Windows.');
  const link = el('a', 'Apri le impostazioni Intervals.icu ↗'); link.href = 'https://intervals.icu/settings'; link.target = '_blank'; link.rel = 'noopener noreferrer'; guide.append(' ', link); card.append(guide);
  const form = el('form'); form.id = 'intervals-form';
  const keyLabel = el('label', 'Chiave API personale'); const key = el('input'); key.name = 'api_key'; key.type = 'password'; key.autocomplete = 'new-password'; key.required = true; key.maxLength = 512; key.placeholder = 'Non sarà mostrata dopo il salvataggio'; keyLabel.append(key);
  const idLabel = el('label', 'ID atleta · 0 indica il tuo account'); const id = el('input'); id.name = 'athlete_id'; id.value = '0'; id.maxLength = 40; idLabel.append(id);
  const submit = el('button', 'Collega e sincronizza', 'primary'); submit.type = 'submit'; form.append(keyLabel, idLabel, submit); card.append(form);
  const daysLabel = el('label', 'Periodo da sincronizzare'); const days = el('select'); days.id = 'intervals-days';
  for (const [value, text] of [[30, 'Ultimi 30 giorni'], [90, 'Ultimi 90 giorni'], [365, 'Ultimo anno'], [3650, 'Ultimi 10 anni']]) { const option = el('option', text); option.value = value; days.append(option); } days.value = '90'; daysLabel.append(days); card.append(daysLabel);
  const actions = el('div', null, 'action-group');
  for (const [id, text, callback] of [['intervals-sync', 'Sincronizza ora', () => action(syncIntervals)], ['intervals-verify', 'Verifica connessione', () => action(async () => { renderIntervals(await api('/integrations/intervals/verify', json('POST', {}))); notice('Connessione Intervals.icu verificata.'); })], ['intervals-disconnect', 'Scollega', () => action(async () => { renderIntervals(await api('/integrations/intervals', { method: 'DELETE' })); notice('Intervals.icu scollegato. Le attività già importate restano nel diario.'); })]]) { const control = button(text, callback); control.id = id; control.disabled = true; actions.append(control); }
  card.append(actions);
  const result = el('div'); result.id = 'intervals-result'; result.setAttribute('role', 'status'); card.append(result);
  card.append(el('p', 'Importa attività e wellness disponibili, con fonte e data di osservazione. Alcuni dati Garmin possono non essere accessibili tramite Intervals.icu. La sincronizzazione prosegue in background.', 'muted'));
  $('#connections').prepend(card);
  form.addEventListener('submit', event => {
    event.preventDefault(); const apiKey = key.value.trim(); const athleteId = id.value.trim() || '0'; key.value = '';
    action(async () => { renderIntervals(await api('/integrations/intervals/connect', json('POST', { api_key: apiKey, athlete_id: athleteId }))); await syncIntervals(); });
  });
}
function enhanceProfile() {
  const form = $('#athlete-form'); form.elements.weekly_minutes.min = '30'; form.elements.birth_date.max=athleteDate();
  form.elements.limitations.maxLength = 1200;
  form.elements.ftp_watts.min = '30'; form.elements.ftp_watts.max = '700';
  form.elements.css.min = '40'; form.elements.css.max = '360';
  form.elements.target_time && (form.elements.target_time.maxLength = 30);
  $('#goal-form').elements.target_time.maxLength = 30;
  $('#session-edit-form').elements.duration_min.min = '15';
  $('#session-edit-form').elements.reason.maxLength = 400;
  form.elements.limitations.placeholder = 'Es. rientro dopo una pausa. Per escludere una disciplina usa le opzioni qui sotto.';
  const recentLabel = el('label', 'Volume settimanale recente · minuti (facoltativo)');
  const recent = el('input'); recent.name = 'recent_weekly_minutes'; recent.type = 'number'; recent.min = 0; recent.max = 2400; recent.placeholder = 'Quello che fai già, non quello che vorresti fare'; recentLabel.append(recent);
  form.elements.weekly_minutes.closest('.form-grid').append(recentLabel);
  const exclusions = el('div', null, 'checklist-grid');
  for (const sport of ['cycling', 'running', 'swimming', 'strength']) { const label = el('label', null, 'checkbox-label'); const input = el('input'); input.type = 'checkbox'; input.name = `exclude_${sport}`; label.append(input, el('span', `Escludi ${sports[sport].toLowerCase()}`)); exclusions.append(label); }
  form.elements.limitations.parentElement.after(exclusions);
  for (const [name, sport] of [['ftp_watts', 'bici'], ['running_threshold', 'corsa'], ['css', 'nuoto']]) {
    const reference = form.elements[name]; const wrapper = el('div', null, 'threshold-reference'); reference.parentElement.before(wrapper); wrapper.append(reference.parentElement);
    const sourceLabel = el('label', `Provenienza · ${sport}`); const source = el('select'); source.name = `${name}_source`;
    for (const [value, text] of [['estimated', 'Stimata'], ['measured', 'Test misurato']]) { const option = el('option', text); option.value = value; source.append(option); }
    sourceLabel.append(source);
    const dateLabel = el('label', `Data del riferimento · ${sport}`); const date = el('input'); date.type = 'date'; date.name = `${name}_date`; date.max = athleteDate(); dateLabel.append(date); wrapper.append(sourceLabel, dateLabel);
  }
  form.elements.threshold_source.closest('.form-grid').hidden = true;
  $('#export-workout').textContent = 'Scarica JSON';
  const fitLink = el('a', 'Scarica FIT · bici/corsa', 'outline button-link'); fitLink.id = 'export-fit'; fitLink.hidden = true; $('#export-workout').after(fitLink);
  fitLink.addEventListener('click', event => {
    event.preventDefault(); if (!state.selected?.id) return; const id = state.selected.id;
    action(async () => {
      const result = await api(`/workouts/${id}/garmin/validate`);
      if (!result.fit_export?.available) throw new Error(result.fit_export?.message || 'Questa seduta non è ancora esportabile in FIT. Usa il JSON o rivedi la struttura.');
      const download = el('a'); download.href = `/workouts/${id}/export.fit`; download.download = `workout-${id}.fit`; document.body.append(download); download.click(); download.remove();
      notice('File FIT preparato. I target RPE restano istruzioni con intensità aperta; verifica la seduta sul dispositivo prima di eseguirla.');
    });
  });
}
const sources = { intervals: 'Intervals.icu', manual: 'Inserimento manuale', local_estimate: 'Stima locale' };
function sourceLabel(value) { return sources[value] || value || 'Fonte non disponibile'; }
function listText(root, title, values, className = '') {
  if (!values?.length) return;
  root.append(el('h3', title)); const list = el('ul', null, className);
  values.forEach(value => list.append(el('li', value))); root.append(list);
}
function renderHome(home) {
  const root = $('#metrics'); root.replaceChildren();
  const colors = window.IronCoachMetricStatus;
  $('#home-date').value = home.date; $('#home-date').max = athleteDate();
  $('#home-next').disabled = home.date >= athleteDate(); $('#home-today').hidden = home.date === athleteDate();
  $('#home-history-note').hidden = home.date === athleteDate();
  $('#daily-proposal').hidden = home.date !== athleteDate();
  updateHeaderDate();
  for (const item of home.condition || []) {
    const latest = item.latest;
    const card = metric(item.label, latest ? `${number(latest.value, 1)} ${item.unit}` : 'N/D', latest ? '' : 'Nessun dato disponibile');
    card.dataset.metricKind = item.kind;
    root.append(card);
    if (colors) { const verdict=metricCoach(item,home.condition||[]); colors.apply(card,verdict,false); }
    if(item.kind==='weight_kg'&&home.body_mass)appendBodyMass(card,home.body_mass);
    addMetricDetails(card, item, home.condition || []);
  }
  const readiness = home.recovery || state.today?.readiness;
  if (readiness) {
    const recoveryDate = String(readiness.date || state.today?.date || home.date);
    const card = metric('Recupero', readiness.readiness == null ? 'N/D' : `${number(readiness.readiness)}/100`, `Copertura segnali ${number(readiness.confidence)}%`);
    card.dataset.metricKind = 'readiness';
    if (!home.recovery) card.dataset.dependsCoach = 'true';
    const status = recoveryDate !== home.date || (!home.recovery && !homeDay && recoveryDate !== athleteDate()) ? {tone:'unknown',label:'Stima da aggiornare',reason:'La stima riguarda un giorno precedente. Aggiorna i dati per il consiglio di oggi.'} : colors?.readiness(readiness.readiness, readiness.confidence);
    if (colors) colors.apply(card, status, false);
    if (readiness.readiness == null && readiness.explanation) {
      const lastDate = (readiness.latest_observations || []).map(point => point.date).sort().at(-1);
      card.append(el('p', lastDate ? 'Ultime misure da aggiornare.' : 'Nessuna misura di recupero disponibile per questo giorno.', 'recovery-missing'));
    }
    addMetricDetails(card, {kind:'readiness', label:'Recupero', recovery:readiness, status}, []); root.append(card);
  }
  $('#home-updated').textContent = `Dati al ${home.date} · Europe/Rome`;
  $('#week-period').textContent = `${dayLabel(home.week_start)} — ${dayLabel(home.date)}`;
  $('#week-heading').textContent = home.date === athleteDate() ? 'La settimana fino a oggi' : `La settimana fino a ${dayLabel(home.date)}`;
  $('.coach-flash button[data-page]').textContent = home.date === athleteDate() ? 'Leggi le motivazioni →' : 'Apri Coach di oggi →';
  const week = home.week || {}; const trend = home.load_trend_percent;
  const ascent = week.elevation_m != null ? `${number(week.elevation_m)} m` : week.elevation_known_m != null ? `${number(week.elevation_known_m)} m` : 'N/D';
  $('#home-week').replaceChildren(
    metric('Ore', number(week.hours, 1), 'Da lunedì a oggi'), metric('Attività', number(week.activities), 'Sedute registrate'),
    metric('Dislivello positivo', ascent, 'Somma dei dislivelli registrati'), metric('Carico', number(week.load, 1), trend == null ? 'Trend non calcolabile: manca un riferimento completo' : `${trend > 0 ? '+' : ''}${number(trend, 1)}% rispetto agli stessi giorni della settimana scorsa`)
  );
  ['hours','activities','elevation_m','load'].forEach((key,index)=>{
    const card=$('#home-week').children[index], assessment=home.week_assessments?.[key];
    if(colors) colors.apply(card,assessment ? {tone:assessment.tone,label:assessment.judgment,reason:assessment.explanation} : colors.info('Volume registrato','Confronto con lo storico personale.'),false);
    addWeeklyDetails(card,key,home);
  });
  const coach = home.coach?.training_assessment || home.coach || {};
  $('#coach-flash').textContent = `${coach.scope==='recorded_training' ? '' : (coach.judgment || 'Non valutabile') + '. '}${coach.score == null ? '' : ` · ${number(coach.score, 1)}/100`}${coach.scope==='recorded_training' ? [coach.evidence?.[0] || coach.explanation,coach.actions?.[0]].filter(Boolean).join(' ') : coach.explanation || coach.unavailable_reason || coach.actions?.[0] || coach.evidence?.[0] || 'Aggiungi attività per una valutazione del lavoro registrato.'}`;
  if (colors) colors.apply($('#coach-flash').closest('.coach-flash'), coach.scope==='recorded_training' ? {tone:coach.tone,label:coach.judgment} : colors.assessment(coach), false);
}
function updateHeaderDate() {
  const chosen = !$('#today').hidden && homeDay ? new Date(homeDay + 'T12:00:00Z') : new Date();
  $('#current-date').textContent = new Intl.DateTimeFormat('it-IT', {timeZone:'Europe/Rome',weekday:'long',day:'numeric',month:'long',year:'numeric'}).format(chosen).toUpperCase();
}
async function selectHomeDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value > athleteDate() || value < '1970-01-01') {
    $('#home-date').value = state.home?.date || athleteDate(); throw new Error('Scegli una data dal 1970 a oggi.');
  }
  try {
    const suffix = value === athleteDate() ? '' : `?day=${value}`;
    const [home, summary] = await Promise.all([api(`/dashboard/home${suffix}`), api(value === athleteDate() ? '/experience/summary' : `/experience/summary?end=${value}`)]);
    homeDay = value === athleteDate() ? null : value;
    state.home = home; state.failedReads.delete('/dashboard/home'); render(); renderReview(summary);
    $('#home-date').focus();
  } catch (error) { $('#home-date').value = state.home?.date || athleteDate(); throw error; }
}
$('#home-prev').addEventListener('click', () => action(() => selectHomeDate(offsetDate(state.home?.date || athleteDate(), -1))));
$('#home-next').addEventListener('click', () => action(() => selectHomeDate(offsetDate(state.home?.date || athleteDate(), 1))));
$('#home-today').addEventListener('click', () => action(() => selectHomeDate(athleteDate())));
$('#home-date').addEventListener('change', event => { const value = event.target.value; action(() => selectHomeDate(value)); });
$('#close-metric').addEventListener('click', () => $('#metric-dialog').close());
function metricCoach(item, peers) {
  const colors=window.IronCoachMetricStatus;
  const own=item.kind==='readiness' ? item.status : colors?.metric(item,peers);
  const matching=(state.home?.condition||[]).find(p=>p.kind===item.kind && p.latest?.date===item.latest?.date && p.latest?.source===item.latest?.source);
  const contextual=item.kind==='readiness' || matching ? state.home?.metric_assessments?.[item.kind] : null;
  if(contextual)return {...contextual,...own,reason:contextual.explanation,action:contextual.actions?.[0]};
  return {...own,reason:item.recovery?.explanation || own?.reason || item.explanation,
    action:state.today?.coach_context?.actions?.[0] || (['critical','caution','watch'].includes(own?.tone)?'Rivaluta la seduta prima di aggiungere carico.':'Mantieni la continuità prevista senza aggiungere lavoro per inseguire il numero.')};
}
function showCoachJudgment(root, assessment) {
  root.replaceChildren(el('h3',assessment.label || assessment.judgment));
  const facts=assessment.evidence?.length ? assessment.evidence : [assessment.reason || assessment.explanation];
  const points=[...new Set(facts.filter(Boolean))];
  const list=el('ul',null,'coach-findings');
  for(const text of points.slice(0,4)) list.append(el('li',text));root.append(list);
  if(points.length>4) {const more=el('details',null,'coach-more');more.append(el('summary','Obiettivo e altri elementi'));const extra=el('ul',null,'coach-findings');for(const text of points.slice(4))extra.append(el('li',text));more.append(extra);root.append(more);}
  const action=assessment.action || assessment.actions?.[0];
  if(action) {const decision=el('div',null,'coach-action');decision.append(el('h3','Indicazione del Coach'),el('p',action));root.append(decision);}
  root.dataset.tone=assessment.tone || 'unknown';
}
function editBodyProfile(body) {
  for(const dialog of document.querySelectorAll('dialog[open]'))dialog.close();
  page('athlete',true);const form=$('#athlete-form');form.closest('details').open=true;
  const field=body.missing_fields?.includes('height_cm')?form.elements.height_cm:form.elements.birth_date;
  field.focus();field.scrollIntoView?.({behavior:'smooth',block:'center'});
}
function appendBodyMass(card,body) {
  const block=el('div',null,'bmi-summary');block.append(el('small','IMC · Indice di massa corporea'));
  block.append(el('strong',body.value==null?'Da completare':`${number(body.value,2)} ${body.unit||'kg/m²'}`,'bmi-value'));
  block.append(el('span',body.classification?.label||body.reason,'bmi-context'));
  if(body.missing_fields?.some(field=>['height_cm','birth_date'].includes(field)))block.append(button('Completa il profilo',()=>editBodyProfile(body),'text-button bmi-profile-link'));
  card.append(block);
}
function bodyMassDetails(root,body) {
  const section=el('section',null,'bmi-detail');section.append(el('h3','Indice di massa corporea'),el('strong',body.value==null?'Non calcolabile':`${number(body.value,2)} ${body.unit||'kg/m²'}`));
  if(body.classification)section.append(el('p',body.classification.label));
  section.append(el('p',body.reason),el('p',body.note,'caption'));
  const list=el('dl',null,'bmi-inputs');for(const [label,value]of [['Altezza di profilo',body.height_cm==null?'Non indicata':`${number(body.height_cm,1)} cm`],['Peso registrato',body.weight_kg==null?'Non disponibile':`${number(body.weight_kg,1)} kg · ${body.weight_date}`],['Età alla pesata',body.age_years==null?'Non disponibile':`${body.age_years} anni`],['Sesso',({male:'Maschio',female:'Femmina',other:'Altro'})[body.sex]||'Non indicato']]){list.append(el('dt',label),el('dd',value));}section.append(list,el('p','Formula: peso in kg ÷ altezza in metri al quadrato.','caption'));
  const source=el('a','Metodo e riferimenti CDC ↗');source.href='https://www.cdc.gov/bmi/faq/';source.target='_blank';source.rel='noopener noreferrer';section.append(source);
  section.append(button('Modifica i dati nel profilo',()=>editBodyProfile(body),'outline'));root.append(section);
}
function addMetricDetails(card, item, peers) {
  const open=button(item.kind==='weight_kg'?'Peso e IMC ↗':'Giudizio del Coach ↗',()=>{
    $('#metric-dialog-title').textContent=item.label;
    let assessment=metricCoach(item,peers);
    if(state.failedReads.has('/dashboard/home') || (card.dataset.dependsCoach && state.failedReads.has('/coach/today'))) assessment={tone:'unknown',label:'Aggiornamento da completare',reason:'Il dato mostrato è l’ultimo caricato: non posso confermare un giudizio aggiornato.',action:'Riprova Aggiorna dati.'};
    showCoachJudgment($('#metric-detail'),assessment); if(item.kind==='weight_kg'&&state.home?.body_mass)bodyMassDetails($('#metric-detail'),state.home.body_mass); $('#metric-dialog').showModal();
  },'metric-open');
  open.setAttribute('aria-label',`Apri dettagli: ${item.label}`); open.setAttribute('aria-haspopup','dialog'); card.append(open);
}
function addWeeklyDetails(card,key,home) {
  const open=button('Giudizio e confronto ↗',()=>{
    $('#metric-dialog-title').textContent=card.querySelector('p').textContent;
    const root=$('#metric-detail'), assessment=state.failedReads.has('/dashboard/home') ? {tone:'unknown',judgment:'Aggiornamento non riuscito',explanation:'Il confronto usa gli ultimi dati caricati. Non posso confermare un giudizio aggiornato.',actions:['Riprova Aggiorna dati.']} : home.week_assessments?.[key] || {tone:'neutral',judgment:'Confronto in preparazione',explanation:'Aggiorna i dati per completare il confronto.'};
    showCoachJudgment(root,assessment);
    root.append(el('h3','Andamento settimanale'));
    const periods=[...(home.week_history||[])].reverse().concat([{start:home.week_start,end:home.date,...home.week}]);
    root.append(weeklyChart(periods,key,assessment));$('#metric-dialog').showModal();
  },'metric-open');
  open.setAttribute('aria-label',`Giudizio e confronto: ${card.querySelector('p').textContent}`);open.setAttribute('aria-haspopup','dialog');card.append(open);
}
function svgNode(tag,attrs={},text) {
  const node=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [key,value] of Object.entries(attrs))node.setAttribute(key,value);if(text!=null)node.textContent=text;return node;
}
function chartFrame(title,unit,minimum,maximum,formatter=null) {
  const svg=svgNode('svg',{viewBox:'0 0 640 260',role:'img',class:'performance-chart'});svg.append(svgNode('title',{},title));
  for(let i=0;i<=4;i++){const y=24+i*47;svg.append(svgNode('line',{x1:57,x2:620,y1:y,y2:y,stroke:'#e3eaee','stroke-dasharray':i===4?'0':'3 5'}));svg.append(svgNode('text',{x:47,y:y+4,'text-anchor':'end',class:'axis-label'},formatter?formatter(maximum-(maximum-minimum)*i/4):number(maximum-(maximum-minimum)*i/4,maximum<10?1:0)));}
  svg.append(svgNode('text',{x:57,y:13,class:'axis-unit'},unit||''));return svg;
}
function weeklyChart(periods,key,assessment) {
  const unit={hours:'ore',activities:'attività',elevation_m:'m D+',load:'punti'}[key],digits=key==='activities'?0:1;
  const ref=Number.isFinite(assessment.reference)&&assessment.reference>=0?assessment.reference:null;
  const values=periods.map(p=>p[key]).filter(Number.isFinite),rawMax=Math.max(1,...values,ref||0);
  const power=10**Math.floor(Math.log10(rawMax/4)),fraction=rawMax/4/power;
  const step=(fraction<=1?1:fraction<=2?2:fraction<=2.5?2.5:fraction<=5?5:10)*power,max=key==='activities'?Math.max(4,Math.ceil(rawMax/4)*4):Math.ceil(rawMax/step)*step;
  const format=value=>Number.isFinite(value)?`${number(value,digits)} ${unit}`:'N/D';
  const range=p=>`${dayLabel(p.start)} – ${dayLabel(p.end)}`;
  const difference=value=>!Number.isFinite(value)||ref==null?'Confronto non disponibile':ref===0?`Riferimento ${format(ref)}`:`${value-ref>=0?'+':'−'}${number(Math.abs(value-ref),digits)} ${unit} (${value-ref>=0?'+':'−'}${number(Math.abs((value/ref-1)*100),0)}%) rispetto al riferimento`;
  const panel=el('figure',null,'chart-panel weekly-panel'),head=el('figcaption',null,'chart-panel-heading');
  const latest=periods.at(-1),current=latest?.[key],main=el('div');
  main.append(el('p','ULTIMA SETTIMANA','chart-kicker'),el('strong',format(current),'chart-kpi'));
  if(latest)main.append(el('small',range(latest),'chart-period'));head.append(main);
  const reference=el('div',null,'chart-comparison');reference.append(el('span','Riferimento personale'),el('strong',ref==null?'In costruzione':format(ref)));head.append(reference);panel.append(head);
  panel.append(el('p',difference(current),'weekly-difference'));
  const legend=el('div',null,'weekly-legend');legend.append(el('span','Volume registrato','weekly-series-key'));
  if(ref!=null)legend.append(el('span','Riferimento personale','weekly-reference-key'));panel.append(legend);
  const chart=el('div',null,'weekly-chart');chart.setAttribute('role','group');chart.setAttribute('aria-label',`Confronto settimanale · ${unit}`);
  const tooltip=el('p','','chart-tooltip');tooltip.setAttribute('role','status');tooltip.setAttribute('aria-live','polite');
  periods.forEach((p,i)=>{
    const value=p[key],known=Number.isFinite(value),row=el('button',null,'weekly-row');row.type='button';row.dataset.period=p.start;
    row.setAttribute('aria-label',`${range(p)}: ${format(value)}. ${difference(value)}`);
    const date=el('span',null,'weekly-row-date');date.append(el('span',dayLabel(p.start)));
    date.append(el('small',i===periods.length-1?'Attuale':`al ${dayLabel(p.end)}`));row.append(date);
    if(i===periods.length-1)row.dataset.current='true';
    const track=el('span',null,'weekly-track');track.setAttribute('aria-hidden','true');
    const bar=el('span',null,'weekly-bar');bar.dataset.value=known?value:'';bar.style.width=`${known?Math.max(0,value)/max*100:0}%`;track.append(bar);
    if(known&&value===0)track.classList.add('is-zero');
    if(!known)track.classList.add('is-missing');
    if(known&&ref!=null){const marker=el('span',null,'weekly-reference-marker');marker.style.left=`${ref/max*100}%`;track.append(marker);}row.append(track,el('strong',format(value),'weekly-row-value'));
    const describe=()=>{tooltip.textContent=`${range(p)} · ${difference(value)}`;};
    row.addEventListener('click',describe);row.addEventListener('focus',describe);
    row.addEventListener('keydown',event=>{const rows=[...chart.querySelectorAll('.weekly-row')];if(['ArrowUp','ArrowDown','Home','End'].includes(event.key)){event.preventDefault();rows[event.key==='Home'?0:event.key==='End'?rows.length-1:Math.max(0,Math.min(rows.length-1,i+(event.key==='ArrowDown'?1:-1)))].focus();}});
    chart.append(row);
  });
  const axis=el('div',null,'weekly-axis');axis.setAttribute('aria-hidden','true');
  for(let i=0;i<=4;i++){const tick=el('span',number(max*i/4,key==='activities'?0:max<10?1:2));tick.style.left=`${i*25}%`;axis.append(tick);}chart.append(axis);panel.append(chart,tooltip);
  panel.append(el('p',ref==null?'Il riferimento sarà disponibile con uno storico confrontabile. N/D indica un dato mancante, non zero.':'Il tratteggio indica il riferimento storico personale, non un obiettivo di carico. N/D indica un dato mancante, non zero.','chart-data-note'));return panel;
}
function sparkline(item,compact=false) {
  const points=(item.points||[]).filter(p=>p.value!=null&&Number.isFinite(p.value));
  if(!points.length)return el('p','Nessuna osservazione nel periodo.','empty-state');
  const reference=['sleep_hours','hrv_ms','resting_hr_bpm'].includes(item.kind)&&!item.stale&&item.latest?.source!=='local_estimate'?window.IronCoachMetricStatus.band(item):null;
  const values=points.map(p=>p.value).concat(reference?[reference.lower,reference.upper]:[]),low=Math.min(...values),high=Math.max(...values),spread=high-low||Math.max(1,high*.1),minimum=item.kind==='tsb'?low-spread*.15:Math.max(0,low-spread*.15),maximum=high+spread*.18;
  const svg=chartFrame(`${item.label}: ${points.length} osservazioni`,item.unit,minimum,maximum);if(compact)svg.classList.add('chart-compact');
  const first=Date.parse(points[0].date+'T12:00:00Z'),last=Date.parse(points.at(-1).date+'T12:00:00Z');
  const positions=points.map(p=>[last===first?335:60+550*(Date.parse(p.date+'T12:00:00Z')-first)/(last-first),212-(p.value-minimum)/(maximum-minimum)*188]);
  const palette=window.IronCoachMetricStatus.palette;
  if(reference){const y=v=>212-(v-minimum)/(maximum-minimum)*188;const band=svgNode('rect',{x:57,y:y(reference.upper),width:563,height:y(reference.lower)-y(reference.upper),fill:'#d8efdc',class:'reference-band'});band.append(svgNode('title',{},`Fascia abituale personale: ${number(reference.lower,1)}–${number(reference.upper,1)} ${item.unit}. Descrive lo storico, non una zona ottimale.`));svg.append(band);for(const v of [reference.lower,reference.upper])svg.append(svgNode('line',{x1:57,x2:620,y1:y(v),y2:y(v),stroke:'#78a886','stroke-dasharray':'4 4'}));}
  points.forEach((p,i)=>{const status=window.IronCoachMetricStatus.metric({...item,points:points.slice(0,i+1),latest:p,baseline:null,reference_range:null,stale:false},(state.health?.metrics||[]).map(peer=>({...peer,latest:peer.points?.find(point=>point.date===p.date),stale:false}))),color=status.tone==='unknown'?palette.neutral:palette[status.tone]||palette.neutral;
    if(i && Date.parse(p.date)-Date.parse(points[i-1].date)<=2*86400000) svg.append(svgNode('line',{x1:positions[i-1][0],y1:positions[i-1][1],x2:positions[i][0],y2:positions[i][1],stroke:'#2a6079','stroke-width':'2.2','stroke-linecap':'round'}));
    const dot=svgNode('circle',{cx:positions[i][0],cy:positions[i][1],r:i===points.length-1?4.5:2.5,fill:i===points.length-1?color:'#2a6079',stroke:'#fff','stroke-width':'1.5'});dot.append(svgNode('title',{},`${dayLabel(p.date)}: ${number(p.value,1)} ${item.unit}`));svg.append(dot);
  });
  for(const i of [...new Set([0,Math.floor((points.length-1)/2),points.length-1])])svg.append(svgNode('text',{x:positions[i][0],y:239,'text-anchor':'middle',class:'axis-label'},dayLabel(points[i].date)));
  return svg;
}
function openHealthMetric(item) {
  const root=$('#metric-detail');$('#metric-dialog-title').textContent=item.label;
  const draw=(metric,fromDay=offsetDate(athleteDate(),-27),toDay=athleteDate())=>{
    root.replaceChildren();root.dataset.tone='neutral';
    const form=el('form',null,'chart-range');
    for(const [name,label,value] of [['from','Dal',fromDay],['to','Al',toDay]]){const field=el('label',label),input=el('input');input.type='date';input.name=name;input.value=value;input.required=true;field.append(input);form.append(field);}
    const submit=el('button','Aggiorna','outline');submit.type='submit';form.append(submit);
    const panel=el('figure',null,'chart-panel');panel.append(el('figcaption',`${metric.label} · ${metric.unit}`,'chart-panel-heading'),sparkline(metric));
    const reference=['sleep_hours','hrv_ms','resting_hr_bpm'].includes(metric.kind)&&!metric.stale&&metric.latest?.source!=='local_estimate'?window.IronCoachMetricStatus.band(metric):null;panel.append(el('p',reference?`Fascia verde: intervallo abituale personale ${number(reference.lower,1)}–${number(reference.upper,1)} ${metric.unit}, da ${reference.count} letture precedenti comparabili. Descrive la variabilità, non un obiettivo da inseguire.`:'Nessuna fascia personale affidabile disponibile per questa metrica. I punti mostrano soltanto le misure registrate.','chart-data-note'));
    const verdict=el('section',null,'health-coach-detail');showCoachJudgment(verdict,metricCoach(metric,state.health?.metrics||[]));
    root.append(form,panel,verdict);
    form.addEventListener('submit',event=>{event.preventDefault();const from=form.elements.from.value,to=form.elements.to.value;action(async()=>{if(from>to)throw new Error('La data iniziale deve precedere quella finale.');const data=await api(`/health/metrics?from=${from}&to=${to}`);const found=data.metrics.find(m=>m.kind===item.kind);draw(found||{...item,points:[],latest:null},from,to);});});
  };draw(item);$('#metric-dialog').showModal();
}
function renderHealth(value) {
  $('#health-status').textContent='';
  const form=$('#health-range');if(!form.dataset.dirty){form.elements.from.value=value.from;form.elements.to.value=value.to;}
  const root=$('#health-metrics');root.replaceChildren();
  for(const item of value.metrics||[]){if(['stress_score','stress_level','steps','daily_activity_load','daily_ctl_load','daily_atl_load','body_battery_min','body_battery_max'].includes(item.kind))continue;if(item.kind==='body_battery'&&!item.latest)continue;const card=el('article',null,'card health-card clickable-card'),latest=item.latest;
    card.append(el('h2',item.label),el('strong',latest?`${number(latest.value,1)} ${item.unit}`:'N/D','health-value'),sparkline(item,true));
    const verdict=window.IronCoachMetricStatus.metric(item,value.metrics||[]);if(verdict.label==='Dato di contesto')continue;window.IronCoachMetricStatus.apply(card,verdict,false);
    const open=button('Analisi e andamento ↗',()=>openHealthMetric(item),'metric-open');open.setAttribute('aria-label',`Apri ${item.label}`);open.setAttribute('aria-haspopup','dialog');card.append(open);root.append(card);
  }
}
function assessmentContent(root, assessment, heading = 'Valutazione') {
  if(assessment.scope==='recorded_training' || assessment.scope==='recorded_session') {
    showCoachJudgment(root,assessment); if(assessment.score!=null)root.prepend(el('strong',`${number(assessment.score)}/100`,'assessment-score')); root.prepend(el('p',heading,'eyebrow')); return;
  }
  root.replaceChildren(el('p', heading, 'eyebrow'), el('h2', assessment.judgment || 'Non valutabile'));
  if (assessment.score != null) root.append(el('strong', `${number(assessment.score, 1)}/100`, 'assessment-score'));
  if (window.IronCoachMetricStatus) window.IronCoachMetricStatus.apply(root, assessment.scope==='recorded_training' ? {tone:assessment.tone,label:assessment.judgment,reason:assessment.explanation} : window.IronCoachMetricStatus.assessment(assessment));
  if (assessment.unavailable_reason) root.append(el('p', assessment.unavailable_reason));
  listText(root, 'Evidenze', assessment.evidence); listText(root, 'Cosa correggere', assessment.actions); listText(root, 'Limiti della valutazione', assessment.limits, 'muted');
  root.append(el('p', `Metodo: ${assessment.rules_version || 'non indicato'}`, 'caption'));
}
function renderCoach(value) {
  assessmentContent($('#coach-overview'), value.training_assessment || value, value.training_assessment ? 'GIUDIZIO SUL LAVORO REGISTRATO' : 'GESTIONE DEL PIANO · ULTIMI 28 GIORNI');
  const root = $('#coach-upcoming'); root.replaceChildren();
  if (!value.upcoming?.length) root.append(el('p', 'Nessuna prossima seduta programmata. Genera una settimana dal piano per leggerne lo scopo.', 'empty-state'));
  for (const workout of value.upcoming || []) {
    const card = el('article', null, 'card'); card.append(el('p', workout.date, 'eyebrow'), el('h3', workout.name), el('p', workout.reason), el('p', workout.expected_benefit), el('small', workout.limits, 'muted'));
    const saved = state.workouts.find(w => w.id === workout.id); if (saved) card.append(button('Apri la seduta', () => action(() => showWorkout(saved.payload, saved)), 'text-button')); root.append(card);
  }
}
function timeLabel(seconds) {
  if(!Number.isFinite(seconds))return '—';const value=Math.max(0,Math.round(seconds)),h=Math.floor(value/3600),m=Math.floor(value%3600/60),sec=value%60;return h?`${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`:`${m}:${String(sec).padStart(2,'0')}`;
}
function activityStreamChart(series,reference=null) {
  return window.IronCoachCharts.activity(series,{reference});
}
function activityRoute(route) {
  const panel=el('figure',null,'activity-route-panel chart-panel');panel.append(el('figcaption','Percorso GPS','chart-panel-heading'));
  if(!route?.available||route.points?.length<2){panel.append(el('p','Percorso GPS non disponibile per questa attività.','empty-state'));return panel;}
  const points=route.points,lat=points.reduce((sum,p)=>sum+p.lat,0)/points.length,scale=Math.max(.01,Math.cos(lat*Math.PI/180));
  const xy=points.map(p=>[p.lon*scale,-p.lat]),xs=xy.map(p=>p[0]),ys=xy.map(p=>p[1]),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),factor=Math.min(580/(maxX-minX||.0001),240/(maxY-minY||.0001));
  const mapped=xy.map(p=>[340+(p[0]-(minX+maxX)/2)*factor,150+(p[1]-(minY+maxY)/2)*factor]);
  const svg=svgNode('svg',{viewBox:'0 0 680 300',role:'img',class:'activity-route'});svg.append(svgNode('title',{},`Traccia GPS con ${points.length} punti osservati`));svg.append(svgNode('rect',{x:0,y:0,width:680,height:300,fill:'#f1f6f5',rx:14}));
  for(let x=0;x<680;x+=40)svg.append(svgNode('line',{x1:x,x2:x,y1:0,y2:300,stroke:'#e1ebe7'}));for(let y=0;y<300;y+=40)svg.append(svgNode('line',{x1:0,x2:680,y1:y,y2:y,stroke:'#e1ebe7'}));
  svg.append(svgNode('polyline',{points:mapped.map(p=>p.join(',')).join(' '),fill:'none',stroke:'#174f65','stroke-width':3.2,'stroke-linecap':'round','stroke-linejoin':'round'}));
  for(const [point,color,label]of [[mapped[0],'#168354','Partenza'],[mapped.at(-1),'#ce3c43','Arrivo']]){const dot=svgNode('circle',{cx:point[0],cy:point[1],r:6,fill:color,stroke:'#fff','stroke-width':2});dot.append(svgNode('title',{},label));svg.append(dot);}
  let zoom=1;const controls=el('div',null,'route-controls');for(const [label,multiplier]of [['−',1/1.4],['+',1.4]]){const control=button(label,()=>{zoom=Math.max(1,Math.min(6,zoom*multiplier));svg.setAttribute('viewBox',`${340-340/zoom} ${150-150/zoom} ${680/zoom} ${300/zoom}`);},'outline');control.setAttribute('aria-label',multiplier>1?'Ingrandisci percorso':'Riduci percorso');controls.append(control);}panel.append(svg,controls);return panel;
}
let activityDetailVersion=0;
async function showActivity(activity) {
  const version=++activityDetailVersion,root=$('#activity-detail');root.replaceChildren(el('h2',activity.name||'Attività'),el('p','Caricamento attività…'));root.dataset.tone='neutral';$('#activity-dialog').showModal();
  const results=await Promise.allSettled([api(`/activities/${activity.id}/detail`),api(`/activities/${activity.id}/assessment`)]);if(version!==activityDetailVersion)return;
  const detail=results[0].status==='fulfilled'?results[0].value:{...activity,summary:activity,streams:[],route:null},rating=detail.coach_rating||(results[1].status==='fulfilled'?results[1].value:null);
  const header=el('div',null,'activity-overview');header.dataset.sport=detail.sport||'other';header.append(el('p',`${sports[detail.sport]||detail.sport||'Sport'}${detail.start_time?' · '+dayLabel(activityDate(detail.start_time)):''}`,'eyebrow'),el('h2',detail.name||activity.name||'Attività'));
  const stats=el('div',null,'activity-stat-grid'),data=detail.summary||activity;
  const isSwim=/swim|nuoto/i.test(detail.sport||''),isRun=/run|corsa/i.test(detail.sport||'');
  for(const [label,value,unit,formatter]of [['Passo medio',isSwim?data.avg_pace_seconds_per_100m:isRun?data.avg_pace_seconds_per_km:null,isSwim?'/100 m':'/km',v=>timeLabel(v)+(isSwim?' /100 m':' /km')],['Durata',data.duration_seconds,'',timeLabel],['Distanza',scaled(data.distance_meters,1000),'km'],['Dislivello',data.elevation_gain_meters,'m D+'],['Velocità media',data.avg_speed==null?null:data.avg_speed*3.6,'km/h'],['FC media',data.avg_hr,'bpm'],['FC massima',data.max_hr,'bpm'],['Potenza media',data.avg_power,'W'],['Potenza normalizzata',data.normalized_power,'W'],['Cadenza',data.avg_cadence,'/min'],['Energia',data.calories,'kcal'],['Carico',data.load,'punti']]){
    if(value==null)continue;const card=el('div',null,'activity-stat');card.dataset.stat=label.includes('FC')?'heart':label.includes('Potenza')?'power':label==='Distanza'?'distance':label==='Dislivello'?'altitude':'general';card.append(el('small',label),el('strong',formatter?formatter(value):`${number(value,['km','km/h'].includes(unit)?1:0)} ${unit}`));stats.append(card);
  }header.append(stats);root.replaceChildren(header,activityRoute(detail.route));
  if(detail.streams?.length){const panel=el('section',null,'activity-stream-panel chart-panel'),top=el('div',null,'chart-panel-heading'),label=el('label','Andamento'),select=el('select');select.name='activity_stream';
    for(const stream of detail.streams){const option=el('option',stream.label);option.value=stream.kind;select.append(option);}label.append(select);top.append(label);panel.append(top);const chart=el('div'),views=new Map();panel.append(chart);const draw=()=>{const stream=detail.streams.find(s=>s.kind===select.value);if(!views.has(stream.kind))views.set(stream.kind,activityStreamChart(stream,detail.chart_references?.[stream.kind]));chart.replaceChildren(views.get(stream.kind));};select.addEventListener('change',draw);draw();root.append(panel);
  }else root.append(el('p',detail.availability?.message||'I riepiloghi sono disponibili; questa attività non contiene serie temporali.','caption'));
  if(rating){const coach=el('section',null,'activity-coach');assessmentContent(coach,rating,'IL COACH SULLA SEDUTA');root.append(coach);}else{const coach=el('section',null,'activity-coach');coach.dataset.tone='unknown';coach.append(el('h3','Valutazione non disponibile'),el('p','Il riepilogo resta consultabile. Riprova per aggiornare il giudizio.'),button('Riprova',()=>action(()=>showActivity(activity))));root.append(coach);}
  if(detail.provider_url){const link=el('a','Apri attività originale ↗','button-link outline');const url=new URL(detail.provider_url);if(url.protocol==='https:'&&url.hostname==='intervals.icu'){link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';root.append(link);}}
}
function renderDuplicates(values) {
  $('#duplicate-card').hidden = !values.length; const root = $('#duplicate-list'); root.replaceChildren();
  for (const item of values) {
    const row = el('article', null, 'duplicate-row');
    for (const activity of [item.activity, item.other]) row.append(el('p', `${activity.name || 'Attività'} · ${sourceLabel(activity.source)} · ${activityDate(activity.start_time)} · ${number(scaled(activity.duration_seconds, 60))} min · ${number(scaled(activity.distance_meters, 1000), 1)} km`));
    const actions = el('div', null, 'action-group'); for (const [choice, label] of [['merge', 'È la stessa attività: unisci'], ['keep', 'Sono attività distinte']]) actions.append(button(label, () => action(async () => { await api(`/activities/duplicates/${item.id}/${choice}`, json('POST', {})); await load(); notice(choice === 'merge' ? 'Fonti riunite in una sola attività.' : 'Attività mantenute separate.'); }))); row.append(actions); root.append(row);
  }
}
const requirementLabels = { weekly_hours: 'Volume settimanale · ore', longest_distance_km: 'Seduta lunga · km', longest_elevation_m: 'Dislivello in una seduta · m', terrain_confirmed: 'Preparazione sul terreno', altitude_confirmed: 'Preparazione in quota' };
function renderGap(root, result) {
  root.replaceChildren(el('h3', result.score == null ? 'Dati insufficienti' : `${number(result.score, 1)}% · copertura dei riferimenti`), el('p', result.explanation, 'muted'));
  const colors = window.IronCoachMetricStatus;
  if (colors) colors.apply(root, colors.coverage(result.score));
  root.append(el('p', `Finestra osservata: ${result.window.from} — ${result.window.to}`, 'caption'));
  for (const leg of result.legs || []) {
    root.append(el('h3', `${sports[leg.sport] || leg.sport} · ${terrainLabels[leg.terrain] || leg.terrain}`));
    for (const requirement of leg.requirements || []) {
      const qualitative = requirement.kind.endsWith('_confirmed');
      const value = qualitative ? requirement.observed == null ? 'Da verificare' : requirement.observed ? 'Confermato' : 'Da preparare' : `${number(requirement.observed, 1)} / ${number(requirement.reference, 1)}`;
      const row = el('div', null, 'review-row'); row.append(el('strong', requirementLabels[requirement.kind] || requirement.kind), el('span', `${value} · ${requirement.score == null ? 'N/D' : `${number(requirement.score, 1)}%`}`));
      if (colors) colors.apply(row, colors.coverage(requirement.score, qualitative), false); root.append(row);
    }
  }
  listText(root, 'Come interpretarlo', result.limits, 'caption');
}
const terrainLabels = { road: 'Strada', trail: 'Trail', mixed: 'Misto', water: 'Acqua' };
function renderGoals(values) {
  state.goals = values; const root = $('#race-goal-list'); root.replaceChildren();
  if (!values.length) root.append(el('p', 'Aggiungi un obiettivo dettagliato per confrontare la preparazione con i tuoi riferimenti.', 'empty-state'));
  for (const goal of values) {
    const card = el('article', null, 'goal-item'); card.dataset.goalId = goal.id; card.append(el('h3', goal.name), el('p', `${goal.date} · ${goal.legs.map(leg => sports[leg.sport] || leg.sport).join(' → ')}`, 'caption'));
    const actions = el('div', null, 'action-group'); const gap = el('div', null, 'gap-result'); gap.hidden = true;
    actions.append(button('Vedi preparazione', () => action(async () => {
      gap.hidden = false; gap.dataset.tone = 'unknown'; gap.replaceChildren(el('p', 'Calcolo in corso…'));
      try { renderGap(gap, await api(`/goals/${goal.id}/gap-analysis`)); }
      catch (error) { gap.replaceChildren(el('p', 'Analisi non disponibile. Riprova con «Vedi preparazione».')); throw error; }
    })), button('Modifica', () => editRaceGoal(goal), 'text-button'));
    actions.append(button('Usa per il piano', () => action(async () => {
      const current = await api('/planning/profile');
      const result = await api('/planning/goal', { ...json('PUT', {goal_id:goal.id}), headers:{'If-Match':String(current.revision)} });
      state.settings = result.profile; state.settingsRevision = result.revision;
      delete $('#goal-form').dataset.dirty; await load(); notice('Obiettivo dettagliato collegato. Ricalcola il calendario per applicare discipline, terreno e dislivello nel rispetto della capacità recente.');
    }), 'text-button'));
    let deleting = false; const remove = button('Elimina', () => {
      if (!deleting) { deleting = true; remove.textContent = 'Conferma eliminazione'; return; }
      action(async () => { await api(`/goals/${goal.id}`, { method: 'DELETE' }); renderGoals(await api('/goals')); if ($('#race-goal-form').dataset.id === String(goal.id)) $('#race-goal-form').hidden = true; notice('Obiettivo eliminato.'); });
    }, 'text-button'); actions.append(remove); card.append(actions, gap); root.append(card);
  }
}
function selectField(label, name, values, value = '') {
  const wrapper = el('label', label); const input = el('select'); input.name = name;
  for (const [key, text] of values) { const option = el('option', text); option.value = key; input.append(option); } input.value = value; wrapper.append(input); return wrapper;
}
function numericField(label, name, value, min = 0, max = 30000, required = false) {
  const wrapper = el('label', label); const input = el('input'); input.type = 'number'; input.name = name; input.min = min; input.max = max; input.step = 'any'; input.required = required; input.value = value ?? ''; wrapper.append(input); return wrapper;
}
function addGoalLeg(value = {}) {
  const root = $('#race-goal-legs'); if (root.children.length >= 10) return;
  const leg = el('fieldset', null, 'goal-leg'); leg.append(el('legend', `Frazione ${root.children.length + 1}`));
  const fields = el('div', null, 'form-grid');
  fields.append(selectField('Disciplina', 'sport', Object.entries(sports).filter(([key]) => ['swimming', 'cycling', 'running'].includes(key)), value.sport || 'running'), selectField('Terreno', 'terrain', Object.entries(terrainLabels), value.terrain || 'road'), numericField('Distanza gara · km', 'distance_km', value.distance_km, 0.001, 2000, true), numericField('Dislivello positivo gara · m', 'elevation_gain_m', value.elevation_gain_m ?? 0), numericField('Dislivello negativo gara · m', 'elevation_loss_m', value.elevation_loss_m ?? 0), numericField('Quota minima · m, facoltativa', 'altitude_min_m', value.altitude_min_m, -500, 9000), numericField('Quota massima · m, facoltativa', 'altitude_max_m', value.altitude_max_m, -500, 9000)); leg.append(fields);
  const requirements = value.requirements || {}; const detail = el('details'); detail.open = true; detail.append(el('summary', 'Riferimenti personali di preparazione'));
  detail.append(el('p', 'Scegli valori motivati dal tuo piano. Un riferimento mancante impedisce il calcolo complessivo. Il volume è confrontato con la media delle ultime quattro settimane complete.', 'caption'));
  const reqFields = el('div', null, 'form-grid'); reqFields.append(numericField(requirementLabels.weekly_hours, 'weekly_hours', requirements.weekly_hours, 0.01, 168), numericField(requirementLabels.longest_distance_km, 'longest_distance_km', requirements.longest_distance_km, 0.001, 2000), numericField(requirementLabels.longest_elevation_m, 'longest_elevation_m', requirements.longest_elevation_m, 0.01, 30000));
  for (const key of ['terrain_confirmed', 'altitude_confirmed']) reqFields.append(selectField(requirementLabels[key], key, [['', 'Da verificare'], ['true', 'Confermata da esperienza specifica'], ['false', 'Ancora da preparare']], requirements[key] == null ? '' : String(requirements[key])));
  detail.append(reqFields); leg.append(detail, button('Rimuovi frazione', () => { leg.remove(); renumberLegs(); }, 'text-button')); root.append(leg); renumberLegs();
}
function renumberLegs() {
  const legs = [...$('#race-goal-legs').children]; legs.forEach((leg, i) => { leg.querySelector('legend').textContent = `Frazione ${i + 1}`; leg.querySelector('button').disabled = legs.length === 1; }); $('#add-goal-leg').disabled = legs.length >= 10;
}
function editRaceGoal(goal = null) {
  const form = $('#race-goal-form'); form.reset(); form.hidden = false; form.dataset.id = goal?.id || ''; delete form.dataset.dirty;
  $('#race-goal-form-title').textContent = goal ? 'Modifica obiettivo' : 'Nuovo obiettivo';
  for (const key of ['name', 'date', 'priority', 'target_time']) form.elements[key].value = goal?.[key] ?? (key === 'priority' ? 'finish' : '');
  $('#race-goal-legs').replaceChildren(); (goal?.legs || [{}]).forEach(addGoalLeg); form.elements.name.focus();
}
$('#race-goal-form').addEventListener('submit', event => {
  event.preventDefault(); const form = event.currentTarget;
  const legs = [...$('#race-goal-legs').children].map(leg => {
    const read = name => leg.querySelector(`[name="${name}"]`).value; const numeric = name => read(name) === '' ? null : Number(read(name));
    return { sport: read('sport'), terrain: read('terrain'), distance_km: numeric('distance_km'), elevation_gain_m: numeric('elevation_gain_m') ?? 0, elevation_loss_m: numeric('elevation_loss_m') ?? 0, altitude_min_m: numeric('altitude_min_m'), altitude_max_m: numeric('altitude_max_m'), requirements: { weekly_hours: numeric('weekly_hours'), longest_distance_km: numeric('longest_distance_km'), longest_elevation_m: numeric('longest_elevation_m'), terrain_confirmed: read('terrain_confirmed') === '' ? null : read('terrain_confirmed') === 'true', altitude_confirmed: read('altitude_confirmed') === '' ? null : read('altitude_confirmed') === 'true' } };
  });
  const payload = { name: form.elements.name.value.trim(), date: form.elements.date.value, priority: form.elements.priority.value, target_time: form.elements.target_time.value.trim() || null, legs };
  action(async () => { if (!payload.name || !legs.length) throw new Error('Inserisci il nome e almeno una frazione.'); await api(form.dataset.id ? `/goals/${form.dataset.id}` : '/goals', json(form.dataset.id ? 'PATCH' : 'POST', payload)); form.hidden = true; delete form.dataset.dirty; renderGoals(await api('/goals')); notice('Obiettivo dettagliato salvato. Apri la preparazione per vedere le lacune.'); });
});
$('#new-race-goal').addEventListener('click', () => editRaceGoal()); $('#cancel-race-goal').addEventListener('click', () => $('#race-goal-form').hidden = true); $('#add-goal-leg').addEventListener('click', () => addGoalLeg());
$('#health-range').addEventListener('submit', event => {
  event.preventDefault(); const form = event.currentTarget;
  action(async () => {
    if (form.elements.from.value > form.elements.to.value) throw new Error('La data iniziale supera la data finale.');
    const range = `?from=${form.elements.from.value}&to=${form.elements.to.value}`;
    try {
      const value = await api(`/health/metrics${range}`); state.failedReads.delete('/health/metrics');
      delete form.dataset.dirty; state.healthRange = range; state.health = value; renderHealth(value);
    } catch (error) { state.failedReads.add('/health/metrics'); markRetainedMetrics(); throw error; }
  });
});
function resetActivityFilters() { state.activityLimit = 50; renderActivities(); }
$('#activity-sport').addEventListener('change', resetActivityFilters); $('#activity-search').addEventListener('input', resetActivityFilters); $('#close-activity').addEventListener('click', () => $('#activity-dialog').close());
const activeSyncs = new Map();
function syncMessage(run) {
  const result = run.result || {}; const labels = { queued: 'In coda', running: 'Sincronizzazione in corso', completed: 'Completata', success: 'Completata', partial: 'Completata parzialmente', partial_success: 'Completata parzialmente', error: 'Non completata', failed: 'Non completata' };
  return `${labels[run.status] || run.status}${result.message ? ` · ${result.message}` : ''}${result.new_activities != null ? ` · ${result.new_activities} nuove attività` : ''}${result.wellness_days != null ? ` · ${result.wellness_days} giorni wellness` : ''}${result.errors?.length ? ` · ${result.errors.length} errori` : ''}`;
}
function watchSync(identifier, source) {
  const target = $(`#${source}-result`); target.replaceChildren(el('p', 'Sincronizzazione avviata. Puoi continuare a usare l’app.'));
  if (activeSyncs.has(identifier)) return; activeSyncs.set(identifier, true);
  const poll = async () => {
    try {
      const run = await api(`/sync/runs/${identifier}`); target.replaceChildren(el('p', syncMessage(run)));
      if (['queued', 'running', 'pending'].includes(run.status)) { activeSyncs.set(identifier, setTimeout(poll, 1800)); return; }
      activeSyncs.delete(identifier);
      for (const error of (run.result?.errors || []).slice(0, 20)) target.append(el('p', `${error.id || ''} ${error.error || error.message || 'Elemento non importato.'}`, 'caption'));
      await load(); notice(syncMessage(run), !['completed', 'success'].includes(run.status));
    } catch (error) { activeSyncs.delete(identifier); target.replaceChildren(el('p', 'Lo stato della sincronizzazione non è raggiungibile. L’operazione potrebbe proseguire sul server.', 'muted'), button('Verifica di nuovo', () => watchSync(identifier, source))); }
  }; poll();
}
function updateStravaControls() {
  for (const id of ['strava-sync', 'strava-disconnect']) if ($(`#${id}`)) $(`#${id}`).disabled = state.busy || !state.strava?.connected;
  if ($('#strava-authorize')) $('#strava-authorize').disabled = state.busy || !state.strava?.configured;
}
function renderStrava(value) {
  state.strava = value; $('#strava-status').replaceChildren(el('strong', value.connected ? 'Account personale collegato' : 'Da collegare'), el('p', value.message || 'Configura il collegamento OAuth del server locale.', 'muted'));
  if (value.scopes) $('#strava-status').append(el('p', `Permessi: ${Array.isArray(value.scopes) ? value.scopes.join(', ') : value.scopes}`, 'caption')); updateStravaControls();
}
function renderStravaActivities(value) {
  const root = $('#strava-activities'); root.replaceChildren(el('p', value.message || 'Vista temporanea delle tue attività Strava.', 'caption'));
  if (value.expires_at) root.append(el('p', `Disponibili fino a ${new Date(value.expires_at).toLocaleString('it-IT', { timeZone: 'Europe/Rome' })}`, 'caption'));
  if (!value.activities?.length) root.append(el('p', 'Nessuna attività temporanea. Sincronizza l’account collegato per aggiornare la vista.', 'empty-state'));
  for (const activity of value.activities || []) {
    const row = el('a', null, 'list-row'); row.href = `https://www.strava.com/activities/${encodeURIComponent(activity.id)}`; row.target = '_blank'; row.rel = 'noopener noreferrer'; const text = el('div');
    text.append(el('strong', activity.name || 'Attività Strava'), el('small', `${activity.start_date ? activityDate(activity.start_date) : ''} · ${activity.sport_type || ''} · ${number(scaled(activity.moving_time, 60))} min · ${number(scaled(activity.distance, 1000), 1)} km`)); row.append(text, el('span', 'Apri su Strava ↗', 'caption')); root.append(row);
  }
}
function addStravaConnection() {
  const card = el('article', null, 'card connection-note'); card.id = 'strava-connection'; card.append(el('p', 'IL TUO ACCOUNT PERSONALE', 'eyebrow'), el('h2', 'Strava'));
  const status = el('div', 'Caricamento collegamento…'); status.id = 'strava-status'; status.setAttribute('role', 'status'); card.append(status);
  card.append(el('p', 'Consulta le tue attività in una vista temporanea separata. I dati di questa connessione non alimentano valutazioni, statistiche o confronti con altre fonti.', 'muted'));
  const controls = el('div', null, 'action-group');
  const authorize = button('Collega Strava', () => action(async () => { const result = await api('/integrations/strava/authorize', json('POST', {})); const url = new URL(result.authorization_url); if (url.protocol !== 'https:' || !['www.strava.com', 'strava.com'].includes(url.hostname)) throw new Error('URL di autorizzazione Strava non valido.'); location.assign(url.href); }), 'primary'); authorize.id = 'strava-authorize';
  const daysLabel = el('label', 'Periodo delle attività'); const days = el('select'); days.id = 'strava-days'; for (const [key, label] of [[7, 'Ultimi 7 giorni'], [30, 'Ultimi 30 giorni'], [90, 'Ultimi 90 giorni']]) { const option = el('option', label); option.value = key; days.append(option); } days.value = '90'; daysLabel.append(days); card.append(daysLabel);
  const sync = button('Aggiorna vista', () => action(async () => { const run = await api(`/sync/strava?days=${Number(days.value)}`, json('POST', {})); watchSync(run.run_id, 'strava'); })); sync.id = 'strava-sync';
  const disconnect = button('Scollega', () => action(async () => { await api('/integrations/strava', { method: 'DELETE' }); renderStrava(await api('/integrations/strava/status')); renderStravaActivities({ activities: [], message: 'Connessione e dati temporanei rimossi.' }); notice('Strava scollegato.'); })); disconnect.id = 'strava-disconnect'; controls.append(authorize, sync, disconnect); card.append(controls);
  const result = el('div'); result.id = 'strava-result'; result.setAttribute('role', 'status'); const activities = el('div', null, 'list'); activities.id = 'strava-activities'; card.append(result, activities); $('#connections').prepend(card); updateStravaControls();
}
$('#current-date').textContent = new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }).format(new Date()).toUpperCase();
$('#backend-origin').textContent = location.origin;
$('#metrics').replaceChildren(...['Fitness · CTL', 'Fatica · ATL', 'Bilancio · TSB', 'Recupero'].map(label => metric(label, '—', 'In attesa dei dati')));
mergePlanSections(); enhanceProfile(); addImportTools(); addIntervalsConnection(); addStravaConnection(); page(location.hash.slice(1) || 'today'); action(load);
window.IronCoachUI = { api, action, load, el, button, number, sourceLabel, sparkline,
  openHealthMetric, appendBodyMass, openWorkout:showWorkout,
  openActivity: id=>action(()=>showActivity(state.activities.find(a=>a.id===id)||{id})),
  context: () => ({weekStart:state.weekStart, revision:state.settingsRevision, raceGoalId:state.settings?.race_goal_id}),
  navigate: id => page(id, true),
  resetAfterRestore: () => {
    state.selected = null; state.settings = null; state.settingsRevision = null;
    for (const dialog of document.querySelectorAll('dialog[open]')) dialog.close();
    for (const form of document.querySelectorAll('form')) { delete form.dataset.dirty; delete form.dataset.thresholdDirty; }
    stopTimer();
  },
};
