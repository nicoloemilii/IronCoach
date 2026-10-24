# Attività, peso, piano e palestra — 18 ottobre 2026

## Modifiche

- Grafici: riferimento comune, opacità 1 per ogni barra, scala cromatica condivisa, valori uguali/colori uguali. Riferimento fuori dalle etichette; assi con precisione corretta, tooltip da mouse/tastiera e indicatore della settimana attuale separato dal colore.
- Salute: badge in una riga dedicata, date prima del grafico; esclusi stress manuale, carico manuale, modelli di carico quotidiano e Body Battery min/max. Le misure importate non vengono sostituite da inserimenti obbligatori. Il peso in Home apre il giudizio del Coach; lo storico grafico è disponibile in Salute e nel profilo atleta.
- Attività: riepilogo completo e voto, serie FC/potenza/velocità/passo/cadenza/altitudine/temperatura quando disponibili, traccia GPS locale e zoom. Nessuna mappa o potenza inventata. Grafici limitati a 900 campioni e GPS a 3000 punti; interruzioni/numeri negativi validi conservati. Provider assente: riepilogo locale utilizzabile.
- Navigazione: Obiettivo e piano riuniti, con due schede interne.
- Allenamenti: creazione manuale nel calendario, fasi personalizzate; palestra con serie, ripetizioni/secondi, kg e recuperi. Guida dal vivo con timer, pausa/ripresa, controllo delle serie e salvataggio a revisione. Il progresso non inventa un’attività completata.
- Profilo: misure osservate, soglie, totali e record per sport su intero storico/90 giorni, collegati all’attività originale. Medie di attività intere non presentate come record su segmenti o FTP.

## Verifica

490 test backend e 116 test DOM/flussi superati. Due grafici SVG sintetici renderizzati in PNG e ispezionati; confronto pari valore/pari colore e opacità verificato anche automaticamente. Creazione e avanzamento delle schede palestra verificati su database isolati: 66 richieste HTTP reali, zero errori HTTP/JavaScript. Verificati salvataggi consecutivi, serie persistenti, timer con pausa/riapertura/ripresa/reset e conservazione delle sedute manuali dopo la generazione. Nessuna attività fittizia creata.

Il layout completo nel browser e i trasferimenti fisici Garmin restano non verificati. Le schede palestra non vengono presentate come allenamenti FIT supportati dal dispositivo.
