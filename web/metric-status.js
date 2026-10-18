'use strict';
// Colors describe available evidence. A large value alone is never "better".
(() => {
  const root = typeof window === 'undefined' ? globalThis : window;
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const status = (tone, label, reason) => ({tone, label, reason});
  const unknown = reason => status('unknown', 'Dati insufficienti', reason || 'Il confronto richiede osservazioni e riferimenti disponibili.');
  const info = (label, reason) => status('neutral', label, reason);
  const quantile = (sorted, q) => {
    const i = (sorted.length - 1) * q, lo = Math.floor(i), hi = Math.ceil(i);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo);
  };

  function band(item) {
    const supplied = item.reference_range;
    if (supplied && supplied.count >= 7 && finite(supplied.lower) && finite(supplied.upper) && supplied.lower <= supplied.upper) return supplied;
    const latest = item.latest;
    if (!latest?.date || !latest.source) return null;
    const since = new Date(latest.date + 'T12:00:00Z');
    if (!Number.isFinite(since.valueOf())) return null;
    since.setUTCDate(since.getUTCDate() - 28);
    const needsPositive = ['hrv_ms','resting_hr_bpm'].includes(item.kind);
    const values = (item.points || []).filter(p => p.source === latest.source && p.date >= since.toISOString().slice(0, 10) && p.date < latest.date && finite(p.value) && (!needsPositive || p.value > 0)).map(p => p.value).sort((a, b) => a - b);
    if (values.length < 7) return null;
    const q1 = quantile(values, .25), q3 = quantile(values, .75), spread = q3 - q1;
    return {lower:Math.max(0, q1 - 1.5 * spread), upper:q3 + 1.5 * spread, count:values.length};
  }
  function againstHistory(value, reference, kind) {
    if (!reference || !finite(reference.lower) || !finite(reference.upper) || reference.lower > reference.upper || !finite(reference.count) || reference.count < 7) return unknown('Servono almeno sette osservazioni precedenti comparabili della stessa fonte.');
    const range = `${reference.lower.toLocaleString('it-IT', {maximumFractionDigits:1})}–${reference.upper.toLocaleString('it-IT', {maximumFractionDigits:1})}`;
    const reason = `Intervallo descrittivo personale ${range}, ricavato dalla variabilità dello storico. Non è una soglia clinica.`;
    if (value < reference.lower || value > reference.upper) return status('caution', 'Fuori dal tuo intervallo', reason + ' Verifica il trend, il metodo di misura e le tue sensazioni.');
    return status('good', 'Nel tuo intervallo abituale', reason + (kind === 'sleep_hours' ? ' La durata abituale non certifica la qualità del sonno.' : ' Un singolo valore non misura da solo il recupero.'));
  }
  const palette={critical:'#dc2626',caution:'#ea6c20',watch:'#d1a500',positive:'#86ad31',good:'#138a56',detraining:'#7a8492',neutral:'#4c8096',unknown:'#ffffff'};
  function five(score,label,reason) {
    const tone=score<35?'critical':score<50?'caution':score<65?'watch':score<80?'positive':'good';
    return {...status(tone,label,reason),level:{critical:1,caution:2,watch:3,positive:4,good:5}[tone]};
  }
  function individual(item,peers=[]) {
    const value=item.latest?.value;
    if(!finite(value)||item.stale||item.latest?.source==='local_estimate') return null;
    const since=new Date(Date.parse(item.latest.date+'T12:00:00Z')-28*86400000).toISOString().slice(0,10);
    const previous=(item.points||[]).filter(p=>p.source===item.latest.source && p.date>=since && p.date<item.latest.date && finite(p.value) && (!['hrv_ms','resting_hr_bpm'].includes(item.kind)||p.value>0)).sort((a,b)=>a.date.localeCompare(b.date));
    if(item.kind==='ctl') {
      const older=previous.filter(p=>p.date<=new Date(new Date(item.latest.date+'T12:00:00Z').valueOf()-14*86400000).toISOString().slice(0,10));
      if(previous.length>=7 && older.length && older.at(-1).value>0) {
        const change=(value/older.at(-1).value-1)*100;
        if(change < -10) return {tone:'detraining',level:6,label:'Base allenante in calo',reason:`CTL ${change.toFixed(0)}% in almeno due settimane: segnale di riduzione dello stimolo, da leggere con eventuale scarico programmato.`};
        return five(change>15?60:change>8?76:90,change>15?'Crescita da controllare':'Base allenante stabile',`Variazione CTL ${change.toFixed(0)}% rispetto al riferimento precedente.`);
      }
      return null;
    }
    if(item.kind==='tsb') return five(value<=-30?20:value<=-25?40:value<-15?58:value<-5?75:value<=15?90:value<=25?70:55,value<=-25?'Fatica elevata':value<-5?'Carico in assimilazione':value<=15?'Bilancio favorevole':'Stimolo recente ridotto','Il bilancio descrive il rapporto tra carico recente e cronico; il programma dipende anche dal recupero.');
    if(item.kind==='atl') {
      const ctl=peers.find(p=>p.kind==='ctl'&&!p.stale&&p.latest?.date===item.latest.date&&p.latest?.source===item.latest.source)?.latest?.value;
      if(finite(ctl)&&ctl>0) { const ratio=value/ctl; return five(ratio>1.6?20:ratio>1.4?40:ratio>1.25?58:ratio>1.1?75:90,ratio>1.4?'Accumulo di fatica':ratio>1.1?'Stimolo da assimilare':'Fatica relativa contenuta',`Carico acuto pari al ${(ratio*100).toFixed(0)}% del carico cronico.`); }
    }
    if(['sleep_hours','hrv_ms','resting_hr_bpm'].includes(item.kind)) {
      const reference=band(item);if(!reference) return null;
      const values=previous.map(p=>p.value).sort((a,b)=>a-b);const baseline=finite(item.baseline)?item.baseline:quantile(values,.5);
      if(!finite(baseline)||baseline<=0) return null;
      let ratio=value/baseline;
      let score=item.kind==='resting_hr_bpm'?(ratio>1.2?20:ratio>1.12?40:ratio>1.07?58:ratio>1.03?75:90):(ratio<.65?20:ratio<.8?40:ratio<.9?58:ratio<.98?75:90);
      if((item.kind==='hrv_ms'&&ratio>1.5)||(item.kind==='resting_hr_bpm'&&ratio<.75)||(item.kind==='sleep_hours'&&ratio>1.4)) score=55;
      if(value<reference.lower || value>reference.upper) score=Math.min(score,55);
      return five(score,score>=80?'Nel tuo riferimento':score>=65?'Lieve scostamento':score>=50?'Scostamento da seguire':score>=35?'Segnale sfavorevole':'Scostamento marcato',`Valore ${((ratio-1)*100).toFixed(0)}% rispetto al riferimento personale. Il colore riguarda questo indicatore, non l’intero atleta.`);
    }
    if(item.kind==='body_battery') return five(value,'Riserva del dispositivo','Stima del dispositivo, da leggere con sensazioni e programma.');
    if(item.kind==='stress_score') return five(100-value,'Stress percepito','Valutazione soggettiva riportata dall’atleta.');
    return null;
  }
  function metric(item, peers = []) {
    const value = item.latest?.value;
    if (!finite(value)) return unknown('Nessun valore utilizzabile nel periodo selezionato.');
    if (item.stale) return status('unknown', 'Dato da aggiornare', 'La misura è datata: aggiorna i dati prima di interpretare il colore.');
    if (item.latest.source === 'local_estimate') return info('Stima locale da confermare', 'Il valore dipende dalle ipotesi di calcolo locali: verifica soglie e provenienza prima di usarlo come giudizio.');
    if (['hrv_ms','resting_hr_bpm'].includes(item.kind) && value <= 0) return status('caution', 'Misura da verificare', 'Il valore non consente un confronto utile: controlla la misura alla fonte.');
    const classified=individual(item,peers); if(classified) return classified;
    if (['hrv_ms','resting_hr_bpm','sleep_hours'].includes(item.kind)) return againstHistory(value, band(item), item.kind);
    if (item.kind === 'weight_kg') return info('Peso registrato', 'Il peso è una misura osservata: senza un obiettivo personale non assegno un giudizio di migliore o peggiore.');
    if (item.kind === 'ctl') return info('Carico cronico', 'Descrive la continuità del carico; un valore più alto non è automaticamente migliore.');
    if (item.kind === 'tsb') {
      if (value <= -30) return status('critical', 'Recupero da rivalutare', 'Il motore del Coach usa TSB ≤ −30 come segnale di carico elevato. Valutalo con sonno, sensazioni e storico.');
      if (value <= -25) return status('caution', 'Fatica da valutare', 'Il carico recente supera quello cronico. Il Coach invita a limitare lo stimolo; non è una diagnosi.');
      return info('Bilancio del carico', 'Un bilancio positivo o negativo può essere coerente con la fase del piano: non assegna da solo un giudizio alla condizione.');
    }
    if (item.kind === 'atl') {
      const balance = peers.find(p => p.kind === 'tsb' && !p.stale && p.latest?.source === item.latest.source && p.latest?.date === item.latest.date);
      if (finite(balance?.latest?.value) && balance.latest.value <= -25) return status('caution', 'Carico recente da valutare', 'Il bilancio TSB della stessa data segnala fatica relativa. Confrontalo con il recupero prima di aumentare il lavoro.');
      return info('Carico recente', 'Misura il lavoro recente; più fatica non significa un allenamento migliore o peggiore.');
    }
    if (item.kind === 'stress_score' && value >= 75) return status('caution', 'Stress percepito elevato', 'Il modello del piano considera questo valore dichiarato quando riduce il lavoro proposto.');
    if (item.kind === 'body_battery' && value < 30) return status('caution', 'Riserva stimata bassa', 'È una stima del dispositivo da confrontare con sensazioni e storico.');
    return info('Dato di contesto', 'Leggi il valore con la sua fonte e il tuo obiettivo; non ha una soglia universale di buono o cattivo.');
  }
  function weekly(value,reference) {
    if(!finite(value))return unknown('Nessuna misura nel periodo.');
    if(!finite(reference)||reference<=0)return info('Riferimento in costruzione','Servono periodi confrontabili.');
    const ratio=value/reference;
    const tone=ratio>1.3?'critical':ratio>1.2?'caution':ratio>1.1?'watch':ratio<.7?'watch':ratio<.9?'positive':'good';
    return status(tone,{critical:'Incremento marcato',caution:'Sopra il riferimento',watch:ratio<.7?'Stimolo ridotto':'Aumento da seguire',positive:'Stimolo contenuto',good:'In linea con il riferimento'}[tone],'Confronto con un unico riferimento personale per tutte le settimane.');
  }
  function readiness(value, confidence = null) {
    if (!finite(value) || value < 0 || value > 100) return unknown('Servono dati di recupero sufficienti per il modello.');
    if (finite(confidence) && confidence < 50) return status('unknown', 'Stima parziale', 'Il modello usa pochi dati di recupero. Completa check-in e osservazioni prima di interpretare il punteggio.');
    return five(value,value<35?'Recupero prioritario':value<50?'Recupero basso':value<65?'Recupero parziale':value<80?'Recupero discreto':'Recupero favorevole','Stima del modello basata sui segnali disponibili.');
  }
  function assessment(value) {
    const score = value?.score;
    if (!finite(score) || score < 0 || score > 100) return unknown('Mancano evidenze sufficienti per un giudizio. Le sedute non confermate non sono considerate automaticamente sbagliate.');
    return five(score,score<35?'Critico':score<50?'Da correggere':score<65?'Da consolidare':score<80?'Discreto':'Ottimale','Valutazione della seduta nel contesto documentato.');
  }
  function coverage(score, personal = false) {
    if (!finite(score) || score < 0 || score > 100) return unknown('Completa riferimenti e dati necessari per il confronto.');
    if (personal && score >= 100) return info('Confermato da te', 'È una conferma personale, distinta dalle capacità osservate nelle attività.');
    if (score >= 100) return status('good', 'Riferimento coperto', 'I requisiti configurati risultano coperti; non è una probabilità di concludere la gara.');
    return status('caution', 'Gap da colmare', 'La copertura dei riferimenti è parziale. Apri le singole voci per identificare il divario.');
  }
  function trend(item) {
    if (item.stale) return status('unknown', 'Trend da aggiornare', 'Le letture recenti sono datate.');
    if (!finite(item.current) || !finite(item.baseline) || !Number.isInteger(item.counts?.recent) || !Number.isInteger(item.counts?.baseline) || item.counts.recent < 3 || item.counts.baseline < 7) return unknown('Servono tre letture recenti e sette letture precedenti della stessa fonte.');
    if (['hrv_ms','resting_hr_bpm'].includes(item.kind) && (item.current <= 0 || item.baseline <= 0)) return status('caution', 'Misure da verificare', 'Il confronto contiene valori non utilizzabili: controlla le misure alla fonte.');
    const evidence=againstHistory(item.current,item.reference_range||null,item.kind);
    if(evidence.tone==='unknown')return evidence;
    return individual({...item,latest:{value:item.current,date:item.date||'2026-01-01',source:item.source||'same_source'},baseline:item.baseline,points:[],reference_range:item.reference_range})||evidence;
  }
  function apply(node, value, showReason = true) {
    if (!node || !value) return;
    const tones = ['good','positive','watch','caution','critical','detraining','neutral','unknown'];
    node.dataset.tone = tones.includes(value.tone) ? value.tone : 'unknown';
    for (const child of [...node.children]) if (child.dataset.statusMarker) child.remove();
    const document = node.ownerDocument;
    const chip = document.createElement('span'); chip.className = 'metric-status'; chip.dataset.statusMarker = 'true';
    const icon = document.createElement('span'); icon.className = 'metric-status-icon'; icon.setAttribute('aria-hidden','true');
    icon.textContent = {good:'✓',caution:'!',critical:'×',neutral:'i',unknown:'?',positive:'↗',watch:'!',detraining:'↘'}[node.dataset.tone];
    const label = document.createElement('span'); label.textContent = value.label; chip.append(icon,label);
    const anchor = [...node.children].find(child => child.tagName === 'STRONG') || [...node.children].find(child => /^H[1-6]$/.test(child.tagName)) || node.firstElementChild;
    if (anchor) anchor.after(chip); else node.append(chip);
    if (showReason && value.reason) { const text = document.createElement('p'); text.className = 'status-reason'; text.dataset.statusMarker = 'true'; text.textContent = value.reason; node.append(text); }
  }
  root.IronCoachMetricStatus = {metric, five, palette, individual, weekly, readiness, assessment, coverage, trend, info, unknown, apply, band};
  if (typeof module !== 'undefined' && module.exports) module.exports = root.IronCoachMetricStatus;
})();
