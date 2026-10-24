# Verifica funzionale del profilo: potenze nello storico analizzato

Eseguita il 23 ottobre 2026 con HTML e tutti gli script di produzione, JSDOM e HTTP reale verso la fixture isolata su `127.0.0.1:8006`. Non è una verifica visiva o con browser/screen reader reale. Nessun dato personale né credenziale/provider reale è stato usato.

`review_dom.json`: esito positivo, 6 verifiche integrate, 96 richieste, 19 risorse corrispondenti byte per byte ai file locali, nessun errore HTTP/DOM e nessuna risorsa esterna. Le sorgenti sono rimaste invariate durante la prova. Le sole mutazioni autorizzate sono due POST di analisi locale e un POST di interruzione. Tutte le 27 tabelle originali hanno conservato conteggio e SHA-256; le due tabelle derivate `effort_analyses` e `effort_scans` contengono rispettivamente sette analisi e due lavori. Provider configurati: zero; chiamate alla tripwire provider: zero.

La prova completa verifica che Home non avvii letture dei record né analisi, che il mount resti fuori da `derived-profile` e che il comando esplicito analizzi sette attività passate multisport escludendo quella futura. I filtri producono 450/150/90 W per tutto lo storico/90/28 giorni, mentre la corsa resta separata a 300 W. Le durate disponibili riportano candidati, intervallo, data e provenienza dell'attività; uno zero misurato rimane selezionabile e apre l'attività originale con il comando Indietro primo nell'intestazione.

Una risposta di lettura fallita, iniettata dopo un HTTP reale 200, conserva risultato e ambito precedenti e propone il retry del filtro corrente. Una risposta HTTP obsoleta trattenuta non può sovrascrivere un filtro più recente. Aggiornamento globale e navigazione conservano periodo, disciplina, durata selezionata e focus; l'interruzione è cooperativa e uscire dalla pagina non invia ulteriori richieste di annullamento.

I 19 test mirati del modulo coprono inoltre avvio incerto dopo errore di rete con riconciliazione GET, polling fallito senza ripetere il POST, risposta di un vecchio job dopo la scoperta di un job nuovo, input non numerici, markup inerte e indice completato che non equivale a potenza presente in tutte le attività.

`review_dom-initial.json` conserva il primo tentativo, fermato da un'aspettativa errata del test: dopo aver scelto 60 s il test presumeva un ritorno automatico ai 5 s al cambio periodo. Il comportamento di produzione manteneva correttamente la durata. Il tentativo finale usa una nuova directory DB isolata, preservando l'archivio del primo tentativo. La fixture applica soltanto un ritardo artificiale di 150 ms per analisi, necessario per rendere osservabile l'interruzione cooperativa; calcoli e route rimangono quelli di produzione.

Il server finale resta disponibile per ulteriori verifiche autorizzate: processo Python effettivo 11292, launcher 24536. I PID sono dati del momento della prova e vanno ricontrollati prima di usarli.
