from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter,QPen,QBrush
from PySide6.QtWidgets import QWidget
class LoadChart(QWidget):
    def __init__(self,values=None,parent=None): super().__init__(parent); self.values=list(values or []); self.setMinimumHeight(210)
    def set_values(self,values): self.values=list(values or []); self.update()
    def paintEvent(self,e):
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing); r=self.rect(); p.fillRect(r,Qt.white)
        if not self.values: p.setPen(Qt.gray); p.drawText(r,Qt.AlignCenter,'Nessun dato'); return
        left,top,right,bottom=42,20,18,30; w=max(1,r.width()-left-right); h=max(1,r.height()-top-bottom); vmax=max(max(self.values),1)
        pen=QPen(Qt.lightGray); p.setPen(pen)
        for i in range(5):
            y=top+h*i/4; p.drawLine(left,int(y),left+w,int(y))
        pts=[]
        for i,v in enumerate(self.values):
            x=left+w*i/max(1,len(self.values)-1); y=top+h*(1-v/vmax); pts.append((x,y))
        pen=QPen(Qt.black); pen.setWidth(2); p.setPen(pen)
        for a,b in zip(pts,pts[1:]): p.drawLine(int(a[0]),int(a[1]),int(b[0]),int(b[1]))
        p.setBrush(QBrush(Qt.black))
        for x,y in pts[-6:]: p.drawEllipse(int(x)-2,int(y)-2,5,5)
        p.setPen(Qt.gray); p.drawText(7,top+5,f'{vmax:.0f}'); p.drawText(10,top+h+5,'0')
