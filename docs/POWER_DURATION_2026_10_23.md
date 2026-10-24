# Migliori finestre di potenza — 23 ottobre 2026

Nel dettaglio dell'attività, la curva mostra la potenza media più alta nei tratti continui validi su 5, 15 e 30 secondi, 1, 5, 10, 20 e 60 minuti. Ogni durata disponibile apre il suo intervallo nel grafico della potenza. La selezione conserva le statistiche dell'intera attività e il confronto delle due metà; cambiando attività, la selezione precedente viene eliminata.

La curva usa i campioni originali prima della riduzione del grafico. Non ricava gli sforzi dalla potenza media riepilogativa o dai punti mostrati. La copertura complessiva, il numero di segmenti validi e la loro durata massima restano visibili. Una finestra disponibile ha copertura del 100%; un'attività può avere copertura complessiva parziale. Le durate troppo lunghe rimangono N/D e non diventano zero watt.

## Metodo e provenienza

`activity_detail.best_efforts` espone risultato, fonte, copertura, otto durate, intervalli assoluti dall'inizio e metodo `raw-power-duration-1`. La media è l'integrale trapezoidale della potenza interpolata linearmente, diviso per la durata. La ricerca considera anche inizi fra due campioni: per ogni tratto lineare del problema valuta estremi e massimi interni della finestra continua. Nel caso analitico 0 W a 0 s, 120 W a 4 s e 0 W a 10 s, il massimo su 5 s è 90 W da 2 a 7 s; limitarsi agli inizi sui campioni o agli eventi darebbe 70 W.

Gli zeri registrati partecipano alla media. Potenze mancanti, negative, booleane o non finite e intervalli troppo lunghi interrompono i segmenti. Si riusa la politica del parser: massimo fra campioni collegati pari a tre volte l'intervallo mediano, con tetto di 10 secondi. Timestamp ambigui, canali di potenza duplicati e input troncati impediscono di presentare una curva completa. I campioni oltre il tempo verificabile vengono esclusi.

Ogni segmento viene scalato separatamente per contenere gli errori numerici. Le somme compensate orientano la ricerca; l'integrazione razionale esatta verifica il risultato finale e i massimi vicini. A parità esatta vince l'inizio precedente. Una cache locale alla singola elaborazione riusa soltanto finestre con identica forma matematica ed energia interna esatta, con un limite di 2.048 voci per segmento. Non conserva dati fra richieste o atleti.

La fonte distingue serie ricevute da Intervals.icu e serie dell'osservazione conservata. La selezione dell'identità mantiene la regola comune del dettaglio: due alias con carichi uguali non autorizzano la scelta arbitraria di serie diverse. Il calcolo non scrive attività, osservazioni, feedback, record o soglie. Non stima FTP o forma fisica. Il risultato riguarda questa attività, non i record personali dell'intero archivio.

## Interazione

L'asse dei watt parte da zero; le durate seguono una scala logaritmica dichiarata. I pulsanti riportano valori e disponibilità anche senza il grafico. Frecce, Home ed End esplorano le durate disponibili; una tabella con intestazioni esplicite offre gli stessi dati. Il metodo rimane apribile separatamente.

La fascia arancione nel grafico indica il periodo selezionato. Se la visualizzazione ridotta non conserva campioni nella finestra di contesto, il grafico torna all'attività completa e lo spiega, senza aggiungere campioni. Le eventuali fasce personali di potenza restano distinte dall'intervallo temporale. La selezione non cambia medie, minimi, massimi o confronto delle metà riferiti alla registrazione intera.

## Verifiche

- Suite completa: **1.173 test backend e 307 frontend superati**. Corretto anche un fixture preesistente che costruiva attività alle 06:00 del giorno corrente: prima di quell'ora quattro test incontravano correttamente il blocco contro attività future. Il fixture usa ora un orologio controllato; le protezioni di produzione rimangono invariate.
- Oracolo indipendente con integrazione razionale diretta: 234 finestre confrontate senza divergenze; ulteriori 60 casi vicini alla precisione IEEE verificano l'inizio del massimo. Inclusi campionamento irregolare, massimi interni, parità, zeri, vuoti, timestamp ambigui, troncamento e scale numeriche estreme.
- Otto scenari HTTP isolati con il router FastAPI: identità canonica, alias ambigui, limite temporale, attività futura, durata trascorsa distinta da quella in movimento, zeri e provenienza remota simulata. Solo SELECT e database invariato.
- Bundle HTML/JS di produzione in JSDOM con server HTTP isolato: sette flussi, 70 tentativi GET, 18 risorse identiche ai file, 27 tabelle invariate, nessun errore HTTP/DOM, chiamata provider o mutazione. Verificati anche 14.401 campioni originali con grafico ridotto e un riepilogo volutamente diverso dalla media della finestra. Non è un controllo visivo.
- Misure sintetiche su 250.000 campioni: circa 0,4 s per potenza costante, 16 s per una serie periodica con massimi ripetuti e 13,9 s per valori variabili. Si riferiscono al solo calcolo, senza SQL, rete o costruzione degli altri grafici; non sono garanzie del tempo API.

Gli artefatti privati sono in `.qa/goal-power/`. Il diff e il pacchetto sorgenti escludono risposte reali, database e credenziali. La revisione è locale; il controllo visivo nel browser resta pendente perché gli strumenti non espongono una scheda controllabile. Il quadro complessivo delle richieste è in [Stato dei requisiti](REQUIREMENTS_STATUS_2026_10_23.md).
