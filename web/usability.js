'use strict';
// UI-only guidance uses a minimal snapshot, never credentials or health values.
(() => {
  const card = document.getElementById('getting-started');
  const steps = document.getElementById('getting-started-steps');
  const optionalGoal = document.getElementById('getting-started-goal');
  const dismiss = document.getElementById('dismiss-getting-started');
  const heading = document.getElementById('page-title');
  const dismissalKey = 'iron-coach:hide-next-steps:v1';
  let dismissed = false;
  let renderedSetup = null;
  try { dismissed = sessionStorage.getItem(dismissalKey) === '1'; } catch (_) { /* Private sessions may disable storage. */ }

  function step(title, description, action, page) {
    const row = document.createElement('div'); row.className = 'getting-started-step';
    const copy = document.createElement('div');
    const name = document.createElement('h3'); name.textContent = title;
    const detail = document.createElement('p'); detail.textContent = description;
    const link = document.createElement('a'); link.href = `#${page}`; link.className = 'outline button-link'; link.textContent = action;
    copy.append(name, detail); row.append(copy, link); return row;
  }

  function focusVisibleContent() {
    if (document.querySelector('dialog[open]')) return;
    const conditionHeading = document.querySelector('#today .section-heading h2');
    const target = conditionHeading && !conditionHeading.closest('[hidden]') ? conditionHeading : heading;
    if (target) { target.tabIndex = -1; target.focus(); }
  }

  function showNextSteps(snapshot) {
    if (!card || !steps || !snapshot || dismissed) return;
    const focused = document.activeElement;
    const focusWasInside = !card.closest('[hidden]') && card.contains(focused);
    const focusedHref = focused?.closest('a')?.getAttribute('href');
    const ready = snapshot.hasProfile === true && snapshot.hasActivities === true;
    const missingProfile = snapshot.hasProfile === false;
    const missingActivities = snapshot.hasActivities === false;
    if (ready || (!missingProfile && !missingActivities)) {
      card.hidden = true;
      if (focusWasInside) focusVisibleContent();
      return;
    }
    const signature = JSON.stringify([missingProfile, missingActivities, snapshot.intervalsConfigured === true, snapshot.hasGoals === false]);
    if (signature === renderedSetup) { card.hidden = false; return; }
    renderedSetup = signature;
    steps.replaceChildren();
    if (missingProfile) steps.append(step('Dai al piano i tuoi tempi', 'Esperienza e disponibilità aiutano a scegliere sedute che entrano nella tua settimana.', 'Completa il profilo', 'athlete'));
    if (missingActivities) {
      steps.append(snapshot.intervalsConfigured === true
        ? step('Porta qui i tuoi allenamenti', 'Intervals.icu è configurato. Da Connessioni puoi avviare e controllare la sincronizzazione.', 'Apri la sincronizzazione', 'connections')
        : step('Aggiungi le prime attività', 'Collega Intervals.icu oppure importa un archivio JSON o FIT per leggere il lavoro svolto.', 'Scegli come aggiungerle', 'connections'));
    }
    if (optionalGoal) optionalGoal.hidden = snapshot.hasGoals !== false;
    card.hidden = false;
    // A refresh can finish a step while its link is focused. Keep the cursor on
    // its replacement, or on the next available action if that step is gone.
    if (focusWasInside && (!focused.isConnected || focused.closest('[hidden]'))) {
      const actions = [...steps.querySelectorAll('a')];
      const next = actions.find(link => link.getAttribute('href') === focusedHref) || actions[0];
      if (next && !document.querySelector('dialog[open]')) next.focus();
      else focusVisibleContent();
    }
  }

  if (dismiss) dismiss.addEventListener('click', () => {
    dismissed = true; card.hidden = true;
    try { sessionStorage.setItem(dismissalKey, '1'); } catch (_) { /* Dismissal still works for the current page. */ }
    // The control disappears, so keep keyboard users at a visible landmark.
    focusVisibleContent();
  });

  function syncNavigation(id, userInitiated) {
    const target = document.getElementById(id);
    if (!target?.classList.contains('page')) return;
    let sectionName = target.getAttribute('aria-label');
    document.querySelectorAll('nav [data-page]').forEach(control => {
      control.setAttribute('aria-controls', control.dataset.page);
      if (control.dataset.page === id) {
        control.setAttribute('aria-current', 'page');
        sectionName = control.querySelector('span')?.textContent.trim() || sectionName;
      }
      else control.removeAttribute('aria-current');
    });
    document.title = sectionName ? `${sectionName} · Iron Coach` : 'Iron Coach';
    // Loading data must never move the cursor out of an unfinished form.
    if (userInitiated === true && heading && !document.querySelector('dialog[open]')) heading.focus();
  }

  document.addEventListener('iron:loaded', event => showNextSteps(event.detail));
  document.addEventListener('iron:pagechange', event => syncNavigation(event.detail?.id, event.detail?.userInitiated));
  const visiblePage = document.querySelector('.page:not([hidden])');
  if (visiblePage) syncNavigation(visiblePage.id, false);
  // A fast initial load may finish before this deferred script is evaluated.
  if (window.ironViewSnapshot) showNextSteps(window.ironViewSnapshot);
})();
