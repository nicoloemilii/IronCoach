# Iron Coach 2.0 — beta web commerciale

Applicazione web per coaching multisport, con calendario, storico, recupero, integrazioni e schede palestra con 201 esercizi. La versione personale Windows resta disponibile.

La modalità **hosted** aggiunge account separati, registrazione su invito o con conferma email, password Argon2id, sessioni revocabili, protezione CSRF, archivi distinti per atleta, credenziali cifrate, esportazione dei dati e richieste di cancellazione. Stripe Checkout e portale di fatturazione usano un prezzo del server e webhook firmati; il semplice ritorno dal pagamento non abilita il prodotto.

Sono inclusi Docker/Caddy, configurazione di esempio, dipendenze bloccate, CI, backup operativo cifrato e ripristino in una cartella nuova. **Non è stata attivata una vendita pubblica**: dominio, hosting, pagamenti, email e documenti del titolare richiedono configurazione e collaudo reali. [Avvio, operazioni e limiti della beta](docs/COMMERCIAL_BETA.md). Desktop/iOS conservano lo stato precedente; non sono il canale commerciale di questa beta.

Verifica della revisione: 617 test backend, 144 frontend e prova integrata hosted con due account e 56 richieste. Layout nel browser e distribuzione Docker non verificati su questa macchina; i servizi esterni sono simulati nei test.

## Interfaccia web

Eseguire `Avvia_Web.bat` e aprire http://127.0.0.1:8000/app. Dalla cartella superiore si può usare `Apri_Iron_Coach.bat`. Il primo avvio crea `.venv-web` e installa le dipendenze web senza PySide6; gli avvii successivi riutilizzano le dipendenze già presenti. Il server ascolta solo sul computer locale.

Se il browser non mostra il software, verificare che il server sia avviato: mantenere aperta la finestra del launcher. Un secondo avvio riconosce Iron Coach già attivo; gli errori rimangono leggibili nella finestra. Il vecchio `IronmanCoach.bat` avvia il client desktop installato in `C:\IronmanCoach`, separato da questa interfaccia web. Aprire la radice `http://127.0.0.1:8000/` da un browser porta all'app; gli asset vengono rivalidati per caricare gli aggiornamenti dei colori.

Da Connessioni puoi caricare file FIT di attività con l'SDK Garmin ufficiale, oppure un archivio JSON nel formato mostrato nell'app. Il vecchio importatore di cartelle FIT richiede anche `fitparse`, incluso nelle dipendenze desktop.

La UI HTML/CSS/JavaScript in `web` include profilo atleta, disponibilità giornaliere, soglie indipendenti con provenienza, calendario settimanale persistente, ripianificazione e annullamento, check-in, guida con timer, feedback e confronto programmato/eseguito, riepilogo per disciplina e checklist gara. Le sessioni completate e i riferimenti agli invii vengono conservati.

Il piano considera capacità recente, distanza gara, priorità, piscina, discipline escluse, recupero e feedback. Il volume è un limite, non un obiettivo da riempire. Il generatore è dichiaratamente aerobico e prudente: non è stato validato come preparazione individuale completa per un Ironman e non applica progressioni intense automatiche. I combinati facili richiedono storico e recupero sufficienti. Le limitazioni in testo libero non diventano automaticamente regole cliniche; usa le esclusioni esplicite delle discipline.

Gli allenamenti bici/corsa sono esportabili come veri file FIT, verificati rileggendoli con l'SDK ufficiale. Il target RPE viene mantenuto come istruzione con intensità aperta: non è un allarme automatico di intensità sul Garmin. Nuoto/forza non sono ancora esportabili in FIT. I file esportati devono essere collaudati sui dispositivi di riferimento Forerunner 955 ed Edge 530.

