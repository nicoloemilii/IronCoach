from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame,QGridLayout,QHBoxLayout,QLabel,QPushButton,QVBoxLayout,QWidget

def label(text,size=13,weight=400,color='#111827'):
    w=QLabel(str(text)); w.setStyleSheet(f'color:{color};font-size:{size}px;font-weight:{weight};'); w.setWordWrap(True); return w

def card(title,value,subtitle=''):
    f=QFrame(); f.setObjectName('Card'); l=QVBoxLayout(f); l.setContentsMargins(18,16,18,16); l.setSpacing(5)
    l.addWidget(label(title.upper(),10,700,'#6b7280')); l.addWidget(label(value,27,700,'#111827'))
    if subtitle: l.addWidget(label(subtitle,11,400,'#6b7280'))
    return f

def button(text,primary=False):
    b=QPushButton(text); b.setObjectName('PrimaryButton' if primary else 'SecondaryButton'); b.setCursor(Qt.PointingHandCursor); return b

class PageBase(QWidget):
    def __init__(self,api):
        super().__init__(); self.api=api; self.root=QVBoxLayout(self); self.root.setContentsMargins(36,30,36,30); self.root.setSpacing(16); self.setObjectName('Page')
    def header(self,title,subtitle=''):
        l=QVBoxLayout(); l.setSpacing(5); l.addWidget(label(title,30,750));
        if subtitle: l.addWidget(label(subtitle,13,400,'#6b7280'))
        self.root.addLayout(l)
