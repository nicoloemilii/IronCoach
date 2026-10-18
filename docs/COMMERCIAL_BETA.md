# Iron Coach 2.0: beta commerciale web

Questa revisione prepara un servizio web per singoli atleti. Non attiva conti di pagamento, domini, email o pubblicazioni esterne e non certifica sicurezza, conformità legale o validità della preparazione sportiva. L’installazione personale esistente resta distinta e non viene copiata automaticamente negli account.

## Funzioni del prodotto

- Piano multisport adattabile, calendario, anteprima/annullamento, preferenze e pause temporanee di disciplina.
- Storico attività, metriche personali, peso/IMC, profilo e record osservati, con provenienza e dati mancanti espliciti.
- Allenamenti manuali e palestra: 201 esercizi, ricerca/filtri, schede riutilizzabili, serie, timer e avanzamento persistente.
- Account con conferma email o invito privato, recupero password, sessioni revocabili e cambio password.
- Prova a durata configurabile; abbonamento Stripe, checkout riutilizzabile e portale per pagamenti/cancellazione.
- Esportazione personale gratuita anche dopo la prova; richiesta di cancellazione con disattivazione immediata dell’account.

Il motore rimane quello prudente documentato nell’app. Non è diventato un servizio medico o una preparazione gara individuale validata. FIT bici/corsa e lettura Intervals restano disponibili; consegna Garmin cloud e collaudo fisico dei dispositivi non sono stati aggiunti. Strava è disabilitato nella modalità hosted: il vecchio cache locale non viene condiviso fra clienti. Desktop e iOS non sono stati trasformati in prodotti commerciali in questa revisione.

## Due modalità distinte

`IRON_MODE=local` conserva il database personale e le credenziali DPAPI. L’accesso HTTP è limitato al computer locale; l’app non richiede un account. Avvia con `Avvia_Web.bat`, che controlla anche le nuove dipendenze.

`IRON_MODE=hosted` richiede un’origine HTTPS e una chiave privata Fernet. La registrazione è chiusa per impostazione predefinita. In `IRON_DATA_DIR` si trovano `accounts.db` e `workspaces/<uuid>/coach.db`: ogni richiesta associa l’identità verificata al relativo archivio. Nessun parametro del client sceglie il workspace. Profilo legacy, credenziali, cache e lavori asincroni rispettano lo stesso confine. Gli account nuovi non ereditano misure o gare dell’installazione personale.

Un solo processo Uvicorn: scheduler e coordinamento dei ripristini sono progettati per una singola istanza. Non aggiungere worker o repliche senza spostare coordinamento e job in servizi condivisi e collaudare la concorrenza. La beta usa SQLite per atleta; non è stata dimensionata con test di carico di produzione.

## Configurazione e distribuzione

1. Copia `.env.example` in `.env` sul server. I file compilati non vanno nel repository o in chat.
2. Imposta dominio, `IRON_PUBLIC_URL=https://tuo-dominio` e contatto ACME. Il dominio deve puntare al server.
3. Genera due chiavi indipendenti con `python -m app.commercial.cli keygen`: una per `IRON_VAULT_KEY`, una per `IRON_BACKUP_KEY`. Conservale separatamente dai dati, in un gestore dei segreti. Perdere la prima rende inutilizzabili le credenziali delle integrazioni; perdere la seconda impedisce il ripristino degli snapshot cifrati.
4. Pubblica condizioni e informativa del titolare, imposta URL HTTPS, versione e supporto. Finché mancano, la registrazione viene rifiutata. Il software conserva le conferme e la versione accettata; i testi legali non sono generati o approvati automaticamente.
5. Configura SMTP con STARTTLS e un mittente autorizzato. Le password non vengono inviate per email; i collegamenti scadono e sono monouso. Se l’email non funziona, usa inizialmente solo inviti e verifica il canale di assistenza.
6. Verifica `python -m app.commercial.cli check`. Poi esegui `docker compose up --build -d` con Docker Engine e Compose moderni. Solo Caddy pubblica 80/443; la porta del backend resta nella rete privata. La fiducia nelle intestazioni del proxy richiede questa topologia.
7. Controlla `/health` e `/ready`, registrazione, login e isolamento di due account sul dominio effettivo prima di invitare clienti.

L’immagine usa un utente senza privilegi e filesystem applicativo in sola lettura. Il volume dati deve essere su storage cifrato e protetto dal fornitore di hosting: i database di allenamento non sono cifrati dall’applicazione. Solo credenziali delle integrazioni e snapshot operativi sono cifrati nel codice. Proteggi accessi amministrativi, segreti, log, copie esterne e monitoraggio sul servizio scelto.

Per invitare un atleta: `python -m app.commercial.cli invite atleta@example.com --days 7`. Il comando restituisce un collegamento privato, da consegnare attraverso un canale autorizzato. Non invia messaggi automaticamente. La registrazione aperta richiede `IRON_REGISTRATION_OPEN=1`, SMTP e documenti del servizio.

## Pagamenti

