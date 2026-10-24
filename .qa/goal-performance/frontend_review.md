# Revisione finale del diario paginato

Revisione in sola lettura del codice congelato il 22 ottobre 2026. Nessun file di produzione modificato, nessun accesso a dati personali o provider. Verifica funzionale e di sorgente; non verifica visiva o con uno screen reader reale.

**Esito:** nessun problema bloccante rilevato nelle interazioni richieste. Un punto di hardening a bassa priorità riguarda soltanto una risposta che viola il contratto attuale del server.

- `web/activity-diary.js:31`: `has_more: true` con `next_cursor: ''` supera la validazione, ma `request('more')` termina senza richiesta. Il pulsante risultante sarebbe inerte. Il backend attuale genera un cursore non vuoto quando esistono altre righe; non è un caso prodotto dall'API conforme. Alla prossima modifica si può rifiutare esplicitamente il cursore vuoto. Nessuna correzione introdotta durante il congelamento.

I casi di concorrenza sono protetti: ogni filtro, aggiornamento o annullamento incrementa la generazione; le risposte e gli errori precedenti non raggiungono lo stato corrente. Il segnale del modulo è inoltrato al `fetch` dal wrapper `api`. Il debounce viene cancellato anche uscendo dal diario; riaprendolo, una lettura interrotta riparte dalla prima pagina. Il coalescing riguarda solo la prima lettura pendente senza pagina già disponibile.

L'aggiornamento globale non attende il diario e il suo `finally` richiama `activityDiary.render()` dopo aver ripristinato i controlli: un caricamento locale ancora pendente mantiene disabilitato il pulsante successivo. Un errore conserva righe e ambito precedenti, mostra che sono risultati vecchi e propone un retry della richiesta corretta; i cursori 409/422 richiedono la prima pagina. Cambiare filtro nasconde il vecchio pulsante successivo. Il caricamento di altre righe riusa i nodi esistenti, deduplica gli ID e sposta il focus solo se era rimasto sul controllo usato o sul body; la navigazione invalida la lettura prima che possa spostare il focus fuori sezione.

La cache contiene soltanto le pagine richieste. `openActivity` conserva il fallback `{id}` per record esterni alla cache; il totale onboarding usa `archive_total`, anche con un filtro senza risultati. La nota di successo avverte che lo storico può cambiare, senza promettere uno snapshot immutabile. Il modulo non esegue scritture né passa al vecchio endpoint completo in caso di errore.

Evidenze: 80/80 test mirati eseguiti durante l'implementazione; il log finale `frontend-final.log` riporta 286/286. `review_dom.json` documenta il bundle completo con HTTP locale su dati sintetici, 105 richieste GET, 17 risorse verificate e 27 tabelle conservate. Gli SHA-256 correnti di `web/activity-diary.js`, `web/app.js`, `web/index.html` e `web/style.css` coincidono con quelli di tale verifica HTTP. Nessun nuovo ciclo esteso di test eseguito per questa revisione.
