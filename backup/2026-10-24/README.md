# Iron Coach: backup sviluppo del 24 ottobre 2026

Copia dei sorgenti, test, documentazione, strumenti QA originali e launcher esterno. Comprende il lavoro in corso sui record di distanza e nuoto. E un backup, non una versione validata per il rilascio.

I documenti in docs/ descrivono le verifiche delle rispettive revisioni; non certificano le modifiche successive. La verifica completa dell integrazione distanza/nuoto e ancora da completare.

Il manifest elenca dimensioni, SHA-256 e SHA Git dei file acquisiti. I dati personali, database, FIT originali, credenziali, ambienti virtuali, dipendenze installate e output runtime sono esclusi.

Gli script .qa/ conservano i percorsi originali. Alcuni dipendono da fixture/output generati esclusi e alcuni script di riavvio contengono PID storici: rivederli prima di eseguirli. Il launcher in external-launcher/ appartiene alla cartella che contiene ironman_coach/.

Per riprendere: leggere docs/REQUIREMENTS_STATUS_2026_10_23.md e docs/HISTORICAL_POWER_RECORDS_2026_10_23.md, verificare lo stato effettivo del codice e completare API/job/test dei record distanza e nuoto. Non confondere test sintetici con verifica visiva, provider reali o prove Garmin su dispositivo.

## Privacy della copia pubblica

Alcune frasi con osservazioni personali nei rapporti storici sono state omesse soltanto in questa copia. I file interessati e la motivazione sono elencati nel manifest; i sorgenti eseguibili del prodotto restano identici. Gli originali sono conservati esclusivamente nel backup locale.
