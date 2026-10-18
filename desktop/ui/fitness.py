from PySide6.QtWidgets import QGridLayout,QHBoxLayout,QTableWidget,QTableWidgetItem
from .base import PageBase,button,card,label
class Fitness(PageBase):
    def __init__(self,api):
        super().__init__(api); self.header('Fitness','CTL, ATL e TSB globali e per sport'); b=button('Aggiorna'); b.clicked.connect(self.refresh); top=QHBoxLayout(); top.addStretch(); top.addWidget(b); self.root.addLayout(top); self.cards=QGridLayout(); self.root.addLayout(self.cards); self.table=QTableWidget(0,4); self.table.setHorizontalHeaderLabels(['Sport','CTL','ATL','TSB']); self.table.horizontalHeader().setStretchLastSection(True); self.root.addWidget(self.table,1); self.status=label(''); self.root.addWidget(self.status); self.refresh()
    def refresh(self):
        try:
            g=self.api.fitness(); s=self.api.fitness_sports();
            while self.cards.count(): self.cards.takeAt(0).widget().deleteLater()
            self.cards.addWidget(card('CTL',f"{g.get('ctl',0):.1f}",'42 giorni'),0,0); self.cards.addWidget(card('ATL',f"{g.get('atl',0):.1f}",'7 giorni'),0,1); self.cards.addWidget(card('TSB',f"{g.get('tsb',0):.1f}"),0,2); self.cards.addWidget(card('Attività',str(g.get('activities_used',0))),0,3)
            rows=[]
            for k,v in sorted(s.get('sports',{}).items()): rows.append((str(k).title(),float(v.get('ctl',0)),float(v.get('atl',0)),float(v.get('tsb',0))))
            self.table.setRowCount(len(rows))
            for i,row in enumerate(rows):
                for j,val in enumerate(row): self.table.setItem(i,j,QTableWidgetItem(f'{val:.1f}' if j else str(val)))
            self.status.setText('Fitness aggiornata.')
        except Exception as exc: self.status.setText(f'Errore: {exc}')
