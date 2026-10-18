# Grafici, disponibilità e palestra — 18 ottobre 2026

## Interfaccia

- Il riepilogo per disciplina della Home diventa una riga compatta in alto, con attività, ore e collegamento al piano. La sezione estesa in fondo è rimossa.
- Tutti i dialoghi espongono Indietro prima del titolo, in alto a sinistra, con intestazione stabile durante lo scorrimento. Corretto il limite di larghezza ereditato dal dettaglio attività.
- Dettaglio attività e record atleta usano accenti coerenti per disciplina e tipo di dato. Il colore decorativo non sostituisce il giudizio del Coach.
- Il filtro attività aggiunge Palestra e Altro; quest’ultimo include le discipline fuori da nuoto, bici, corsa e forza.

## Grafici e fasce

Le serie delle attività hanno minimo, massimo e media dei campioni disponibili, assi leggibili, lettura con puntatore/tastiera, zoom temporale e spostamento della finestra. Valori negativi validi, picchi e interruzioni restano presenti. La media dei campioni visualizzati non viene presentata come media ponderata dell’intera attività; una serie ridotta è dichiarata.

Una fascia obiettivo può essere impostata per la vista corrente. Rimane disponibile passando da una serie all’altra finché il dettaglio resta aperto; non modifica misure, soglie o piano. Senza valori personali non viene inventata una fascia ottimale di frequenza cardiaca, velocità o cadenza.

Per la potenza bici, soltanto con FTP dichiarata nel profilo, viene proposto il riferimento endurance Z2 al 56–75% della FTP. La provenienza stimata/misurata e l’uso della FTP attuale sono espliciti: non è l’obiettivo di ogni seduta né una ricostruzione della FTP storica. Metodo: [Andrew Coggan, TrainingPeaks](https://www.trainingpeaks.com/blog/power-training-levels/). Le zone cardiache richiedono riferimenti individuali; non si ricavano dal massimo di una singola attività. [Garmin: personalizzazione delle zone](https://support.garmin.com/en-IE/?faq=w8qlOr7BQ16Z82HVrevpw9).

Per HRV, sonno e frequenza a riposo la fascia verde è l’intervallo abituale personale già usato dal prodotto: almeno sette letture precedenti comparabili della stessa fonte. È descrittiva, non una prescrizione. Dati datati o insufficienti non producono una fascia. Peso, altitudine e temperatura non ricevono un obiettivo universale. I grafici settimanali evidenziano il riferimento ±10%, qualificato come confronto storico. Le letture osservate senza classificazione sufficiente restano visibili in blu.

## Coach nel piano

Il Coach interpreta localmente richieste di disponibilità e pause, non è una chat generativa. Esempi supportati: «Niente mattina», «Solo pomeriggio», «Non allenarmi lunedì e mercoledì sera», «Pausa dagli allenamenti». La successiva revisione aggiunge pause di disciplina e periodi nel testo, come «non voglio fare nuoto fino a gennaio»; Dal/Al restano il riferimento senza un periodo nel testo o con lettura automatica disattivata. Richieste ambigue o non supportate vengono respinte senza salvataggi. Dettagli in `docs/COACH_PAUSE_DISCIPLINE_2026_10_18.md`.

`POST /planning/coach/preview` mostra giorni, fasce e sedute interessate, senza scritture. `POST /planning/coach/apply` ricontrolla la firma dell’anteprima e la revisione del profilo. Modifiche concorrenti richiedono una nuova anteprima. Le regole persistono nel profilo, sono rimovibili e rientrano nel backup esistente.

Le fasce convenzionali sono mattina 06–12, pomeriggio 12–18 e sera 18–22. Indicano disponibilità, non un orario prenotato. Il calendario applica le regole alle sedute future e la generazione rispetta giorni bloccati e capienza delle fasce. Le sedute esistenti nei giorni interamente bloccati restano evidenziate come conflitti da spostare o saltare: non vengono cancellate o considerate svolte. Durata, esercizi e intensità non vengono riscritti dal comando di disponibilità. Le scelte manuali incompatibili ricevono un avviso.

## Palestra

Tre basi modificabili, catalogo ricercabile di esercizi, riordinamento, anteprima di esercizi/serie/recuperi e riutilizzo di una scheda in una nuova seduta. Scegliere una base prepara una bozza; la scrittura avviene solo con Salva nel piano. La guida dal vivo mostra avanzamento e passaggio al prossimo esercizio incompleto, oltre al timer e alle serie già persistenti. Riutilizzare una scheda non azzera il progresso della precedente.

## Verifica

511 test backend e 130 test frontend superati. Prova integrata con otto moduli frontend e FastAPI isolato: 131 richieste, zero errori HTTP/JavaScript; verificati creazione delle bozze, serie persistenti, anteprima/applicazione/rimozione delle regole, aggiornamento delle fasce del calendario e conflitti di pausa. Nessuna attività fittizia creata e nessuna scrittura di prova nel database personale.

Due SVG sintetici renderizzati in PNG e ispezionati per leggibilità di assi, fasce, colori e interruzioni. Il layout completo in un browser reale resta non verificato. Il server locale aggiornato serve gli asset correnti e registra le nuove route.
