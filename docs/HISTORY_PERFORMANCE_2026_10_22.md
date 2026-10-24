# Diario e calcolo dello storico — 22 ottobre 2026

Il diario carica 50 attività alla volta, cerca i nomi nell'archivio e filtra per disciplina sul server. La Home può terminare il caricamento mentre il diario è ancora in attesa. Le pagine successive aggiungono righe senza ricreare quelle già presenti; da tastiera il focus passa alla prima nuova attività. Un record aperto dal Profilo resta consultabile anche se non compare nella pagina caricata.

Ricerca e aggiornamenti annullano le letture superate e ignorano risposte obsolete. Un errore conserva le attività già mostrate, indica i filtri cui appartengono e offre un nuovo tentativo. Un cursore invalidato richiede la prima pagina. L'assenza di risultati per un filtro resta distinta da un archivio vuoto. Il client di produzione non ripiega sull'intero archivio in caso di errore.

## Letture e conservazione

`GET /activities/diary` accetta `limit` da 1 a 100, `sport`, `q` fino a 200 caratteri e `cursor`. Restituisce attività, conteggi dell'archivio e dei risultati, disponibilità della pagina successiva e posizione di continuazione. L'ordinamento è data decrescente, poi identificativo decrescente. Ricerca Unicode casefold, percentuali e underscore letterali e classificazione delle discipline sono verificati. L'endpoint precedente `/activities` resta disponibile per compatibilità.

Il cursore conserva il massimo identificativo della prima lettura: i nuovi inserimenti compaiono dopo un aggiornamento. Non congela le righe esistenti; modifiche o cancellazioni possono cambiare le pagine successive. I rating usano il momento iniziale e il contesto registrato disponibile nella singola lettura, comprese eventuali importazioni retrodatate. L'interfaccia invita ad aggiornare per riallineare lo storico. Filtri e workspace sono verificati prima di continuare. In modalità ospitata ogni richiesta conserva l'autenticazione e il database dell'utente.

Nella singola richiesta SQLite, conteggi, righe, osservazioni e contesto del giudizio condividono una transazione di lettura. Le funzioni Unicode sono locali alla connessione; il GET non modifica schema o record. Un indice fisico su data e identificativo viene aggiunto all'avvio quando manca, dopo copia online del database esistente. Il formato dei record e le versioni dello schema restano invariati.

Per ogni pagina il giudizio legge l'unione delle finestre precedenti di 42 giorni e delle finestre wellness necessarie. Due risultati separati da anni non richiedono il caricamento degli anni intermedi. Uno storico molto denso può comunque richiedere tutte le attività comprese nelle finestre, senza troncare arbitrariamente le evidenze. Carico nullo, conflitti della fonte e attività future conservano le regole già documentate.

## Calcolo e misure

Mediane aggiornabili per disciplina, somme su intervalli e recupero riutilizzato per giorno evitano scansioni ripetute. Le attività con lo stesso istante non diventano precedenti l'una dell'altra. Le somme mantengono una rappresentazione binaria esatta; quando l'incertezza dell'aritmetica precedente può cambiare voto o testo arrotondato, viene eseguito il calcolo precedente. Input costruiti vicino a questi confini possono ancora richiedere lavoro aggiuntivo: non si promette una complessità universale o un tempo massimo.

Il confronto usa gli output completi conservati prima della modifica, con fixture sintetiche identiche e tre SELECT in ogni scenario. Include SQL e calcolo; esclude preparazione dei dati e serializzazione. Tutti i sei JSON canonici coincidono byte per byte, compresi voti, spiegazioni e confidenza.

| Attività | Dense in 35 giorni: prima → dopo | Una ogni 12 ore: prima → dopo |
| ---: | ---: | ---: |
| 1.000 | 0,656 → 0,066 s | 1,303 → 0,142 s |
| 3.000 | 4,552 → 0,164 s | 10,266 → 0,431 s |
| 10.000 | 45,921 → 0,530 s | 117,476 → 1,492 s |

Sono osservazioni locali su Python 3.11.15, non una garanzia di servizio. Una prova separata del router su 10.000 attività restituisce la prima pagina da 50 in circa 35 ms nello storico distribuito e 386 ms in quello denso. Nel primo caso vengono caricati 134 identificativi; nel secondo il contesto utile comprende tutte le 10.000 attività. Le risposte sono circa 95 KB.

Sono corretti anche due casi numerici estremi preesistenti: durate che diventano zero nella conversione in minuti non ricevono un voto senza altre evidenze; confronti non rappresentabili vengono omessi e non producono divisioni per zero o spiegazioni con Infinity/NaN. Le componenti del recupero riutilizzate non condividono dizionari modificabili tra risultati.

## Verifiche e limiti

- **1.152 test backend e 286 frontend superati**, con un avviso preesistente Starlette/httpx.
- Audit indipendente: 16.600 confronti dell'intero giudizio con le regole di riferimento, nessuna divergenza; verificati 2.322 ricorsi conservativi al calcolo precedente. Ulteriori 8.808 casi IEEE verificano le somme esatte, inclusi subnormali e conversione in minuti.
- API di produzione isolate: 38 GET su 147 attività, tre pagine 50/50/47, filtri, errori di validazione, rating equivalenti all'elenco completo e tutte le 27 tabelle invariate.
- HTML e tutti gli script di produzione in JSDOM con HTTP reale isolato: sette flussi, 105 tentativi GET, 17 risorse identiche ai sorgenti, nessun errore HTTP/DOM inatteso, nessuna risorsa esterna e 27 tabelle invariate. Un errore transitorio è iniettato intenzionalmente; cancellazioni e risposte trattenute verificano i casi concorrenti. Non è un controllo visivo.
- Server personale riavviato dopo copia SQLite: 14 letture API, 16 asset verificati, indice presente e contenuti delle 27 tabelle invariati.

Gli artefatti privati sono in `.qa/goal-performance/`; dati personali, credenziali, database e risposte reali sono esclusi dal diff e dal pacchetto sorgenti. La revisione resta locale. Il browser controllabile non è disponibile: il collaudo visivo del nuovo diario resta pendente, insieme all'uso continuativo con atleti, alla validazione ospitata finale e alle prove Garmin sui dispositivi autorizzati. L'obiettivo generale resta aperto.