Configura prodotto/prezzo ricorrente nell’account Stripe del titolare e imposta `IRON_STRIPE_SECRET_KEY`, `IRON_STRIPE_PRICE_ID`, `IRON_STRIPE_WEBHOOK_SECRET`. Registra `/billing/webhook` per gli eventi `customer.subscription.created`, `customer.subscription.updated` e `customer.subscription.deleted`.

Il server usa sempre il prezzo configurato; non accetta prezzi o customer ID dal browser. Il checkout aperto viene riutilizzato. Il webhook verifica firma e corpo originale, conserva gli ID elaborati e rilegge lo stato corrente dell’abbonamento per non riattivare accessi con eventi vecchi. Il piano deve contenere il prezzo previsto e appartenere al customer del relativo account. L’accesso pagato richiede stato attivo/trialing e periodo non scaduto.

Parti in modalità test. Verifica pagamento riuscito, fallito, doppio clic, rinnovo, ritardo/duplicazione webhook e cancellazione dal portale. Prezzo, imposte, fatture, rimborsi, condizioni della prova e paesi serviti sono decisioni/configurazioni del titolare. Solo dopo il collaudo reale abilita `IRON_BILLING_REQUIRED=1`. La durata della prova locale è `IRON_TRIAL_DAYS`; nessuna carta è addebitata dalla sola registrazione. Restano consultabili/esportabili i dati se la prova termina.

## Dati, backup e cancellazione

Da Account, l’atleta può scaricare dati di account e allenamento senza password hash, sessioni o chiavi. La richiesta di cancellazione richiede password ed email, disattiva l’account e revoca le sessioni. Prima va chiuso un eventuale abbonamento ancora attivo; l’app non simula una cancellazione Stripe.

La rimozione fisica è un’operazione amministrativa esplicita: a servizio arrestato, `python -m app.commercial.cli purge-deletions --older-than-days 30`. Il parametro deve seguire l’informativa effettiva del titolare: 30 giorni è un valore tecnico predefinito, non una prescrizione legale. Si eliminano soltanto workspace di account disattivati con richiesta scaduta; l’identità residua viene anonimizzata nel registro operativo. Le copie di backup e i documenti di fatturazione richiedono politiche di conservazione distinte.

Snapshot operativo: `python -m app.commercial.cli backup --output /percorso-esterno/iron-coach.enc`. Il comando include SQLite account e workspace, profili e credenziali cifrate, con manifest e hash, quindi cifra l’archivio con la chiave di backup. Non sovrascrive un file esistente. Ogni database usa l’online backup SQLite; per un punto temporale comune a tutti gli archivi arresta i processi di scrittura. Limiti: 128 MB compressi e 2 GB complessivi. Oltre tali dimensioni usa una procedura collaudata di backup cifrato del volume.

Ripristino: `python -m app.commercial.cli restore --input /percorso/iron-coach.enc --target /nuova-cartella`. La destinazione deve essere inesistente. Si verificano autenticità, percorsi, manifest, hash e integrità SQLite; sessioni e token di recupero vengono revocati. I dati correnti non vengono sostituiti automaticamente. Verifica la copia, riconcilia stato pagamenti e cancellazioni intervenute dopo il backup, poi cambia il volume a servizio arrestato. Non perdere la chiave delle integrazioni usata per quei dati.

## Verifiche e limiti del rilascio

- 619 test backend, inclusi 39 nuovi controlli di account, identità, isolamento, CSRF, sessioni, recupero, pagamenti, backup/ripristino e cancellazione.
- 144 test frontend, inclusi moduli di accesso, consenso, token transitori, protezione delle richieste e prezzo restituito dal server.
- Prova integrata su FastAPI reale e JSDOM isolati: due account, 88 richieste, nessun errore imprevisto, rifiuto atteso di una richiesta con sessione precedente, scheda palestra e progresso persistiti, esportazione isolata.
- Audit delle dipendenze runtime Python e test npm senza vulnerabilità note alla verifica. È una fotografia delle banche dati degli audit, non una garanzia di assenza di vulnerabilità. Le dipendenze test npm non entrano nell’immagine.
- CI per Windows/Linux, audit e build Docker predisposti. La build Docker e i controlli Linux/Windows sono stati verificati su GitHub. La CI include inoltre uno smoke test del container con account, dati isolati e recupero operativo. Docker Engine/Compose utilizzabili e verifica visiva nel browser non erano disponibili sulla macchina locale. I test dei servizi Stripe/SMTP/Intervals non hanno usato conti di produzione.

Prima della vendita pubblica rimangono collaudo sul dominio con TLS, email e Stripe reali in test mode, prova di carico e recupero operativo sul servizio scelto, revisione indipendente della sicurezza, documenti/ruoli/conservazione dei dati del titolare e verifica delle licenze e del marchio commerciale. Le funzioni di coaching mantengono i limiti delle versioni precedenti.

Riferimenti tecnici: [sessioni OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html), [hash password FastAPI](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/), [webhook Stripe](https://docs.stripe.com/billing/subscriptions/webhooks), [Fernet](https://cryptography.io/en/latest/fernet/). Le API e i test locali sono l’evidenza delle funzioni implementate; questi riferimenti non certificano il prodotto.
