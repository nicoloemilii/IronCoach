# Dal piano al lavoro confermato — 22 ottobre 2026

Nel calendario è ora disponibile una revisione settimanale comprimibile. Mostra le esecuzioni confermate, i minuti effettivamente disponibili e le sedute passate da chiarire. Da una seduta da confermare si apre direttamente il programma e il relativo feedback, anche quando un vecchio feedback non costituisce più un’evidenza utilizzabile.

Le proposte di abbinamento arrivano dal server, invece di dipendere dalle prime 50 attività caricate nel diario. Confrontano disciplina normalizzata, giorno nel calendario Europe/Rome e durata. Mostrano fonte, identificativo, differenza di giorno e durata, eventuale associazione a un’altra seduta e possibili duplicati. Non presentano una percentuale di confidenza calibrata e non selezionano automaticamente un’attività. Scegliere una proposta modifica soltanto la bozza; il salvataggio del feedback resta esplicito.

La ricerca considera i due giorni precedenti e successivi alla seduta, esclude le registrazioni con data e ora future e restituisce al massimo 12 proposte fra 500 registrazioni vicine. Il limite e l’eventuale troncamento sono dichiarati. Un’attività fuori dalla ricerca non viene dichiarata inesistente. Una vecchia associazione non disponibile non viene sostituita silenziosamente.

## Coerenza delle evidenze

Calendario, revisione e giudizio di esecuzione del Coach usano la stessa validazione del feedback. Lo stato persistito `completed`, da solo, non prova l’esecuzione. Un’attività futura o mancante, una disciplina o data incompatibile e dati non leggibili lasciano una lacuna da chiarire. Il codice non riscrive automaticamente i vecchi stati del ledger.

Una durata mancante resta `null`. L’attività può confermare l’avvenuta registrazione senza fornire minuti o aderenza alla durata. Uno zero finito registrato resta invece zero. Il completamento dichiarato non sostituisce una durata assente nell’attività associata. Mancando la durata, il confronto complessivo resta non disponibile; una riduzione non viene dedotta da uno zero inventato. RPE elevato o sensazioni dichiarate peggiori restano segnali distinti.

L’evaluatore non assume più RPE atteso 4 quando il programma non lo specifica. Il completamento dell’evaluatore arriva al massimo al 100%; il confronto settimanale della durata considera anche gli scostamenti oltre la durata prevista. Queste valutazioni riguardano l’esecuzione dichiarata o registrata, non adattamenti fisiologici o validazione clinica del piano.

Le letture di JSON hanno limiti di dimensione, profondità e numero di elementi. Un programma non leggibile resta consultabile nel calendario con un avviso e i soli dati di base; la modifica richiede di risolvere il problema, senza salvare quel contenuto sostitutivo nelle revisioni.

## Salvataggio e compatibilità

L’anteprima web include un’impronta dei record coinvolti: programma, attività, collegamenti e decisioni sui duplicati. Il salvataggio con tale impronta controlla lo stato in una transazione SQLite `BEGIN IMMEDIATE`; se cambia, restituisce 409 e richiede di rileggere le proposte. Le bozze non vengono cancellate o inviate nuovamente in automatico. I token già usati vengono scartati dopo il salvataggio.

I client precedenti possono ancora inviare l’identificativo senza token. Anche per questi client restano obbligatori i controlli di data/ora, disciplina, distanza dal giorno programmato, durata valida e unicità del collegamento. Le scritture aggiornano un solo feedback, una valutazione e un adattamento per seduta; una ripetizione non moltiplica il trend.

`summary.completed_minutes` del calendario ora rappresenta i minuti effettivamente confermati, oppure `null` se una conferma ha durata sconosciuta. `completed_planned_minutes` conserva separatamente la quota programmata delle sedute confermate. `execution_status` descrive l’evidenza; `status` resta il ledger usato per proteggere le modifiche alle sedute. Non sono state introdotte migrazioni o nuove tabelle.

## Verifiche e limiti

Suite completa: 1005 test backend e 239 frontend superati; resta un avviso preesistente Starlette/httpx.

La revisione settimanale usa sei query con 100 sedute nel test isolato, contro circa 500 nella lettura precedente; legge le attività della finestra e quelle esplicitamente collegate, senza caricare tutto lo storico. Il giudizio di esecuzione del Coach è caricato in gruppi. Sono misure su dati sintetici, non un impegno sui tempi di produzione.

La verifica HTTP indipendente controlla 17 invarianti, fra cui rifiuto dell’attività futura senza scritture, stato legacy conservato ma non conteggiato come eseguito, durata sconosciuta distinta da zero, alias sportivi e calendario Europe/Rome, unicità dei collegamenti e anteprime obsolete. I GET verificati non modificano il database.

L’integrazione carica HTML e JavaScript di produzione in JSDOM con un server locale isolato: 96 richieste HTTP, due salvataggi espliciti su dati sintetici, zero errori HTTP o DOM. Conferma una bici da 75 minuti contro 60 previsti e una seduta palestra con durata sconosciuta, conservando il feedback legacy non valido. Non contatta fornitori e non usa lo storico personale.

La verifica visiva di questa revisione resta da eseguire: al momento del controllo il browser integrato non era disponibile. Le prove DOM e HTTP non sostituiscono l’ispezione nel browser. Gli artefatti locali sono in `.qa/goal-execution/`; database, risposte personali e risultati QA non entrano nel pacchetto dei sorgenti.

Il server personale è stato avviato dopo una copia SQLite e verificato con 12 letture API e 14 asset confrontati con il workspace. Il ledger di sedute e feedback è rimasto invariato. La sincronizzazione Intervals esistente è stata collaudata con successo dopo aver corretto l’ambiente di rete del processo locale; nessun feedback sintetico è stato scritto nell’archivio personale.
