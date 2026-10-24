'use strict';
// Display the backend's raw-stream comparison. This module never fetches data,
// changes training advice, or derives a physiological classification.
(() => {
  const make = (tag, text, cls) => { const node = document.createElement(tag); if (text != null) node.textContent = text; if (cls) node.className = cls; return node; };
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const number = (value, digits = 1) => finite(value) ? value.toLocaleString('it-IT', {maximumFractionDigits: digits}) : '—';
  const signed = (value, unit) => finite(value) ? `${value > 0 ? '+' : ''}${number(value)} ${unit}` : `— ${unit}`;
  const duration = value => {
    if (!finite(value) || value < 0) return '—';
    const seconds = Math.round(value), hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60);
    return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}` : `${minutes}:${String(seconds % 60).padStart(2, '0')}`;
  };
  const reasonLabels = {
    unsupported_sport: 'Il confronto congiunto di potenza e frequenza cardiaca è disponibile per le attività di bici.',
    missing_time: 'Manca una scala temporale utilizzabile per abbinare le misure.',
    ambiguous_time: 'La scala temporale contiene dati ambigui: il confronto non viene calcolato.',
    missing_power: 'Manca una serie di potenza utilizzabile.',
    ambiguous_power: 'La serie di potenza contiene dati ambigui.',
    missing_heart_rate: 'Manca una serie di frequenza cardiaca utilizzabile.',
    ambiguous_heart_rate: 'La serie di frequenza cardiaca contiene dati ambigui.',
    invalid_window: 'La durata della registrazione non consente di definire due metà confrontabili.',
    truncated_input: 'La registrazione supera il limite elaborabile: non viene presentato un confronto completo.',
    insufficient_coverage: 'La copertura delle misure abbinate è insufficiente in almeno una metà.',
    insufficient_paired_duration: 'Il tempo con entrambe le misure disponibili è insufficiente in almeno una metà.',
    zero_first_ratio: 'Il rapporto W/bpm della prima metà è zero: la variazione percentuale non è calcolabile.',
    numeric_limit: 'I valori non consentono un confronto numerico affidabile.'
  };
  const sourceLabels = {intervals_remote_streams: 'Serie ricevute da Intervals.icu', stored_observation_streams: 'Serie conservate nell’osservazione della fonte'};
  const halfName = key => key === 'first' ? 'Prima metà' : 'Seconda metà';
  const bounds = half => `${duration(half?.start_s)} — ${duration(half?.end_s)}`;

  function render(detail) {
    const value = detail?.effort_comparison;
    if (!value || typeof value !== 'object' || value.status === 'not_applicable') return null;
    const available = value.status === 'available';
    const root = make('section', null, 'activity-effort'); root.dataset.status = value.status || 'unavailable'; root.setAttribute('aria-label', 'Potenza e frequenza cardiaca nelle due metà');
    const heading = make('div', null, 'effort-heading'); heading.append(make('h3', 'Potenza e frequenza cardiaca, nelle due metà'));
    const window = value.window || {};
    heading.append(make('p', `Intera registrazione · ${duration(window.start_s)} — ${duration(window.end_s)}. Questo confronto resta riferito all’intera registrazione quando cambi lo zoom del grafico.`, 'effort-scope'));
    root.append(heading);
    if (window.source === 'last_timestamp') root.append(make('p', 'Durata ricavata dall’ultimo timestamp ricevuto: un’eventuale coda non registrata non è quantificabile.', 'effort-scope'));
    const reasons = [...new Set(Array.isArray(value.reasons) ? value.reasons.filter(reason => typeof reason === 'string') : [])];
    if (!available) {
      const unavailable = make('div', null, 'effort-unavailable');
      unavailable.append(make('p', 'Confronto non disponibile con questi dati.'));
      for (const reason of reasons.length ? reasons : ['unknown']) unavailable.append(make('p', reasonLabels[reason] || 'I dati disponibili non soddisfano i requisiti del confronto.', 'caption'));
      root.append(unavailable);
    }
    const halves = Array.isArray(value.halves) ? value.halves : [];
    const ordered = ['first', 'second'].map(key => ({key, ...(halves.find(half => half?.key === key) || {})}));
    const showHalves = available || value.status === 'insufficient_data';
    if (showHalves) {
      const grid = make('div', null, 'effort-halves');
      for (const half of ordered) {
        const card = make('div', null, 'effort-half'); card.dataset.half = half.key;
        card.append(make('h4', halfName(half.key)), make('p', bounds(half), 'caption'));
        const values = make('dl', null, 'effort-values');
        for (const [label, amount, unit] of [['Potenza', half.power_mean_w, 'W'], ['Frequenza cardiaca', half.heart_rate_mean_bpm, 'bpm']]) {
          const pair = make('div'); pair.append(make('dt', label), make('dd', `${number(amount)} ${unit}`)); values.append(pair);
        }
        card.append(values, make('p', `Dati abbinati: ${duration(half.paired_seconds)} su ${duration(half.duration_seconds)} · ${number(half.coverage_pct)}%`, 'effort-coverage'));
        card.append(make('p', `Potenza zero nei dati abbinati: ${duration(half.power_zero_seconds)}. Non identifica automaticamente una sosta.`, 'caption'));
        grid.append(card);
      }
      root.append(grid, make('p', 'Le medie sono pesate per il tempo con entrambe le misure utilizzabili; possono differire dai riepiloghi dell’attività.', 'caption'));
    }
    if (available) {
      const difference = value.difference || {};
      root.append(make('p', `Seconda rispetto alla prima: potenza ${signed(difference.power_w, 'W')} · frequenza cardiaca ${signed(difference.heart_rate_bpm, 'bpm')}.`, 'effort-change'));
    }

    const method = make('details', null, 'effort-method'); method.append(make('summary', 'Dati e metodo del confronto'));
    if (showHalves) {
      const wrapper = make('div', null, 'effort-table-wrap'); wrapper.tabIndex = 0; wrapper.setAttribute('role', 'region'); wrapper.setAttribute('aria-label', 'Tabella dei dati nelle due metà');
      const table = make('table'); table.append(make('caption', 'Intera registrazione · medie pesate sul tempo abbinato'));
      const head = make('thead'), header = make('tr');
      for (const label of ['Periodo', 'Potenza · W', 'FC · bpm', 'Tempo abbinato', 'Copertura', 'Rapporto · W/bpm', 'Tempo a potenza zero']) { const cell = make('th', label); cell.scope = 'col'; header.append(cell); }
      head.append(header); table.append(head); const body = make('tbody');
      for (const half of ordered) {
        const row = make('tr'), title = make('th', `${halfName(half.key)} · ${bounds(half)}`); title.scope = 'row'; row.append(title);
        for (const amount of [number(half.power_mean_w), number(half.heart_rate_mean_bpm), duration(half.paired_seconds), `${number(half.coverage_pct)}%`, available ? number(half.ratio_w_per_bpm, 3) : '—', duration(half.power_zero_seconds)]) row.append(make('td', amount));
        body.append(row);
      }
      table.append(body); wrapper.append(table); method.append(wrapper);
    }
    method.append(make('p', `Variazione del rapporto W/bpm: ${available ? signed(value.change_pct, '%') : 'non disponibile'}.`, 'effort-ratio'));
    method.append(make('p', 'Rapporto W/bpm = potenza media pesata ÷ frequenza cardiaca media pesata. Variazione = 100 × (rapporto della seconda metà ÷ rapporto della prima metà − 1).', 'caption'));
    method.append(make('p', 'Descrive queste misure nella registrazione. Non è un punteggio di fitness, una zona ottimale o una prescrizione di allenamento.', 'caption'));
    const definition = value.method || {};
    if (definition.weighting === 'trapezoidal') method.append(make('p', 'Medie pesate per il tempo, con integrazione trapezoidale tra campioni consecutivi utilizzabili.', 'caption'));
    if (finite(definition.gap_limit_seconds)) method.append(make('p', `Intervallo massimo collegato tra campioni: ${number(definition.gap_limit_seconds)} s. Gli intervalli più lunghi restano esclusi.`, 'caption'));
    if (finite(definition.min_coverage_pct) || finite(definition.min_paired_seconds_per_half)) method.append(make('p', `Requisiti per ciascuna metà: copertura almeno ${number(definition.min_coverage_pct)}% e tempo abbinato almeno ${duration(definition.min_paired_seconds_per_half)}. Sono criteri di disponibilità dei dati.`, 'caption'));
    if (definition.zero_power === 'included') method.append(make('p', 'Gli zeri di potenza registrati sono inclusi. Misure mancanti o non utilizzabili non vengono sostituite con zero.', 'caption'));
    method.append(make('p', `Fonte: ${sourceLabels[value.source] || (typeof value.source === 'string' ? value.source : 'non disponibile')}.`, 'caption'));
    for (const limit of Array.isArray(value.limits) ? value.limits : []) if (typeof limit === 'string') method.append(make('p', limit, 'caption'));
    root.append(method); return root;
  }
  window.IronActivityEffort = {render};
})();
