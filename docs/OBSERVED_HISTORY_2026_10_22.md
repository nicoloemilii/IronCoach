# Profilo, record e carichi coerenti — 22 ottobre 2026

Il profilo distingue totali completi, subtotali misurati e dati sconosciuti. Quattro attività senza durata non diventano zero minuti e non producono un avviso di calo del volume. Il confronto dei due periodi consecutivi di 28 giorni mostra date, minuti registrati e copertura per attività; la variazione compare soltanto con durate complete in entrambi i periodi e una base precedente positiva. Descrive lo storico registrato, non riposo, prestazione o adattamento fisiologico.

I record di attività intere sono disponibili su 28 giorni, 90 giorni e tutto lo storico. Ogni record espone data, fonte e apertura dell'attività originale; quando mancano misure, indica la copertura dei candidati. Il periodo selezionato e il focus sui relativi comandi vengono conservati durante un aggiornamento. Un aggiornamento fallito mantiene i dati precedenti, li marca come potenzialmente non aggiornati e offre una nuova lettura.

## Copertura e valori

I totali completi in `totals` sono numerici soltanto quando ogni attività della finestra ha una misura utilizzabile e l'aggregato è rappresentabile. `coverage_summary` espone `known`, `expected`, `missing`, `invalid`, `complete`, `status` e `known_value`. Il subtotale disponibile rimane distinto dal totale completo. Una finestra senza attività ha zero attività e zero volume registrato; una finestra con attività senza misure resta sconosciuta. Gli zeri effettivamente registrati sono conservati.

Il confronto `volume_comparison` include le due finestre, conteggi di copertura, stato e motivi di indisponibilità. Un aggregato non rappresentabile non diventa completo soltanto perché tutti i campioni individuali sono finiti. La priorità sul calo superiore al 30% richiede un confronto disponibile, senza colmare con zero le durate mancanti.

Le attività successive alla data scelta o al momento corrente sono escluse dal profilo. I confini del calendario sono convertiti da Europe/Rome a UTC. I record sono massimi delle attività intere registrate: non sono segmenti, migliori tempi su distanze standard, curve di potenza o soglie FTP.

## Identità delle osservazioni

Prima della correzione, alcune sezioni sceglievano la prima osservazione Intervals associata a un'attività e altre l'ultima. Dopo un'unione di duplicati, questo poteva produrre carichi diversi per lo stesso record. `observed_load.py` applica una sola regola a diario, rating, profilo, confronti delle segnalazioni e analisi dello storico:

1. Se l'attività conserva un'identità Intervals, si preferisce l'osservazione con quella stessa chiave della fonte, anche quando il carico è esplicitamente nullo.
2. Altrimenti si usa l'unica osservazione Intervals disponibile, oppure il carico su cui concordano tutte le osservazioni utilizzabili equivalenti.
3. Se gli alias non concordano, il carico resta sconosciuto con stato di conflitto. Non si sceglie arbitrariamente per ordine di creazione.
4. Senza osservazioni Intervals si usa il carico canonico finito e non negativo. Le osservazioni malformate non ripristinano un valore precedente.

L'equivalenza del carico non prova l'equivalenza dell'intero dettaglio. Senza identità univoca, il dettaglio conserva i riepiloghi disponibili e segnala l'ambiguità; non apre un'altra attività remota soltanto perché ha lo stesso carico. Nessun record originale o JSON viene modificato durante queste letture.

Le letture per una selezione di attività usano gruppi di massimo 500 identificativi. Lo snapshot globale del modello fitness conserva una query a scorrimento, con contesto separato per sessione/workspace. Il profilo risolve i carichi una volta e riusa la mappa in tutte le discipline e finestre. Il parser limita la dimensione del payload e il numero di osservazioni per attività; dati fuori dai limiti restano indisponibili.

Il diario espone il carico registrato e la sua provenienza, senza sostituire un carico mancante con una stima da NP e FTP predefinita. Il parametro FTP preesistente resta accettato per compatibilità. Gli endpoint del modello fitness conservano i propri calcoli dichiarati: quando il modello richiede un numero per un carico osservato indisponibile, il suo zero di imputazione non è una misura registrata. Un carico osservato positivo resta utilizzabile dal modello anche quando manca la durata.

## Rating e discipline

Il rating esclude HRV e FC a riposo non positive, segnali dichiarati stimati e misure dello stesso giorno o successive alla seduta. Riusa le regole di validità già presenti nel modulo Salute. JSON, identificativi di obiettivi e frazioni gara malformati non impediscono la lettura delle attività. Una valutazione mirata conserva il contesto precedente e produce lo stesso risultato del calcolo completo sui dati di prova; l'endpoint di valutazione limita anche la lettura del contesto temporale. Il dettaglio evita di ricalcolare i rating delle altre attività.

Diario, profilo e servizi riconoscono gli stessi alias espliciti di bici, corsa, nuoto e forza. `Crossfit`, `StrengthTraining`, `gym`, `palestra` e le varianti dei pesi sono Forza; `Ride` è Bici. Un generico `Workout` resta Altro, perché il nome non identifica una disciplina. Il campo originale della fonte viene conservato; `sport_category` è una classificazione aggiuntiva per il client.

## Verifiche e limiti

- Suite completa: **1.109 test backend e 271 frontend superati**; un avviso preesistente Starlette/httpx.
- Audit indipendente del profilo: dieci scenari HTTP sintetici e dodici invarianti verificati, senza scritture. Durate mancanti, zeri, aggregati parziali, valori non finiti, overflow, alias, periodi dei record e confini temporali sono coperti.
- Il resolver è verificato con identità canoniche, alias equivalenti e in conflitto, null espliciti, JSON corrotti, gruppi di 500 identificativi e snapshot separati. I test proteggono anche l'equivalenza fra rating mirato e completo.
- Integrazione HTML/JS di produzione in JSDOM con server sintetico: 95 tentativi GET, sette flussi funzionali, 16 risorse verificate e 27 tabelle invariate. Nessun errore HTTP o DOM, nessuna risorsa esterna e nessuna chiamata provider. Tre cancellazioni intenzionali di richieste durante la navigazione sono registrate separatamente dai fallimenti. Non è una verifica visiva.
- Server personale aggiornato dopo copia SQLite: 13 letture API e 15 asset verificati; contenuti delle 27 tabelle invariati. Il contratto del nuovo profilo e i tre periodi dei record sono presenti anche nella risposta reale.

Le verifiche integrate e del server locale sono registrate in `.qa/goal-history/`. I pacchetti dei sorgenti escludono dati personali, credenziali, risposte reali e archivi QA. La revisione è locale. La verifica visiva dei nuovi flussi resta pendente finché il browser integrato non è disponibile. Il calcolo completo dei rating su storici molto densi conserva un costo elevato; la correttezza della lettura mirata non dimostra prestazioni lineari di ogni elenco.

Restano aperti il collaudo visivo dei nuovi flussi, le prestazioni su storici densi, l'uso continuativo con atleti, l'esercizio ospitato finale e la prova dei trasferimenti Garmin sui dispositivi autorizzati. Questa revisione non certifica il completamento dell'obiettivo generale.
