# Coach contestuale — 18 ottobre 2026

Dashboard, approfondimenti e proposta giornaliera condividono la stessa valutazione. Il contesto comprende carico settimanale e quattro periodi comparabili, CTL/ATL/TSB osservati, recupero e copertura dei segnali, intervalli personali, check-in, densità delle sedute, obiettivo attivo con distanze e dislivello e discipline effettivamente registrate. La scelta della disciplina mantiene esclusioni e disponibilità piscina; il motore conserva storico e risposte agli allenamenti.

Regole operative esplicite, non soglie cliniche: oltre il 30% sopra la mediana del carico dei periodi precedenti il segnale è rosso e la proposta è limitata a 30 minuti facili, RPE massimo 3. Recupero sotto 35, TSB osservato <= -30 o dolore/fatica marcata nel check-in portano al riposo. Recupero sotto 50, due scostamenti dagli intervalli personali o alta densità recente richiedono una riduzione. L’obiettivo non può annullare questi limiti. Gli indicatori mancanti non vengono inventati e le stime locali non diventano osservazioni favorevoli.

La policy è applicata sia alla proposta GET sia alla creazione POST, comprese durata totale e fasi. Quando esiste già una seduta programmata e serve alleggerire, l’interfaccia propone di rivederla con anteprima; il backend rifiuta una nuova seduta aggiuntiva. Nessuna seduta esistente viene modificata automaticamente.

Le card condizione aprono solo giudizio e indicazione del Coach. Le card settimanali aggiungono un grafico a barre cronologico con valori, date e titolo accessibile. Dislivello: somma dei valori presenti, attività senza misura escluse dal conteggio. Etichetta visibile: Coach.

Verifica: 377 test Python e 73 test DOM/regole/usabilità. Il controllo API sul server personale ha rilevato carico 391, +49% sul riferimento, recupero 53,6/100, obiettivo Xtream Triathlon, dislivello 492 m e proposta coerente di 30 minuti RPE 3. Nessun nuovo allenamento personale è stato salvato dalla verifica. Test di salvataggio e blocco dei duplicati eseguiti su database isolato.

Limite: la verifica visiva nel browser resta non eseguita perché l’accesso browser è stato negato; i test DOM non la sostituiscono. Il colore rosso è stato aggiornato rispetto alla precedente verifica cromatica: il vecchio minimo 5,30:1 non è una nuova certificazione del layout.
