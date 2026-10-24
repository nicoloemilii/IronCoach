# Stato delle richieste e lavoro residuo — 23 ottobre 2026

L'obiettivo generale resta controllare funzionamento e stabilità dell'intera app, introdurre metriche e funzioni utili e migliorare estetica ed esperienza fino a ottenere un prodotto distintivo per sport e benessere. Le prove seguenti sostengono comportamenti specifici; non dimostrano da sole il raggiungimento di quell'obiettivo generale.

La revisione attuale verifica 1.261 test backend: 1.258 nella suite completa e tre nuovi casi inclusi nei 170 controlli mirati finali. I test frontend sono 326. L’analisi esplicita delle potenze storiche è verificata anche su HTTP/JSDOM isolato; 27 tabelle originali conservate e solo due tabelle derivate modificate. Il nuovo flusso delle potenze è verificato con HTML e JavaScript di produzione su HTTP isolato e sul server personale in sola lettura. Il browser controllabile continua a restituire inventari vuoti: una richiesta di apertura in coda non equivale a una pagina visualizzata e ispezionata.

## Richieste annotate

| Richiesta | Stato verificabile | Evidenza attuale e limite |
| --- | --- | --- |
| Grafici più intuitivi e completi, colori coerenti anche per valori massimi | Implementazione e prove funzionali presenti | `web/app.js`, `web/charts.js`, `web/metric-status.js`; test app/charts. Riferimenti, unità, valori e colori espliciti. Il nuovo layout richiede collaudo visivo. |
| Badge Salute e selezione delle date sopra il grafico | Implementazione presente | `web/app.js`, `web/style.css`; date conservate nei refresh e grafici limitati al periodo nei test app. Posizionamento effettivo da riconfermare nel browser attuale. |
| Rimuovere card di contesto, Body Battery minimo/massimo e stress manuale | Implementato nel rendering | `renderHealth` esclude i tipi richiesti e le card di solo contesto. I dati originali restano conservati; non viene inventato stress del dispositivo. |
| Vedere le attività con dati, percorso e serie, come nei servizi sportivi | Flusso disponibile sui dati ricevuti | Dettaglio, GPS osservato, serie originali, unità e interruzioni; test activity-detail, activity-effort e HTTP. Percorso locale senza cartografia esterna; nessuna equivalenza completa con tutte le funzioni Garmin/Strava dichiarata. |
| Unire Obiettivi e piano | Implementato | `mergePlanSections` e schede interne in `web/app.js`, navigazione coperta dai test app/usability. |
| Pianificare manualmente e creare esercizi da seguire dal vivo | Implementato e verificato su dati isolati | `manual-training.js`, servizi e API training; creazione, fasi, esercizi, serie, pesi, timer e progresso persistito. Non genera automaticamente attività svolte o consegne Garmin. |
| Sviluppare la sezione palestra | Funzioni disponibili | Libreria esercizi, modelli, composizione della scheda e ripresa del progresso; `tests/test_manual_training.py` e test manual-training. Usabilità continuativa da provare con atleti. |
| Profilo con tutti i dati e record | Copertura parziale rispetto a una replica dei servizi citati | Mostra dati disponibili, provenienza, totali, copertura e record di attività intere su tre periodi. Le potenze per durata sono disponibili nella singola attività e nello storico, con copertura, origine, analisi esplicita e cancellazione. Restano migliori tempi su distanze standard, record di vasca e dati che il provider non consegna. |
| Peso in Home e storico in Salute | Implementato | `renderHome`, `renderHealth`, servizio health e test app/profile; osservazioni assenti restano mancanti. |
| IMC dal peso e dal profilo, con sesso ed età | Implementato con limiti espliciti | `body_metrics.py`, `tests/test_body_metrics.py`: peso osservato, altezza dichiarata ed età alla pesata. Formula adulta indipendente dal sesso; nessun percentile pediatrico inventato. |
| Sintesi settimanale compatta in alto nella Home | Implementata | `.home-week-brief`, `web/usability.js`, test app. La precedente sezione estesa non è riproposta come una seconda sintesi. |
| Colore nel dettaglio attività e nei record | Implementazione presente | Stili per disciplina, tipo di misura e card; test di struttura e dati disponibili. Il giudizio estetico complessivo resta da verificare visivamente. |
| Indietro sempre in alto a sinistra, senza sovrapposizioni | Regola applicata alle finestre esistenti | Header dei dialog e CSS comuni; ordine del pulsante verificato anche nel nuovo roundtrip HTTP. Nessuna prova visiva attuale di ogni dimensione e dialog. |
| Filtro Altro nel diario | Implementato | Categoria condivisa server/client e diario paginato; `test_activity_diary.py`, test activity-diary e HTTP reale sintetico. |
| Parlare con il Coach per cambiare giorni, fasce e pause di disciplina | Richieste previste implementate | `time_constraints.py`, API planning, test time-constraints e plan-coach. La frase «non voglio fare nuoto fino a gennaio» ha anteprima e applicazione controllata. L'interprete ha intenti circoscritti e non accetta ogni frase libera. |
| Aree di riferimento nei grafici | Disponibili quando giustificate dai dati | Fasce personali, provenienza delle soglie e riferimenti del periodo. Non viene dichiarata una zona ottimale universale per ogni metrica. |
| Avviare sul browser | Server verificato; collaudo visivo pendente | Server locale aggiornato e risorse confrontate byte per byte. L'inventario dello strumento browser è vuoto; apertura in coda, nessuna schermata attuale verificata. |
| Mostrare solo modifiche o diff | Soddisfatto nella consegna | Diff per ogni revisione, con controllo di applicabilità e hash. Nessuna riscrittura integrale dei file esistenti nella risposta. |

Le immagini che individuano regioni della pagina sono evidenze dell'interfaccia; non forniscono da sole ulteriori istruzioni da eseguire. I riferimenti sopra descrivono il codice corrente e i relativi test; i resoconti delle revisioni precedenti rimangono distinti dalle nuove verifiche.

## Blocchi ancora aperti

1. **Record e dati:** le potenze su finestre dello storico sono implementate con disponibilità, fonti, invalidazione e costo espliciti. Completare migliori tempi su distanze standard e record di vasca soltanto sui dati che li sostengono; verificare il recupero storico durante l’uso reale.
2. **Esperienza verificata:** collaudare tutti i flussi principali nel browser a schermi piccoli e grandi, incluse modifiche al piano, palestra, recupero, attività e record; poi verificare l'uso continuativo con atleti. I test funzionali sono necessari ma non sostituiscono questa prova.
3. **Integrazioni effettive:** completare e provare consegna ed esecuzione Garmin con accesso ufficiale e dispositivi autorizzati. L'export FIT verificato non dimostra il trasferimento cloud o l'esecuzione fisica.
4. **Valore del prodotto:** verificare con atleti che decisioni, dati e interazioni siano comprensibili e utili. Il numero di test o di metriche non dimostra da solo un risultato distintivo nel mercato.

Per una distribuzione commerciale si aggiungono i collaudi sul servizio effettivo descritti in [Commercial beta](COMMERCIAL_BETA.md): dominio, email, pagamenti, recupero operativo e carico reale. Questa revisione non pubblica o configura tali servizi.

Il lavoro residuo comprende quindi sviluppo locale e verifiche che dipendono da browser, accessi e uso reale. Una percentuale unica o una data di completamento del risultato generale non è ancora supportata dalle evidenze. Le prossime revisioni devono chiudere questi blocchi, evitando di equiparare una nuova funzione al completamento dell'obiettivo.
