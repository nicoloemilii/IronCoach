# Verifica Ironman Coach 1.3 — 18 ottobre 2026

La revisione aggiunge i flussi di obiettivo attivo, bilancio settimanale, anteprima delle modifiche, recupero personale, checklist per gara e backup. La presentazione usa colori semantici accompagnati da icona e testo.

## Risultati

| Controllo | Esito |
|---|---|
| Backend completo, SQLite isolato e provider simulati | 490 test superati dopo gli aggiornamenti del Coach |
| Frontend: regole, DOM e usabilità con dati sintetici | 116 test superati |
| Flussi integrati contro FastAPI locale in JSDOM | 6 completati, 124 richieste, zero errori HTTP/JavaScript |
| Contrasto di 15 coppie testo/sfondo degli stati cromatici | Minimo 5,30:1 |
| Sintassi di app.js, progress.js, metric-status.js, usability.js | Valida |
| Layout visivo in un browser reale | Non verificato |

La suite Python segnala un avviso di deprecazione del trasporto httpx di Starlette; non risultano test falliti.

## Interpretazione dei colori

- **Verde:** confronto favorevole con il riferimento disponibile. Per HRV, frequenza a riposo e sonno significa entro il proprio intervallo abituale, senza certificare salute o recupero.
- **Ambra:** scostamento da verificare, dati di misura dubbi o gap rispetto ai riferimenti della gara.
- **Rosso:** segnale delle regole del Coach che richiede di rivalutare recupero o gestione del piano.
- **Blu:** dato di contesto, stima locale o conferma personale.
- **Grigio:** dati insufficienti, datati, aggiornamento fallito o analisi non disponibile.

Il riferimento descrittivo usa almeno sette osservazioni precedenti della stessa fonte entro 28 giorni e i limiti Q1 − 1,5 × IQR / Q3 + 1,5 × IQR. Anche un valore HRV insolitamente alto è ambra. I trend separano le tre letture recenti dal periodo di riferimento; HRV e frequenza a riposo non positive non completano il numero minimo di osservazioni utilizzabili. Le osservazioni originali rimangono consultabili. CTL, ATL, volume e dislivello non sono migliori soltanto perché aumentano.

## Prova integrata

Server su `127.0.0.1:8766`, `IRON_AUTO_SYNC=0`, dati sintetici in `.qa/live-color-20261018`. Nessun ripristino dei dati personali e nessun accesso ad account reali.

1. Home: sette card e presenza di stati blu, verde, ambra e rosso coerenti con i dati preparati.
2. Salute: caricamento dei trend personali.
3. Coach: conferma esplicita del riposo e annullamento della conferma.
4. Calendario: obiettivo attivo, anteprima senza scritture e riduzione della seduta dopo conferma.
5. Obiettivi: salvataggio e rilettura delle note operative.
6. Connessioni: esportazione JSON, caricamento file, anteprima e ripristino esplicito del solo database sintetico.

Lo script `web/tests/live-smoke.cjs` richiede `IRON_QA_LIVE=1`, rifiuta origini diverse dalla porta di prova e verifica i nomi delle fixture prima delle modifiche. È escluso dalla suite ordinaria e richiede un server sintetico già predisposto. Le risorse esterne dei font sono sostituite localmente durante questa prova.

## Problemi corretti durante la verifica

- Allineata la lettura frontend di `resolution.status` al contratto reale: il riposo confermato non è più presentato come seduta non svolta.
- I valori conservati dopo un aggiornamento fallito perdono il colore favorevole e mostrano un messaggio locale; il retry riuscito ripristina la classificazione.
- Una stima di recupero relativa al giorno precedente resta grigia ed espone la data.
- Caricamenti o errori nelle analisi attività/gara non conservano il precedente bordo verde.
- Le letture HRV/FC non utilizzabili non completano artificialmente il numero minimo per un trend favorevole.
- Il modulo per modificare sedute abilita soltanto i campi pertinenti e usa il minimo di 15 minuti richiesto dall'API.
- Eliminare una gara attiva rimuove il collegamento e le checklist associate, senza rendere il backup non ripristinabile.

## Limiti

### Correzione dell'avvio web

Il successivo controllo ha verificato che nessun server rispondeva sulla porta 8000. Il launcher desktop puntava inoltre all'installazione separata `C:\IronmanCoach`, priva dei file web aggiornati. La versione del checkout è stata avviata in background su `127.0.0.1:8000` e lasciata attiva per l'utente. Pagina `/app`, CSS e quattro script rispondono HTTP 200. La radice richiesta da un browser reindirizza a `/app`; la richiesta JSON conserva lo stato API. Le risposte dell'interfaccia richiedono rivalidazione della cache.

Il nuovo `Apri_Iron_Coach.bat` nella cartella superiore richiama il launcher web corretto. `Avvia_Web.bat` verifica le dipendenze, riconosce un'istanza già attiva e conserva i messaggi di errore. Esecuzione reale del batch verificata con esito 0 senza avviare un secondo server. La suite completa aggiornata comprende 344 test backend superati.

Un tentativo successivo con il browser integrato è stato rifiutato dal controllo di autorizzazione dell'origine locale. Non sono stati utilizzati browser alternativi o aggiramenti. I controlli HTTP e i test restano distinti dal collaudo visivo.

JSDOM verifica comportamento e contratti, senza misurare il layout. Non è stato possibile verificare visivamente i formati 360/768/1440 pixel: l'approvazione automatica ha rifiutato l'avvio di Firefox con il motivo `Computer Use was not approved to use firefox`. Il contrasto delle coppie selezionate non dimostra da solo accessibilità completa.

Non sono stati collaudati account Strava/Intervals reali, invii Garmin cloud o esecuzione dei FIT su dispositivi fisici. Il server di prova è stato arrestato al termine.
