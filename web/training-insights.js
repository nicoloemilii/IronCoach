'use strict';
// Observed training patterns. This view never changes the plan or writes preferences.
(() => {
  const root = document.getElementById('training-insights');
  if (!root) return;
  const content = document.getElementById('insights-content');
  const status = document.getElementById('insights-status');
  const dateInput = document.getElementById('insights-date');
  const form = document.getElementById('insights-period');
  const make = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; };
  const number = (value, digits = 0) => value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString('it-IT', { maximumFractionDigits: digits });
  const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Rome', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  const dayLabel = value => new Intl.DateTimeFormat('it-IT', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(`${value}T12:00:00Z`));
  const signed = value => value == null ? 'Confronto non disponibile' : `${value > 0 ? '+' : ''}${number(value, 1)}% sul periodo precedente`;
  let controller = null, version = 0, loadedDate = null, pendingDate = null;
  dateInput.max = today(); dateInput.value = today();

  function historyQuality() {
    const activities = document.getElementById('activities'), ui = window.IronCoachUI;
    if (!activities || !ui) return;
    const card = make('article', null, 'card history-quality'); card.id = 'history-quality';
    const copy = make('div'), message = make('p', 'Apri il diario per verificare le registrazioni.', 'caption'); message.setAttribute('role', 'status');
    copy.append(make('p', 'DATI DI CUI FIDARTI', 'eyebrow'), make('h2', 'Qualità dello storico'), message);
    const scan = make('button', 'Cerca possibili doppioni', 'outline'); scan.type = 'button';
    const note = make('p', 'Il controllo confronta orario, disciplina e misure registrate. Ti propone le coppie da rivedere; solo una tua decisione può unirle.', 'caption');
    card.append(copy, scan, note); activities.prepend(card);
    const warning = make('aside', null, 'history-quality-warning'); warning.id = 'history-quality-warning'; warning.hidden = true;
    const warningText = make('p'); warningText.setAttribute('role', 'status');
    const reviewLink = make('a', 'Verifica lo storico →', 'outline button-link'); reviewLink.href = '#activities'; warning.append(warningText, reviewLink);
    document.getElementById('notice')?.after(warning);
    let readVersion = 0;
    async function refresh() {
      const request = ++readVersion;
      try {
        const summary = await ui.api('/activities/duplicates/summary'); if (request !== readVersion) return;
        const count = summary.unresolved_pairs ?? ((summary.unreviewed_pairs ?? 0) + (summary.pending_candidates ?? 0));
        card.dataset.hasDuplicates = String(count > 0);
        warning.hidden = !count;
        warningText.textContent = count > 0 ? 'Alcune coppie di attività hanno gli stessi orari e le stesse misure. I totali e i confronti potrebbero includere doppioni: rivedi le registrazioni prima di interpretarli.' : '';
        const finding = count === 0 ? 'Nessuna coppia esatta da verificare' : `${number(count)} ${count === 1 ? 'coppia esatta da verificare' : 'coppie esatte da verificare'}`;
        message.textContent = `${finding} · ${number(summary.scanned_activities)} attività controllate. ${summary.summary}${summary.truncated ? ' Il controllo è parziale: raggiunto il limite della scansione.' : ''}`;
        scan.textContent = summary.pending_candidates > 0 ? 'Aggiorna le coppie da verificare' : 'Cerca possibili doppioni';
      } catch (_) { if (request === readVersion) { message.textContent = 'Il controllo dello storico non è disponibile. Riprova con «Cerca possibili doppioni».'; warning.hidden = true; } }
    }
    scan.addEventListener('click', () => ui.action(async () => {
      const result = await ui.api('/activities/duplicates/scan', { method: 'POST', body: '{}' });
      await ui.load(); await refresh();
      message.textContent = `${number(result.new_candidates)} ${result.new_candidates === 1 ? 'nuova coppia' : 'nuove coppie'} da rivedere. ${result.summary}${result.truncated ? ' Scansione parziale.' : ''}`;
      const review = document.getElementById('duplicate-card');
      if (review && !review.hidden) { review.tabIndex = -1; review.focus(); review.scrollIntoView({ block: 'start' }); }
    }));
    document.addEventListener('iron:pagechange', event => { if (event.detail?.id === 'activities') refresh(); });
    document.addEventListener('iron:loaded', refresh);
    refresh();
  }
  historyQuality();

  function tile(label, value, note) {
    const card = make('article', null, 'insight-kpi');
    card.append(make('p', label), make('strong', value), make('small', note)); return card;
  }
  function section(title, description) {
    const card = make('section', null, 'insight-panel');
    card.append(make('h3', title), make('p', description, 'caption')); return card;
  }
  function rhythm(data) {
    const card = section('Ogni giorno ha il suo spazio', 'Seleziona un giorno per leggerne il lavoro registrato. Un giorno senza registrazioni non prova che tu abbia riposato.');
    const grid = make('div', null, 'rhythm-grid'); grid.setAttribute('role', 'group'); grid.setAttribute('aria-label', 'Attività nei 28 giorni');
    const detail = make('p', 'Scegli un giorno per i dettagli.', 'rhythm-detail'); detail.setAttribute('role', 'status');
    for (const day of data.continuity.days) {
      const value = day.minutes, count = day.activities;
      const partial = count > 0 && day.known_duration_activities != null && day.known_duration_activities < count;
      const label = `${dayLabel(day.date)}: ${number(count)} attività${value == null ? ', durata non disponibile' : `, ${number(value)} minuti${partial ? ' noti, durata parziale' : ''}`}`;
      const button = make('button', null, 'rhythm-day'); button.type = 'button';
      button.dataset.level = !count ? '0' : value == null ? 'unknown' : value < 30 ? '1' : value < 60 ? '2' : '3';
      button.setAttribute('aria-label', label); button.setAttribute('aria-pressed', 'false'); button.title = label;
      button.append(make('span', day.date.slice(-2)));
      button.addEventListener('click', () => {
        grid.querySelectorAll('button').forEach(n => n.setAttribute('aria-pressed', String(n === button)));
        detail.textContent = `${label}.${!count ? ' Nessuna attività registrata in questa data.' : ''}`;
      }); grid.append(button);
    }
    const labels = make('div', null, 'rhythm-dates'); labels.append(make('span', dayLabel(data.period.start)), make('span', dayLabel(data.period.end)));
    const legend = make('p', 'Minuti registrati · chiaro: nessuna attività · verde: meno di 30 / 30–59 / 60 o più · tratteggio: durata assente. I colori indicano quantità, senza definire un obiettivo.', 'caption');
    card.append(grid, labels, detail, legend); return card;
  }
  function distribution(data) {
    const card = section('Come distribuisci il tuo tempo', 'Quota dei minuti con durata disponibile. Il confronto è con i 28 giorni precedenti, non con un ideale imposto.');
    if (!data.sports.length) { card.append(make('p', 'Aggiungi attività per leggere la distribuzione per disciplina.', 'empty-state')); return card; }
    for (const sport of data.sports) {
      const row = make('div', null, 'insight-sport'); row.dataset.sport = sport.key;
      const heading = make('div', null, 'insight-sport-heading');
      heading.append(make('strong', sport.label), make('span', `${number(sport.current_minutes)} min · ${number(sport.current_share_pct, 1)}%`));
      const track = make('div', null, 'insight-sport-track'); track.setAttribute('aria-hidden', 'true');
      const fill = make('span'); fill.style.width = `${Math.max(0, Math.min(100, sport.current_share_pct || 0))}%`; track.append(fill);
      const delta = sport.share_change_pp;
      row.append(heading, track, make('small', delta == null ? 'Quota precedente non confrontabile' : `${delta > 0 ? '+' : ''}${number(delta, 1)} punti percentuali · prima ${number(sport.previous_share_pct, 1)}%`)); card.append(row);
    }
    return card;
  }
  function weekly(data) {
    const card = section('Otto settimane, una direzione', 'Finestre consecutive di 7 giorni. Le prime quattro formano il periodo precedente; le ultime quattro il periodo selezionato.');
    const controls = make('div', null, 'insight-chart-controls'); controls.setAttribute('role', 'group'); controls.setAttribute('aria-label', 'Misura delle settimane');
    const plot = make('div', null, 'insight-week-chart'); plot.setAttribute('role', 'group');
    const readout = make('p', 'Seleziona una colonna per i dettagli.', 'rhythm-detail'); readout.setAttribute('role', 'status');
    const legend = make('p', 'Blu: periodo precedente · verde: periodo selezionato. I colori identificano i periodi, senza dare un voto al carico.', 'caption');
    const tableDetails = make('details', null, 'insight-method'); tableDetails.append(make('summary', 'Leggi i valori in tabella'));
    const table = make('table'), caption = make('caption', 'Totali delle finestre di 7 giorni'), head = make('thead'), headRow = make('tr');
    for (const label of ['Periodo', 'Attività', 'Minuti', 'Carico', 'Copertura carico']) { const th = make('th', label); th.scope = 'col'; headRow.append(th); }
    head.append(headRow); const body = make('tbody');
    data.weeks.forEach(week => { const row = make('tr'); for (const value of [`${dayLabel(week.start)} – ${dayLabel(week.end)}`, number(week.activities), number(week.minutes), number(week.load, 1), week.load_coverage_pct == null ? '—' : `${number(week.load_coverage_pct)}%`]) row.append(make('td', value)); body.append(row); });
    table.append(caption, head, body); tableDetails.append(table);
    let selected = 'minutes'; const buttons = [];
    function draw() {
      buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.metric === selected)));
      plot.setAttribute('aria-label', selected === 'minutes' ? 'Volume settimanale in minuti' : 'Carico registrato settimanale'); plot.replaceChildren();
      const max = Math.max(1, ...data.weeks.map(w => w[selected] || 0));
      data.weeks.forEach((week, index) => {
        const value = week[selected], unit = selected === 'minutes' ? 'min' : 'punti';
      const partial = week.activities > 0 && (selected === 'load' ? week.load_coverage_pct !== 100 : week.coverage?.minutes?.complete === false);
        const coverage = selected === 'load' ? week.load_coverage_pct : week.coverage?.minutes?.percent;
        const text = `${dayLabel(week.start)} – ${dayLabel(week.end)}: ${number(value, 1)} ${unit}${partial ? ` · dato parziale, copertura ${number(coverage)}%` : ''}${week.partial_day ? ' · ultimo giorno in corso' : ''}`;
        const column = make('button', null, `insight-week ${index < 4 ? 'previous' : 'current'}`); column.type = 'button'; column.setAttribute('aria-label', text); column.setAttribute('aria-pressed', 'false');
        const space = make('span', null, 'insight-bar-space'), bar = make('span', null, 'insight-week-bar'); bar.style.height = value == null ? '6px' : `${Math.max(2, value / max * 100)}%`;
        if (value == null || partial) bar.classList.add('partial');
        bar.append(make('strong', value == null ? 'N/D' : `${number(value, 0)}${partial ? '*' : ''}`)); space.append(bar);
        column.append(space, make('small', dayLabel(week.start)));
        column.addEventListener('click', () => { plot.querySelectorAll('button').forEach(n => n.setAttribute('aria-pressed', String(n === column))); readout.textContent = text; }); plot.append(column);
      });
      readout.textContent = selected === 'load' ? 'Il carico somma solo le misure disponibili. * indica una copertura incompleta; non confrontare direttamente totali con coperture diverse.' : 'Seleziona una colonna per i dettagli del periodo.';
    }
    for (const [key, label] of [['minutes', 'Volume · min'], ['load', 'Carico registrato']]) { const b = make('button', label, 'outline'); b.type = 'button'; b.dataset.metric = key; b.addEventListener('click', () => { selected = key; draw(); }); controls.append(b); buttons.push(b); }
    card.append(controls, plot, readout, legend, tableDetails); draw(); return card;
  }
  function render(data) {
    content.replaceChildren();
    const current = data.volume.current, previous = data.volume.previous;
    const kpis = make('div', null, 'insight-kpis');
    kpis.append(tile('Giorni con attività', `${number(data.continuity.active_days)} / 28`, `Periodo precedente: ${number(data.continuity.previous_active_days)} giorni`),
      tile('Tempo registrato', current.minutes == null ? 'N/D' : `${number(current.minutes / 60, 1)} h${current.coverage?.minutes?.complete === false ? ' *' : ''}`, current.coverage?.minutes?.complete === false ? 'Totale parziale: alcune durate mancano' : signed(data.volume.minutes_change_pct)),
      tile('Settimane con attività', `${number(data.continuity.active_weeks)} / 4`, 'Finestre consecutive di 7 giorni'),
      tile('Intervallo massimo senza registrazioni', `${number(data.continuity.longest_gap_days)} giorni`, 'Non distingue riposo da attività non sincronizzate'));
    const period = make('p', `${dayLabel(data.period.start)} – ${dayLabel(data.period.end)}${data.period.includes_partial_day ? ' · oggi è ancora in corso' : ''}`, 'insight-period-label');
    content.append(period, kpis);
    const quality = data.quality;
    if (quality && (quality.current_possible_duplicates > 0 || quality.previous_possible_duplicates > 0)) {
      const warning = make('aside', null, 'insight-quality-note');
      warning.append(make('strong', 'Prima del confronto, verifica lo storico'), make('p', quality.summary));
      const link = make('a', 'Rivedi le registrazioni →', 'outline button-link'); link.href = '#activities'; warning.append(link); content.append(warning);
    }
    if (!current.activities && !previous.activities) {
      const empty = make('div', null, 'insight-empty'); empty.append(make('h3', 'La tua storia comincia dalle attività.'), make('p', 'Non risultano registrazioni nei due periodi. Collega una fonte o importa le tue attività per esplorare il ritmo della preparazione.'));
      const link = make('a', 'Apri Connessioni →', 'outline button-link'); link.href = '#connections'; empty.append(link); content.append(empty);
    } else {
      content.append(rhythm(data)); const columns = make('div', null, 'insight-columns'); columns.append(weekly(data), distribution(data)); content.append(columns);
    }
    const method = make('details', null, 'insight-method'); method.append(make('summary', 'Come leggere questi dati · fonti e copertura'));
    const list = make('ul'); for (const line of data.methodology || []) list.append(make('li', line)); method.append(list);
    const coverage = data.coverage.current;
    const cover = make('p', `Durata: ${number(coverage.minutes.known)}/${number(coverage.minutes.total)} attività · carico: ${number(coverage.load.known)}/${number(coverage.load.total)} · distanza: ${number(coverage.distance.known)}/${number(coverage.distance.total)} · dislivello: ${number(coverage.elevation.known)}/${number(coverage.elevation.total)}.`, 'caption');
    method.append(cover); content.append(method);
  }
  async function load(force = false) {
    const asOf = dateInput.value;
    if (!asOf || asOf > today() || asOf < dateInput.min) {
      controller?.abort(); version++; pendingDate = null; root.removeAttribute('aria-busy'); content.hidden = true; status.classList.add('error');
      status.textContent = 'Scegli una data valida dal 1 gennaio 1970 a oggi.'; return;
    }
    if (!force && (pendingDate === asOf || loadedDate === asOf)) return;
    controller?.abort(); controller = new AbortController(); const own = controller, requestVersion = ++version; pendingDate = asOf;
    root.setAttribute('aria-busy', 'true'); content.hidden = true; status.classList.remove('error'); status.textContent = 'Leggo il tuo storico…';
    const timeout = setTimeout(() => own.abort(), 30000);
    try {
      const response = await fetch(`/dashboard/training-insights?as_of=${encodeURIComponent(asOf)}`, { signal: own.signal, headers: { Accept: 'application/json' } });
      const data = await response.json(); if (requestVersion !== version) return;
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Non riesco a leggere lo storico.');
      render(data); loadedDate = asOf; content.hidden = false; status.textContent = 'Dati registrati · fuso Europe/Rome. Questa vista descrive lo storico e non modifica il piano.';
    } catch (error) {
      if (requestVersion !== version) return;
      loadedDate = null; status.classList.add('error'); status.textContent = error.name === 'AbortError' ? 'Il caricamento richiede troppo tempo. Premi Esplora per riprovare.' : 'Storico non disponibile. Premi Esplora per riprovare.';
    } finally { clearTimeout(timeout); if (requestVersion === version) { pendingDate = null; root.removeAttribute('aria-busy'); } }
  }
  form.addEventListener('submit', event => { event.preventDefault(); load(true); });
  document.addEventListener('iron:pagechange', event => { if (event.detail?.id === 'athlete') load(); });
  document.addEventListener('iron:loaded', () => { if (!document.getElementById('athlete').hidden) load(true); else loadedDate = null; });
  document.querySelectorAll('[data-open-insights]').forEach(button => button.addEventListener('click', () => {
    window.IronCoachUI.navigate('athlete'); document.getElementById('insights-title').focus(); root.scrollIntoView({ block: 'start' });
  }));
  if (!document.getElementById('athlete').hidden) load();
})();
