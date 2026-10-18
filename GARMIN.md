# Garmin: stato effettivo e contratto di integrazione

Verifica delle fonti ufficiali: 17 ottobre 2026.

## Funzioni disponibili

- Struttura allenamenti indipendente dal provider: sport, nome, data, fasi a tempo/distanza, ripetizioni annidate e obiettivi con unità esplicite.
- Normalizzazione delle sedute esistenti; validazione di durate, distanze, target mancanti, numeri non finiti e limiti delle ripetizioni.
- Registro persistente dei tentativi di invio, con una riga per allenamento/revisione. Un secondo click riutilizza la stessa riga. Modificare la struttura o la data genera una nuova revisione e conserva lo storico.
- Esportazione JSON scaricabile. **È il formato aperto di Ironman Coach, non un FIT/TCX né un file importabile da Garmin.**
- Esportazione binaria FIT per bici e corsa tramite SDK Garmin ufficiale, con controllo CRC e prove software di decodifica. **L'importazione e l'esecuzione sui dispositivi Forerunner 955 ed Edge 530 non sono ancora state provate.**
- Stato reale e trasparente: `official_access_required`, `not_sent`, `blocked`; `remote_published` resta `false`.

Non sono attivi collegamento account, OAuth, importazione cloud, invio a Garmin Connect, sincronizzazione con il dispositivo o conferma di presenza sul dispositivo. Il registro locale non è una coda remota e non effettua tentativi automatici successivi.

## Cosa confermano le fonti