**Invio Garmin cloud non attivo:** senza accesso approvato al Garmin Connect Developer Program non esiste ancora un account collegato. Il pulsante restituisce il motivo del blocco e registra tentativi senza duplicati, non una conferma fittizia. Il JSON è un formato interno, non un file importabile da Garmin. Apple Salute resta un'integrazione successiva. Intervals.icu importa attività e wellness; Strava offre una consultazione personale temporanea separata dalle analisi. Vedi [GARMIN.md](GARMIN.md) e [architettura Iron Coach](docs/IRON_COACH.md).

Le API e il motore rimangono riutilizzabili dal client SwiftUI in `ios`. Il passaggio a iPhone richiederà comunque build, integrazioni native e verifica con Xcode.

La modalità `local` è personale, senza login e limitata al loopback e ai nomi localhost/127.0.0.1. Per ospitare clienti usa esclusivamente `IRON_MODE=hosted`, HTTPS e la configurazione descritta nella guida commerciale. Il profilo usa revisioni per proteggere le bozze; i vecchi client desktop che non inviano la revisione restano compatibili soltanto con l’installazione personale e possono sovrascrivere il profilo.

Calendario e check-in usano Europe/Rome; i timestamp delle attività restano UTC nel database. Per le vecchie attività importate con fuso non noto serve una verifica della provenienza prima di correggerle. Le nuove importazioni JSON richiedono esplicitamente l'offset.

## Verifica sviluppo

La Home consente di selezionare un giorno precedente con data e frecce; riepiloghi e metriche vengono ricostruiti fino a quel giorno. Le proposte e le modifiche per oggi restano nascoste nella vista storica. Le card della condizione fisica aprono soltanto giudizio e indicazione del Coach, senza grafici, tabelle o fonti. Le card settimanali aggiungono il confronto esplicito con i periodi precedenti. Banner promozionale e legenda sono rimossi; le note personali sono facoltative in Salute. Il riepilogo comprende palestra, camminate/escursioni e altre discipline.

`GET /dashboard/home?day=YYYY-MM-DD` aggiunge alla risposta `recovery`, con indicatori disponibili/mancanti e date delle ultime osservazioni. Le misure di ieri non vengono spostate a oggi per produrre un punteggio. `GET /experience/summary?end=YYYY-MM-DD` consulta il riepilogo fino alla data scelta. Il Coach espone `unavailable_reason`, distinguendo attività registrate, esecuzioni valutabili e sedute da confermare. Date future sono rifiutate.

### Collegamento Intervals.icu

Apri Connessioni → Collega Intervals.icu. Da `https://intervals.icu/settings`, sezione Developer Settings, genera una API key personale. Inseriscila nell'app locale, mantieni ID atleta `0` per il tuo account, scegli il periodo e premi **Collega e sincronizza**. La chiave viene verificata con il profilo remoto prima del salvataggio. Su Windows è protetta con DPAPI per l'utente corrente in `data/.secrets/intervals.dpapi`; non viene restituita dalle API, salvata nel browser o inserita nel database. Per configurazione tramite ambiente sono supportate `INTERVALS_API_KEY` e `INTERVALS_ATHLETE_ID` (default `0`).

Puoi sincronizzare attività e wellness, verificare il collegamento o scollegare conservando i dati importati. Sono mostrati ultimo risultato, ultima sincronizzazione completa ed eventuali errori parziali. Il backend attivo aggiorna i dati all'avvio e ogni 30 minuti; `IRON_AUTO_SYNC=0` disabilita gli aggiornamenti automatici. Intervals non fornisce i dati Strava e può limitare quelli Garmin. L'invio di allenamenti al calendario Intervals/Garmin resta separato. La lettura delle attività e la sincronizzazione wellness sono state verificate anche con l'account Intervals già configurato; i test automatici usano provider simulati e database isolati.

La configurazione e la sincronizzazione sono limitate al computer locale. Il salvataggio protetto dall'interfaccia richiede Windows; su altri sistemi usa variabili d'ambiente. Scollegare dalla UI crea una configurazione disabilitata che prevale sulle variabili d'ambiente.

