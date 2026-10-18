from PySide6.QtWidgets import QComboBox,QDoubleSpinBox,QFormLayout,QGroupBox,QHBoxLayout,QSpinBox,QTableWidget,QTableWidgetItem
from .base import PageBase,button,card,label
class Coach(PageBase):
    def __init__(self,api):
        super().__init__(api); self.header('Coach','Decisione giornaliera, recupero e workout'); bar=QHBoxLayout(); self.sport=QComboBox(); self.sport.addItem('Automatico',None); self.sport.addItem('Ciclismo','cycling'); self.sport.addItem('Corsa','running'); self.sport.addItem('Nuoto','swimming'); self.refresh_btn=button('Aggiorna'); self.gen=button('Genera workout',True); self.refresh_btn.clicked.connect(self.refresh); self.gen.clicked.connect(self.generate); bar.addWidget(label('Sport',12,600,'#6b7280')); bar.addWidget(self.sport); bar.addStretch(); bar.addWidget(self.refresh_btn); bar.addWidget(self.gen); self.root.addLayout(bar); self.cards=QHBoxLayout(); self.root.addLayout(self.cards); self.info=label(''); self.root.addWidget(self.info); self.table=QTableWidget(0,4); self.table.setHorizontalHeaderLabels(['Fase','Durata','Target','% FTP']); self.table.horizontalHeader().setStretchLastSection(True); self.root.addWidget(self.table,1)
        action=QHBoxLayout(); action.addWidget(label('RPE reale',12,600,'#6b7280')); self.rpe=QDoubleSpinBox(); self.rpe.setRange(0,10); self.rpe.setDecimals(1); self.rpe.setSingleStep(.5); self.evaluate_btn=button('Valuta workout'); self.evaluate_btn.clicked.connect(self.evaluate); action.addWidget(self.rpe); action.addWidget(self.evaluate_btn); action.addStretch(); self.root.addLayout(action)
        g=QGroupBox('Dati giornalieri'); f=QFormLayout(g); self.sleep=QDoubleSpinBox(); self.sleep.setRange(0,16); self.sleep.setDecimals(1); self.hrv=QDoubleSpinBox(); self.hrv.setRange(0,300); self.rhr=QDoubleSpinBox(); self.rhr.setRange(0,250); self.stress=QDoubleSpinBox(); self.stress.setRange(0,100); self.steps=QSpinBox(); self.steps.setRange(0,100000); self.battery=QDoubleSpinBox(); self.battery.setRange(0,100); save=button('Salva dati'); save.clicked.connect(self.save_metrics); f.addRow('Sonno (h)',self.sleep); f.addRow('HRV (ms)',self.hrv); f.addRow('FC riposo',self.rhr); f.addRow('Stress',self.stress); f.addRow('Passi',self.steps); f.addRow('Body Battery',self.battery); f.addRow(save); self.root.addWidget(g); self.status=label('Caricamento...'); self.root.addWidget(self.status); self.current_workout_id=None; self.current_workout=None; self.refresh()
    def clear_cards(self):
        while self.cards.count(): self.cards.takeAt(0).widget().deleteLater()
    def refresh(self):
        try:
            d=self.api.coach_today(); self.clear_cards(); rd=d.get('readiness',{}); fit=d.get('fitness',{}); dec=d.get('decision',{}); self.cards.addWidget(card('Readiness','n/d' if rd.get('readiness') is None else f"{rd.get('readiness'):.0f}/100",f"conf. {rd.get('confidence',0):.0f}%")); self.cards.addWidget(card('TSB',f"{fit.get('tsb',0):.1f}")); self.cards.addWidget(card('Decisione',str(dec.get('recommendation','-')).title(),f"{dec.get('confidence',0):.0f}%")); self.cards.addWidget(card('Sport',str(dec.get('sport','-')).title())); self.show_workout(d.get('workout',{})); self.status.setText('Stato aggiornato.')
        except Exception as exc: self.status.setText(f'Errore: {exc}')
    def generate(self):
        try: w=self.api.create_workout(self.sport.currentData()); self.current_workout_id=w.get('id'); self.current_workout=w; self.show_workout(w); self.status.setText(f"Workout #{self.current_workout_id} salvato e pronto per la valutazione.")
        except Exception as exc: self.status.setText(f'Errore: {exc}')
    def show_workout(self,w):
        self.current_workout=w; self.info.setText(f"{w.get('name','-')} • {w.get('duration_min','-')} min • RPE {w.get('expected_rpe','-')}\n{w.get('reason','')}"); steps=w.get('steps',[]); self.table.setRowCount(len(steps))
        for i,s in enumerate(steps): self.table.setItem(i,0,QTableWidgetItem(str(s.get('type','')).capitalize())); self.table.setItem(i,1,QTableWidgetItem(f"{s.get('duration_min','-')} min")); self.table.setItem(i,2,QTableWidgetItem(f"{s.get('target_power_w')} W" if s.get('target_power_w') is not None else '—')); self.table.setItem(i,3,QTableWidgetItem(f"{s.get('target_power_pct_ftp')}%" if s.get('target_power_pct_ftp') is not None else '—'))
    def evaluate(self):
        if not self.current_workout_id: self.status.setText('Genera prima un workout salvato.'); return
        try: r=self.api.evaluate_workout(self.current_workout_id,{'rpe':self.rpe.value()}); self.status.setText(f"Valutazione: {r.get('evaluation',{}).get('adaptation','hold')}. Il segnale è stato registrato nello storico del Coach.")
        except Exception as exc: self.status.setText(f'Errore valutazione: {exc}')
    def save_metrics(self):
        p={'sleep_hours':self.sleep.value() or None,'hrv_ms':self.hrv.value() or None,'resting_hr_bpm':self.rhr.value() or None,'stress_score':self.stress.value() or None,'steps':self.steps.value() or None,'body_battery':self.battery.value() or None}
        try: self.api.save_daily_metrics(p); self.status.setText('Dati salvati e Coach ricalcolato.'); self.refresh()
        except Exception as exc: self.status.setText(f'Errore: {exc}')
