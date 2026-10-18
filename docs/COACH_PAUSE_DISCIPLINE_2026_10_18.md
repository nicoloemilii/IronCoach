# Coach: pause di disciplina e periodi nel messaggio

La richiesta «non voglio fare nuoto fino a gennaio» produce una pausa del solo nuoto da oggi al 31 dicembre, con ripresa dal 1° gennaio successivo. L’interpretazione è mostrata prima di salvare. «Fino a fine gennaio» include invece il mese di gennaio; una data con giorno è inclusa nella pausa.

## Richieste supportate

- Pause di nuoto, bici, corsa e palestra, anche di più discipline insieme.
- Pause limitate a giorni o fasce: «Niente nuoto la mattina» lascia disponibili le altre discipline e il nuoto nelle altre fasce.
- «Fino a gennaio», «fino al 15 gennaio 2027», «per due settimane», «da domani per 10 giorni», «per tre mesi», «dal 1 novembre al 31 dicembre», «da novembre a gennaio», «a gennaio», «fino a fine mese/anno» e «la prossima settimana».
- Date Dal/Al manuali quando la lettura del periodo dal messaggio è disattivata. Senza un’espressione temporale, i campi restano il riferimento anche in modalità automatica.

Le date riconosciute aggiornano Dal/Al nell’anteprima. Mesi senza anno usano la prossima occorrenza pertinente; le assunzioni, compreso il cambio d’anno, sono esplicite. Le durate contano entrambi gli estremi; i mesi seguono il calendario. Periodi impossibili, oltre due anni, richieste contraddittorie o espressioni non supportate vengono rifiutati senza salvare. È un interprete locale circoscritto, non una chat generativa.

## Effetto sul piano

`TimeConstraint` aggiunge `sports`, vuoto per le disponibilità globali. Con una disciplina e nessuna fascia, la pausa copre la giornata intera di quella disciplina. Le regole si conservano nel profilo, scadono per data e possono essere rimosse dall’elenco.

L’anteprima elenca nome, data e azione sulle sedute. Alla conferma le sole sedute future pianificate interessate da una pausa completa vengono tolte dal piano attivo tramite stato `superseded`; non vengono cancellati dati, create attività, registrati mancati svolgimenti o assegnati voti. Le sedute completate e gli altri sport restano invariati. Sedute già iniziate/confermate vengono conservate come conflitti da chiarire; per combinati o pubblicazioni Garmin occorre prima riconciliare la struttura o la consegna.

Preferenza, audit e rimozione sono salvati in una sola transazione con controllo della revisione e firma dell’anteprima. Un errore annulla tutta l’operazione. Le preferenze si gestiscono dall’elenco; l’annullamento del calendario riguarda le sedute della settimana e non elimina la regola. Rimuovere la pausa consente di ricalcolare le sedute da riprogrammare.

Generazione settimanale, proposta giornaliera, spostamenti e disponibilità delle singole discipline rispettano le regole per data. Il tempo omesso non viene trasferito automaticamente agli altri sport, compreso quello delle sedute manuali tolte dal piano. Le scelte manuali incompatibili ricevono un avviso. La scadenza riabilita la disciplina nelle successive proposte e generazioni senza modificare esclusioni permanenti o accesso alla piscina.

## Verifica

580 test backend e 134 frontend superati. Coperti cambio d’anno, anni bisestili, confini inclusivi, disciplina/fascia, protezioni, assenza di redistribuzione, rollback e scadenza.

Prova integrata su FastAPI e JSDOM isolati con la frase esatta dello screenshot: 66 richieste frontend, nessun errore HTTP/JavaScript. Verificati anteprima senza scritture, rimozione del solo nuoto futuro, conservazione di storico e altre discipline, generazione senza nuoto durante la pausa e ripresa dopo la scadenza. Nessuna attività fittizia o modifica di prova delle preferenze personali. Layout completo nel browser non verificato.
