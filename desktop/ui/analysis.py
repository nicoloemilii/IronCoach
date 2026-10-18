from datetime import datetime,timedelta
from PySide6.QtWidgets import QGridLayout,QHBoxLayout,QTableWidget,QTableWidgetItem
from .base import PageBase,button,card,label
from .chart import LoadChart
class Analysis(PageBase):
    def __init__(self,api):
        super().__init__(api); self.header('Analisi','Andamento del carico e qualità dei dati'); b=button('Aggiorna'); b.clicked.connect(self.refresh); top=QHBoxLayout(); top.addStretch(); top.addWidget(b); self.root.addLayout(top); self.cards=QGridLayout(); self.root.addLayout(self.cards); self.root.addWidget(label('Carico giornaliero — ultimi 28 giorni',14,700)); self.chart=LoadChart(); self.root.addWidget(self.chart); self.table=QTableWidget(0,3); self.table.setHorizontalHeaderLabels(['Indicatore','Valore','Nota']); self.root.addWidget(self.table,1); self.status=label(''); self.root.addWidget(self.status); self.refresh()
    def refresh(self):
        try:
            d=self.api.coach_today(); q=self.api.quality(); a=self.api.activities(); f=d.get('fitness',{}); state=d.get('athlete_state',{}); recent=state.get('acute',{}).get('28d',{});
            while self.cards.count(): self.cards.takeAt(0).widget().deleteLater()
            self.cards.addWidget(card('CTL',f"{f.get('ctl',0):.1f}"),0,0); self.cards.addWidget(card('ATL',f"{f.get('atl',0):.1f}"),0,1); self.cards.addWidget(card('TSB',f"{f.get('tsb',0):.1f}"),0,2); self.cards.addWidget(card('Carico 28g',f"{recent.get('load',0):.0f}"),0,3)
            end=datetime.utcnow().date(); daily=[]
            for n in range(27,-1,-1):
                day=end-timedelta(days=n); daily.append(sum(float(x.get('load',0) or 0) for x in a if str(x.get('start_time',''))[:10]==day.isoformat()))
            self.chart.set_values(daily)
            rows=[('Attività','%s'%q.get('valid_activities',q.get('activities',len(a))), 'record locali'),('Copertura HR',q.get('hr_coverage_pct','n/d'),'dati disponibili'),('Copertura potenza',q.get('power_coverage_pct','n/d'),'dati disponibili'),('Readiness','disponibile' if d.get('readiness',{}).get('readiness') is not None else 'n/d','nessun dato non viene interpretato come recupero')]
            self.table.setRowCount(len(rows));
            for i,row in enumerate(rows):
                for j,v in enumerate(row): self.table.setItem(i,j,QTableWidgetItem(str(v)))
            self.status.setText('Analisi aggiornata.')
        except Exception as exc: self.status.setText(f'Errore: {exc}')
