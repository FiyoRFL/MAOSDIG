"""
DJ MAOS - Desde 2002
Baja el audio de un link de YouTube con yt-dlp y te deja arrastrar el archivo a tu DAW.
Hecho para macOS (también corre en Windows).
"""
import os
import re
import sys
import json
import time
import threading
from collections import deque
from urllib.parse import urlparse, parse_qs

from PySide6.QtCore import Qt, Signal, QMimeData, QUrl, QTimer, QEvent, QPointF
from PySide6.QtGui import (QPainter, QColor, QFont, QIcon, QPalette, QDrag, QPixmap,
                           QPolygonF, QPen, QActionGroup, QDesktopServices)
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QPushButton,
                               QLineEdit, QFrame, QMenu, QFileDialog,
                               QMessageBox)

APP_VERSION = "1.0.0"
# URL del version.json ({"version","url" del instalador,"sha256","notes"}).
# Vacío = sin auto-update de la app (el menú no lo muestra).
UPDATE_URL = ""

# ----------------------------------------------------------------- rutas ---
FROZEN = getattr(sys, "frozen", False)
APP_DIR = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
if IS_MAC:
    CFG_DIR = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "DJMAOS")
else:
    CFG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "DJMAOS")
CFG_FILE = os.path.join(CFG_DIR, "config.json")
BIN_DIR = os.path.join(CFG_DIR, "bin")     # Mac: yt-dlp editable (dentro del .app no se puede tocar)
MAC_PATHS = ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"]   # Homebrew / MacPorts
NOWINDOW = 0x08000000 if IS_WIN else 0   # CREATE_NO_WINDOW
DETACHED = 0x00000008 if IS_WIN else 0    # DETACHED_PROCESS

# ------------------------------------------------------------ tipografías ---
if IS_MAC:
    FONT_UI, FONT_LCD = "Helvetica Neue", "Menlo"
elif IS_WIN:
    FONT_UI, FONT_LCD = "Segoe UI", "Consolas"
else:
    FONT_UI, FONT_LCD = "Sans Serif", "Monospace"


def fpt(size):
    """Los tamaños están pensados para Windows (96 dpi); en Mac se escalan para verse igual."""
    return size * 4.0 / 3.0 if IS_MAC else size


# --------------------------------------------------------------- colores ---
BG = "#0d0d0c"
ACCENT = "#f2f2f2"          # beige
ACCENT_HOVER = "#ffffff"
ACCENT_DIM = "#8a8a8a"
LCD_BG = "#e4e4e2"
LCD_BAR = "#c2c2c0"
LCD_BORDER = "#2a2a2a"
LCD_TXT = "#101010"
BTN = "#181818"
BTN_HOVER = "#262626"
BTN_BORDER = "#6a6a6a"
PANEL = "#161616"

# ---------------------------------------------------------------- links ----
YT_HOSTS = ("youtube.com", "youtu.be")
PLAYLIST_IDS = ("PL", "OLAK5uy", "UU", "FL")   # playlists reales (RD... = mixes, no)
URL_RE = re.compile(r"https?://[^\s,;\"'<>]+")


def is_youtube(url):
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in YT_HOSTS)


def find_urls(text, only_youtube=False):
    out = []
    for u in URL_RE.findall(text or ""):
        u = u.rstrip(").]")
        if only_youtube and not is_youtube(u):
            continue
        if u not in out:
            out.append(u)
    return out


def classify(url):
    """'single' | 'both' (video dentro de una playlist) | 'playlist' (solo playlist)."""
    if not is_youtube(url):
        return "single"
    u = urlparse(url)
    q = parse_qs(u.query)
    lid = (q.get("list") or [""])[0]
    if not lid.startswith(PLAYLIST_IDS):
        return "single"            # sin list=, o mix automático (RD...)
    has_video = bool(q.get("v")) or (u.hostname or "").lower().endswith("youtu.be")
    if u.path.rstrip("/") == "/playlist" or not has_video:
        return "playlist"
    return "both"


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4])


# --------------------------------------------------------------- config ----
def tool(name):
    exe = name + (".exe" if IS_WIN else "")
    cands = []
    if IS_MAC:
        cands.append(os.path.join(BIN_DIR, exe))                  # copia editable (se actualiza acá)
    cands.append(os.path.join(APP_DIR, exe))                      # junto al programa / dentro del .app
    if IS_MAC:                                                    # Finder no hereda el PATH de la terminal
        cands += [os.path.join(p, exe) for p in MAC_PATHS]
    for c in cands:
        if os.path.exists(c):
            return c
    return name


def child_env():
    env = dict(os.environ)
    if IS_MAC:
        env["PATH"] = os.pathsep.join(MAC_PATHS + [env.get("PATH", "")])
    return env


POPEN_EXTRA = {} if IS_WIN else {"start_new_session": True}       # grupo propio para poder cancelar todo


