# Coach, sincronizzazione e disponibilità wellness — 18 ottobre 2026

## Riscontro sui dati reali

Ispezione in sola lettura di 31 giorni wellness e 22 attività tramite la connessione esistente, senza stampare chiavi, nomi o risposte integrali. Nessun valore di Body Battery o stress era presente, neppure nei campi personalizzati annidati. `ctlLoad` e `atlLoad` erano presenti in tutti i giorni esaminati. Il dislivello era disponibile per 9 attività su 22.

La sincronizzazione reale successiva è terminata con `success`: 36 giorni wellness importati, zero nuove attività, zero errori. La risposta locale mostra 28 osservazioni visibili per ciascun nuovo campo di carico giornaliero. Body Battery e stress rimangono senza osservazioni, coerentemente con la risposta remota.

## Modifiche

- `POST /sync/refresh` sincronizza i provider collegati: Intervals attività e wellness negli ultimi 35 giorni; Strava consultazione separata a 90 giorni. Un job già attivo viene riutilizzato. La risposta contiene solo ID dei job e messaggi, senza credenziali.
- Il pulsante Aggiorna dati attende i job, rilegge i dati e distingue successo, esito parziale e sincronizzazione ancora in corso. Un errore non viene presentato come successo; il ricaricamento locale viene tentato anche in caso di errore.
- Il Coach distingue `training_assessment`, sul lavoro registrato, dal precedente voto sull'esecuzione delle sedute. Le attività non abbinate possono contribuire al primo; non diventano esecuzioni confermate per il secondo.
- Le card settimanali espongono giudizio e quattro confronti sugli stessi giorni della settimana. Una mediana richiede almeno due periodi con misure complete e attività. Oltre +30% il volume/carico è elevato rispetto al solito; sotto −30% è ridotto; nell'intervallo è coerente con lo storico. Sono euristiche descrittive, non soglie cliniche o una certificazione di carico ottimale. TSB osservato ≤ −25/−30 mantiene le indicazioni prudenziali già usate dal Coach. Le stime locali non attivano questi segnali come osservazioni reali.
- Per un dislivello incompleto si conserva `elevation_m = null`, aggiungendo totale noto e conteggio delle attività senza misura. La Home mostra `≥ totale noto` e «parziale»; non sostituisce gli assenti con zero. La sincronizzazione conserva correttamente uno zero esplicito.
- Le card della condizione fisica aprono solo giudizio e indicazione operativa, senza fonti, grafici o tabelle. Provenienza e dati restano conservati nel backend. La valutazione riguarda il dato osservato e lo storico personale, senza certificare il recupero.
- La proposta quotidiana esplicita obiettivo, esecuzione e decisione pratica, mantenendo i blocchi conservativi già presenti nel motore.

## Campi wellness

`BodyBatteryMin` e `BodyBatteryMax` sono supportati sia al livello principale sia in `customFields`. Restano minimi/massimi del giorno: non sostituiscono Body Battery attuale e non alimentano il modello come un valore attuale. `stress` Intervals rimane distinto dallo stress Garmin e dallo stress percepito manuale su 100. `ctlLoad` e `atlLoad` sono importati come carichi quotidiani dei rispettivi modelli; non vengono contati una seconda volta come segnale di recupero.

Configurazione ufficiale Body Battery: [istruzioni del manutentore Intervals.icu](https://forum.intervals.icu/t/solved-hrv-tracking-via-garmin-body-battery/36154/5). Distinzione stress soggettivo/Garmin: [discussione Intervals.icu](https://forum.intervals.icu/t/fill-in-more-wellness-data-read-from-garmin/56055). Schema verificato: [API ufficiale](https://intervals.icu/api/v1/docs).

## Verifica

374 test backend e 72 test frontend superati. Inclusi: soglie ai limiti, dati nulli/zeri, assenza di storico, stime locali, backup e API complete, importazioni dei nuovi campi, sincronizzazione di entrambi i provider, riuso dei job, protezione dell'origine e dei segreti, esiti parziali, dettagli senza fonti e confronti settimanali.

Server aggiornato e lasciato attivo su `127.0.0.1:8000`. La verifica reale API conferma un giudizio sul carico, quattro periodi precedenti, 492 m di dislivello noto con cinque attività prive della misura e i nuovi campi quotidiani. Il controllo resta distinto da un collaudo visivo nel browser.
