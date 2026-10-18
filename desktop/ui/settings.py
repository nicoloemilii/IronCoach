from PySide6.QtWidgets import QFileDialog,QFormLayout,QGroupBox,QHBoxLayout,QLineEdit,QSpinBox
from .base import PageBase,button,label
class Settings(PageBase):
    def __init__(self,api):
        super().__init__(api); self.header('Impostazioni','Sincronizzazione, import FIT e stato del backend'); g=QGroupBox('Intervals.icu'); f=QFormLayout(g); self.days=QSpinBox(); self.days.setRange(1,3650); self.days.setValue(3650); b=button('Sincronizza',True); b.clicked.connect(self.sync); f.addRow('Giorni',self.days); f.addRow(b); self.root.addWidget(g); imp=QGroupBox('FIT'); fi=QFormLayout(imp); self.directory=QLineEdit('data/fit'); ch=button('Scegli...'); ch.clicked.connect(self.choose); go=button('Importa FIT'); go.clicked.connect(self.import_fit); row=QHBoxLayout(); row.addWidget(self.directory,1); row.addWidget(ch); fi.addRow('Cartella',row); fi.addRow(go); self.root.addWidget(imp); be=QGroupBox('Backend locale'); bf=QFormLayout(be); self.health=label('non verificato'); v=button('Verifica'); v.clicked.connect(self.check); bf.addRow('Stato',self.health); bf.addRow(v); self.root.addWidget(be); self.status=label(''); self.root.addWidget(self.status); self.root.addStretch(); self.check()
    def choose(self):
        d=QFileDialog.getExistingDirectory(self,'Seleziona cartella FIT','C:/IronmanCoach/data/fit');
        if d:self.directory.setText(d)
    def sync(self):
        try:r=self.api.sync_intervals(self.days.value());self.status.setText(f"Sync: {r.get('remote_activities',0)} remote, {r.get('new_activities',0)} nuove.")
        except Exception as exc:self.status.setText(f'Errore sync: {exc}')
    def import_fit(self):
        try:r=self.api.import_fit(self.directory.text().strip() or 'data/fit');self.status.setText(f'Import FIT: {r}')
        except Exception as exc:self.status.setText(f'Errore FIT: {exc}')
    def check(self):
        try:r=self.api.health();self.health.setText(f"online • {r.get('database','?')}");self.status.setText('Backend operativo.')
        except Exception as exc:self.health.setText('offline');self.status.setText(str(exc))
