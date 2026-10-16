"""Saca capturas de la ventana de DJ MAOS sin abrir pantalla (para ver cómo queda en Mac)."""
import os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PySide6.QtWidgets import QApplication
import DJMAOS as F

tmp = tempfile.mkdtemp()
F.CFG_DIR, F.CFG_FILE = tmp, os.path.join(tmp, "config.json")     # config temporal, no toca la real
app = QApplication([])
w = F.Fiyodig()
w.cfg.update({"auto_app": False, "auto_ytdlp": False, "watch_clip": False})
w.show(); app.processEvents()
w.grab().save("preview_1_reposo.png")

w.entry.setText("https://youtu.be/FkzSKtMYIYk?list=RDFkzSKtMYIYk")
w.busy, w.t0, w.cur_i, w.cur_n = True, time.time() - 6.4, 2, 3
w.queue.append({"url": "x", "mode": "single"}); w._ui_busy(True); w.refresh_status(); w.bar.set_value(55)
w.tick(); app.processEvents(); w.grab().save("preview_2_bajando.png")

w.queue.clear(); w.busy = False; w._ui_busy(False); w.cur_n = 1; w.refresh_status(); w.bar.set_value(100)
w.lbl_now.setText("Now:001.01.00")
w.panel.set_files(["/x/It_Ain_t_Nothin_But_Music.wav", "/x/Otro_Tema.wav"]); app.processEvents()
w.grab().save("preview_3_terminado.png")
print("Fuentes usadas:", F.FONT_UI, "/", F.FONT_LCD, "| sistema:", sys.platform)
