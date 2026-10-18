# Revisione di interfaccia e valutazioni

- Scala per singola metrica: rosso, arancio, giallo, verde chiaro, verde. Grigio solo per riduzione osservata della base CTL di oltre il 10% su almeno 14 giorni e con almeno sette letture precedenti. Riduzione di carico e detraining fisiologico non sono equivalenti: anche uno scarico programmato può ridurre CTL. Assenza/stima locale/stale sono stati non valutati, separati.
- Giudizi in punti brevi, decisione evidenziata, contesto gara espandibile. Date rimosse dalle card Home. Frecce per tornare indietro da Coach e dettagli.
- Grafici SVG responsivi con assi, unità, griglia, valori e riferimenti. Le card Salute sono apribili e hanno un intervallo individuale preimpostato a quattro settimane. I vuoti non sono valori zero e interruzioni superiori a due giorni non vengono collegate.
- Rimosse spiegazioni ripetitive e blocco di revisione delle sedute non confermate da Coach. I comandi di modifica delle sedute restano nel calendario.
- Voto documentato delle attività registrate, con componenti e confidenza; non richiede abbinamenti al piano. Aderenza all’esecuzione esposta separatamente. Query aggregate per lo storico esteso.
- Diario fisico con episodi risolvibili/riapribili, sede, intensità e ipotesi personale. Analisi degli antecedenti: tre giorni precedenti contro 28 giorni anteriori, confronti della stessa fonte. Nessuna diagnosi o causa inferita. Le restrizioni preservano la precedenza del riposo.
- Profilo osservato ricalcolato alla lettura e dopo aggiornamento dati. Nessuna età, peso, FTP o disponibilità inventata; valori manuali conservati.
- Database/schema additivo, compatibilità dei backup precedenti e inclusione dei nuovi episodi nei backup.

Verifica: 418 test backend e 91 test DOM/regole/flussi. Verifica visiva del browser non eseguita: accesso precedentemente negato. I test sintetici non sostituiscono la prova visiva su dispositivi reali.

Pubblicazione GitHub: repository accessibile, ma prima scrittura respinta dal controllo automatico perché il tool richiede approvazione e la policy della sessione è never. Nessuna pubblicazione eseguita; pacchetto sorgente locale senza dati personali, credenziali, database o ambienti virtuali.