Backend: `.venv-web\Scripts\python.exe -m pytest tests -q -p no:cacheprovider` dopo aver installato `pytest` e `httpx`. Frontend: `npm install` e `npm test` in `web/tests` (Node 22.13+). I test DOM usano risposte sintetiche; è stato verificato anche il caricamento del frontend in JSDOM con le API locali reali, senza modificare i dati. La verifica visiva nel browser non è stata completata: l'approvazione automatica ha rifiutato l'avvio di Firefox.

## Iron Coach 1.1

Home mostra condizione, sonno/HRV e riepilogo da lunedì; Salute espone grafici e fonti; Coach espone giudizi con evidenze e dati mancanti. Obiettivi consente frazioni multisport, terreno, dislivello e quota con riferimenti personalizzati: la percentuale è un indice euristico di copertura, non una probabilità di concludere la gara. Dalla versione 1.3 il pulsante **Usa per il piano** collega l'obiettivo dettagliato: discipline, distanze e dislivello orientano la ripartizione del tempo e le indicazioni delle sedute, entro i limiti di capacità e disponibilità. Il generatore rimane aerobico e prudente, senza diventare un piano gara validato.

Le attività simili da fonti diverse richiedono una conferma prima della fusione. Dati manuali e importati restano separati; Intervals ha precedenza quando il valore è disponibile. L'aggiornamento del database crea un backup preventivo in `data/backups` e registra la migrazione applicata.

Per Strava configura sul server `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET` e `STRAVA_REDIRECT_URI` (default `http://127.0.0.1:8000/integrations/strava/callback`), registrando il callback nella tua applicazione Strava. Le credenziali OAuth sono protette con DPAPI. Le attività API sono una vista personale in memoria, con scadenza massima di 24 ore: non entrano in Coach, statistiche o obiettivi. La leaderboard è esclusa. Dettagli, schema ER e API: [docs/IRON_COACH.md](docs/IRON_COACH.md).

## Usabilità 1.2

La Home propone solo i prossimi passi pertinenti, nascondibili per la sessione. Profilo e attività importate sono sufficienti per completare la configurazione; una connessione esterna e una gara restano facoltative. Il menu mobile mantiene visibili le sezioni e la navigazione da tastiera porta al titolo della pagina senza spostare il cursore durante un aggiornamento.

Durante il caricamento puoi navigare e continuare a compilare i moduli. Gli errori parziali mostrano quali dati non sono aggiornati e un comando per riprovare, separatamente dalla conferma di un salvataggio riuscito. I moduli conservano le bozze durante l'aggiornamento; la chiusura della pagina segnala modifiche non salvate. Il feedback appena salvato non viene sostituito da una risposta precedente arrivata in ritardo.

Lo storico visualizza 50 attività alla volta con conteggio e caricamento progressivo; ricerca e filtri ripartono dal primo gruppo. Trail, attività virtuali e gravel sono riconosciuti nelle rispettive discipline. I dati mancanti sono mostrati come tali. Il periodo scelto in Salute resta selezionato dopo l'aggiornamento generale.

Verifiche storiche della revisione 1.2: 232 test backend e 40 test DOM superati, inclusi conflitti di revisione, scritture SQLite concorrenti, caricamenti parziali, bozze, storico lungo e risposte fuori ordine. Nessuna nuova prova con account reali o verifica visiva in un browser.

## Funzioni 1.3

