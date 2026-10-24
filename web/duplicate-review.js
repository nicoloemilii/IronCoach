'use strict';
// Preview reads are cancellable. Decisions require a separate explicit action.
(() => {
  const ui = window.IronCoachUI, activities = document.getElementById('activities');
  if (!ui || !activities || document.getElementById('duplicate-review-dialog')) return;
  const make = (tag, text, css) => { const node = document.createElement(tag); if (text != null) node.textContent = text; if (css) node.className = css; return node; };
  const button = (label, work, css = 'outline') => { const node = make('button', label, css); node.type = 'button'; node.addEventListener('click', work); return node; };
  const labels = { name: 'Nome', source: 'Fonte principale', source_id: 'Identificativo della fonte', sport: 'Disciplina', start_time: 'Data e ora · UTC', duration_seconds: 'Durata · secondi', distance_meters: 'Distanza · metri', elevation_gain_meters: 'Dislivello · metri', avg_hr: 'Frequenza media · bpm', max_hr: 'Frequenza massima · bpm', avg_power: 'Potenza media · W', normalized_power: 'Potenza normalizzata · W', avg_speed: 'Velocità media · m/s', avg_cadence: 'Cadenza media · /min', calories: 'Energia · kcal', load: 'Carico · punti' };
  const valueText = value => value == null ? '—' : typeof value === 'number' ? Number.isFinite(value) ? value.toLocaleString('it-IT', { maximumFractionDigits: 2 }) : '—' : typeof value === 'object' ? Object.hasOwn(value, 'invalid_number') ? 'Dato non valido' : JSON.stringify(value) : String(value);
  const sourceText = value => value == null ? 'Non disponibile' : ui.sourceLabel ? ui.sourceLabel(value) : String(value);
  const dateTime = value => {
    if (value == null) return '—';
    const date = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(String(value)) ? value : value + 'Z');
    return Number.isFinite(date.valueOf()) ? new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date) : 'Data non disponibile';
  };
  const duration = value => {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return '—';
    const hours = Math.floor(value / 3600), minutes = (value % 3600) / 60;
    return hours ? `${valueText(hours)} h${minutes ? ` ${valueText(minutes)} min` : ''}` : `${valueText(minutes)} min`;
  };
  const visible = () => !activities.hidden;
  const history = make('article', null, 'card duplicate-history-card'); history.id = 'duplicate-history-card';
  const historyTitle = make('h2', 'Decisioni precedenti'); historyTitle.tabIndex = -1;
  const historyStatus = make('p', 'Apri il diario per leggere le decisioni sui doppioni.', 'duplicate-history-message'); historyStatus.setAttribute('role', 'status');
  const historyList = make('div', null, 'duplicate-history-list'), historyLimits = make('div', null, 'duplicate-history-limits');
  const historyRetry = button('Aggiorna cronologia', () => loadHistory());
  history.append(make('p', 'LE TUE VERIFICHE', 'eyebrow'), historyTitle, historyStatus, historyRetry, historyList, historyLimits);
  const pendingCard = document.getElementById('duplicate-card'); if (pendingCard) pendingCard.after(history); else activities.append(history);
  const dialog = make('dialog', null, 'duplicate-review-dialog'); dialog.id = 'duplicate-review-dialog'; dialog.setAttribute('aria-labelledby', 'duplicate-review-title');
  const top = make('div', null, 'dialog-top duplicate-review-top'), back = button('← Indietro', () => { if (!mutating) dialog.close(); }, 'back-button');
  const title = make('h2', 'Confronta le registrazioni'); title.id = 'duplicate-review-title'; top.append(back, title);
  const status = make('p', '', 'duplicate-review-status'); status.setAttribute('role', 'status');
  const content = make('div', null, 'duplicate-review-content'), actions = make('div', null, 'duplicate-review-actions action-group');
  const reload = button('Ricarica confronto', () => openPreview(candidateId));
  const merge = button('Conferma unione', () => decide('merge'), 'primary'), keep = button('Sono attività distinte', () => decide('keep'));
  const undo = button('Conferma annullamento', () => undoDecision(), 'primary'); actions.append(reload, merge, keep, undo);
  dialog.append(top, status, content, actions); document.body.append(dialog);
  let preview = null, candidateId = null, review = null, mode = 'preview', trigger = null, mutating = false;
  let previewVersion = 0, previewRead = null, historyVersion = 0, historyRead = null;
  function cancel(read) { if (read) { clearTimeout(read.timeout); read.controller.abort(); } }
  function current(version) { return version === previewVersion && dialog.open; }
  function controls() {
    back.disabled = mutating; reload.disabled = mutating; historyRetry.disabled = mutating;
    merge.hidden = keep.hidden = mode !== 'preview'; undo.hidden = mode !== 'undo'; reload.hidden = mode !== 'preview' || Boolean(preview);
    merge.disabled = mutating || !preview?.merge_allowed || !preview?.state_token;
    keep.disabled = mutating || !preview || !preview.state_token;
    undo.disabled = mutating || !review?.undo_available;
  }
  async function read(path, slot) {
    const controller = new AbortController(), request = { controller, timeout: setTimeout(() => controller.abort(), 30000) };
    if (slot === 'preview') previewRead = request; else historyRead = request;
    try {
      const response = await fetch(path, { method: 'GET', headers: { Accept: 'application/json' }, signal: controller.signal });
      const body = await response.json();
      if (!response.ok) { const error = new Error(typeof body.detail === 'string' ? body.detail : 'Lettura non disponibile.'); error.status = response.status; throw error; }
      return body;
    } finally { clearTimeout(request.timeout); if (slot === 'preview' && previewRead === request) previewRead = null; if (slot === 'history' && historyRead === request) historyRead = null; }
  }
  function activityCard(activity, caption) {
    const card = make('article', null, 'duplicate-activity'); card.append(make('p', caption, 'eyebrow'), make('h3', activity?.name || 'Attività senza nome'));
    const sport = { cycling: 'Bici', bike: 'Bici', running: 'Corsa', run: 'Corsa', swimming: 'Nuoto', swim: 'Nuoto', strength: 'Forza' }[activity?.sport] || valueText(activity?.sport);
    card.append(make('p', `#${valueText(activity?.id)} · ${sourceText(activity?.source)} · ${dateTime(activity?.start_time)} · Europe/Rome`), make('p', `Identificativo fonte: ${valueText(activity?.source_id)} · Disciplina: ${sport}`, 'caption'), make('p', `Durata: ${duration(activity?.duration_seconds)} · Distanza: ${valueText(typeof activity?.distance_meters === 'number' ? activity.distance_meters / 1000 : null)} km`)); return card;
  }
  function table(headers, rows, css, caption) {
    const wrapper = make('div', null, 'duplicate-review-table'), table = make('table', null, css), head = make('thead'), tr = make('tr');
    wrapper.tabIndex = 0; wrapper.setAttribute('role', 'region'); wrapper.setAttribute('aria-label', `${caption} Scorri orizzontalmente per leggere tutte le colonne.`);
    table.append(make('caption', caption)); headers.forEach(label => { const cell = make('th', label); cell.scope = 'col'; tr.append(cell); }); head.append(tr); table.append(head);
    const body = make('tbody'); for (const entry of rows) { const row = make('tr'); if (entry.source) row.dataset.source = entry.source; entry.cells.forEach(value => row.append(make('td', value))); body.append(row); }
    table.append(body); wrapper.append(make('p', 'Scorri orizzontalmente per leggere tutte le colonne.', 'caption duplicate-table-hint'), table); return wrapper;
  }
  function drawPreview(body) {
    content.replaceChildren(); const pair = make('div', null, 'duplicate-activities');
    pair.append(activityCard(body.other, 'REGISTRAZIONE CONSERVATA'), activityCard(body.activity, 'REGISTRAZIONE DA RIUNIRE')); content.append(pair);
    content.append(make('p', `L’attività #${valueText(body.other?.id)} resta nel diario con la sua fonte principale. I campi già presenti sono conservati; soltanto quelli mancanti vengono copiati dall’altra registrazione. Gli zeri restano valori registrati.`));
    const rows = (body.fields || []).map(field => {
      const same = field.before_keep != null && field.before_remove != null && JSON.stringify(field.before_keep) === JSON.stringify(field.before_remove);
      const origin = field.source === 'imported' ? 'Copiato dall’altra registrazione' : field.source === 'missing' ? 'Non disponibile in entrambe' : same ? 'Valori uguali: conservato quello già presente' : 'Già presente: conservato';
      return { source: field.source, cells: [labels[field.field] || field.field, valueText(field.before_keep), valueText(field.before_remove), valueText(field.result), origin] };
    });
    content.append(table(['Campo', 'Registrazione conservata', 'Altra registrazione', 'Dopo l’unione', 'Origine del risultato'], rows, 'duplicate-diff-table', 'Confronto campo per campo. — indica un dato mancante.'));
    const linkNames = { identities: 'Identità delle fonti', observations: 'Osservazioni originali', feedback: 'Feedback', evaluations: 'Valutazioni', candidates: 'Altri confronti' };
    content.append(table(['Collegamenti', 'Registrazione conservata', 'Altra registrazione', 'Totale coinvolto'], Object.entries(linkNames).map(([key, label]) => ({ cells: [label, valueText(body.links?.[key]?.other), valueText(body.links?.[key]?.activity), valueText(body.links?.[key]?.total)] })), 'duplicate-links-table', 'Collegamenti coinvolti nella decisione; gli altri confronti restano da verificare.'));
    content.append(make('p', `Risultato: #${valueText(body.result?.id ?? body.other?.id)} · ${valueText(body.result?.name ?? body.other?.name)} · fonte principale ${sourceText(body.result?.source ?? body.other?.source)}.`, 'caption'));
    if (body.blocking_reasons?.length) { const block = make('div', null, 'duplicate-blocking'); block.append(make('h3', 'Unione non disponibile')); const list = make('ul'); body.blocking_reasons.forEach(message => list.append(make('li', message))); block.append(list); content.append(block); }
    merge.textContent = `Conferma unione · conserva #${valueText(body.other?.id)}`;
  }
  async function openPreview(identifier, origin) {
    if (mutating) return;
    cancel(previewRead); const version = ++previewVersion; candidateId = identifier; mode = 'preview'; preview = null; review = null;
    if (origin) trigger = origin; else if (!dialog.open) trigger = document.activeElement;
    title.textContent = 'Confronta le registrazioni'; status.textContent = 'Leggo il confronto aggiornato…'; status.classList.remove('error'); content.replaceChildren(); controls();
    dialog.setAttribute('aria-busy', 'true'); if (!dialog.open) dialog.showModal(); back.focus();
    try { const body = await read(`/activities/duplicates/${encodeURIComponent(identifier)}/preview`, 'preview'); if (!current(version)) return; preview = body; drawPreview(body); status.textContent = body.merge_allowed ? 'Controlla il risultato prima di confermare. Nessuna modifica è stata salvata.' : 'Le registrazioni restano separate finché non scegli una decisione disponibile.'; }
    catch (error) { if (current(version)) { status.classList.add('error'); status.textContent = error.name === 'AbortError' ? 'Il confronto richiede troppo tempo. Ricarica per riprovare.' : error.message || 'Confronto non disponibile.'; } }
    finally { if (current(version)) { dialog.removeAttribute('aria-busy'); controls(); } }
  }
  async function refreshAfterDecision() {
    try { await ui.load(); } catch (_) { historyStatus.textContent = 'Decisione salvata. Aggiorna il diario per rileggere tutte le attività.'; }
    if (visible()) await loadHistory();
    if (visible() && !dialog.open) (trigger?.isConnected ? trigger : historyTitle).focus();
  }
  async function decide(decision) {
    if (mutating || mode !== 'preview' || !preview?.state_token || decision === 'merge' && !preview.merge_allowed) return;
    const token = preview.state_token, id = candidateId, version = previewVersion; mutating = true; controls();
    try {
      await ui.action(async () => {
        try {
          await ui.api(`/activities/duplicates/${encodeURIComponent(id)}/${decision}`, { method: 'POST', body: JSON.stringify({ state_token: token }) });
          if (current(version)) { preview = null; content.replaceChildren(make('p', decision === 'merge' ? 'Fonti riunite. Puoi rileggere la decisione e, quando consentito, annullarla nella cronologia.' : 'Attività mantenute separate. La decisione è disponibile nella cronologia.')); status.textContent = 'Decisione salvata.'; dialog.close(); }
          await refreshAfterDecision();
        } catch (error) {
          if (!current(version)) return;
          status.classList.add('error');
          if ([409, 428].includes(error.status)) { preview = null; status.textContent = 'Il confronto è cambiato o non è più valido. Ricaricalo, controlla il nuovo risultato e conferma di nuovo. Nessun nuovo tentativo viene eseguito automaticamente.'; }
          else { preview = null; status.textContent = `${error.message || 'Decisione non completata.'} Ricarica il confronto per verificare lo stato prima di riprovare.`; }
        }
      });
    } finally { mutating = false; controls(); }
  }
  function openUndo(entry, origin) {
    if (mutating || !entry.undo_available) return;
    cancel(previewRead); previewVersion++; mode = 'undo'; preview = null; review = entry; trigger = origin; title.textContent = 'Annulla la decisione';
    status.classList.remove('error'); status.textContent = 'Controlla la decisione prima di annullarla.';
    content.replaceChildren(make('p', reviewDescription(entry)), make('p', 'L’annullamento ripristina le registrazioni e i collegamenti conservati dalla decisione soltanto se lo stato attuale lo consente. Se i dati sono cambiati, il server blocca l’operazione.'));
    controls(); if (!dialog.open) dialog.showModal(); back.focus();
  }
  async function undoDecision() {
    if (mutating || mode !== 'undo' || !review?.undo_available) return;
    const id = review.id, version = previewVersion; mutating = true; controls();
    try {
      await ui.action(async () => {
        try { await ui.api(`/activities/duplicates/reviews/${encodeURIComponent(id)}/undo`, { method: 'POST', body: '{}' }); if (current(version)) { review = null; dialog.close(); } await refreshAfterDecision(); }
        catch (error) { if (current(version)) { review = null; status.classList.add('error'); status.textContent = error.status === 409 ? 'Lo stato delle attività è cambiato: annullamento non eseguito. Aggiorna la cronologia e verifica le registrazioni.' : `${error.message || 'Annullamento non completato.'} Aggiorna la cronologia prima di un nuovo tentativo.`; } }
      });
    } finally { mutating = false; controls(); }
  }
  function reviewDescription(entry) {
    const names = entry.summary || {}; return `${entry.action === 'merge' ? 'Unione delle fonti' : 'Attività mantenute distinte'} · ${names.activity_name || '#' + entry.activity_id} / ${names.other_name || '#' + entry.other_id}`;
  }
  async function loadHistory() {
    if (!visible()) return;
    cancel(historyRead); const version = ++historyVersion; historyStatus.textContent = 'Leggo le decisioni precedenti…'; history.setAttribute('aria-busy', 'true');
    try {
      const body = await read('/activities/duplicates/review-history?limit=20', 'history'); if (version !== historyVersion || !visible()) return;
      historyList.replaceChildren(); historyLimits.replaceChildren();
      for (const entry of body.reviews || []) {
        const row = make('article', null, 'duplicate-history-row'); row.dataset.status = entry.status; row.append(make('h3', reviewDescription(entry)), make('p', `Registrata: ${dateTime(entry.created_at)}${entry.status === 'undone' ? ` · Annullata: ${dateTime(entry.undone_at)}` : ''}`));
        if (entry.summary?.sources?.length) row.append(make('p', `Fonti: ${entry.summary.sources.map(sourceText).join(' · ')}`));
        if (entry.undo_available) { const control = button('Annulla questa decisione', () => openUndo(entry, control)); control.setAttribute('aria-haspopup', 'dialog'); row.append(control); }
        else row.append(make('p', entry.undo_reason || (entry.status === 'undone' ? 'Decisione già annullata.' : 'Annullamento non disponibile.'), 'caption'));
        historyList.append(row);
      }
      for (const line of body.limits || []) historyLimits.append(make('p', line, 'caption'));
      historyStatus.textContent = body.reviews?.length ? 'Le decisioni sono mostrate dalla più recente. Aprile prima di annullarle.' : 'Nessuna decisione precedente. Aprire un confronto non modifica lo storico.';
    } catch (error) { if (version === historyVersion && visible()) historyStatus.textContent = error.name === 'AbortError' ? 'La cronologia richiede troppo tempo. Riprova con Aggiorna cronologia.' : 'Cronologia non disponibile. Riprova con Aggiorna cronologia.'; }
    finally { if (version === historyVersion) history.removeAttribute('aria-busy'); }
  }
  dialog.addEventListener('cancel', event => { if (mutating) event.preventDefault(); });
  dialog.addEventListener('close', () => { cancel(previewRead); previewVersion++; preview = null; review = null; dialog.removeAttribute('aria-busy'); if (visible()) (trigger?.isConnected ? trigger : historyTitle).focus(); });
  document.addEventListener('iron:pagechange', () => { cancel(previewRead); previewVersion++; preview = null; if (dialog.open) dialog.close(); cancel(historyRead); historyVersion++; history.removeAttribute('aria-busy'); if (visible()) loadHistory(); });
  document.addEventListener('iron:loaded', () => { if (dialog.open && !mutating) { cancel(previewRead); previewVersion++; preview = null; status.textContent = 'I dati sono stati aggiornati. Ricarica il confronto prima di decidere.'; dialog.removeAttribute('aria-busy'); controls(); } if (visible()) loadHistory(); else { cancel(historyRead); historyVersion++; history.removeAttribute('aria-busy'); } });
  window.addEventListener('pagehide', () => { cancel(previewRead); cancel(historyRead); previewVersion++; historyVersion++; });
  ui.openDuplicateReview = openPreview; ui.loadDuplicateReviews = loadHistory;
  if (visible()) loadHistory();
})();
