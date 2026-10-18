from PySide6.QtWidgets import QFrame,QGridLayout,QHBoxLayout
from .base import PageBase,button,card,label
class Dashboard(PageBase):
    def __init__(self,api):
        super().__init__(api); self.header('Dashboard','Stato attuale dell\'atleta e prossima decisione del Coach'); self.body=QVBoxLayout(); self.root.addLayout(self.body); self.refresh_btn=button('Aggiorna'); self.refresh_btn.clicked.connect(self.refresh); self.refresh()
    def clear(self):
        while self.body.count():
            it=self.body.takeAt(0); w=it.widget()
            if w: w.deleteLater()
    def refresh(self):
        try:
            d=self.api.coach_today(); self.clear(); fit=d.get('fitness',{}); rd=d.get('readiness',{}); dec=d.get('decision',{}); w=d.get('workout',{}); ev=d.get('event',{}); prof=d.get('profile',{})
            top=QHBoxLayout(); top.addStretch(); top.addWidget(self.refresh_btn); self.body.addLayout(top)
            g=QGridLayout(); g.addWidget(card('Readiness','n/d' if rd.get('readiness') is None else f"{rd.get('readiness'):.0f}/100",f"conf. {rd.get('confidence',0):.0f}%"),0,0); g.addWidget(card('TSB',f"{fit.get('tsb',0):.1f}",'bilancio fatica/fitness'),0,1); g.addWidget(card('Decisione',str(dec.get('recommendation','-')).title(),f"conf. {dec.get('confidence',0):.0f}%"),0,2); g.addWidget(card('Sport',str(dec.get('sport','-')).title()),0,3); self.body.addLayout(g)
            box=QFrame(); box.setObjectName('Card'); l=QVBoxLayout(box); l.setContentsMargins(18,16,18,16); l.addWidget(label('DECISIONE DEL COACH',11,700,'#6b7280')); l.addWidget(label(dec.get('reasons',['Nessuna raccomandazione'])[0],16,500)); l.addWidget(label(f"Fase: {ev.get('phase','-')} • Evento: {ev.get('name','-')} • Giorni: {ev.get('days_to_event','-')}",12,400,'#6b7280')); self.body.addWidget(box)
            box2=QFrame(); box2.setObjectName('Card'); l2=QVBoxLayout(box2); l2.setContentsMargins(18,16,18,16); l2.addWidget(label('PROSSIMO ALLENAMENTO',11,700,'#6b7280')); l2.addWidget(label(w.get('name','-'),21,700)); l2.addWidget(label(f"{w.get('duration_min','-')} min • RPE {w.get('expected_rpe','-')} • {str(w.get('sport','-')).title()}",13,400,'#6b7280')); self.body.addWidget(box2)
            profg=QGridLayout(); profg.addWidget(label('Peso'),0,0); profg.addWidget(label(f"{prof.get('weight_kg','-')} kg",14,600),0,1); profg.addWidget(label('FTP'),0,2); profg.addWidget(label(f"{prof.get('cycling',{}).get('ftp_watts',prof.get('ftp_watts','-'))} W",14,600),0,3); profg.addWidget(label('Obiettivo'),1,0); profg.addWidget(label(prof.get('goals',{}).get('event','-'),14,600),1,1,1,3); self.body.addLayout(profg); self.body.addStretch()
        except Exception as exc:
            self.body.addWidget(label(f'Errore: {exc}',13,500,'#b91c1c'))
