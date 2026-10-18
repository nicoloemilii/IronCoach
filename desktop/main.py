import sys,subprocess,time,urllib.request
from PySide6.QtWidgets import QApplication,QFrame,QLabel,QHBoxLayout,QListWidget,QListWidgetItem,QMainWindow,QMessageBox,QStackedWidget,QVBoxLayout,QWidget
from desktop.api_client import ApiClient,BASE_URL
from desktop.ui.dashboard import Dashboard
from desktop.ui.coach import Coach
from desktop.ui.activities import Activities
from desktop.ui.fitness import Fitness
from desktop.ui.history import History
from desktop.ui.analysis import Analysis
from desktop.ui.profile import Profile
from desktop.ui.settings import Settings
APP_DIR=r'C:\IronmanCoach'
def backend_available():
    try:
        with urllib.request.urlopen(f'{BASE_URL}/health',timeout=1): return True
    except Exception:return False
def start_backend():
    if backend_available():return None
    p=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000'],cwd=APP_DIR,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    for _ in range(120):
        if backend_available():return p
        if p.poll() is not None:raise RuntimeError('Il backend non è riuscito ad avviarsi.')
        time.sleep(.1)
    p.terminate();raise RuntimeError('Timeout avvio backend.')
class MainWindow(QMainWindow):
    def __init__(self,backend_process=None):
        super().__init__();self.backend_process=backend_process;self.api=ApiClient();self.setWindowTitle('Ironman Coach');self.resize(1500,900);self.setMinimumSize(1150,720);root=QWidget();self.setCentralWidget(root);lay=QHBoxLayout(root);lay.setContentsMargins(0,0,0,0);lay.setSpacing(0)
        side=QFrame();side.setObjectName('Sidebar');side.setFixedWidth(235);sl=QVBoxLayout(side);sl.setContentsMargins(20,24,20,20);sl.setSpacing(8);logo=QLabel('IRONMAN\nCOACH');logo.setObjectName('Logo');sl.addWidget(logo);sub=QLabel('Adaptive Training System');sub.setObjectName('Subtitle');sl.addWidget(sub);sl.addSpacing(22);self.nav=QListWidget();self.nav.setObjectName('Navigation');self.pages=QStackedWidget();sl.addWidget(self.nav,1);sl.addWidget(QLabel('Desktop • v1.0'))
        defs=[('Dashboard',Dashboard),('Coach',Coach),('Allenamenti',Activities),('Fitness',Fitness),('Storico',History),('Analisi',Analysis),('Profilo',Profile),('Impostazioni',Settings)]
        for name,cls in defs:self.nav.addItem(QListWidgetItem(name));self.pages.addWidget(cls(self.api))
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex);self.nav.setCurrentRow(0);lay.addWidget(side);lay.addWidget(self.pages,1)
        self.setStyleSheet("QMainWindow{background:#f5f7fa;}#Sidebar{background:#111827;}#Logo{color:white;font-size:25px;font-weight:800;letter-spacing:2px;}#Subtitle{color:#9ca3af;font-size:11px;}#Navigation{background:transparent;border:none;outline:none;color:#d1d5db;font-size:14px;}#Navigation::item{padding:13px 12px;border-radius:8px;margin:2px 0;}#Navigation::item:selected{background:#2563eb;color:white;}#Navigation::item:hover{background:#1f2937;}#Page{background:#f5f7fa;}#Card{background:white;border:1px solid #e5e7eb;border-radius:12px;}QPushButton{min-height:34px;}#PrimaryButton{background:#2563eb;color:white;border:none;border-radius:8px;padding:8px 16px;font-weight:600;}#SecondaryButton{background:white;color:#111827;border:1px solid #d1d5db;border-radius:8px;padding:8px 16px;font-weight:600;}QTableWidget{background:white;border:1px solid #e5e7eb;gridline-color:#e5e7eb;}QHeaderView::section{background:#f9fafb;border:none;border-bottom:1px solid #e5e7eb;padding:8px;font-weight:600;}QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox{background:white;border:1px solid #d1d5db;border-radius:6px;padding:7px;}QGroupBox{background:white;border:1px solid #e5e7eb;border-radius:10px;margin-top:8px;padding:12px;}")
    def closeEvent(self,event):
        if self.backend_process is not None and self.backend_process.poll() is None:
            self.backend_process.terminate()
            try:self.backend_process.wait(timeout=3)
            except subprocess.TimeoutExpired:self.backend_process.kill()
        event.accept()
def main():
    app=QApplication(sys.argv);app.setApplicationName('Ironman Coach')
    try:p=start_backend()
    except Exception as e:QMessageBox.critical(None,'Ironman Coach',f'Impossibile avviare il backend:\n\n{e}');return 1
    try:w=MainWindow(p);w.show();return app.exec()
    except Exception as e:QMessageBox.critical(None,'Ironman Coach',f'Errore interfaccia:\n\n{e}');return 1
if __name__=='__main__':sys.exit(main())
