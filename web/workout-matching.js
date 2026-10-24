'use strict';
// Proposals come from the server activity window, beyond the client's first page.
// Selecting one
// changes the local feedback draft only; saving remains an explicit action.
(() => {
  const ui = window.IronCoachUI;
  if (!ui) return;
  const {el, button, number} = ui;
  const signed = value => value == null ? 'non disponibile' : `${value > 0 ? '+' : ''}${number(value, 1)}`;
  function create({root, select, form, onRefresh}) {
    let candidates = new Map();
    let currentActivityId = null;
    function markSelection() {
      for (const card of root.querySelectorAll('[data-match-activity]')) {
        const chosen = select.value === card.dataset.matchActivity;
        card.classList.toggle('selected', chosen);
        card.querySelector('button')?.setAttribute('aria-pressed', String(chosen));
      }
      const chosen = candidates.get(select.value);
      select.dataset.matchToken = chosen?.match_token || '';
    }
    select.addEventListener('change', () => {
      form.dataset.dirty = 'true';
      markSelection();
    });
    function reset() {
      candidates = new Map(); currentActivityId = null;
      select.replaceChildren(el('option', 'Solo feedback manuale')); select.firstChild.value = '';
      delete select.dataset.matchToken;
      root.replaceChildren(); root.removeAttribute('aria-busy');
    }
    function loading() {
      candidates = new Map(); delete select.dataset.matchToken;
      root.setAttribute('aria-busy', 'true');
      root.replaceChildren(el('p', 'Cerco le attività vicine a questa seduta…', 'caption'));
    }
    function unavailable(retry) {
      root.removeAttribute('aria-busy');
      root.replaceChildren(el('p', 'Non è stato possibile controllare gli abbinamenti. Puoi salvare il feedback manuale oppure riprovare.', 'caption'));
      if (retry) root.append(button('Ricontrolla attività', retry, 'outline'));
    }
    function saved(activityId) {
      candidates = new Map(); root.removeAttribute('aria-busy'); delete select.dataset.matchToken;
      select.replaceChildren(el('option', 'Solo feedback manuale')); select.firstChild.value = '';
      if (activityId != null) {
        const option = el('option', 'Attività abbinata al feedback salvato'); option.value = String(activityId); select.append(option); select.value = String(activityId);
      }
      root.replaceChildren(el('p', 'Feedback salvato. Per cambiare o riconfermare l’abbinamento, rileggi le proposte aggiornate.', 'caption'));
      if (onRefresh) root.append(button('Ricontrolla proposte', onRefresh, 'text-button'));
    }
    function render(value, {activityId = null, preserveSelection = false} = {}) {
      const selected = preserveSelection ? select.value : activityId == null ? '' : String(activityId);
      currentActivityId = activityId == null ? null : String(activityId);
      candidates = new Map((value?.items || []).map(item => [String(item.id), item]));
      select.replaceChildren(el('option', 'Solo feedback manuale')); select.firstChild.value = '';
      for (const item of candidates.values()) {
        const current = item.current === true || String(item.id) === currentActivityId;
        if (item.available !== true && !current) continue;
        const option = el('option', `${item.activity_date || 'Data non disponibile'} · ${item.name || 'Attività'} · ${number(item.duration_min, 1)} min${current ? ' · abbinamento salvato' : ''}`);
        option.value = String(item.id); select.append(option);
      }
      // Do not silently replace a draft selection when data arrives late.
      if (selected && ![...select.options].some(option => option.value === selected)) {
        const option = el('option', 'Attività della bozza · ricontrolla prima di salvare'); option.value = selected; select.append(option);
      }
      select.value = selected;
      root.replaceChildren(el('h4', 'Quale attività hai svolto?'), el('p', 'Queste sono proposte per data e disciplina. Controlla i dettagli: nessun abbinamento viene scelto automaticamente.', 'caption'));
      root.removeAttribute('aria-busy');
      if (value?.window) root.append(el('p', `${value.window.start} — ${value.window.end} · ${value.window.timezone || 'Europe/Rome'}`, 'caption'));
      const list = el('div', null, 'workout-match-list');
      for (const item of candidates.values()) {
        const current = item.current === true || String(item.id) === currentActivityId;
        const card = el('article', null, 'workout-match-card'); card.dataset.matchActivity = String(item.id);
        card.append(el('h5', item.name || 'Attività senza nome'), el('p', `${item.activity_date || 'Data non disponibile'} · ${ui.sourceLabel?.(item.source) || item.source || 'Fonte non disponibile'} · ${number(item.duration_min, 1)} min`, 'caption'));
        const days = item.day_delta === 0 ? 'Stesso giorno' : `${signed(item.day_delta)} giorni rispetto al piano`;
        card.append(el('p', `${days} · Differenza durata: ${signed(item.duration_delta_min)} min`, 'caption'));
        if (current) card.append(el('p', 'Abbinamento già salvato', 'badge'));
        if (item.duplicate_warning) card.append(el('p', typeof item.duplicate_warning === 'string' ? item.duplicate_warning : 'Possibile duplicato da verificare nel diario.', 'warning'));
        if (item.available !== true) card.append(el('p', item.unavailable_reason || 'Attività non disponibile per un nuovo abbinamento.', 'caption'));
        const choose = button('Usa questa attività', () => {
          if (item.available !== true || !item.match_token) return;
          select.value = String(item.id); form.dataset.dirty = 'true'; markSelection();
          select.dispatchEvent(new Event('input', {bubbles: true}));
        }, 'outline');
        choose.disabled = item.available !== true || !item.match_token;
        if (choose.disabled) delete choose.dataset.busyDisabled;
        choose.setAttribute('aria-label', `Usa questa attività: ${item.name || 'senza nome'} del ${item.activity_date || 'giorno non disponibile'}`);
        card.append(choose); list.append(card);
      }
      if (!candidates.size) list.append(el('p', 'Nessuna attività disponibile nella finestra di ricerca. Il feedback manuale resta disponibile.', 'empty-state'));
      root.append(list);
      if (selected && !candidates.get(selected)?.match_token) root.append(el('p', 'L’attività selezionata non è confermabile con queste proposte. Scegli un’attività disponibile oppure il feedback manuale.', 'warning'));
      if (value?.counts?.truncated) root.append(el('p', `Sono mostrate ${number(value.counts.returned)} proposte su ${number(value.counts.total)} attività considerate. La ricerca ha raggiunto il limite; controlla il diario prima di scegliere.`, 'warning'));
      for (const limit of value?.limits || []) root.append(el('p', limit, 'caption'));
      if (onRefresh) root.append(button('Ricontrolla proposte', onRefresh, 'text-button'));
      markSelection();
    }
    return {reset, loading, unavailable, saved, render, tokenFor: id => candidates.get(String(id))?.match_token || null};
  }
  window.IronWorkoutMatching = {create};
})();
