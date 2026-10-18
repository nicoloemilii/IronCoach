from PySide6.QtWidgets import QDoubleSpinBox,QFormLayout,QGroupBox,QHBoxLayout,QLineEdit
from .base import PageBase,button,label
class Profile(PageBase):
    def __init__(self,api):
        super().__init__(api); self.header('Profilo','Anagrafica, soglie e obiettivo gara'); self.name=QLineEdit(); self.age=QDoubleSpinBox(); self.age.setRange(0,100); self.weight=QDoubleSpinBox(); self.weight.setRange(0,250); self.height=QDoubleSpinBox(); self.height.setRange(0,230); self.ftp=QDoubleSpinBox(); self.ftp.setRange(1,1000); self.run=QDoubleSpinBox(); self.run.setRange(0,3600); self.css=QDoubleSpinBox(); self.css.setRange(0,300); self.event=QLineEdit(); self.date=QLineEdit(); self.target=QLineEdit();
        g=QGroupBox('Atleta'); f=QFormLayout(g); f.addRow('Nome',self.name); f.addRow('Età',self.age); f.addRow('Peso (kg)',self.weight); f.addRow('Altezza (cm)',self.height); f.addRow('FTP (W)',self.ftp); f.addRow('Soglia corsa (sec/km)',self.run); f.addRow('CSS nuoto (sec/100m)',self.css); self.root.addWidget(g)
        e=QGroupBox('Obiettivo'); ef=QFormLayout(e); ef.addRow('Evento',self.event); ef.addRow('Data',self.date); ef.addRow('Target time',self.target); self.root.addWidget(e)
        b=button('Salva profilo',True); b.clicked.connect(self.save); row=QHBoxLayout(); row.addStretch(); row.addWidget(b); self.root.addLayout(row); self.status=label(''); self.root.addWidget(self.status); self.load()
    def load(self):
        try:
            p=self.api.profile(); self.name.setText(str(p.get('name') or '')); self.age.setValue(float(p.get('age') or 0)); self.weight.setValue(float(p.get('weight_kg') or 0)); self.height.setValue(float(p.get('height_cm') or 0)); self.ftp.setValue(float(p.get('cycling',{}).get('ftp_watts',p.get('ftp_watts',170)) or 170)); self.run.setValue(float(p.get('running',{}).get('threshold_pace_sec_per_km') or 0)); self.css.setValue(float(p.get('swimming',{}).get('css_sec_per_100m') or 0)); g=p.get('goals',{}); self.event.setText(str(g.get('event') or '')); self.date.setText(str(g.get('event_date') or '')); self.target.setText(str(g.get('target_time') or '')); self.status.setText('Profilo caricato.')
        except Exception as exc: self.status.setText(f'Errore: {exc}')
    def save(self):
        p={'name':self.name.text().strip() or None,'age':int(self.age.value()) or None,'weight_kg':self.weight.value() or None,'height_cm':self.height.value() or None,'ftp_watts':self.ftp.value(),'cycling':{'ftp_watts':self.ftp.value()},'running':{'threshold_pace_sec_per_km':self.run.value() or None},'swimming':{'css_sec_per_100m':self.css.value() or None},'goals':{'event':self.event.text().strip() or None,'event_date':self.date.text().strip() or None,'target_time':self.target.text().strip() or None}}
        try: self.api.save_profile(p); self.status.setText('Profilo salvato.')
        except Exception as exc: self.status.setText(f'Errore: {exc}')