La [Training API](https://developer.garmin.com/gc-developer-program/training-api/) pubblica allenamenti e piani nel calendario Garmin Connect. Con il consenso dell'utente, Connect gestisce il collegamento con i dispositivi compatibili; l'utente deve sincronizzarli. Una pubblicazione riuscita su Connect non prova da sola l'arrivo sul dispositivo.

Le [FAQ del programma](https://developer.garmin.com/gc-developer-program/program-faq/) indicano accesso per uso aziendale soggetto ad approvazione, documentazione nel portale sviluppatori e OAuth 2.0. Le pagine pubbliche consultate non forniscono il contratto tecnico sufficiente a implementare chiamate, scope, callback o payload di pubblicazione: il software non li inventa.

L'[Activity API](https://developer.garmin.com/gc-developer-program/activity-api/) fornisce dati registrati durante le attività e file dettagliati, dopo consenso e sincronizzazione con Connect. La [Health API](https://developer.garmin.com/gc-developer-program/health-api/) espone dati giornalieri disponibili e autorizzati. Disponibilità delle metriche e condizioni commerciali vanno verificate per il progetto approvato.

La [specifica ufficiale dei file Workout FIT](https://developer.garmin.com/fit/articles/file-types/workout.html), l'[esempio Garmin EncodeWorkout](https://github.com/garmin/fit-java-sdk/blob/main/src/main/java/com/garmin/fit/examples/EncodeWorkout.java) e l'[Encoder Python ufficiale](https://github.com/garmin/fit-python-sdk#encoder) consentono l'esportazione locale senza accesso al programma cloud. La versione utilizzata è `garmin-fit-sdk 21.218.0`. Il formato file e le API cloud sono percorsi distinti.

## Download FIT: comportamento e limiti

Il pulsante di download restituisce un vero file di allenamento FIT, non un'attività completata. Il file contiene nell'ordine File Id, Workout e Workout Step. Non pubblica una seduta nel calendario Connect e non invia nulla a orologio/ciclocomputer.

- Sono supportate bici e corsa. Nuoto e forza vengono rifiutati fino a quando saranno disponibili strutture specifiche e prove adeguate.
- Le ripetizioni sono espanse in una sequenza equivalente, con un massimo di 50 fasi. Oltre il limite l'esportazione viene bloccata.
- Tempo e distanza usano i campi raw ufficiali in millisecondi e centimetri. Watt e bpm assoluti applicano gli offset prescritti di 1000 e 100; percentuali FTP/FC massima restano relative. Il passo in s/km viene convertito in velocità m/s con intervallo invertito.
- RPE è conservato nelle note e convertito esplicitamente in target aperto. **Non produce un allarme RPE e non viene tradotto in una frequenza cardiaca inventata.** L'avviso è disponibile prima del download e negli header della risposta.
- Potenza, frequenza cardiaca e cadenza frazionarie vengono arrotondate all'unità con avviso. Valori sotto la precisione rappresentabile vengono rifiutati.
- Il nome è limitato a 63 byte UTF-8; le note per fase a 180 byte. Il nome non influisce sullo scopo fisiologico della seduta.
- Ogni esportazione controlla l'integrità del binario generato. Le prove software verificano anche il significato dei target decodificati, ma non sostituiscono un test sull'hardware.

Il trasferimento manuale è un passaggio separato da verificare seguendo il manuale del dispositivo. L'app non scrive via USB, non modifica file presenti sui dispositivi e non visualizza una conferma di caricamento sul dispositivo.

## Dipendenze per completare l'integrazione reale

1. Approvazione del progetto al Garmin Connect Developer Program, con Training, Activity e Health API necessarie.
2. Documentazione tecnica approvata: autorizzazione OAuth 2.0, token, scope, revoca, endpoint, contratti, limiti ed errori.
3. Credenziali server protette; consenso, callback HTTPS e gestione sicura dei token. Non inserire password Garmin nella web app.
4. Implementazione di adapter, creazione/aggiornamento/cancellazione delle sedute secondo il contratto effettivo, codici di errore e limiti di chiamata.
5. Test di sport, ripetizioni, target e calendario sui modelli indicati, Forerunner 955 ed Edge 530. La compatibilità resta non verificata fino alla prova reale.
6. Prova completa: pubblicazione Connect, sincronizzazione reale, esecuzione, importazione, deduplicazione e confronto programmato/eseguito. Non visualizzare una conferma sul dispositivo se l'API non la fornisce.

L'interfaccia server corrente è il punto da sostituire con l'adapter ufficiale; nessuna variabile d'ambiente da sola lo abilita e non è presente un falso pulsante di login.

## API locali

| Metodo e percorso | Risultato |
| --- | --- |
| `GET /integrations/garmin/status` | Stato, capacità effettive, dipendenze e link ufficiali |
| `GET /workouts/{id}/garmin/validate` | Struttura normalizzata, errori, avvisi, hash revisione e disponibilità/conversioni dell'export FIT |
| `POST /workouts/{id}/garmin/send` | Tentativo locale persistito, sempre bloccato senza pubblicazione remota |
| `GET /workouts/{id}/garmin/status` | Tentativo della revisione corrente e storico |
| `GET /workouts/{id}/export.json` | Download JSON con validazione inclusa e indicazione non importabile su Garmin |
| `GET /workouts/{id}/export.fit` | Download FIT bici/corsa; 422 per struttura non supportata, 503 se manca l'SDK |

Il codice HTTP 200 di `/send` indica che la richiesta locale è stata gestita. Il campo `status: blocked`, la motivazione e `remote_published: false` sono il risultato dell'operazione: la UI deve leggerli e non mostrare un successo di pubblicazione.

## Schema aperto di allenamento

```json
{
  "schema_version": "ironman-coach.workout.v1",
  "name": "Bici con ripetute",
  "sport": "cycling",
  "date": "2026-10-18",
  "steps": [
    {
      "type": "warmup",
      "duration": {"type": "time", "value": 600, "unit": "s"},
      "target": {"type": "rpe", "value": 2, "unit": "score"}
    },
    {
      "type": "repeat",
      "repeat_count": 3,
      "steps": [
        {
          "type": "interval",
          "duration": {"type": "time", "value": 240, "unit": "s"},
          "target": {"type": "power", "min": 180, "max": 195, "unit": "W"}
        },
        {
          "type": "recovery",
          "duration": {"type": "time", "value": 180, "unit": "s"},
          "target": {"type": "open"}
        }
      ]
    }
  ]
}
```

Target ammessi: potenza (`W`, `percent_ftp`), frequenza cardiaca (`bpm`, `percent_max_hr`), passo (`s/km`, `s/100m`), RPE (`score`, scala 1–10), cadenza (`rpm`) o `open` esplicito. RPE resta un'indicazione all'atleta: non viene presentato come obiettivo supportato da Garmin. I limiti tecnici del formato non sono una prescrizione fisiologica.

Il contenuto canonico e la data determinano l'hash della revisione. Il testo motivazionale e gli indicatori di recupero non cambiano l'identità tecnica della seduta. Nessun target mancante viene dedotto automaticamente.

## Verifica

`tests/test_garmin_delivery.py` usa un database SQLite in memoria con due sole tabelle. Copre revisioni/idempotenza, stato bloccato, esportazione, 404, unità, target mancanti, payload corrotti e limiti numerici/strutturali. Non usa l'archivio dell'atleta né un account Garmin.

`tests/test_fit_export.py` verifica header, lunghezza, CRC e rilevamento di alterazioni, sequenza dei messaggi, file type Workout, identità stabile, durata/distanza, watt/bpm assoluti e relativi, velocità derivata dal passo, RPE aperto, ordine delle ripetizioni, limiti e download HTTP senza invio remoto.
