from PySide6.QtWidgets import QGridLayout,QHBoxLayout,QTableWidget,QTableWidgetItem
from .base import PageBase,button,card,label
class History(PageBase):
    def __init__(self,api):
        super().__init__(api); self.header('Storico','Volume, capacità storica e sedute'); b=button('Aggiorna'); b.clicked.connect(self.refresh); top=QHBoxLayout(); top.addStretch(); top.addWidget(b); self.root.addLayout(top); self.cards=QGridLayout(); self.root.addLayout(self.cards); self.table=QTableWidget(0,7); self.table.setHorizontalHeaderLabels(['Data','Sport','Nome','Min','Km','Carico','Sorgente']); self.root.addWidget(self.table,1); self.status=label(''); self.root.addWidget(self.status); self.refresh()
    def refresh(self):
        try:
            d=self.api.coach_today(); h=d.get('historical',{}); a=h.get('all_time',{}); r=h.get('recent_90d',{}); c=d.get('historical_capacity',{});
            while self.cards.count(): self.cards.takeAt(0).widget().deleteLater()
            vals=[('Attività',a.get('activities',0)),('Ore',f"{a.get('duration_hours',0):.1f}"),('Carico totale',f"{a.get('load',0):.0f}"),('Picco sett.',f"{a.get('max_weekly_load',0):.0f}"),('90 giorni',f"{r.get('duration_hours',0):.1f} h"),('Frequenza',f"{r.get('avg_activities_per_week',0):.1f}/sett"),('Confidenza',f"{c.get('confidence',0):.0f}%"),('Dati storici','OK' if h.get('status')=='ok' else 'n/d')]
            for i,(t,v) in enumerate(vals): self.cards.addWidget(card(t,str(v)),i//4,i%4)
            rows=self.api.activities()[:300]; self.table.setRowCount(len(rows))
            for i,x in enumerate(rows):
                dur=float(x.get('duration_seconds') or 0); dist=float(x.get('distance_meters') or 0); vals=[str(x.get('start_time','')).replace('T',' ')[:16],str(x.get('sport','')).title(),str(x.get('name','') or ''),f'{dur/60:.0f}',f'{dist/1000:.1f}' if dist else '—',f"{x.get('load',0):.1f}",str(x.get('source',''))]
                for j,v in enumerate(vals): self.table.setItem(i,j,QTableWidgetItem(v))
            self.status.setText(f'{len(rows)} attività mostrate.')
        except Exception as exc: self.status.setText(f'Errore: {exc}')
