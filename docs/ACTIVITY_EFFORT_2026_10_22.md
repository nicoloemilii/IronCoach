# Serie registrate e confronto dello sforzo — 22 ottobre 2026

Il dettaglio delle attività calcola le statistiche sui campioni originali, prima della riduzione grafica. La visualizzazione conserva picchi e interruzioni; i riepiloghi dichiarano che restano riferiti alla registrazione completa quando si cambia lo zoom. Due pulsanti permettono di leggere i campioni anche senza puntatore. Le fasce obiettivo personali e il riferimento endurance basato sulla FTP esistente restano disponibili.

Per la bici, quando sono presenti potenza e frequenza cardiaca, un solo riquadro nello stesso pannello confronta le due metà temporali: medie pesate, minuti con entrambe le misure disponibili e copertura. Blu e viola identificano i periodi; non indicano un esito favorevole o sfavorevole. Formula, dati e limiti sono espandibili. La tabella ha intestazioni e un contenitore scorrevole raggiungibile da tastiera.

## Dati e calcoli

- L'analisi usa le serie grezze già lette dal dettaglio; non abbina due serie campionate separatamente per il grafico. Non introduce chiamate aggiuntive ai provider, tabelle, scritture o modifiche del piano.
- La finestra parte da zero. Il termine è il tempo trascorso dichiarato dalla fonte, limitato al tempo realmente trascorso dalla partenza. La durata in movimento non sostituisce il tempo trascorso. Se quest'ultimo manca, si usa l'ultimo timestamp ricevuto: la UI avverte che una coda non registrata non è quantificabile. Le parti iniziali e finali mancanti entro la finestra concorrono alla copertura mancante.
- Le metà hanno uguale durata trascorsa, non uguale numero di campioni. Gli intervalli che attraversano il punto medio vengono divisi in quel punto.
- Sono utilizzabili soltanto intervalli tra indici originali adiacenti con tempi strettamente crescenti, potenza finita non negativa e FC finita positiva a entrambi gli estremi. Nessun riempimento da medie, soglie o campioni precedenti.
- L'integrazione è trapezoidale, con interpolazione lineare tra gli estremi dello stesso breve intervallo. Potenza e FC usano esattamente lo stesso supporto temporale.
- Il massimo vuoto collegabile è il minore fra 10 secondi e tre volte la mediana degli intervalli positivi validi. Gli intervalli più lunghi sono esclusi. Questo criterio è una scelta tecnica esplicita, non una soglia fisiologica.
- Gli zeri di potenza restano inclusi. Il tempo a potenza zero conta soltanto gli intervalli con entrambi gli estremi a zero; non viene interpretato come sosta o assenza di movimento.
- La copertura di ciascuna metà è il tempo abbinato diviso per l'intera durata della metà. Per mostrare il confronto percentuale servono almeno l'80% di copertura e almeno 300 secondi abbinati in ciascuna metà, senza ambiguità temporali o troncamento dei dati grezzi. Anche questi sono criteri tecnici di disponibilità, non di salute o prestazione.
- Rapporto descrittivo: `potenza media pesata / FC media pesata`, in W/bpm. Variazione: `100 × (rapporto seconda metà / rapporto prima metà − 1)`. Se il primo rapporto è zero, la variazione resta indisponibile. Il segno indica soltanto la direzione del rapporto osservato. I valori esposti sono arrotondati.
- Le medie delle singole metà restano consultabili con copertura insufficiente, con avviso; una scala ambigua o un input troncato non mostra un confronto. I valori assenti non diventano zero.

Le statistiche dei singoli grafici usano il proprio canale completo. La media temporale esclude gli intervalli non supportati; se manca qualsiasi intervallo utilizzabile, la media dei campioni originali è esplicitamente etichettata. Queste medie possono differire sia dal riepilogo del provider sia dalle medie abbinate fra FC e potenza. La riduzione del grafico non entra nei calcoli.

## Robustezza e confini

Il parser considera al massimo 64 righe di canali e 250.000 punti per canale, dichiarando il troncamento. I canali ripetuti sono esclusi; non viene scelto arbitrariamente l'ultimo. Sono esclusi tutti i campioni dei gruppi con timestamp duplicati; i tempi fuori ordine non vengono riordinati. I punti GPS con un asse temporale presente devono appartenere a quell'asse valido. Una traccia separata senza timestamp mantiene esplicitamente tempi sconosciuti.

Le interruzioni scartate restano confini tra segmenti anche quando la visualizzazione è ridotta a 900 punti. Numeri enormi non rappresentabili, tipi di canale malformati e JSON troppo annidati non impediscono l'apertura dei riepiloghi disponibili. La generazione degli assi è limitata e gestisce anche valori finiti estremi senza cicli illimitati.

Il risultato è descrittivo. Non misura fitness, deriva cardiaca, efficienza aerobica o rischio; non assegna zone ottimali universali e non modifica gli allenamenti. Riscaldamento, ripetute, terreno e condizioni della seduta possono cambiare il rapporto. Non viene stabilito che la seduta soddisfi condizioni aerobiche costanti.

Il metodo differisce dall'EF di TrainingPeaks, che usa potenza normalizzata o passo corretto per pendenza e richiede un contesto appropriato di sforzo stabile. Non ne trasferiamo la classificazione percentuale a questa formula. [Definizione ufficiale TrainingPeaks](https://help.trainingpeaks.com/hc/en-us/articles/204071724-Aerobic-Decoupling-Pw-Hr-and-Pa-HR-and-Efficiency-Factor-EF).

In uno studio controllato, caldo e disidratazione modificavano la risposta cardiaca durante esercizio. Non attribuiamo quindi il cambiamento osservato alla sola forma fisica. Questa è una nostra scelta interpretativa prudente, non una validazione clinica del nuovo indicatore. [González-Alonso e colleghi, 1997](https://journals.physiology.org/doi/full/10.1152/jappl.1997.82.4.1229). Anche alcuni farmaci influenzano la FC; l'app non deduce assunzioni o condizioni dell'atleta. [American Heart Association](https://www.heart.org/en/health-topics/consumer-healthcare/medication-information/how-do-beta-blocker-drugs-affect-exercise).

## Verifiche

| Ambito | Evidenza |
| --- | --- |
| Backend completo | 1.032 test superati; un avviso preesistente Starlette/httpx |
| Frontend completo | 260 test superati |
| Calcoli | Segnali lineari con medie analitiche note, campionamento irregolare, intervalli attraversanti il punto medio, zeri, null, code mancanti, tempi ambigui, troncamento e soglie di copertura |
| Grafici | Statistiche grezze distinte dai punti ridotti, assenza di collegamenti sui vuoti omessi, scope dello zoom, conversione delle unità e assi finiti estremi |
| HTTP e DOM isolati | HTML/JS di produzione in JSDOM, 45 richieste GET reali, 16 risorse identiche ai sorgenti, dieci controlli integrati, nessun errore HTTP/DOM o risorsa esterna; tutte le tabelle invariate |
| Server personale | Copia SQLite prima del riavvio, 13 letture API e 15 asset verificati; contenuti delle 27 tabelle invariati al confronto |
| Layout nel browser | Non verificato in questa revisione: l'inventario del browser integrato è vuoto. JSDOM non sostituisce una prova visiva |

Gli artefatti privati sono in `.qa/goal-effort/`. Database, credenziali, risposte personali, immagini e risultati QA sono esclusi dal diff e dal pacchetto dei sorgenti. Questa revisione è locale; non costituisce pubblicazione o validazione sui dispositivi.
