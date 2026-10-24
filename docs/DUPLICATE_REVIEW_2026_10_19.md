# Confronto e annullamento delle decisioni sui duplicati

Il diario apre un confronto prima di unire due registrazioni. La registrazione indicata come conservata mantiene identificativo, nome, fonte e valori già presenti. Solo gli undici campi numerici mancanti vengono completati dall’altra registrazione; uno zero registrato resta distinto da un dato mancante. Il confronto mostra valori prima e dopo e quantità di identità, osservazioni, feedback, valutazioni e confronti coinvolti.

L’anteprima è in sola lettura. La conferma dell’unione richiede la sua impronta `state_token`: una chiamata senza anteprima restituisce 428, una conferma dopo modifiche ai dati coinvolti restituisce 409. Il server ricontrolla lo stato all’interno di una transazione SQLite `BEGIN IMMEDIATE`. Due feedback incompatibili, una coppia che punta alla stessa attività, identità in conflitto o dati collegati non leggibili impediscono l’unione.

Le decisioni di unione e di mantenimento separato creano un diario locale nella stessa transazione. Il diario conserva copie prima e dopo delle due attività e di tutti i collegamenti incidenti a entrambe, compresi i confronti con attività terze. Le osservazioni originali mantengono il loro contenuto; feedback e valutazioni trasferiti aggiornano i riferimenti all’attività conservata. La cronologia pubblica espone soltanto un riepilogo, senza copie grezze del diario.

L’annullamento ripristina i record precedenti solo se lo stato coinvolto corrisponde ancora a quello successivo alla decisione e gli identificativi e le identità da ripristinare sono liberi. Una sincronizzazione, un nuovo feedback o il riutilizzo di un identificativo possono impedire l’operazione. Le decisioni concatenate devono essere annullate dalla più recente. Le fusioni anteriori a questa versione non acquisiscono retroattivamente copie recuperabili.

Le copie hanno limiti espliciti: 5.000 record coinvolti, 4 MB per copia serializzata e profondità JSON 30. Un superamento blocca la revisione reversibile. Questa funzione modifica lo storico locale dell’atleta; non elimina o modifica attività sui servizi dei fornitori.

## Migrazione e backup

La migrazione additiva v3 crea `duplicate_reviews` e conserva i dati esistenti. Prima dell’aggiornamento di un database SQLite su file viene creata una copia online nella cartella locale `backups`. Le nuove installazioni ricevono la stessa tabella; i workspace ospitati la ricevono attraverso la migrazione del proprio database.

La cronologia operativa resta nel database locale ed è inclusa nelle copie SQLite e nei backup operativi del database. Non viene aggiunta al JSON personale portatile, per mantenerne la compatibilità. Il ripristino di un JSON azzera la cronologia nella stessa transazione del ripristino; la copia SQLite precedente conserva il vecchio stato. Un errore nel ripristino conserva anche la cronologia. Una modifica della cronologia dopo l’anteprima del backup invalida quell’anteprima.

## Verifica

La suite completa supera 888 test backend e 219 frontend. I 37 nuovi test del servizio verificano anche una sincronizzazione SQLite concorrente durante l’anteprima, collisioni, annullamenti concatenati, rollback e isolamento degli utenti. Migrazione e backup hanno prove dedicate; 17 test DOM coprono il nuovo modulo del confronto.

Una verifica HTTP indipendente, con vincoli SQLite attivi, ripristina esattamente i record di tutte le tabelle coinvolte, compresi ID, timestamp e stringhe JSON originali. Verifica inoltre reimportazioni dopo l’annullamento e che un errore a metà ripristino conservi dati e diario. L’anteprima esegue solo `BEGIN` e letture, senza istruzioni di modifica.

Nel browser reale, con l’app di produzione su un database sintetico isolato, sono state completate unione→annullamento e mantenimento separato→annullamento. Entrambe restituiscono le sei tabelle coinvolte allo stato iniziale. L’interfaccia è stata ispezionata a 375, 745 e 1440 px: nessun overflow della pagina o del dialog, tabelle con scorrimento interno e focus da tastiera, Indietro in alto a sinistra e ritorno del focus dopo il salvataggio. Nessuna prova di fusione è stata eseguita sullo storico personale configurato.