def ensure_user_ytdlp():
    """Mac: yt-dlp se actualiza solo, así que vive en una carpeta del usuario.
    La primera vez se copia el que viene dentro de la app."""
    if not IS_MAC:
        return
    dst, src = os.path.join(BIN_DIR, "yt-dlp"), os.path.join(APP_DIR, "yt-dlp")
    try:
        if not os.path.exists(dst) and os.path.exists(src):
            import shutil
            import subprocess
            os.makedirs(BIN_DIR, exist_ok=True)
            shutil.copy2(src, dst)
            os.chmod(dst, 0o755)
            subprocess.run(["xattr", "-d", "com.apple.quarantine", dst], capture_output=True)
    except Exception:
        pass


DEFAULTS = {
    "folder": os.path.join(os.path.expanduser("~"), "Music", "DJMAOS"),
    "format": "wav", "seq": 1,
    "playlist_mode": "ask",       # ask | single | full
    "auto_ytdlp": True, "watch_clip": True, "auto_app": True,
    "skip_version": "", "last_clip": "",
}


def load_cfg():
    cfg = dict(DEFAULTS)
    try:
        with open(CFG_FILE, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_cfg(cfg):
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
        with open(CFG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


# --------------------------------------------------------------- widgets ---
class SegBar(QWidget):
    N = 20

    def __init__(self, parent):
        super().__init__(parent)
        self.value = 0.0

    def set_value(self, v):
        self.value = max(0.0, min(100.0, v))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(LCD_BAR))
        gap = 2
        w = (self.width() - 4 - gap * (self.N - 1)) / self.N
        full = int(self.value / 100.0 * self.N + 0.5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#171717"))
        for i in range(full):
            p.drawRect(int(2 + i * (w + gap)), 2, int(w), self.height() - 4)


class Led(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.on = False

    def set_on(self, on):
        self.on = on
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor("#ff2a2a" if self.on else "#3a3a3a"))
        p.setPen(QColor("#a01010" if self.on else "#6a6a6a"))
        p.drawEllipse(2, 2, self.width() - 5, self.height() - 5)


class Logo(QWidget):
    """Logo del sello. Usa logo.png (junto al exe) si existe; si no, un sol de mayo provisorio."""

    def __init__(self, parent):
        super().__init__(parent)
        self.pix = None
        for base in (APP_DIR, getattr(sys, "_MEIPASS", APP_DIR)):
            path = os.path.join(base, "logo.png")
            if os.path.exists(path):
                pm = QPixmap(path)
                if not pm.isNull():
                    self.pix = pm
                    break

    def paintEvent(self, _):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if self.pix:
            pm = self.pix.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap((self.width() - pm.width()) // 2, (self.height() - pm.height()) // 2, pm)
            return
        cx, cy = self.width() / 2.0, self.height() / 2.0
        R = min(cx, cy) - 2
        p.setPen(QPen(QColor(ACCENT), 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), R, R)
        f = QFont(FONT_UI)
        f.setBold(True)
        f.setPointSizeF(fpt(13))
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, "DJ")


NAV_CSS = ("QPushButton { color:#171717; background:transparent; border:none; font-size:11px; }"
           "QPushButton:hover { background:rgba(0,0,0,45); }")


class DragPanel(QLabel):
    """Gris vacío; rojo con el nombre cuando hay archivo. Se arrastra al DAW.
    Con varios archivos (cola / playlist): flechas o rueda para elegir,
    Shift+arrastrar lleva todos."""

    def __init__(self, parent):
        super().__init__(parent)
        self.files, self.idx = [], 0
        self._press, self._shift = None, False
        self.setAlignment(Qt.AlignCenter)
        self.prev = QPushButton("◀", self)
        self.next = QPushButton("▶", self)
        for b in (self.prev, self.next):
            b.setStyleSheet(NAV_CSS)
            b.setCursor(Qt.PointingHandCursor)
            b.hide()
        self.prev.setGeometry(2, 18, 20, 24)
        self.next.setGeometry(299, 18, 20, 24)
        self.prev.clicked.connect(lambda: self.step(-1))
        self.next.clicked.connect(lambda: self.step(1))
        self.count = QLabel(self)
        self.count.setGeometry(250, 45, 66, 13)
        self.count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.count.setStyleSheet("color:#3a3a3a; background:transparent; border:none; font-size:8px;")
        self.count.hide()
        self.render()

    def set_files(self, files):
        self.files = list(files)
        self.idx = max(0, len(self.files) - 1)
        self.render()

    def step(self, d):
        if len(self.files) > 1:
            self.idx = (self.idx + d) % len(self.files)
            self.render()

    def wheelEvent(self, e):
        self.step(-1 if e.angleDelta().y() > 0 else 1)

    def render(self):
        multi = len(self.files) > 1
        self.prev.setVisible(multi)
        self.next.setVisible(multi)
        self.count.setVisible(multi)
        if not self.files:
            self.setText("")
            self.setStyleSheet(f"background:{PANEL}; border:1px solid {BTN_BORDER};")
            self.setCursor(Qt.ArrowCursor)
            self.setToolTip("")
            return
        name = os.path.splitext(os.path.basename(self.files[self.idx]))[0]
        font = QFont(FONT_UI)
        font.setPointSizeF(fpt(9 if multi else 10))
        font.setBold(True)
        self.setFont(font)
        txt = self.fontMetrics().elidedText(f">> DRAG {name} <<", Qt.ElideMiddle,
                                            self.width() - (48 if multi else 16))
        self.setText(txt)
        self.setStyleSheet(f"background:{ACCENT}; color:#171717; border:1px solid #f0f0f0;")
        self.setCursor(Qt.OpenHandCursor)
        self.count.setText(f"{self.idx + 1}/{len(self.files)}")
        self.setToolTip("Arrastrá el archivo hasta tu DAW" +
                        ("\nRueda o flechas: cambiar · Shift+arrastrar: llevar todos" if multi else ""))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.position().toPoint()
            self._shift = bool(e.modifiers() & Qt.ShiftModifier)

    def mouseMoveEvent(self, e):
        if not (self.files and self._press and e.buttons() & Qt.LeftButton):
            return
        if (e.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        self._press = None
        paths = self.files if self._shift else [self.files[self.idx]]
        paths = [p for p in paths if os.path.exists(p)]
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self.grab().scaledToWidth(180, Qt.SmoothTransformation))
        drag.exec(Qt.CopyAction)

    def mouseReleaseEvent(self, e):
        self._press = None


DIG_CSS = (f"QPushButton {{ background:{ACCENT}; color:#171717; border:none; }}"
           f"QPushButton:hover {{ background:{ACCENT_HOVER}; }}")
DIG_CSS_BUSY = ("QPushButton { background:#6f6f6f; color:#2a2a2a; border:none; }"
                "QPushButton:hover { background:#7a7a7a; }")


# ------------------------------------------------------------ ventana ------
class Fiyodig(QWidget):
    W, H = 400, 336
    sig_progress = Signal(float)
    sig_item = Signal(int, int)
    sig_file = Signal(str)
    sig_job_done = Signal(bool, str)
    sig_flash = Signal(str)
    sig_ytdlp = Signal(str)
    sig_app_update = Signal(object, bool)
    sig_app_progress = Signal(float)
    sig_app_ready = Signal(str)
    sig_app_fail = Signal(str)

    def __init__(self):
        super().__init__()
        ensure_user_ytdlp()
        self.cfg = load_cfg()
        self.queue = deque()
        self.busy = False
        self.updating = False
        self.in_batch = False
        self.canceled = False
        self.proc = None
        self.cur_job = None
        self.cur_i, self.cur_n, self.cur_pct = 1, 1, 0.0
        self.jobs_done, self.errors = 0, 0
        self.results = []
        self.had_file = False
        self.t0 = 0.0
        self.auto_loaded = ""
        self.flash_token = 0

        self.setWindowTitle("DJ MAOS")
        self.setFixedSize(self.W, self.H)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window | Qt.WindowMinimizeButtonHint)
        ico = os.path.join(APP_DIR, "logo.ico")
        if os.path.exists(ico):
            self.setWindowIcon(QIcon(ico))
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor(BG))
        self.setPalette(pal)

        self._build()

        self.sig_progress.connect(self.on_progress)
        self.sig_item.connect(self.on_item)
        self.sig_file.connect(self.on_file)
        self.sig_job_done.connect(self.on_job_done)
        self.sig_flash.connect(lambda t: self.flash(t))
        self.sig_ytdlp.connect(self.on_ytdlp)
        self.sig_app_update.connect(self.on_app_update)
        self.sig_app_progress.connect(self.on_app_progress)
        self.sig_app_ready.connect(self.on_app_ready)
        self.sig_app_fail.connect(self.on_app_fail)
        QApplication.clipboard().dataChanged.connect(self.check_clipboard)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)
        QTimer.singleShot(500, self.on_start)

    # ---- construcción UI ---------------------------------------------------
    @staticmethod
    def _font(fam, size, bold=False):
        fo = QFont(fam)
        fo.setPointSizeF(fpt(size))
        fo.setBold(bold)
        return fo

    @staticmethod
    def _btn_css():
        return (f"QPushButton {{ background:{BTN}; color:{ACCENT}; border:1px solid {BTN_BORDER}; }}"
                f"QPushButton:hover {{ background:{BTN_HOVER}; }}")

    def _lbl(self, parent, text, x, y, w, h, font, color=ACCENT,
             align=Qt.AlignLeft | Qt.AlignVCenter):
        l = QLabel(text, parent)
        l.setGeometry(x, y, w, h)
        l.setFont(font)
        l.setAlignment(align)
        l.setStyleSheet(f"color:{color}; background:transparent;")
        return l

    def _build(self):
        f = self._font
        self._lbl(self, "DJ MAOS", 0, 0, self.W, 30, f(FONT_UI, 12, True), align=Qt.AlignCenter)

        self.btn_opt = QPushButton("Options", self)
        self.btn_opt.setGeometry(7, 6, 58, 19)
        self.btn_opt.setFont(f(FONT_UI, 8))
        self.btn_opt.setCursor(Qt.PointingHandCursor)
        self.btn_opt.setStyleSheet(
            f"QPushButton {{ background:{BG}; color:{ACCENT}; border:1px solid {ACCENT_DIM}; border-radius:7px; }}"
            f"QPushButton:hover {{ background:{BTN_HOVER}; }} QPushButton::menu-indicator {{ image:none; }}")
        self._build_menu()

        mini = QPushButton("▬", self)
        mini.setGeometry(344, 4, 22, 22)
        mini.setFlat(True)
        mini.setCursor(Qt.PointingHandCursor)
        mini.setStyleSheet("QPushButton { color:#d8d8d8; border:none; font-weight:bold; background:transparent; }")
        mini.clicked.connect(self.showMinimized)
        close = QPushButton("✕", self)
        close.setGeometry(372, 3, 22, 24)
        close.setFlat(True)
        close.setCursor(Qt.PointingHandCursor)
        close.setStyleSheet("QPushButton { color:#c8553d; border:none; font-size:14px; font-weight:bold; background:transparent; }")
        close.clicked.connect(self.close)

        self.logo = Logo(self)
        self.logo.setGeometry(12, 32, 62, 62)
        self._lbl(self, "DJ MAOS MUSIC", 100, 40, 200, 20, f(FONT_UI, 12, True),
                  color=ACCENT, align=Qt.AlignCenter)
        self._lbl(self, "DESDE 2002", 100, 60, 200, 14, f(FONT_UI, 7),
                  color=ACCENT_DIM, align=Qt.AlignCenter)

        b = QPushButton("BROWSER", self)
        b.setGeometry(316, 38, 74, 24)
        b.setFont(f(FONT_UI, 9))
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(self._btn_css())
        b.clicked.connect(self.open_browser)

        self.led = Led(self)
        self.led.setGeometry(354, 93, 17, 17)
        self._lbl(self, "REC", 350, 109, 26, 12, f(FONT_UI, 7), color=ACCENT_DIM, align=Qt.AlignCenter)

        border = QFrame(self)
        border.setGeometry(19, 95, 318, 96)
        border.setStyleSheet(f"background:{LCD_BORDER};")
        lcd = QFrame(border)
        lcd.setGeometry(2, 2, 314, 92)
        lcd.setStyleSheet(f"background:{LCD_BG};")

        lcd_font = f(FONT_LCD, 10, True)
        self.lbl_seq = self._lbl(lcd, "", 5, 2, 170, 20, lcd_font, LCD_TXT)
        self.lbl_now = self._lbl(lcd, "Now:001.01.00", 160, 2, 150, 20, lcd_font, LCD_TXT,
                                 Qt.AlignRight | Qt.AlignVCenter)
        self._refresh_seq()
        self.lbl_status = self._lbl(lcd, "READY.", 0, 27, 314, 22, lcd_font, LCD_TXT, Qt.AlignCenter)

        self.bar = SegBar(lcd)
        self.bar.setGeometry(4, 54, 306, 14)
        for (x, y, w, h) in ((0, 70, 314, 2), (57, 70, 2, 22)):
            d = QFrame(lcd)
            d.setGeometry(x, y, w, h)
            d.setStyleSheet(f"background:{LCD_BORDER};")
        self._lbl(lcd, "LINK:", 6, 72, 50, 20, lcd_font, LCD_TXT)

        self.entry = QLineEdit(lcd)
        self.entry.setGeometry(64, 73, 246, 18)
        self.entry.setFont(f(FONT_UI, 9))
        self.entry.setPlaceholderText("URL YouTube...")
        self.entry.setStyleSheet(f"QLineEdit {{ background:transparent; border:none; color:{LCD_TXT}; "
                                 f"selection-background-color:#a8a8a8; }}")
        pal = self.entry.palette()
        pal.setColor(QPalette.PlaceholderText, QColor("#7d7d7d"))
        self.entry.setPalette(pal)
        self.entry.returnPressed.connect(self.dig)

        fb = QPushButton("FOLDER", self)
        fb.setGeometry(19, 208, 82, 30)
        fb.setFont(f(FONT_UI, 10))
        fb.setCursor(Qt.PointingHandCursor)
        fb.setStyleSheet(self._btn_css())
        fb.clicked.connect(self.pick_folder)

        self.btn_cancel = QPushButton("CANCEL", self)
        self.btn_cancel.setGeometry(130, 208, 98, 30)
        self.btn_cancel.setFont(f(FONT_UI, 10))
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.setStyleSheet(
            f"QPushButton {{ background:{BTN}; color:#d9826a; border:1px solid {BTN_BORDER}; }}"
            f"QPushButton:hover {{ background:{BTN_HOVER}; }}")
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_cancel.hide()

        self.btn_dig = QPushButton("DIG", self)
        self.btn_dig.setGeometry(260, 208, 80, 30)
        self.btn_dig.setFont(f(FONT_UI, 10, True))
        self.btn_dig.setCursor(Qt.PointingHandCursor)
        self.btn_dig.setStyleSheet(DIG_CSS)
        self.btn_dig.clicked.connect(self.dig)

        shadow = QFrame(self)
        shadow.setGeometry(22, 261, 321, 60)
        shadow.setStyleSheet("background:#3a3a3a;")
        self.panel = DragPanel(self)
        self.panel.setGeometry(19, 258, 321, 60)

    def _toggle(self, menu, label, key):
        a = menu.addAction(label)
        a.setCheckable(True)
        a.setChecked(bool(self.cfg.get(key, True)))
        a.triggered.connect(lambda on, k=key: self._set_cfg(k, bool(on)))

    def _set_cfg(self, key, val):
        self.cfg[key] = val
        save_cfg(self.cfg)

    def _build_menu(self):
        menu = QMenu(self)
        fm = menu.addMenu("Formato de salida")
        g1 = QActionGroup(self)
        for fmt in ("wav", "mp3", "flac"):
            a = fm.addAction(fmt.upper())
            a.setCheckable(True)
            a.setChecked(self.cfg["format"] == fmt)
            g1.addAction(a)
            a.triggered.connect(lambda _=False, x=fmt: self._set_cfg("format", x))
        pm = menu.addMenu("Playlists")
        g2 = QActionGroup(self)
        for key, label in (("ask", "Preguntar"), ("single", "Siempre solo el video"),
                           ("full", "Siempre la playlist completa")):
            a = pm.addAction(label)
            a.setCheckable(True)
            a.setChecked(self.cfg["playlist_mode"] == key)
            g2.addAction(a)
            a.triggered.connect(lambda _=False, k=key: self._set_cfg("playlist_mode", k))
        menu.addSeparator()
        self._toggle(menu, "Actualizar yt-dlp al abrir", "auto_ytdlp")
        self._toggle(menu, "Detectar link copiado", "watch_clip")
        if UPDATE_URL:
            self._toggle(menu, "Buscar actualizaciones al abrir", "auto_app")
        menu.addSeparator()
        menu.addAction("Actualizar yt-dlp ahora", lambda: self.update_ytdlp())
        if UPDATE_URL:
            menu.addAction("Buscar actualización de DJ MAOS", lambda: self.check_app_update(True))
        menu.addAction("Abrir carpeta de destino", self.open_folder)
        menu.addSeparator()
        menu.addAction("Acerca de DJ MAOS", self.about)
        self.btn_opt.setMenu(menu)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.position().y() < 32:
            h = self.windowHandle()
            if h:
                h.startSystemMove()

    def changeEvent(self, e):
        if e.type() == QEvent.ActivationChange and self.isActiveWindow():
            QTimer.singleShot(60, self.check_clipboard)
        super().changeEvent(e)

    # ---- estado / LCD ------------------------------------------------------
    def _refresh_seq(self):
        n = self.cfg["seq"]
        self.lbl_seq.setText(f"Sq:{n:02d}-Sequence{n:02d}")

    def sampling_text(self):
        t = ">>> SAMPLING"
        if self.cur_n > 1:
            t += f" {self.cur_i}/{self.cur_n}"
        if self.queue:
            t += f" +{len(self.queue)}"
        return t + " >>>"

    def base_status(self):
        if self.busy:
            return self.sampling_text()
        if self.updating:
            return ">>> UPDATING >>>"
        return "READY."

    def set_status(self, text):
        self.lbl_status.setText(text)

    def refresh_status(self):
        self.set_status(self.base_status())

    def flash(self, text, ms=1600):
        """Mensaje corto en el LCD que vuelve solo al estado base."""
        self.flash_token += 1
        tok = self.flash_token
        self.set_status(text)
        QTimer.singleShot(ms, lambda: tok == self.flash_token and self.refresh_status())

    def refresh_progress(self):
        frac = (self.cur_i - 1 + self.cur_pct / 100.0) / max(1, self.cur_n)
        total = self.jobs_done + 1 + len(self.queue)
        self.bar.set_value(100.0 * (self.jobs_done + frac) / total)

    def tick(self):
        if not self.busy:
            return
        t = time.time() - self.t0
        self.lbl_now.setText(f"Now:{int(t // 2) + 1:03d}.{int((t % 2) // 0.5) + 1:02d}.{int((t % 0.5) * 200):02d}")

    def _ui_busy(self, busy):
        self.led.set_on(busy)
        self.btn_cancel.setVisible(busy)
        self.btn_dig.setStyleSheet(DIG_CSS_BUSY if busy else DIG_CSS)
        self.btn_dig.setToolTip("Agregar a la cola" if busy else "")

    # ---- acciones simples --------------------------------------------------
    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Carpeta de destino", self.cfg["folder"])
        if d:
            self._set_cfg("folder", d)
            self.flash("FOLDER OK.")

    def open_folder(self):
        os.makedirs(self.cfg["folder"], exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.cfg["folder"]))

    def open_browser(self):
        QDesktopServices.openUrl(QUrl("https://www.youtube.com"))

    def about(self):
        QMessageBox.information(self, "DJ MAOS", f"DJ MAOS MUSIC - Desde 2002  v{APP_VERSION}\n"
                                                 "")

    # ---- portapapeles ------------------------------------------------------
    def check_clipboard(self):
        if not self.cfg.get("watch_clip", True):
            return
        urls = find_urls(QApplication.clipboard().text(), only_youtube=True)
        if not urls:
            return
        key = "\n".join(urls)
        if key == self.cfg.get("last_clip"):
            return
        cur = self.entry.text().strip()
        if cur and cur != self.auto_loaded:
            return                      # el usuario está escribiendo otra cosa
        self.cfg["last_clip"] = key
        save_cfg(self.cfg)
        text = " ".join(urls)
        self.entry.setText(text)
        self.entry.setCursorPosition(len(text))
        self.auto_loaded = text
        self.flash("LINK LOADED.")

    # ---- cola --------------------------------------------------------------
    def resolve_mode(self, url):
        kind, pm = classify(url), self.cfg.get("playlist_mode", "ask")
        if kind == "single":
            return "single"
        if kind == "both" and pm == "single":
            return "single"
        if pm == "full":
            return "playlist"
        box = QMessageBox(self)
        box.setWindowTitle("Playlist")
        box.setIcon(QMessageBox.Question)
        if kind == "both":
            box.setText("Este link es de un video dentro de una playlist.\n¿Qué querés bajar?")
            b_full = box.addButton("Playlist completa", QMessageBox.AcceptRole)
            b_one = box.addButton("Solo este video", QMessageBox.AcceptRole)
        else:
            box.setText("Este link es una playlist.\n¿La bajo completa?")
            b_full = box.addButton("Bajar playlist", QMessageBox.AcceptRole)
            b_one = None
        box.setInformativeText("Una playlist grande puede tardar y ocupar mucho espacio.")
        box.addButton("Cancelar", QMessageBox.RejectRole)
        box.setDefaultButton(b_one or b_full)
        box.exec()
        c = box.clickedButton()
        if c is b_full:
            return "playlist"
        if b_one is not None and c is b_one:
            return "single"
        return None

    def is_duplicate(self, url):
        if any(j["url"] == url for j in self.queue):
            return True
        return bool(self.busy and self.cur_job and self.cur_job["url"] == url)

    def dig(self):
        text = self.entry.text().strip()
        urls = find_urls(text)
        if not urls and text and " " not in text and "." in text:
            urls = ["https://" + text]
        if not urls:
            self.flash("NO LINK." if not text else "BAD LINK.")
            return
        added = dup = 0
        for url in urls:
            if self.is_duplicate(url):
                dup += 1
                continue
            mode = self.resolve_mode(url)
            if mode is None:
                continue
            self.queue.append({"url": url, "mode": mode})
            added += 1
        if not added:
            self.flash("ALREADY QUEUED." if dup else "CANCELED.")
            return
        if self.busy or self.updating:
            self.entry.clear()
            self.auto_loaded = ""
            self.flash(f"+{added} QUEUED" if self.busy else ">>> UPDATING >>>")
        self.start_next()

    def start_next(self):
        if self.busy or self.updating or not self.queue:
            return
        if not self.in_batch:                      # lote nuevo
            self.in_batch = True
            self.jobs_done = self.errors = 0
            self.results = []
            self.panel.set_files([])
            self.t0 = time.time()
            if self.had_file:
                self.cfg["seq"] += 1
                self._refresh_seq()
                save_cfg(self.cfg)
                self.had_file = False
            self.bar.set_value(0)
        job = self.queue.popleft()
        os.makedirs(self.cfg["folder"], exist_ok=True)
        self.cur_job = job
        self.cur_i, self.cur_n, self.cur_pct = 1, 1, 0.0
        self.busy = True
        self.canceled = False
        self._ui_busy(True)
        self.refresh_status()
        threading.Thread(target=self._worker, args=(job,), daemon=True).start()

    def _worker(self, job):
        import subprocess
        base = self.cfg["folder"]
        if job["mode"] == "playlist":
            tpl = os.path.join(base, "%(playlist_title|Playlist)s",
                               "%(playlist_index)02d_%(title)s.%(ext)s")
            mode_flag = "--yes-playlist"
        else:
            tpl = os.path.join(base, "%(title)s.%(ext)s")
            mode_flag = "--no-playlist"
        cmd = [tool("yt-dlp"), "-x", "--restrict-filenames",
               "--audio-format", self.cfg["format"], "--audio-quality", "0",
               mode_flag, "--newline", "--progress", "--no-warnings",
               "--print", "before_dl:FIYODIG_ITEM|%(playlist_index|1)s|%(n_entries|1)s",
               "--print", "after_move:FIYODIG_FILE|%(filepath)s",
               "-o", tpl, job["url"]]
        ff = tool("ffmpeg")
        if os.path.isabs(ff):
            cmd[1:1] = ["--ffmpeg-location", os.path.dirname(ff)]

        files, last_err = 0, ""
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace",
                                 creationflags=NOWINDOW, env=child_env(), **POPEN_EXTRA)
            self.proc = p
            for line in p.stdout:
                line = line.rstrip("\r\n")
                if line.startswith("FIYODIG_ITEM|"):
                    try:
                        _, i, n = line.split("|", 2)
                        self.sig_item.emit(int(i), int(n))
                    except ValueError:
                        pass
                elif line.startswith("FIYODIG_FILE|"):
                    files += 1
                    self.sig_file.emit(line.split("|", 1)[1].strip())
                elif line.startswith("ERROR"):
                    last_err = line
                else:
                    m = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%", line)
                    if m:
                        self.sig_progress.emit(float(m.group(1)))
            p.wait()
            rc = p.returncode
        except FileNotFoundError:
            self.sig_job_done.emit(False, "NO YT-DLP!")
            return
        except Exception:
            rc = 1
        ok = files > 0 and (rc == 0 or job["mode"] == "playlist")
        self.sig_job_done.emit(ok, last_err)

    def on_item(self, i, n):
        self.cur_i, self.cur_n, self.cur_pct = i, max(n, 1), 0.0
        self.refresh_status()
        self.refresh_progress()

    def on_progress(self, pct):
        self.cur_pct = pct
        self.refresh_progress()

    def on_file(self, path):
        self.results.append(path)
        self.had_file = True
        self.panel.set_files(self.results)

    def on_job_done(self, ok, err):
        self.busy = False
        self.proc = None
        if self.canceled:
            self.finish_batch(canceled=True)
            return
        if ok:
            self.jobs_done += 1
        else:
            self.errors += 1
            self.jobs_done += 1
            self.last_err = err
        if self.queue:
            self.start_next()
            return
        self.finish_batch()

    def finish_batch(self, canceled=False):
        self.in_batch = False
        self.busy = False
        self.cur_job = None
        self.cur_i = self.cur_n = 1
        self._ui_busy(False)
        self.lbl_now.setText("Now:001.01.00")
        if canceled:
            self.bar.set_value(0)
            self.flash("CANCELED.", 1800)
            self.cleanup_partials()
        elif self.results:
            self.bar.set_value(100)
            self.entry.setCursorPosition(len(self.entry.text()))
            self.set_status("READY." + (f" {self.errors} ERR" if self.errors else ""))
        else:
            self.bar.set_value(0)
            self.set_status("NO YT-DLP!" if "NO YT-DLP" in getattr(self, "last_err", "") else "ERROR.")
        self.start_next()      # por si se encoló algo mientras tanto (p.ej. tras update)

    def cancel(self):
        if not self.busy:
            return
        self.canceled = True
        self.queue.clear()
        self.kill_proc()

    def kill_proc(self):
        import subprocess
        p = self.proc
        if p and p.poll() is None:
            try:
                if IS_WIN:
                    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                                   creationflags=NOWINDOW, capture_output=True)
                else:                       # Mac/Linux: matar el grupo (yt-dlp + ffmpeg)
                    import signal
                    try:
                        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                    except Exception:
                        p.kill()
            except Exception:
                pass

    def cleanup_partials(self):
        """Borra .part/.ytdl que dejó la descarga cancelada."""
        base = self.cfg["folder"]
        try:
            for root, _, names in os.walk(base):
                for n in names:
                    if n.endswith((".part", ".ytdl")):
                        fp = os.path.join(root, n)
                        if os.path.getmtime(fp) >= self.t0 - 2:
                            os.remove(fp)
        except Exception:
            pass

    # ---- actualizar yt-dlp -------------------------------------------------
    def update_ytdlp(self):
        if self.updating or self.busy:
            return
        self.updating = True
        self.refresh_status()
        threading.Thread(target=self._ytdlp_worker, daemon=True).start()

    def _ytdlp_worker(self):
        import subprocess
        msg = "YT-DLP ERR."
        try:
            r = subprocess.run([tool("yt-dlp"), "-U"], creationflags=NOWINDOW, env=child_env(),
                               capture_output=True, text=True, timeout=90,
                               encoding="utf-8", errors="replace")
            out = (r.stdout + r.stderr).lower()
            if "up to date" in out:
                msg = "YT-DLP OK."
            elif "updated yt-dlp" in out or "updating to" in out:
                msg = "YT-DLP UPDATED."
            elif r.returncode == 0:
                msg = "YT-DLP OK."
        except FileNotFoundError:
            msg = "NO YT-DLP!"
        except Exception:
            pass
        self.sig_ytdlp.emit(msg)

    def on_ytdlp(self, msg):
        self.updating = False
        self.flash(msg, 1800)
        self.start_next()

    # ---- auto-update de DJ MAOS -------------------------------------------
    def on_start(self):
        if self.cfg.get("auto_ytdlp", True):
            self.update_ytdlp()
        if self.cfg.get("auto_app", True):
            self.check_app_update(False)
        self.check_clipboard()

    def check_app_update(self, manual=False):
        if not UPDATE_URL:
            if manual:
                self.flash("NO UPDATE URL")
            return
        threading.Thread(target=self._app_check_worker, args=(manual,), daemon=True).start()

    def _app_check_worker(self, manual):
        try:
            from urllib.request import urlopen, Request
            req = Request(UPDATE_URL, headers={"User-Agent": f"DJMAOS/{APP_VERSION}"})
            with urlopen(req, timeout=8) as r:
                info = json.loads(r.read().decode("utf-8"))
            if vtuple(info["version"]) > vtuple(APP_VERSION):
                self.sig_app_update.emit(info, manual)
            elif manual:
                self.sig_flash.emit("UP TO DATE.")
        except Exception:
            if manual:
                self.sig_flash.emit("UPDATE CHECK ERR.")

    def on_app_update(self, info, manual):
        ver = str(info.get("version", ""))
        if not manual and self.cfg.get("skip_version") == ver:
            return
        if not (FROZEN and (IS_WIN or IS_MAC)):
            self.flash(f"NEW v{ver}")
            return
        box = QMessageBox(self)
        box.setWindowTitle("DJ MAOS")
        box.setIcon(QMessageBox.Information)
        notes = str(info.get("notes", "")).strip()
        box.setText(f"Hay una versión nueva de DJ MAOS: v{ver}  (tenés la v{APP_VERSION}).")
        box.setInformativeText((notes + "\n\n" if notes else "") + ("Se abre la descarga en tu navegador." if IS_MAC else "¿Actualizar ahora?"))
        b_yes = box.addButton("Descargar" if IS_MAC else "Actualizar", QMessageBox.AcceptRole)
        box.addButton("Más tarde", QMessageBox.RejectRole)
        b_skip = box.addButton("Omitir esta versión", QMessageBox.DestructiveRole)
        box.exec()
        c = box.clickedButton()
        if c is b_skip:
            self._set_cfg("skip_version", ver)
        elif c is b_yes and IS_MAC:
            QDesktopServices.openUrl(QUrl(str(info.get("url_mac") or info.get("url", ""))))
        elif c is b_yes:
            if self.busy:
                self.flash("FINISH DIG FIRST")
                return
            self.updating = True
            self.refresh_status()
            threading.Thread(target=self._app_download_worker, args=(info,), daemon=True).start()

    def _app_download_worker(self, info):
        import hashlib
        import tempfile
        from urllib.request import urlopen, Request
        dest = os.path.join(tempfile.gettempdir(), "DJMAOS_Setup.exe")
        try:
            url = str(info["url"])
            sha = str(info.get("sha256", "")).strip().lower()
            if not url.lower().startswith("https://") or len(sha) != 64:
                raise ValueError("update info")
            h = hashlib.sha256()
            req = Request(url, headers={"User-Agent": f"DJMAOS/{APP_VERSION}"})
            with urlopen(req, timeout=30) as r, open(dest, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    if total:
                        self.sig_app_progress.emit(100.0 * got / total)
            if h.hexdigest() != sha:
                raise ValueError("hash")
            self.sig_app_ready.emit(dest)
        except Exception:
            try:
                os.remove(dest)
            except OSError:
                pass
            self.sig_app_fail.emit("UPDATE FAILED.")

    def on_app_progress(self, pct):
        self.set_status(f">>> UPDATING APP {int(pct)}% >>>")
        self.bar.set_value(pct)

    def on_app_fail(self, msg):
        self.updating = False
        self.bar.set_value(0)
        self.flash(msg, 2500)
        self.start_next()

    def on_app_ready(self, setup_exe):
        """Corre el instalador en silencio (reemplaza toda la carpeta de la app,
        incluido _internal) y lo reabre. Nosotros nos cerramos para liberar los archivos."""
        import subprocess
        try:
            subprocess.Popen([setup_exe, "/VERYSILENT", "/SUPPRESSMSGBOXES",
                              "/NORESTART", "/CLOSEAPPLICATIONS"],
                             creationflags=NOWINDOW | DETACHED, close_fds=True)
        except Exception:
            self.on_app_fail("UPDATE FAILED.")
            return
        self.close()

    def closeEvent(self, e):
        self.canceled = True
        self.kill_proc()
        save_cfg(self.cfg)
        super().closeEvent(e)
        QApplication.quit()


def apply_theme(app):
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in ((QPalette.Window, BG), (QPalette.WindowText, ACCENT), (QPalette.Base, BTN),
                      (QPalette.AlternateBase, BTN_HOVER), (QPalette.Text, ACCENT),
                      (QPalette.Button, BTN), (QPalette.ButtonText, ACCENT),
                      (QPalette.ToolTipBase, BTN), (QPalette.ToolTipText, ACCENT),
                      (QPalette.Highlight, "#a8a8a8"), (QPalette.HighlightedText, "#171717"),
                      (QPalette.PlaceholderText, ACCENT_DIM)):
        pal.setColor(role, QColor(col))
    app.setPalette(pal)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_theme(app)
    w = Fiyodig()
    scr = app.primaryScreen().availableGeometry()
    w.move((scr.width() - w.W) // 2, (scr.height() - w.H) // 3)
    w.show()
    sys.exit(app.exec())
