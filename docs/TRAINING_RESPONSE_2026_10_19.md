# Il lavoro e i giorni dopo — verifica del 19 ottobre 2026

Il Profilo atleta collega il carico delle giornate registrate alle variazioni di HRV, frequenza cardiaca a riposo e sonno tra due date. Si possono scegliere un intervallo di uno o due giorni, la metrica e la fonte. Il confronto è descrittivo: non misura un effetto causale, non genera un punteggio di salute e non modifica il piano.

## Dati e calcoli

`GET /dashboard/training-response?as_of=YYYY-MM-DD&days=84&lag=1` legge esclusivamente la sessione dell’atleta corrente. Accetta da 28 a 180 giornate e una distanza di uno o due giorni. La data limite segue Europe/Rome, dal 1970 a oggi.

Le giornate di attività terminano alla data limite meno il ritardo scelto. Le letture abbinate sono quelle datate nel giorno dell’attività e nel giorno successivo selezionato. Non esistono orari verificati per molte misure wellness: queste date non dimostrano una rilevazione prima e dopo l’esercizio. La lettura più recente può appartenere a una giornata ancora in corso.

Il carico giornaliero richiede un valore registrato finito per ogni attività di quel giorno. L’osservazione Intervals prevale anche quando indica un dato mancante; nessuna FTP predefinita viene usata per stimare il carico. Uno zero realmente registrato resta zero. Una giornata senza attività registrate non viene trasformata in riposo o carico nullo.

Le coppie richiedono due letture utilizzabili della stessa fonte nell’app. Questo non certifica identico dispositivo, protocollo o orario. HRV e FC a riposo non positive, valori non finiti, sonno fuori intervallo e osservazioni contrassegnate come stime locali non entrano nei confronti. Le stime e gli zeri esclusi rimangono consultabili; i valori non rappresentabili sono segnalati e non vengono serializzati come Infinity o NaN.

Giornate con possibili doppioni non risolti sono escluse esplicitamente da questo confronto, comprese corrispondenze approssimative già da rivedere e controparti fuori periodo. Le scelte di mantenere attività distinte vengono rispettate. Nessun record viene unito, cancellato o rimosso dai totali delle altre viste.

Per ogni metrica, fonte e ritardo, la mediana dei carichi delle coppie utilizzabili divide i gruppi «fino alla mediana» e «sopra la mediana». Le parità restano nello stesso gruppo. Si mostrano variazione mediana, quartili centrali e numerosità. La differenza tra le mediane viene mostrata solo con almeno cinque coppie per gruppo; questa soglia di visualizzazione non è una validazione statistica o clinica. I colori distinguono i gruppi di carico, senza indicare un risultato favorevole o un carico ottimale.

Le coppie possono condividere osservazioni e non sono indipendenti. A due giorni si riportano anche le attività del giorno intermedio. Carichi di sport o fornitori diversi possono usare metodi diversi; sonno, vita quotidiana, tempi di misura e altri fattori non vengono isolati. La data limite seleziona osservazioni oggi archiviate: non ricostruisce quali dati fossero disponibili allora.

## Interazione e stabilità

Il grafico a dispersione espone i valori del campione selezionato tramite frecce, Home/Fine e comandi precedente/successivo. Lo zero ha una linea esplicita. La tabella conserva date, letture, fonte, carico e motivi di esclusione. La copertura è apribile, così il grafico resta centrale nella pagina.

Letture lente, errori, cambi di data, metrica, fonte e ritardo non permettono a una risposta obsoleta di sostituire quella corrente. La fonte iniziale privilegia le coppie disponibili; le scelte successive vengono conservate quando ancora valide. Scale numeriche non rappresentabili sospendono il solo grafico e mantengono la tabella.

Le query e i controlli hanno limiti espliciti; un troncamento sospende il confronto e segnala conteggi parziali. Il conteggio delle decisioni sui duplicati usa un indice per gruppo anziché confrontare ogni gruppo con tutte le decisioni. Su 3.000 gruppi sintetici il solo helper è passato da 1.601 a 108 ms, con risultati invariati: la misura non comprende HTTP o frontend e non è una promessa di produzione.

## Evidenze

- Suite completa: 847 test backend e 202 frontend superati; 21 test dedicati ripetuti dopo l’affinamento del grafico. Resta un avviso preesistente Starlette/httpx.
- Dieci risposte API isolate passate al frontend reale in JSDOM: selezioni, ritardi, tastiera, dati vuoti, estremi, troncati e stimati verificati. Tutte le letture SQL sono SELECT; database sintetico e storage invariati.
- Browser reale a 375, 745 e 1440 px: nessun overflow della pagina o della sezione; tabella ampia confinata al proprio scorrimento. Verificati collegamento rapido, tastiera, cambio metrica e ritardo; dimensioni temporanee ripristinate e nessun errore JavaScript rilevato.
- Server locale riavviato dopo un backup: nove letture API, inclusi entrambi i ritardi, e 12 asset verificati rispetto ai sorgenti. Le 25 tabelle del database conservano lo stesso contenuto precedente al riavvio e alla navigazione.

Le evidenze locali sono in `.qa/goal-response/`, incluse firme dei sorgenti, log, fixture sintetiche, confronto delle prestazioni e immagini del browser. Database, immagini e artefatti di verifica non appartengono al pacchetto sorgenti.

L’efficacia per decisioni di allenamento e benessere richiede ulteriori verifiche longitudinali e riscontro degli atleti. Questa funzione offre un’esplorazione trasparente dello storico, senza presentare associazioni come prescrizioni validate.