1. **Obiettivo attivo nel piano.** La gara dettagliata selezionata resta collegata al profilo. Le frazioni orientano le quote delle discipline; terreno e dislivello aggiungono indicazioni contestuali. Le quote sono pesi dichiarati di ripartizione, senza aumentare automaticamente carico o intensità. Modificare o eliminare la gara segnala che il piano deve essere rivisto.
2. **Bilancio settimanale.** Coach distingue programma, volumi registrati ed esecuzioni confermate. Le sedute passate senza riscontro restano da confermare. Puoi dichiarare una seduta saltata o un giorno di riposo indicando il motivo, e annullare la scelta finché non sono presenti nuove evidenze. L'aderenza descrive la durata confermata; non assegna un voto alle sedute senza dati.
3. **Anteprima delle modifiche.** Prima di spostare, ridurre o saltare una seduta vengono mostrati prima/dopo, combinati interessati e impatto sui minuti disponibili. La conferma ricontrolla il contesto: se piano, profilo o gara cambiano, richiede una nuova anteprima. Restano protette esecuzioni confermate e pubblicazioni Garmin.
4. **Trend personali di recupero.** HRV, frequenza a riposo e sonno sono confrontati con letture precedenti della stessa fonte. Servono tre letture recenti e almeno sette precedenti. Sono visibili provenienza, dati insufficienti, obsolescenza e check-in, senza dedurre una diagnosi dal singolo numero.
5. **Preparazione per gara.** Ogni obiettivo ha una checklist con gap fisici, tecnica, terreno/quota, transizioni multisport, attrezzatura, alimentazione, idratazione e ritmo. Spunte e note personali sono distinte dalle evidenze delle attività e non cambiano la percentuale gara. Se cambia l'obiettivo, le conferme interessate richiedono una revisione.
6. **Backup personale.** Da Connessioni puoi esportare i dati in JSON, caricare un backup e verificarne l'anteprima prima del ripristino esplicito. L'operazione conserva una copia SQLite precedente e sostituisce i dati personali; connessioni e credenziali restano sul computer. Il file esportato non contiene segreti o riferimenti agli invii Garmin. Il ripristino non pubblica o revoca allenamenti remoti e richiede di ricaricare l'app.

Ogni metrica ha una scala indipendente: rosso, arancio, giallo, verde chiaro e verde. Il grigio segnala un calo documentato della base CTL su almeno due settimane, compatibile anche con uno scarico programmato: non certifica una perdita fisiologica di prestazione. Dati assenti, datati o basati su ipotesi restano distinti dalla scala. Il colore del singolo indicatore non eredita quello del giudizio globale del Coach. Grafici e dettagli usano riferimenti personali e una finestra iniziale di quattro settimane.

Da Salute puoi segnalare infortunio, contrattura e sede, malessere o raffreddore, aggiungendo la tua ipotesi sulla causa. Il sistema confronta i giorni precedenti con lo storico senza attribuire cause mediche; le segnalazioni attive limitano la proposta giornaliera. Il profilo osservato si aggiorna automaticamente e conserva separatamente disponibilità, preferenze e soglie manuali. Le attività mostrano un voto 0–100 dell’adeguatezza dello stimolo nel contesto disponibile; l’aderenza al programma rimane distinta.

Per usare una cartella dati separata imposta `IRON_DATA_DIR` prima dell'avvio; il valore predefinito è `data`. Database, backup, profilo legacy e credenziali DPAPI seguono quella cartella. Per verifiche isolate usa anche `IRON_AUTO_SYNC=0` e un ambiente senza credenziali personali; le variabili delle integrazioni rimangono attive indipendentemente dal percorso.

Verifica 1.3 del 18 ottobre 2026: 490 test backend e 116 test frontend superati. La prova integrata in JSDOM con server locale e dati sintetici isolati ha completato sei flussi con 124 richieste, zero errori HTTP e zero errori JavaScript. Dettagli in `docs/VERIFICA_1_3.md`. Il controllo statico delle coppie cromatiche selezionate ha un contrasto minimo di 5,30:1. Layout e comportamento visivo nei tre formati non sono ancora stati verificati in un browser: l'avvio automatico di Firefox è stato rifiutato. Attività e wellness Intervals sono stati verificati anche con l’account configurato. Restano da eseguire le prove con account Strava e il trasferimento fisico dei FIT su Forerunner 955 ed Edge 530; l'invio cloud Garmin continua a richiedere accesso ufficiale.

I test di pianificazione, feedback, FIT e invio usano database isolati. L'avvio aggiunge nuove tabelle senza modificare le colonne delle tabelle esistenti; per questa evoluzione viene conservata una copia del database precedente in `data/backups`.

