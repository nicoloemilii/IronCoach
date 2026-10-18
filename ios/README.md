# Ironman Coach per iPhone — prima base nativa

Client SwiftUI iOS 17+ del backend in `ironman_coach`. Include dashboard reale, recupero con dati mancanti espliciti, dettaglio dei passi delle sedute, creazione di sedute giornaliere per nuoto/bici/corsa e modifica dell'obiettivo gara. Nessun dato demo e nessuna sincronizzazione simulata.

## Avvio su Mac

1. Installare Xcode e XcodeGen (`brew install xcodegen`).
2. Da questa cartella eseguire `xcodegen generate`, aprire `IronmanCoach.xcodeproj` e scegliere il proprio team di firma.
3. Installare le dipendenze Python del backend. Dalla cartella `ironman_coach` avviare `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000` solo su rete privata.
4. In Connessioni inserire l'URL del server raggiungibile da iPhone. Per HTTP locale usare un hostname `.local` risolvibile; la configurazione ATS consente rete locale senza disattivare globalmente la sicurezza. Con indirizzi IP usare HTTPS con certificato attendibile. Sul simulatore il backend può essere sul Mac.
5. Toccare Collega e carica, verificare obiettivo, dashboard e creazione di una seduta.

Il client usa `/coach/today`, `/coach/workouts`, `/athlete/profile`. Il salvataggio obiettivo rilegge il profilo completo per preservare FTP e altri campi del backend legacy. Evitare modifiche concorrenti da desktop durante il salvataggio: il server non ha versionamento del profilo.

## Confini attuali

- Garmin, Strava e HealthKit non sono ancora implementati nel client. Le attività già importate nel backend sono utilizzate dal coach.
- Il motore attuale propone sedute giornaliere: piano settimanale, disponibilità oraria, distanza gara, feedback iOS e guida con timer sono lavoro successivo.
- Il backend è per un solo atleta, senza autenticazione: non pubblicarlo su Internet. Prima di distribuirlo servono autenticazione, isolamento utenti, gestione sicura dei token e consensi.
- Questa base non è stata compilata su Windows: build, firma, permessi rete e funzionamento sul dispositivo devono essere verificati in Xcode. Non è un'app pronta per App Store.
- Il progetto desktop, gli archivi e i dati esistenti non vengono modificati da questa aggiunta. Le azioni di salvataggio effettuate dall'app scrivono nel backend collegato.
