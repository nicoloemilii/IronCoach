'use strict';
// Calendar associations between recorded work and stored observations. GET only.
(() => {
  const insights = document.getElementById('training-insights');
  const athlete = document.getElementById('athlete');
  const dateInput = document.getElementById('insights-date');
  const periodForm = document.getElementById('insights-period');
  if (!insights || !athlete || !dateInput || !periodForm || document.getElementById('training-response')) return;
  const make = (tag, text, cls) => { const node = document.createElement(tag); if (text != null) node.textContent = text; if (cls) node.className = cls; return node; };
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const number = (value, digits = 1) => finite(value) ? value.toLocaleString('it-IT', { maximumFractionDigits: digits }) : '—';
  const signed = value => finite(value) ? `${value > 0 ? '+' : ''}${number(value)}` : '—';
  const observed = (value, unit = '') => finite(value) ? `${number(value)}${unit ? ' ' + unit : ''}` : 'Non disponibile';
  const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Rome', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  const reasons = { missing_load: 'Carico mancante', load_overflow: 'Carico fuori intervallo', duplicate_review: 'Possibili doppioni da verificare', missing_before: 'Lettura del giorno attività assente', missing_after: 'Lettura del giorno successivo assente', source_mismatch: 'Fonti diverse', invalid_before: 'Lettura del giorno attività non valida', invalid_after: 'Lettura del giorno successivo non valida', dataset_truncated: 'Storico parziale: coppia esclusa dal confronto', change_overflow: 'Variazione fuori intervallo', estimated_before: 'Stima locale del giorno attività: esclusa', estimated_after: 'Stima locale del giorno successivo: esclusa' };
  const root = make('section', null, 'training-response'); root.id = 'training-response'; root.setAttribute('aria-labelledby', 'response-title');
  const heading = make('div', null, 'response-heading'), intro = make('div');
  const title = make('h2', 'Il lavoro e i giorni dopo'); title.id = 'response-title'; title.tabIndex = -1;
  intro.append(make('p', 'ESPLORA LA TUA RISPOSTA', 'eyebrow'), title, make('p', 'Carico registrato e variazioni delle tue osservazioni, a uno o due giorni di distanza. Un’associazione di calendario non dimostra una causa.'));
  const controls = make('div', null, 'response-controls');
  function control(label, id) { const wrapper = make('label', label), input = make('select'); input.id = id; wrapper.htmlFor = id; wrapper.append(input); controls.append(wrapper); return input; }
  const lag = control('Quando osservare la variazione', 'response-lag');
  for (const [value, label] of [['1', 'Il giorno dopo'], ['2', 'Due giorni dopo']]) { const option = make('option', label); option.value = value; lag.append(option); }
  const metricSelect = control('Misura', 'response-metric'), sourceSelect = control('Fonte', 'response-source');
  metricSelect.disabled = sourceSelect.disabled = true;
  heading.append(intro, controls);
  const status = make('p', 'Apri il profilo atleta per esplorare queste osservazioni.', 'response-status'); status.id = 'response-status'; status.setAttribute('role', 'status');
  const retry = make('button', 'Riprova', 'outline'); retry.type = 'button'; retry.hidden = true; retry.addEventListener('click', () => load(true));
  const content = make('div', null, 'response-content'); content.id = 'response-content'; content.hidden = true;
  root.append(heading, status, retry, content); insights.after(root);
  const jump = make('button', 'Esplora carico e giorni successivi ↓', 'response-jump'); jump.type = 'button';
  jump.addEventListener('click', () => { title.focus(); root.scrollIntoView?.({ block: 'start' }); });
  (insights.querySelector('.insights-heading > div') || insights).append(jump);
  let controller = null, version = 0, pendingKey = null, loadedKey = null, timeout = null;
  let data = null, selectedMetric = null;
  const selectedSources = new Map();
  const visible = () => !athlete.hidden;
  const sameSource = (row, series) => row.before_source === series.source && row.after_source === series.source;
  const usable = (row, series) => row.eligible === true && sameSource(row, series) && [row.load, row.before, row.after, row.change].every(finite);
  const reason = (row, series) => {
    if (row.eligible && !sameSource(row, series)) return reasons.source_mismatch;
    const codes = row.exclusion_reasons?.length ? row.exclusion_reasons : row.exclusion_reason ? [row.exclusion_reason] : [];
    return codes.length ? codes.map(code => reasons[code] || code).join(' · ') : row.eligible ? 'Coppia disponibile' : 'Dati insufficienti';
  };
  const sourceName = (source, series) => source == null ? 'Non disponibile' : source === series.source ? series.source_label || source : source;

  function updateSources() {
    const metric = (data.metrics || []).find(item => item.kind === selectedMetric);
    sourceSelect.replaceChildren();
    for (const series of metric?.series || []) { const option = make('option', series.source_label || series.source); option.value = series.source; sourceSelect.append(option); }
    const wanted = selectedSources.get(selectedMetric);
    if ([...sourceSelect.options].some(option => option.value === wanted)) sourceSelect.value = wanted;
    else {
      const best = (metric?.series || []).reduce((chosen, series) => !chosen || (finite(series.paired_days) ? series.paired_days : 0) > (finite(chosen.paired_days) ? chosen.paired_days : 0) ? series : chosen, null);
      if (best) sourceSelect.value = best.source;
    }
    sourceSelect.disabled = !sourceSelect.options.length;
    selectedSources.set(selectedMetric, sourceSelect.value);
  }
  metricSelect.addEventListener('change', () => { selectedMetric = metricSelect.value; updateSources(); draw(); });
  sourceSelect.addEventListener('change', () => { selectedSources.set(selectedMetric, sourceSelect.value); draw(); });
  lag.addEventListener('change', () => load(true));

  function svgNode(tag, attributes, text) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attributes || {})) node.setAttribute(key, String(value));
    if (text != null) node.textContent = text;
    return node;
  }
  function sampleText(row, metric, series) {
    return `${row.training_date} → ${row.response_date}: carico ${observed(row.load, 'punti')}, ${number(row.activities, 0)} attività registrate. ${metric.label} · lettura del giorno attività: ${observed(row.before, metric.unit)}; lettura giorno +${data.lag_days}: ${observed(row.after, metric.unit)}; variazione ${signed(row.change)} ${metric.unit}. Fonte: ${series.source_label || series.source}.${Number(data.lag_days) === 2 ? ` Giorno intermedio: ${number(row.intervening_activities, 0)} attività registrate.` : ''}`;
  }
  function scatter(metric, series) {
    const panel = make('div', null, 'response-chart'), rows = (series.rows || []).filter(row => usable(row, series));
    panel.append(make('p', 'Ogni punto è una coppia della stessa fonte. Il colore distingue carichi fino alla mediana e sopra la mediana; non indica un carico ideale. La linea tratteggiata indica variazione nulla.', 'response-legend'));
    if (!rows.length) { panel.append(make('p', 'Non ci sono coppie confrontabili da mostrare nel grafico. Leggi i giorni esclusi nella tabella.', 'response-empty')); return panel; }
    const svg = svgNode('svg', { class: 'response-plot', viewBox: '0 0 720 320', role: 'group', tabindex: '0', 'aria-label': `${metric.label}: carico registrato e variazione a ${data.lag_days} giorni`, 'aria-describedby': 'response-keyboard response-readout' });
    svg.append(svgNode('title', {}, `${metric.label} · ${series.source_label || series.source}`));
    const left = 72, right = 690, top = 25, bottom = 245;
    const maxLoad = Math.max(1, ...rows.map(row => row.load)) * 1.06;
    const low = Math.min(0, ...rows.map(row => row.change)), high = Math.max(0, ...rows.map(row => row.change));
    const spread = high - low || 2, minY = low - spread * .15, maxY = high + spread * .15;
    const spanY = maxY - minY;
    // Finite observations can still overflow when deriving a plotting range.
    // Keep their exact table values available instead of emitting invalid SVG.
    if (![maxLoad, low, high, spread, minY, maxY, spanY].every(finite) || maxLoad <= 0 || spanY <= 0) { panel.append(make('p', 'Grafico non disponibile: i valori superano l’intervallo rappresentabile. Puoi leggere i valori registrati nella tabella.', 'response-empty')); return panel; }
    const x = value => left + value / maxLoad * (right - left), y = value => bottom - (value - minY) / spanY * (bottom - top);
    svg.append(svgNode('line', { x1: left, y1: top, x2: left, y2: bottom, class: 'response-axis' }), svgNode('line', { x1: left, y1: bottom, x2: right, y2: bottom, class: 'response-axis' }), svgNode('line', { x1: left, y1: y(0), x2: right, y2: y(0), class: 'response-zero', stroke: '#82929b', 'stroke-dasharray': '5 5' }));
    svg.append(svgNode('text', { x: right + 8, y: y(0) + 4, class: 'response-axis-label' }, '0'));
    for (let index = 0; index <= 4; index++) {
      const load = maxLoad * (index / 4), change = minY + spanY * (index / 4);
      svg.append(svgNode('text', { x: x(load), y: bottom + 23, 'text-anchor': 'middle', class: 'response-axis-label' }, number(load)), svgNode('text', { x: left - 10, y: y(change) + 4, 'text-anchor': 'end', class: 'response-axis-label' }, signed(change)));
    }
    svg.append(svgNode('text', { x: (left + right) / 2, y: 300, 'text-anchor': 'middle', class: 'response-axis-title' }, 'Carico registrato · punti'), svgNode('text', { x: 18, y: 135, transform: 'rotate(-90 18 135)', 'text-anchor': 'middle', class: 'response-axis-title' }, `Variazione · ${metric.unit}`));
    const readout = make('p', null, 'response-readout'); readout.id = 'response-readout'; readout.setAttribute('role', 'status'); readout.setAttribute('aria-live', 'polite');
    const navigation = make('div', null, 'response-chart-controls'); navigation.setAttribute('role', 'group'); navigation.setAttribute('aria-label', 'Navigazione dei campioni');
    const previous = make('button', '← Campione precedente', 'outline'), next = make('button', 'Campione successivo →', 'outline'); previous.type = next.type = 'button';
    previous.setAttribute('aria-controls', 'response-readout'); next.setAttribute('aria-controls', 'response-readout'); navigation.append(previous, next);
    const samples = []; let selected = 0;
    function select(index) { selected = Math.max(0, Math.min(rows.length - 1, index)); previous.disabled = selected === 0; next.disabled = selected === rows.length - 1; samples.forEach((sample, i) => { sample.setAttribute('aria-pressed', String(i === selected)); sample.dataset.selected = String(i === selected); sample.querySelector('.response-point').setAttribute('r', i === selected ? '7' : '5'); }); readout.textContent = sampleText(rows[selected], metric, series); }
    previous.addEventListener('click', () => select(selected - 1)); next.addEventListener('click', () => select(selected + 1));
    rows.forEach((row, index) => {
      const group = finite(series.load_split_median) && row.load > series.load_split_median ? 'higher' : 'lower';
      const sample = svgNode('g', { class: 'response-sample', role: 'button', tabindex: '-1', 'aria-label': sampleText(row, metric, series), 'data-group': group });
      sample.append(svgNode('circle', { cx: x(row.load), cy: y(row.change), r: 16, fill: 'transparent', class: 'response-hit' }), svgNode('circle', { cx: x(row.load), cy: y(row.change), r: 5, fill: group === 'lower' ? '#527ea8' : '#8d68b3', class: 'response-point', 'data-group': group }));
      sample.addEventListener('click', () => { select(index); svg.focus(); }); svg.append(sample); samples.push(sample);
    });
    svg.addEventListener('keydown', event => { const moves = { ArrowRight: selected + 1, ArrowDown: selected + 1, ArrowLeft: selected - 1, ArrowUp: selected - 1, Home: 0, End: rows.length - 1 }; if (event.key in moves) { event.preventDefault(); select(moves[event.key]); } });
    const help = make('p', 'Nel grafico: frecce per cambiare campione, Home e Fine per il primo e l’ultimo. Puoi anche toccare un punto.', 'response-legend'); help.id = 'response-keyboard';
    panel.append(svg, navigation, help, readout); select(0); return panel;
  }
  function samplesTable(metric, series) {
    const details = make('details', null, 'response-table'); details.append(make('summary', 'Leggi tutti i giorni · valori e motivi di esclusione'));
    const table = make('table'), head = make('thead'), heading = make('tr');
    table.append(make('caption', 'Valori registrati per la fonte selezionata. — indica un valore assente o non valido; 0 è uno zero registrato.'));
    for (const label of ['Giorno attività', `Giorno +${data.lag_days}`, 'Carico · punti', 'Attività', `Lettura del giorno attività · ${metric.unit}`, `Lettura giorno +${data.lag_days} · ${metric.unit}`, `Variazione · ${metric.unit}`, 'Fonte giorno attività', `Fonte giorno +${data.lag_days}`, 'Esito', 'Attività intermedie']) { const th = make('th', label); th.scope = 'col'; heading.append(th); }
    head.append(heading); const body = make('tbody');
    for (const row of series.rows || []) { const tr = make('tr'); tr.dataset.eligible = String(usable(row, series)); for (const value of [row.training_date, row.response_date, number(row.load), number(row.activities, 0), number(row.before), number(row.after), signed(row.change), sourceName(row.before_source, series), sourceName(row.after_source, series), reason(row, series), number(row.intervening_activities, 0)]) tr.append(make('td', value)); body.append(tr); }
    table.append(head, body); details.append(table); return details;
  }
  function draw() {
    content.replaceChildren();
    const period = data.period || {};
    content.append(make('p', `${period.start || '—'} → ${period.end || '—'} · ${number(period.days, 0)} giorni · osservazioni fino al ${period.response_end || period.as_of || '—'} · ${data.timezone || 'Europe/Rome'}${period.includes_partial_response_day ? ' · ultimo giorno ancora in corso' : ''}`, 'response-period'));
    const coverage = make('dl', null, 'response-coverage');
    for (const [key, label] of [['recorded_training_days', 'Giorni con attività registrate'], ['complete_load_days', 'Carico completo'], ['eligible_training_days', 'Carico utilizzabile'], ['missing_load_days', 'Carico mancante'], ['load_overflow_days', 'Carico fuori intervallo'], ['duplicate_days', 'Giorni con doppioni da verificare'], ['days_without_activities', 'Giorni senza attività registrate']]) { const cell = make('div'); cell.append(make('dt', label), make('dd', number(data.coverage?.[key], 0))); coverage.append(cell); }
    const coverageDetails = make('details', null, 'response-coverage-details');
    coverageDetails.append(make('summary', `${number(data.coverage?.eligible_training_days, 0)} giorni con carico utilizzabile su ${number(data.coverage?.recorded_training_days, 0)} · Copertura ed esclusioni`), coverage);
    content.append(coverageDetails);
    if (data.quality?.summary) content.append(make('p', data.quality.summary, 'response-quality'));
    if (data.quality?.truncated || data.quality?.review_truncated) content.append(make('p', 'Il controllo dello storico è parziale: interpreta la copertura con cautela.', 'response-quality'));
    const metric = (data.metrics || []).find(item => item.kind === selectedMetric), series = metric?.series?.find(item => item.source === sourceSelect.value);
    if (!metric || !series) content.append(make('p', 'Non ci sono osservazioni disponibili per questo periodo. I dati mancanti non descrivono il tuo recupero.', 'response-empty'));
    else {
      const panel = make('section', null, 'response-panel'); panel.append(make('h3', `${metric.label} · ${series.source_label || series.source}`));
      const rows = series.rows || [], shown = rows.filter(row => usable(row, series)).length;
      panel.append(make('p', `${number(shown, 0)} coppie confrontabili · ${number(rows.length - shown, 0)} giorni esclusi per questa misura e fonte · divisione del carico alla mediana: ${observed(series.load_split_median, 'punti')}.`, 'response-summary'));
      const groups = make('div', null, 'response-groups');
      for (const key of ['lower', 'higher']) { const group = (series.groups || []).find(item => item.key === key) || {}, card = make('article', null, 'response-group'); card.dataset.group = key; card.append(make('h4', group.label || (key === 'lower' ? 'Carico fino alla mediana' : 'Carico sopra la mediana')), make('p', `${number(group.count, 0)} coppie · carico ${number(group.load_min)}–${number(group.load_max)} punti`), make('small', 'Variazione mediana'), make('strong', finite(group.median_change) ? `${signed(group.median_change)} ${metric.unit}` : 'Non disponibile'), make('p', `Intervallo centrale 50%: ${signed(group.q25_change)} → ${signed(group.q75_change)} ${metric.unit}`, 'caption')); groups.append(card); }
      panel.append(scatter(metric, series), groups);
      const comparison = series.comparison || {};
      const text = comparison.available === true && finite(comparison.higher_minus_lower) ? `Differenza tra le mediane (carico sopra − fino alla mediana): ${signed(comparison.higher_minus_lower)} ${metric.unit}. È una descrizione delle coppie osservate, non un effetto dimostrato.` : comparison.reason || `Confronto non disponibile: servono almeno ${number(comparison.min_pairs_per_group ?? comparison.minimum_per_group ?? 5, 0)} coppie in ciascun gruppo e carichi distinti.`;
      panel.append(make('p', text, 'response-comparison'), samplesTable(metric, series)); content.append(panel);
    }
    const method = make('details', null, 'response-method'); method.append(make('summary', 'Metodo, limiti e fonti'));
    const list = make('ul');
    for (const line of ['Associazioni tra giorni di calendario, non prove di causa o indicazioni sul carico ottimale.', 'Usa le osservazioni attualmente conservate con data entro il giorno scelto. Non ricostruisce quando i dati erano disponibili in passato.', 'Le date wellness non verificano che la misura sia stata rilevata prima o dopo l’allenamento.', ...(Number(data.lag_days) === 2 ? ['A due giorni di distanza può esserci altro lavoro nel giorno intermedio; il numero di attività registrate è riportato per ciascuna coppia.'] : []), ...(data.methodology || [])]) list.append(make('li', line));
    method.append(list); content.append(method);
  }
  function render(value) {
    data = value; metricSelect.replaceChildren();
    for (const metric of data.metrics || []) { const option = make('option', metric.label); option.value = metric.kind; metricSelect.append(option); }
    const metrics = data.metrics || [];
    if (!metrics.some(metric => metric.kind === selectedMetric)) selectedMetric = metrics.find(metric => metric.kind === 'hrv_ms')?.kind || metrics[0]?.kind || null;
    metricSelect.value = selectedMetric || ''; metricSelect.disabled = !metrics.length;
    updateSources(); draw();
  }
  function cancel() { controller?.abort(); clearTimeout(timeout); timeout = null; version++; pendingKey = null; root.removeAttribute('aria-busy'); }
  async function load(force = false) {
    if (!visible()) return;
    const asOf = dateInput.value, day = new Date(`${asOf}T12:00:00Z`);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(asOf) || !Number.isFinite(day.valueOf()) || day.toISOString().slice(0, 10) !== asOf || asOf < '1970-01-01' || asOf > today()) { cancel(); loadedKey = null; content.hidden = true; retry.hidden = true; status.classList.add('error'); status.textContent = 'Scegli una data valida dal 1 gennaio 1970 a oggi.'; return; }
    const key = `${asOf}|${lag.value}`;
    if (!force && (pendingKey === key || loadedKey === key)) return;
    cancel(); const requestVersion = version, own = new AbortController(); controller = own; pendingKey = key;
    root.setAttribute('aria-busy', 'true'); content.hidden = true; retry.hidden = true; metricSelect.disabled = sourceSelect.disabled = true; status.classList.remove('error'); status.textContent = 'Leggo carichi e osservazioni…';
    const ownTimeout = setTimeout(() => own.abort(), 30000); timeout = ownTimeout;
    try {
      const response = await fetch(`/dashboard/training-response?as_of=${encodeURIComponent(asOf)}&days=84&lag=${encodeURIComponent(lag.value)}`, { method: 'GET', headers: { Accept: 'application/json' }, signal: own.signal });
      const value = await response.json(); if (requestVersion !== version || !visible()) return;
      if (!response.ok) throw new Error('Lettura non disponibile');
      render(value); loadedKey = key; content.hidden = false; status.textContent = 'Solo osservazioni registrate: questa esplorazione non modifica il piano.';
    } catch (error) {
      if (requestVersion !== version || !visible()) return;
      loadedKey = null; status.classList.add('error'); status.textContent = error.name === 'AbortError' ? 'Il caricamento richiede troppo tempo. Riprova.' : 'Carichi e osservazioni non disponibili. Riprova.'; retry.hidden = false;
    } finally { clearTimeout(ownTimeout); if (requestVersion === version) { pendingKey = null; timeout = null; root.removeAttribute('aria-busy'); } }
  }
  periodForm.addEventListener('submit', event => { event.preventDefault(); load(true); });
  document.addEventListener('iron:pagechange', event => { if (event.detail?.id === 'athlete' && visible()) load(true); else if (!visible()) { cancel(); loadedKey = null; } });
  document.addEventListener('iron:loaded', () => { if (visible()) load(true); else { cancel(); loadedKey = null; } });
  window.addEventListener('pagehide', cancel);
  if (visible()) load();
})();