Moduli: Dashboard, Coach, Allenamenti, Fitness, Storico, Analisi, Profilo, Impostazioni.

Installazione: eseguire `INSTALL_FINAL.bat`. L'installer aggiorna solo `app`, `desktop` e i file di avvio; non sovrascrive `data\coach.db` né `.venv` esistenti.


## Attività, peso e allenamenti manuali

Il dettaglio di ogni attività include riepilogo completo, passo per corsa/nuoto, grafici selezionabili e traccia GPS quando disponibili. Le serie Intervals sono lette con le credenziali protette già configurate: dati assenti e interruzioni restano espliciti. Il tracciato è una visualizzazione GPS locale, senza cartografia stradale o invio delle coordinate a servizi di mappe esterni.

Il peso in Home apre il giudizio del Coach; lo storico grafico è disponibile da Salute e dal profilo atleta. Si usano misurazioni wellness osservate: valori temporanei copiati dal profilo non diventano pesate. Il profilo espone misure, soglie, totali e migliori attività su tutto lo storico e negli ultimi 90 giorni. Sono record di attività intere, non record su distanze o segmenti non misurati.

Obiettivo e piano condividono una sola voce di navigazione. Allenamenti permette di creare sedute manuali con giorno e fasi e schede palestra con serie, ripetizioni o durata, kg e recuperi. La guida palestra conserva avanzamento e timer tra aperture, senza creare attività o completamenti del calendario inventati. I salvataggi usano revisioni per proteggere modifiche simultanee.

I grafici settimanali confrontano tutte le barre con lo stesso riferimento e la stessa opacità. L’intervallo di Salute è sopra il grafico; le card manuali, duplicate e di contesto CTL/ATL quotidiano non occupano la griglia.

La revisione attività/peso/palestra ha inoltre superato una prova integrata di 66 richieste API su database isolato: salvataggi consecutivi, timer palestra e conservazione delle sedute manuali dopo generazione del piano. Dettagli in `docs/ATTIVITA_PESO_PALESTRA_2026_10_18.md`.

## Grafici, disponibilità e palestra

I grafici delle attività aggiungono riepiloghi, lettura puntuale, zoom temporale e fasce obiettivo personalizzabili. Salute distingue la fascia abituale personale da un obiettivo; la potenza bici può mostrare il riferimento endurance soltanto con FTP dichiarata. Riepilogo Home compatto, Indietro in alto a sinistra, filtro Altro e record colorati completano la revisione.

Dal piano puoi chiedere al Coach di evitare determinate fasce o giorni nel periodo scelto, leggere l’anteprima e applicarla. Le regole restano modificabili; i conflitti con sedute esistenti sono espliciti. La palestra aggiunge basi, catalogo, ricerca, riordino e riutilizzo delle schede.

Verifica aggiornata: 511 test backend, 130 frontend e 131 richieste integrate isolate senza errori. Grafici SVG ispezionati; layout completo nel browser ancora da verificare. Metodo e limiti in [Grafici e disponibilità](docs/GRAFICI_E_DISPONIBILITA_2026_10_18.md).

La card Peso include ora l’IMC, calcolato da peso registrato e altezza confermata nel profilo. Data di nascita e sesso completano il contesto; i riferimenti per adulti non vengono applicati ai più giovani. Verifica aggiornata: 540 test backend e 132 frontend, più 59 richieste integrate isolate senza errori. Dettagli e fonti in [IMC](docs/IMC_2026_10_18.md).

Il Coach ora comprende pause temporanee delle singole discipline e periodi nel messaggio: «non voglio fare nuoto fino a gennaio», «niente corsa per due settimane», «niente bici la mattina». Prima di applicare mostra date interpretate e sedute interessate. Le pause scadono automaticamente e non aumentano il carico degli altri sport. Verifica aggiornata: 580 test backend, 134 frontend e 66 richieste integrate isolate senza errori. [Dettagli](docs/COACH_PAUSE_DISCIPLINE_2026_10_18.md).
