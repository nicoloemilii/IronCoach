# Potenze nello storico: risultati, copertura e analisi esplicita

Il Profilo atleta confronta le migliori potenze medie su 5, 15 e 30 secondi e su 1, 5, 10, 20 e 60 minuti. Ogni risultato indica attività, data, intervallo dall'inizio, fonte delle serie e data dell'analisi. Il pulsante apre l'attività originale. Il confronto distingue le discipline e offre tutto lo storico, gli ultimi 90 giorni e gli ultimi 28 giorni.

La sezione legge soltanto risultati già calcolati. Aprirla, cambiare filtro o controllare l'avanzamento non scarica serie dal provider e non calcola nuove curve. Le attività non analizzate, obsolete, prive di potenza o con fonte ambigua restano esplicite nella copertura. Un archivio interamente indicizzato può comunque avere dati incompleti: non è una prova di record assoluto o di miglioramento fisiologico.

## Comandi e provenienza

- **Analizza dati disponibili** esamina, su richiesta, tutte le attività già presenti e iniziate entro l'avvio dell'analisi. Usa le serie conservate localmente e risultati derivati ancora validi; non richiede credenziali e non effettua richieste al provider.
- **Recupera serie Intervals** richiede una connessione già configurata. Può recuperare riepilogo e serie tempo/potenza mancanti; la scelta di aggiornare forza il recupero. Nessuna credenziale compare nel browser. Un risultato remoto conserva impronta e data del recupero, senza salvare i campioni originali nelle nuove tabelle.
- **Interrompi** evita di iniziare la prossima attività. Il calcolo o la richiesta dell'attività corrente può terminare. Cambiare pagina sospende il controllo nel browser, mentre il lavoro prosegue sul server.

L'elenco dei candidati è fissato all'avvio, comprende tutte le discipline e non viene tagliato alle prime attività dell'archivio. Il totale dell'avanzamento si riferisce a questo elenco; i filtri della tabella riguardano soltanto i risultati. Attività eliminate o modificate durante il calcolo vengono rilevate prima della scrittura e non ricevono risultati da una versione precedente.

Le finestre utilizzano lo stesso metodo della [singola attività](POWER_DURATION_2026_10_23.md): integrazione trapezoidale, interpolazione lineare e ricerca continua sui tratti validi. Gli zeri registrati partecipano alla media. Dati mancanti e interruzioni separano i tratti. Nessuna media dell'intera attività viene trasformata in un record per durata. Il periodo è determinato dalla data di inizio dell'attività in Europe/Rome; attività e campioni futuri sono esclusi rispetto al limite dell'analisi.

L'identità canonica Intervals prevale sugli alias; alias con lo stesso carico non dimostrano che le serie siano uguali. Le cache vengono confrontate con identità e impronta del contenuto selezionato, versione del metodo, date, qualità e intervalli. A parità di potenza rappresentata, vince l'attività cronologicamente precedente e poi la prima finestra, con identificativo come ultimo criterio deterministico.

## Stabilità e confini operativi

La beta con un solo processo ammette due lavoratori e otto analisi in attesa, con una sola analisi attiva per database. Il recupero Intervals rispetta i mutex già usati da connessione e sincronizzazione: se occupati, l'analisi termina con un messaggio esplicito. Le attività remote vengono elaborate in sequenza; errori di autenticazione o limite del provider interrompono il lotto senza tentativi ripetuti. Un aggiornamento remoto fallito conserva una curva precedente ancora valida e la sua data. Il tempo dipende dal numero di attività, dalla lunghezza delle serie e dalle risposte del provider; non viene promesso un tempo fisso.

Ogni lavoratore conserva il contesto del proprio profilo e apre sessioni legate al suo database. Le due tabelle additive contengono soltanto curve derivate e avanzamento. La migrazione crea prima una copia SQLite di un database esistente; le attività e le osservazioni originali restano inalterate. Il backup JSON portabile esclude queste tabelle ricostruibili. Il ripristino le svuota nella stessa transazione dei dati personali: l'eliminazione dell'identificativo UUID impedisce a un vecchio lavoratore di scrivere dopo il ripristino, anche se le attività reimportate sono identiche.

Gli identificativi attivi rimasti da un processo precedente vengono mostrati come interrotti. Non viene avviata alcuna scansione automatica al riavvio o durante una lettura. Questa coda locale non è un coordinatore distribuito per più processi o host.

## Verifiche e lavoro residuo

La suite completa supera 1.258 test backend; i 170 controlli mirati finali comprendono tre nuovi casi, per 1.261 test verificati. La suite frontend supera 326 test. Il nuovo roundtrip HTTP/JSDOM esegue 96 richieste e confronta 19 asset; tutte le 27 tabelle originali restano invariate e soltanto le due derivate cambiano.

Le prove sintetiche coprono selezione canonica, zero reale, vuoti, timestamp futuri, confini di calendario, cache obsolete o alterate, cancellazione, limiti della coda, isolamento dei contesti, errori del provider e ripristino durante il calcolo. Il roundtrip HTTP usa applicazione e JavaScript di produzione, dati isolati e un blocco delle chiamate al provider. I controlli distinguono le tabelle derivate dai dati originali.

Il server personale aggiornato supera sette letture API e il confronto di 18 asset; tutte le 27 tabelle preesistenti restano invariate e le due nuove tabelle derivate sono vuote. Queste letture non avviano analisi storiche.

Il collaudo visivo attuale resta da completare: l'inventario del browser controllabile non espone finestre o schede. Le prove DOM e HTTP non dimostrano l'aspetto effettivo a ogni dimensione. Non è stata avviata una scansione storica sul database personale per collaudare questa funzione.

Questa revisione aggiunge le potenze storiche; migliori tempi su distanze standard, record di vasca e dati non forniti dalle integrazioni restano separati dal lavoro concluso. Non dichiara equivalenza completa con Garmin o Strava, consegna a un dispositivo o utilità validata con atleti.
