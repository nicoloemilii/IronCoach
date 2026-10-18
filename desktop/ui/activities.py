from PySide6.QtWidgets import QFileDialog,QHBoxLayout,QLineEdit,QTableWidget,QTableWidgetItem,QTabWidget,QWidget,QVBoxLayout
from .base import PageBase,button,label
class Activities(PageBase):
    def __init__(self,api):
        super().__init__(api); self.header('Allenamenti','Attività eseguite e workout programmati'); bar=QHBoxLayout(); self.search=QLineEdit(); self.search.setPlaceholderText('Cerca nome, sport o sorgente'); self.search.textChanged.connect(self.populate); s=button('Sincronizza Intervals',True); s.clicked.connect(self.sync); f=button('Importa FIT'); f.clicked.connect(self.import_fit); r=button('Aggiorna'); r.clicked.connect(self.refresh); bar.addWidget(self.search,1); bar.addWidget(s); bar.addWidget(f); bar.addWidget(r); self.root.addLayout(bar); self.tabs=QTabWidget(); a=QWidget(); al=QVBoxLayout(a); self.table=QTableWidget(0,9); self.table.setHorizontalHeaderLabels(['Data','Sport','Nome','Durata','Distanza','FC','Potenza','Carico','Sorgente']); self.table.horizontalHeader().setStretchLastSection(True); al.addWidget(self.table); self.tabs.addTab(a,'Attività eseguite'); p=QWidget(); pl=QVBoxLayout(p); self.planned=QTableWidget(0,6); self.planned.setHorizontalHeaderLabels(['Data','Sport','Workout','Min','Stato','ID']); self.planned.horizontalHeader().setStretchLastSection(True); pl.addWidget(self.planned); self.tabs.addTab(p,'Workout Coach'); self.root.addWidget(self.tabs,1); self.status=label(''); self.root.addWidget(self.status); self.rows=[]; self.workout_rows=[]; self.refresh()
    def refresh(self):
        try: self.rows=self.api.activities(); self.workout_rows=self.api.workouts(); self.populate(); self.populate_workouts(); self.status.setText(f'{len(self.rows)} attività • {len(self.workout_rows)} workout Coach.')
        except Exception as exc: self.status.setText(f'Errore: {exc}')
    def populate(self):
        q=self.search.text().lower().strip(); rows=[x for x in self.rows if not q or q in str(x.get('name','')).lower() or q in str(x.get('sport','')).lower() or q in str(x.get('source','')).lower()]; self.table.setRowCount(len(rows))
        for i,x in enumerate(rows):
            dur=float(x.get('duration_seconds') or 0); dist=float(x.get('distance_meters') or 0); power=x.get('normalized_power') or x.get('avg_power'); vals=[str(x.get('start_time','')).replace('T',' ')[:16],str(x.get('sport','')).title(),str(x.get('name','') or ''),f'{dur/60:.0f} min',f'{dist/1000:.1f} km' if dist else '—',f"{x.get('avg_hr'):.0f}" if x.get('avg_hr') is not None else '—',f'{power:.0f} W' if power is not None else '—',f"{x.get('load',0):.1f}",str(x.get('source',''))]
            for j,v in enumerate(vals): self.table.setItem(i,j,QTableWidgetItem(v))
    def populate_workouts(self):
        self.planned.setRowCount(len(self.workout_rows))
        for i,x in enumerate(self.workout_rows):
            vals=[str(x.get('date','')),str(x.get('sport','')).title(),str(x.get('name','')),f"{x.get('duration_min','-')}",str(x.get('status','')).title(),str(x.get('id',''))]
            for j,v in enumerate(vals): self.planned.setItem(i,j,QTableWidgetItem(v))
    def sync(self):
        try: r=self.api.sync_intervals(); self.status.setText(f"Sync completato: {r.get('new_activities',0)} nuove."); self.refresh()
        except Exception as exc: self.status.setText(f'Errore sync: {exc}')
    def import_fit(self):
        d=QFileDialog.getExistingDirectory(self,'Seleziona cartella FIT','C:/IronmanCoach/data/fit');
        if not d:return
        try:r=self.api.import_fit(d);self.status.setText(f'Import FIT completato: {r}');self.refresh()
        except Exception as exc:self.status.setText(f'Errore FIT: {exc}')
