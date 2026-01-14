#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, os, re, queue, math, logging, io, traceback, datetime
import threading, time

from pathlib import Path
from typing import List, Dict, Any

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSettings
from PySide6.QtGui import QAction, QIcon, QCursor, QPainter, QPen, QBrush, QLinearGradient, QColor, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QTableWidget, QTableWidgetItem,
    QHeaderView, QSpinBox, QComboBox, QLineEdit, QMenu, QAbstractItemView,
    QStyledItemDelegate, QMessageBox, QInputDialog, QTabWidget, QPlainTextEdit, QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QToolTip
)
from license_check import check_license, save_token_text, APP_LICENSE_FILE
from yt_dlp import YoutubeDL
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import certifi  
os.environ["SSL_CERT_FILE"] = certifi.where()
APP_DIR = Path(__file__).resolve().parent
USER_DATA_DIR = Path.home() / ".myduyen"
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
TOKEN_FILE = USER_DATA_DIR / "gsheets_token.json"
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
CREDENTIALS_FILE = APP_DIR / "credentials.json"
COOKIE_FILE = APP_DIR / "cookies.txt"  # Cookie file for YouTube
INSTAGRAM_COOKIE_FILE = APP_DIR / "instagram_cookies.txt"  # Cookie file for Instagram

def ensure_embedded_credentials() -> Path:
    """Trả về Path tới file credentials.json."""
    return CREDENTIALS_FILE
# ------------------------ Helpers ------------------------

# === NEW: helpers dọn .part khi dính 416 ===
def _delete_part_files_by_id(out_dir: Path, video_id: str):
    """
    Xóa mọi file *.part ứng với mẫu [<id>] để tránh resume sai → 416.
    (Tên chuẩn từ _ydl_opts: ... [%(id)s].%(ext)s)
    """
    if not video_id:
        return
    patt = f"*[{video_id}].*.part"
    for p in out_dir.glob(patt):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass

def _has_416(err: Exception) -> bool:
    msg = repr(err).lower()
    return ("requested range not satisfiable" in msg) or ("http error 416" in msg)

def _sanitize_yt_watch_url(u: str) -> str:
    """
    Làm sạch tham số thời gian (&t=, ?t=, &start=, &time_continue=, &si=...) khỏi URL YouTube.
    Giữ nguyên các tham số còn lại.
    """
    if not u:
        return u
    try:
        from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
        p = urlparse(u)
        host = (p.netloc or "").lower()
        path = p.path or ""

        # Chỉ xử lý YouTube
        if ("youtube.com" in host and "/watch" in path) or ("youtu.be" in host):
            # youtu.be/<id>?t=123  → đổi sang dạng /watch?v=<id>
            if "youtu.be" in host:
                video_id = path.strip("/").split("/")[0] if path.strip("/") else ""
                if video_id:
                    # chuyển sang watch URL chuẩn
                    q = dict(parse_qsl(p.query, keep_blank_values=True))
                    q["v"] = video_id
                    # bỏ các tham số thời gian
                    for k in ("t", "start", "time_continue", "si"):
                        q.pop(k, None)
                    new_query = urlencode(q, doseq=True)
                    return urlunparse(("https", "www.youtube.com", "/watch", "", new_query, ""))

            # youtube.com/watch?...  → giữ lại mọi tham số trừ thời gian
            q = dict(parse_qsl(p.query, keep_blank_values=True))
            for k in ("t", "start", "time_continue", "si"):
                q.pop(k, None)
            new_query = urlencode(q, doseq=True)
            return urlunparse((p.scheme or "https", p.netloc, p.path, p.params, new_query, p.fragment))
    except Exception:
        # có lỗi thì trả về nguyên URL
        return u
    return u

def _gs_get_creds(credentials_path: str, token_path: str | None = None) -> Credentials:
    """
    Lấy credentials với các bước:
    1) Nếu đã có token → refresh nếu cần.
    2) Cố gắng mở local server trên 127.0.0.1:55009; nếu fail → port=0.
    3) Nếu vẫn không nhận redirect → fallback run_console() (copy-paste code).
    4) Lưu token vào USER_DATA_DIR (ghi chắc hơn APP_DIR).
    """
    try:
        token_file = TOKEN_FILE if token_path is None else Path(token_path)
        if token_file.parent and not token_file.parent.exists():
            token_file.parent.mkdir(parents=True, exist_ok=True)

        creds = None
        if token_file.exists():
            creds = Credentials.from_authorized_user_file(str(token_file), SHEETS_SCOPES)

        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                token_file.write_text(creds.to_json(), encoding="utf-8")
                return creds
            except Exception:
                # token hỏng → xoá để chạy luồng mới
                try:
                    token_file.unlink(missing_ok=True)
                except Exception:
                    pass
                creds = None

        # Không có token hợp lệ → chạy OAuth flow
        flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SHEETS_SCOPES)

        # 1) thử port “thân thiện” 55009 (hay dùng trong log của bạn)
        try:
            creds = flow.run_local_server(
                host="127.0.0.1",
                port=55009,
                open_browser=True,
                authorization_prompt_message="🔐 Trình duyệt sẽ mở để cấp quyền Google Sheets…",
                success_message="✅ Đăng nhập thành công. Bạn có thể đóng tab này.",
                timeout_seconds=180
            )
        except Exception:
            # 3) Cuối cùng: thử một lần nữa với cấu hình đặc biệt
            try:
                print("⚠️ Thử lại với cấu hình đặc biệt...")
                creds = flow.run_local_server(
                    bind_addr="127.0.0.1",
                    port=0,
                    open_browser=True,
                    authorization_prompt_message="🔐 Vui lòng cấp quyền trong trình duyệt...",
                    success_message="✅ Đăng nhập thành công!",
                    timeout_seconds=300
                )
            except Exception as final_err:
                # Nếu vẫn không được, báo lỗi rõ ràng
                raise RuntimeError(
                    "Không thể hoàn tất OAuth flow. Vui lòng kiểm tra:\n"
                    "1. Trình duyệt có mở được không?\n"
                    "2. Firewall có chặn localhost không?\n"
                    "3. Cổng 55009 hoặc các cổng khác có bị chiếm không?\n"
                    f"Chi tiết lỗi: {final_err}"
                )


        token_file.write_text(creds.to_json(), encoding="utf-8")
        return creds

    except Exception as e:
        raise RuntimeError(f"OAuth failed: {e}")

def _gs_extract_spreadsheet_id(url: str) -> str:
    # .../spreadsheets/d/<ID>/...
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    return m.group(1) if m else ""

def _gs_extract_gid(url: str) -> str | None:
    m = re.search(r"[?#&]gid=(\d+)", url)
    return m.group(1) if m else None

def _gs_get_sheet_name_by_gid(service, spreadsheet_id: str, gid: str) -> str | None:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sh in meta.get("sheets", []):
        props = sh.get("properties", {})
        if str(props.get("sheetId")) == str(gid):
            return props.get("title")
    return None

def gs_get_values_from_url(sheet_url: str, credentials_path: str, a1_range: str | None = None):
    """
    Đọc dữ liệu từ Google Sheet theo URL (hỗ trợ gid). Mặc định lấy A1:Z1000.
    """
    spreadsheet_id = _gs_extract_spreadsheet_id(sheet_url)
    if not spreadsheet_id:
        raise RuntimeError("Không lấy được Spreadsheet ID từ URL.")

    # Lưu ở USER_DATA_DIR để chắc quyền ghi
    creds = _gs_get_creds(credentials_path, None)

    service = build("sheets", "v4", credentials=creds)

    sheet_name = None
    gid = _gs_extract_gid(sheet_url)
    if gid:
        sheet_name = _gs_get_sheet_name_by_gid(service, spreadsheet_id, gid)
        if not sheet_name:
            raise RuntimeError("Không tìm thấy sheet theo GID.")
    else:
        # sheet đầu tiên
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheets = meta.get("sheets", [])
        sheet_name = (sheets[0]["properties"]["title"] if sheets else "Sheet1")

    rng = a1_range or f"{sheet_name}!A1:Z1000"
    resp = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=rng).execute()
    return resp.get("values", [])
def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", str(APP_DIR))
    return str(Path(base) / name)

QUALITY_OPTIONS = ["1080p", "720p", "480p", "360p"]

def detect_platform(url: str) -> str:
    u = (url or "").lower()
    if "tiktok.com" in u: return "tt"
    if "instagram.com" in u: return "ig"
    if "facebook.com" in u or "fb.watch" in u: return "fb"
    if "youtube.com" in u or "youtu.be" in u: return "yt"
    if "dailymotion.com" in u or "dai.ly" in u: return "dm"
    if ("reddit.com" in u) or ("v.redd.it" in u) or ("old.reddit.com" in u) or ("redd.it" in u): return "rd"
    return "other"

def build_format(quality: str, platform: str = "any") -> str:
    """
    Chọn format linh hoạt với fallback để tránh lỗi "format not available".
    """
    if quality == "Best":
        return (
            "bestvideo+bestaudio[acodec^=mp4a]/"
            "bestvideo+bestaudio/"
            "best"
        )

    h = {"1080p": 1080, "720p": 720, "480p": 480, "360p": 360}.get(quality, 0)
    if h:
        # ✅ Flexible format with fallbacks - tránh fail khi không có đúng độ phân giải
        return (
            f"bv[height<={h}]+ba/"
            f"bv*[height<={h}]+ba/"
            "bestvideo+bestaudio/"
            "best"
        )

    return "bestvideo+bestaudio/best"



def split_urls(text: str):
    urls = []
    for line in re.split(r"[\r\n\s]+", (text or "").strip()):
        if line and line.startswith("http"):
            urls.append(line)
    return urls

# ---- Nhận diện/mở rộng kênh/playlist thành danh sách video (explode) ----
_YDL_EXPAND_OPTS = {
    "quiet": True,
    "skip_download": True,
    "extract_flat": True,      # lấy danh sách nhanh, không tải metadata nặng
    "noplaylist": False,
    "lazy_playlist": False,
    # ✅ ép dùng web client cho YouTube ngay từ bước expand (tránh PO Token)
    "extractor_args": {
        "youtube": {
            "player_client": ["web", "web_embedded"],
            "player_skip": ["web_creator", "android", "ios", "tv", "tv_embedded", "mediaconnect"]
        }
    }
}

YOUTUBE_WATCH = "https://www.youtube.com/watch?v="

PLAYLIST_ID_RE = re.compile(r"[?&]list=([A-Za-z0-9_\-]+)")

def canonicalize_playlist_url(u: str) -> str:
    """Chuẩn hoá URL playlist YouTube. Nền tảng khác trả nguyên."""
    if not u:
        return u
    lu = u.lower()
    if ("youtube.com" in lu) or ("youtu.be" in lu):
        m = PLAYLIST_ID_RE.search(u)
        if m:
            pid = m.group(1)
            return f"https://www.youtube.com/playlist?list={pid}"
    return u

def canonicalize_channel_url(u: str) -> str:
    """
    Đưa link kênh YouTube về tab /videos; KHÔNG đụng vào URL video
    và KHÔNG áp dụng cho TikTok/Instagram/Facebook.
    """
    if not u:
        return u
    lu = u.lower()

    # Chỉ xử lý YouTube
    if ("youtube.com" in lu) or ("youtu.be" in lu):
        # Nếu là video watch thì giữ nguyên
        if "/watch" in lu:
            return u
        # Nếu là dạng kênh/slug thì thêm /videos
        if ("/channel/" in lu) or ("/user/" in lu) or ("/c/" in lu) or ("/@" in lu):
            return u.rstrip("/") + "/videos"
    return u

def _normalize_video_url(entry: Dict[str, Any]) -> str:
    if not entry: return ""
    u = entry.get("url") or ""
    vid = entry.get("id") or ""
    if u.startswith("http"): return u
    if vid: return f"{YOUTUBE_WATCH}{vid}"
    return u

def _flatten_entries(node: Dict[str, Any]) -> List[str]:
    out = []
    if not node: return out
    if "entries" in node and isinstance(node["entries"], list):
        for e in node["entries"]:
            if isinstance(e, dict) and "entries" in e:
                out.extend(_flatten_entries(e))
            elif isinstance(e, dict):
                u = _normalize_video_url(e)
                if u: out.append(u)
    else:
        u = _normalize_video_url(node)
        if u: out.append(u)
    return out

def looks_like_playlist_or_channel(u: str) -> bool:
    """Chỉ coi là playlist/kênh nếu là YouTube."""
    if not u:
        return False
    lu = u.lower()
    if ("youtube.com" in lu) or ("youtu.be" in lu):
        # playlist id
        if "list=" in lu or "/playlist" in lu:
            return True
        # các kiểu kênh/slug YouTube
        if ("/channel/" in lu) or ("/user/" in lu) or ("/c/" in lu) or ("/@" in lu):
            # đừng nhầm video YouTube (/watch?v=) là kênh
            if "/watch" in lu:
                return False
            return True
    # TikTok/IG/FB/...: KHÔNG explode
    return False

def get_video_title(url: str) -> str | None:
    """
    Lấy title từ YouTube video URL. Trả về None nếu không lấy được.
    """
    try:
        from urllib.parse import urlparse
        host = (urlparse(url or "").netloc or "").lower()
        is_yt = ("youtube.com" in host) or ("youtu.be" in host)
        if not is_yt:
            return None
        
        # Sanitize URL trước khi lấy title
        url = _sanitize_yt_watch_url(url)
        
        opts = {
            "quiet": True,
            "skip_download": True,
            "extractor_args": {
                "youtube": {
                    "player_client": ["web", "web_embedded"],
                    "player_skip": ["web_creator", "android", "ios", "tv", "tv_embedded", "mediaconnect"]
                }
            }
        }
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info and isinstance(info, dict):
                return info.get("title")
    except Exception:
        pass
    return None

def expand_url_to_videos(u: str) -> List[str]:
    """
    Chỉ expand playlist/kênh cho YouTube. Nền tảng khác trả [u].
    """
    try:
        if not looks_like_playlist_or_channel(u):
            return [u]

        # Lúc này chắc chắn là YouTube
        if "list=" in u or "/playlist" in u:
            u = canonicalize_playlist_url(u)
        else:
            u = canonicalize_channel_url(u)

        with YoutubeDL(_YDL_EXPAND_OPTS) as ydl:
            info = ydl.extract_info(u, download=False)

        if not info:
            return [u]
        if "entries" in info:
            vids = _flatten_entries(info)
            seen, uniq = set(), []
            for v in vids:
                if v and v not in seen:
                    seen.add(v); uniq.append(v)
            return uniq or [u]
        return [u]
    except Exception:
        return [u]
def extract_urls_from_text(text: str) -> list[str]:
    if not text: return []
    urls = []
    for part in re.split(r"[ \t]+", text.strip()):
        if part.startswith("http://") or part.startswith("https://"):
            urls.append(part)
    return urls

def parse_cell_content(cell: str):
    """
    Trả về (regular_urls, preventive_urls, sound_urls).
    Nhận diện 'link dự phòng', 'original_sound'/'original sound'.
    """
    regular, preventive, sound = [], [], []
    if not cell: return regular, preventive, sound
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", cell) if ln.strip()]
    in_prev = in_sound = False
    for ln in lines:
        lower = ln.lower()
        if "link dự phòng" in lower:
            in_prev, in_sound = True, False
            preventive += extract_urls_from_text(ln)
            continue
        if "original_sound" in lower or "original sound" in lower:
            in_sound, in_prev = True, False
            sound += extract_urls_from_text(ln)
            continue

        urls = extract_urls_from_text(ln)
        if in_prev: preventive += urls
        elif in_sound: sound += urls
        else: regular += urls
    return regular, preventive, sound

_SUPPORTED = (
    "youtube.com","youtu.be",
    "instagram.com",
    "facebook.com","fb.watch",
    "tiktok.com",
    "x.com","twitter.com",
    "dailymotion.com","dai.ly",
    "reddit.com","v.redd.it","old.reddit.com","redd.it"
)
def is_valid_video_url(u: str) -> bool:
    if not u or not (u.startswith("http://") or u.startswith("https://")): return False
    return any(dom in u.lower() for dom in _SUPPORTED)

# ------------------------ Glow viền hàng active ------------------------
class GlowDelegate(QStyledItemDelegate):
    def __init__(self, table, active_rows_getter):
        super().__init__(table)
        self.table = table
        self._get_active = active_rows_getter
        self._phase = 0.0
        # ✅ Tắt animation để tăng performance - chỉ update khi cần
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(500)  # ✅ Tăng từ 30ms lên 500ms để giảm CPU
    def _tick(self):
        self._phase = (self._phase + 0.02) % 1.0
        if self.table.isVisible():
            self.table.viewport().update()
    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        row = index.row()
        if row not in self._get_active(): return
        r = option.rect.adjusted(1, 1, -1, -1)
        t = self._phase
        c1 = QColor(56,189,248,200); c2 = QColor(59,130,246,200)
        pulse = 0.5 + 0.5*math.sin(2*math.pi*t)
        c1.setAlpha(int(100 + 80*pulse)); c2.setAlpha(int(100 + 80*pulse))
        grad = QLinearGradient(r.left(), r.top(), r.right(), r.bottom())
        grad.setColorAt((t+0.00)%1.0, c1); grad.setColorAt((t+0.25)%1.0, c2)
        grad.setColorAt((t+0.50)%1.0, c1); grad.setColorAt((t+0.75)%1.0, c2)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QBrush(grad), 2.0)); painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(r, 6, 6)

# ------------------------ Ô nhập “click để paste” ------------------------
class PasteOnClickLineEdit(QLineEdit):
    pastedOne = Signal(str)
    pastedMany = Signal(list)
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.setFixedHeight(34); self.setClearButtonEnabled(True)
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            cb = QApplication.clipboard().text().strip()
            urls = split_urls(cb)
            if len(urls) > 1: self.pastedMany.emit(urls); return
            elif len(urls) == 1: self.pastedOne.emit(urls[0]); return
        super().mousePressEvent(e)

# ------------------------ Worker tải đơn ------------------------
class DownloadWorker(QThread):
    progress = Signal(int, int)
    status   = Signal(int, str)
    done     = Signal(int, bool, str)
    log      = Signal(str)

    def __init__(self, row: int, url: str, out_dir: Path, fmt: str,
                 filename_base: str | None = None,
                 per_folder: bool = False,
                 from_collection: bool = False,
                 audio_only: bool = False,
                 convert_av1: bool = False,
                 parent=None):
        super().__init__(parent)
        self.row = row
        self.url = url
        self.out_dir = out_dir
        self.fmt = fmt
        self.filename_base = filename_base
        self.per_folder = per_folder
        self.from_collection = from_collection
        self.audio_only = audio_only
        self.convert_av1 = convert_av1
        self._pause_evt = threading.Event(); self._pause_evt.set()
        self._stop_flag = False
        self._was_paused = False

    def pause(self):
        """Tạm dừng tiến trình tải (giữ kết nối, đứng trong hook)."""
        self._pause_evt.clear()
        try:
            self.status.emit(self.row, "Paused")
        except Exception:
            pass

    def resume(self):
        """Tiếp tục sau khi pause."""
        self._pause_evt.set()
        try:
            self.status.emit(self.row, "Downloading")
        except Exception:
            pass

    def stop(self):
        """Hủy job đang chạy (sẽ raise trong hook)."""
        self._stop_flag = True
        # nếu đang pause, cho thoát khỏi vòng chờ ngay
        self._pause_evt.set()
        try:
            self.status.emit(self.row, "Canceling…")
        except Exception:
            pass

    class _YTDLPLogger:
        def __init__(self, outer): self.outer = outer
        def debug(self, msg):  self.outer.log.emit(f"[{self.outer.row}] {msg}")
        def warning(self, msg): self.outer.log.emit(f"[{self.outer.row}] WARNING: {msg}")
        def error(self, msg):  self.outer.log.emit(f"[{self.outer.row}] ERROR: {msg}")
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

    def _ydl_opts(self):
        """
        - per_folder=True: ghi vào subfolder cho từng item (video + thumbnail cùng chỗ)
        - convert_av1=True: re-encode video → H.264 (libx264). Tắt → giữ nguyên video (copy).
        - Luôn remux MP4; ép audio → AAC để tương thích.
        - ✅ YouTube: dùng ios/android client + cookies.txt (bypass SABR, nsig, age-restrict).
        - ✅ Instagram: dùng instagram_cookies.txt (bypass login required & rate-limit).
        - ✅ Các nền tảng khác: không dùng cookies để tránh lỗi.
        """
        from urllib.parse import urlparse
        from shutil import which

        host = (urlparse(self.url or "").netloc or "").lower()
        is_yt = ("youtube.com" in host) or ("youtu.be" in host)
        is_ig = "instagram.com" in host
        is_tt = "tiktok.com" in host
        is_fb = ("facebook.com" in host) or ("fb.watch" in host)
        is_dm = ("dailymotion.com" in host) or ("dai.ly" in host)
        is_rd = ("reddit.com" in host) or ("v.redd.it" in host) or ("old.reddit.com" in host) or ("redd.it" in host)
        is_tg = ("t.me" in host) or ("telegram.org" in host)

        # ---- ffmpeg ----
        def _which_ffmpeg():
            # ✅ Tìm ffmpeg ở nhiều vị trí, bao gồm subdirectory ffmpeg/
            search_paths = [
                APP_DIR / "ffmpeg.exe",                    # ffmpeg.exe trong APP_DIR
                APP_DIR / "ffmpeg",                         # ffmpeg trong APP_DIR
                APP_DIR / "ffmpeg" / "ffmpeg.exe",          # ffmpeg.exe trong APP_DIR/ffmpeg/
                APP_DIR / "ffmpeg" / "ffmpeg",              # ffmpeg trong APP_DIR/ffmpeg/
                Path(sys.executable).parent / "ffmpeg.exe", # ffmpeg.exe trong thư mục Python
                Path(sys.executable).parent / "ffmpeg",     # ffmpeg trong thư mục Python
            ]
            for p in search_paths:
                if Path(p).exists():
                    return str(p)
            # Cuối cùng, thử tìm trong PATH
            return which("ffmpeg")
        
        def _verify_ffmpeg(path: str | None) -> bool:
            """Verify ffmpeg thực sự có thể chạy được bằng cách test chạy"""
            if not path:
                return False
            try:
                import subprocess
                p = Path(path)
                
                # Nếu là file, kiểm tra tồn tại
                if p.is_file():
                    if not p.exists():
                        return False
                    # Trên Windows, nếu không có extension, thử thêm .exe
                    if sys.platform == "win32" and not path.lower().endswith((".exe", ".bat", ".cmd")):
                        exe_path = p.parent / f"{p.name}.exe"
                        if exe_path.exists():
                            path = str(exe_path)
                        else:
                            # Thử thêm .exe vào path hiện tại
                            path = str(p) + ".exe"
                            if not Path(path).exists():
                                return False
                    # Test chạy ffmpeg với -version
                    try:
                        result = subprocess.run(
                            [path, "-version"],
                            capture_output=True,
                            timeout=5,
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                        )
                        return result.returncode == 0
                    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                        return False
                # Nếu là thư mục, tìm ffmpeg bên trong
                elif p.is_dir():
                    for exe_name in ("ffmpeg.exe", "ffmpeg"):
                        exe_path = p / exe_name
                        if exe_path.exists() and exe_path.is_file():
                            try:
                                result = subprocess.run(
                                    [str(exe_path), "-version"],
                                    capture_output=True,
                                    timeout=5,
                                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                                )
                                if result.returncode == 0:
                                    return True
                            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                                continue
                    return False
                else:
                    return False
            except Exception:
                return False

        ffmpeg_path = _which_ffmpeg()
        have_ffmpeg = _verify_ffmpeg(ffmpeg_path)
        
        # ✅ Debug: log ffmpeg path nếu có
        if ffmpeg_path:
            try:
                self.log.emit(f"[{self.row}] 🔍 FFmpeg found: {ffmpeg_path} (verified: {have_ffmpeg})")
            except Exception:
                pass

        # ✅ Đã loại bỏ hoàn toàn cookie - không cần thiết

        # ---- Headers / extractor args ----
        headers = {"User-Agent": self.UA}
        referer = (
            "https://www.instagram.com/"   if is_ig else
            "https://www.facebook.com/"    if is_fb else
            "https://www.tiktok.com/"      if is_tt else
            "https://www.dailymotion.com/" if is_dm else
            "https://www.reddit.com/"      if is_rd else
            "https://t.me/"                if is_tg else
            None
        )
        if referer:
            headers["Referer"] = referer

        # ✅ Tối ưu extractor_args cho tất cả nền tảng - không cần cookies
        extractor_args = {}
        
        # YouTube: strategy phụ thuộc có cookie hay không
        if is_yt:
            if COOKIE_FILE.exists():
                # Có cookie: dùng ios/android client (bypass nsig/SABR tốt hơn)
                extractor_args["youtube"] = {
                    "player_client": ["ios", "android", "web"],
                    "player_skip": ["web_creator", "tv", "tv_embedded", "mediaconnect"]
                }
            else:
                # Không cookie: dùng web client
                extractor_args["youtube"] = {
                    "player_client": ["web", "web_embedded"],
                    "player_skip": ["web_creator", "ios", "android", "tv", "tv_embedded", "mediaconnect"]
                }
        
        # TikTok: không cần extractor_args đặc biệt, để yt-dlp tự động xử lý
        if is_tt:
            extractor_args["tiktok"] = {
                "webpage_download": ["1"]
            }
        
        # Facebook: bật HD
        if is_fb:
            extractor_args["facebook"] = {"hd": ["1"]}
        
        # Instagram: không cần extractor_args đặc biệt
        # Telegram: không cần extractor_args đặc biệt
        # Reddit: không cần extractor_args đặc biệt
        # Dailymotion: không cần extractor_args đặc biệt

        # ---- Chọn format ----
        desired_fmt = self.fmt
        if not have_ffmpeg and ("+" in desired_fmt) and not self.audio_only:
            desired_fmt = "best[ext=mp4][height<=720]/best"
        if self.audio_only:
            desired_fmt = "bestaudio/best"
        
        # ✅ TikTok & Facebook: dùng format linh hoạt hơn
        if is_tt or is_fb:
            if not self.audio_only:
                desired_fmt = "bv*+ba/b"  # Flexible best video + audio

        # ---- Thư mục output + mẫu tên file ----
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        safe_base = None
        if getattr(self, "filename_base", None):
            safe_base = re.sub(r'[\\/:*?"<>|]+', "_", self.filename_base)

        target_dir = self.out_dir
        subdir_tpl = None
        if self.per_folder:
            subdir_tpl = safe_base if safe_base else "%(title).190B [%(id)s]"
        if subdir_tpl:
            target_dir = self.out_dir / subdir_tpl
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                target_dir = self.out_dir

        if safe_base:
            outtmpl = str(target_dir / f"{safe_base}.%(ext)s")
        else:
            base_tpl = "%(title).190B [%(id)s]" if self.per_folder else "%(title)s"
            outtmpl = str(target_dir / f"{base_tpl}.%(ext)s")

        # ---- yt-dlp options core ----
        opts: dict[str, Any] = {
            "outtmpl": outtmpl,
            "format": desired_fmt,
            "quiet": True,
            "noprogress": True,

            # Network / độ ổn định
            "retries": 10,
            "fragment_retries": 10,
            "concurrent_fragment_downloads": 4,
            "http_chunk_size": 10 * 1024 * 1024,

            "geo_bypass": True,
            "geo_bypass_country": "US",
            "http_headers": headers,
            "extractor_args": extractor_args,

            "windowsfilenames": True,
            "trim_file_name": 180,
            "format_sort": ["res:2160,1440,1080,720,480,360", "fps", "hdr:12", "codec:avc1,h264,vp9,av01"],
            "format_sort_force": True,
            "prefer_ffmpeg": True,

            "progress_hooks": [self._hook],
        }

        # ✅ Cookie handling: Platform-specific cookie files to avoid errors
        # YouTube: cookies.txt file (for SABR, nsig, age-restricted videos)
        if is_yt and COOKIE_FILE.exists():
            try:
                opts["cookiefile"] = str(COOKIE_FILE)
                self.log.emit(f"[{self.row}] 🍪 YouTube: Using cookies from {COOKIE_FILE}")
            except Exception:
                pass
        
        # Instagram: instagram_cookies.txt file (for login required & rate-limit)
        if is_ig and INSTAGRAM_COOKIE_FILE.exists():
            try:
                opts["cookiefile"] = str(INSTAGRAM_COOKIE_FILE)
                self.log.emit(f"[{self.row}] 🍪 Instagram: Using cookies from {INSTAGRAM_COOKIE_FILE}")
            except Exception:
                pass

        # ---- FFmpeg & postprocessors ----
        if have_ffmpeg:
            if self.audio_only:
                opts.setdefault("postprocessors", []).append({
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "0",
                })
            else:
                opts["merge_output_format"] = "mp4"
                opts["ffmpeg_location"] = ffmpeg_path

                # Remux nhanh
                opts.setdefault("postprocessors", []).append({
                    "key": "FFmpegVideoRemuxer",
                    "preferedformat": "mp4",
                })

                # Convert có điều kiện:
                if self.convert_av1:
                    # Ép H.264 + AAC (bắt buộc khi cắt video)
                    opts.setdefault("postprocessors", []).append({
                        "key": "FFmpegVideoConvertor",
                        "preferedformat": "mp4",
                    })
                    opts.setdefault("postprocessor_args", []).extend([
                        "-c:v", "libx264",
                        "-pix_fmt", "yuv420p",
                        "-c:a", "aac",
                        "-b:a", "192k",
                        "-ar", "48000",
                        "-movflags", "+faststart",
                    ])
                else:
                    # Copy video stream; chỉ ép audio → AAC để tương thích MP4
                    opts.setdefault("postprocessors", []).append({
                        "key": "FFmpegVideoConvertor",
                        "preferedformat": "mp4",
                    })
                    opts.setdefault("postprocessor_args", []).extend([
                        "-c:v", "copy",
                        "-c:a", "aac",
                        "-b:a", "192k",
                        "-ar", "48000",
                        "-movflags", "+faststart",
                    ])

        # ---- Thumbnail + folder riêng (nếu bật) ----
        if self.per_folder and not self.audio_only:
            opts["writethumbnail"] = True
            if have_ffmpeg:
                opts.setdefault("postprocessors", []).append({
                    "key": "FFmpegThumbnailsConvertor",
                    "format": "jpg",
                })

        return opts


    def _hook(self, d):
        # Chặn khi người dùng Pause
        while not self._pause_evt.is_set() and not self._stop_flag:
            if not self._was_paused:
                # cập nhật trạng thái một lần khi vừa vào pause
                self.status.emit(self.row, "Paused")
                self._was_paused = True
            time.sleep(0.2)
        if self._was_paused and self._pause_evt.is_set() and not self._stop_flag:
            # vừa resume
            self._was_paused = False
            self.status.emit(self.row, "Downloading")

        # Nếu user bấm Stop → hủy ngay
        if self._stop_flag:
            raise KeyboardInterrupt("UserCanceled")

        st = d.get("status")
        st = d.get("status")
        if st == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            if total:
                pct = int(downloaded * 100 / max(1, total))
                if pct != getattr(self, '_last_pct', -1):
                    self.progress.emit(self.row, max(0, min(100, pct)))
                    self._last_pct = pct
            else:
                if not hasattr(self, '_sent_indeterminate'):
                    self.progress.emit(self.row, -1)
                    self._sent_indeterminate = True
            if not hasattr(self, '_sent_downloading'):
                self.status.emit(self.row, "Downloading")
                self._sent_downloading = True
        elif st == "finished":
            if not hasattr(self, '_sent_merging'):
                self.status.emit(self.row, "Merging")
                self._sent_merging = True

    def run(self):
        self.status.emit(self.row, "Starting")
        self.progress.emit(self.row, -1)
        self.log.emit(f"[{self.row}] Start download: {self.url}")

        def _is_canceled(ex: Exception) -> bool:
            return isinstance(ex, KeyboardInterrupt) or "UserCanceled" in repr(ex)

        # Chuẩn bị opts + lấy trước metadata (để biết id cho việc xoá .part khi cần)
        opts = self._ydl_opts()
        video_id = ""
        try:
            with YoutubeDL({**opts, "skip_download": True}) as y_info:
                info = y_info.extract_info(self.url, download=False)
                video_id = (info or {}).get("id") or ""
        except Exception:
            pass

        # --- Attempt 1: tải bình thường ---
        try:
            with YoutubeDL(opts) as ydl:
                ydl.download([self.url])
            self.progress.emit(self.row, 100)
            self.status.emit(self.row, "Bong")
            self.log.emit(f"[{self.row}] Done")
            self.done.emit(self.row, True, "")
            return
        except Exception as e:
            if _is_canceled(e):
                self.status.emit(self.row, "Canceled")
                self.log.emit(f"[{self.row}] Canceled by user")
                self.done.emit(self.row, False, "Canceled")
                return
            self.log.emit(f"[{self.row}] First attempt failed: {e!r}")

            # ✅ Thông báo rõ ràng khi gặp lỗi phổ biến
            msg = (repr(e) or "").lower()
            try:
                # Instagram: Login required / Rate-limit / Chrome permission
                if "instagram" in self.url.lower():
                    has_ig_cookie = INSTAGRAM_COOKIE_FILE.exists()
                    if ("could not copy chrome cookie" in msg) or ("permission denied" in msg and "chrome" in msg):
                        self.log.emit(f"[{self.row}] ⚠️ Instagram: Chrome cookie error → App sẽ thử instaloader/gallery-dl")
                    elif ("login required" in msg or "login_required" in msg or "checkpoint_required" in msg or "rate" in msg):
                        if not has_ig_cookie:
                            self.log.emit(f"[{self.row}] ⚠️ Instagram: Login required → Click '🍪 Import Cookie' chọn Instagram và import cookies!")
                        else:
                            self.log.emit(f"[{self.row}] ⚠️ Instagram: Video có thể bị private hoặc cookies hết hạn")
                    elif ("429" in msg):
                        self.log.emit(f"[{self.row}] ⚠️ Instagram: Rate-limit (429) → Đợi vài phút hoặc import cookies mới")
                
                # YouTube: Members-only
                if ("members-only" in msg or "member" in msg or "error 153" in msg or "player configuration error" in msg):
                    self.status.emit(self.row, "Members-only")
                    self.log.emit(f"[{self.row}] ⚠️ YouTube: Members-only → Import cookies từ tài khoản có membership")
                
                # YouTube: nsig/SABR/PO Token errors
                if ("nsig extraction failed" in msg) or ("sabr streaming" in msg) or ("n challenge" in msg) or ("po token" in msg):
                    self.log.emit(f"[{self.row}] ⚠️ YouTube: nsig/SABR/PO Token error → Giải pháp: 1) Import cookies (🍪) 2) Cài Node.js 3) pip install -U yt-dlp")
                
                # YouTube: 403 Forbidden
                if "403" in msg and "forbidden" in msg:
                    self.log.emit(f"[{self.row}] ⚠️ YouTube: 403 Forbidden → Giải pháp: 1) Import cookies (🍪) 2) pip install -U yt-dlp")
                
                # Format not available
                if ("only images are available" in msg) or ("format is not available" in msg):
                    cookie_exists = COOKIE_FILE.exists()
                    if not cookie_exists:
                        self.log.emit(f"[{self.row}] ⚠️ Format not available → Click '🍪 Import Cookie' để mở khóa formats!")
                    else:
                        self.log.emit(f"[{self.row}] ⚠️ Format not available → Thử giảm quality (720p/480p) hoặc pip install -U yt-dlp")
                
                # FFmpeg missing
                if ("ffmpeg" in msg or "ffprobe" in msg) and ("not found" in msg or "could not be found" in msg):
                    self.log.emit(f"[{self.row}] ⚠️ FFmpeg not found → Download FFmpeg và thêm vào PATH: https://ffmpeg.org/download.html")
                
                # TikTok: Impersonation
                if "tiktok" in self.url.lower() and ("impersonat" in msg or "not available" in msg):
                    self.log.emit(f"[{self.row}] ⚠️ TikTok: Video not available → Thử: pip install 'yt-dlp[default]' hoặc pip install -U yt-dlp")
            except Exception:
                pass

            # Nếu lỗi là 416 → dọn .part và thử lại fresh (tắt resume)
            if _has_416(e):
                try:
                    _delete_part_files_by_id(self.out_dir, video_id)
                except Exception:
                    pass
                try:
                    fresh = dict(opts)
                    fresh["continuedl"] = False
                    fresh["http_chunk_size"] = 0
                    fresh["concurrent_fragment_downloads"] = 1
                    with YoutubeDL(fresh) as y0:
                        y0.download([self.url])
                    self.progress.emit(self.row, 100)
                    self.status.emit(self.row, "Bong")
                    self.log.emit(f"[{self.row}] Retry fresh after 416 → OK")
                    self.done.emit(self.row, True, "")
                    return
                except Exception as e0:
                    if _is_canceled(e0):
                        self.status.emit(self.row, "Canceled")
                        self.log.emit(f"[{self.row}] Canceled by user")
                        self.done.emit(self.row, False, "Canceled")
                        return
                    self.log.emit(f"[{self.row}] Fresh retry after 416 failed: {e0!r}")

        # --- Retry TikTok với format đơn giản ---
        try:
            from urllib.parse import urlparse
            host = urlparse(self.url or "").netloc.lower()
            is_tt = "tiktok.com" in host
            if is_tt:
                self.status.emit(self.row, "Retry(TikTok/simple)")
                opts2 = self._ydl_opts()
                # ✅ Dùng format đơn giản nhất
                opts2["format"] = "best"
                # ✅ Loại bỏ extractor_args phức tạp
                opts2.pop("extractor_args", None)
                # ✅ Thêm force generic để fallback
                opts2["force_generic_extractor"] = False
                with YoutubeDL(opts2) as y2:
                    y2.download([self.url])
                self.progress.emit(self.row, 100)
                self.status.emit(self.row, "Bong")
                self.log.emit(f"[{self.row}] Retry TikTok OK")
                self.done.emit(self.row, True, "")
                return
        except Exception as e2:
            if _is_canceled(e2):
                self.status.emit(self.row, "Canceled")
                self.log.emit(f"[{self.row}] Canceled by user")
                self.done.emit(self.row, False, "Canceled")
                return
            self.log.emit(f"[{self.row}] Retry(TikTok) failed: {e2!r}")

        # --- Retry Facebook với format đơn giản ---
        try:
            from urllib.parse import urlparse
            host = urlparse(self.url or "").netloc.lower()
            is_fb = ("facebook.com" in host) or ("fb.watch" in host)
            if is_fb:
                self.status.emit(self.row, "Retry(Facebook/simple)")
                opts_fb = self._ydl_opts()
                opts_fb["format"] = "best"
                # Loại bỏ HD requirement
                if "extractor_args" in opts_fb and "facebook" in opts_fb["extractor_args"]:
                    opts_fb["extractor_args"].pop("facebook", None)
                with YoutubeDL(opts_fb) as yfb:
                    yfb.download([self.url])
                self.progress.emit(self.row, 100)
                self.status.emit(self.row, "Bong")
                self.log.emit(f"[{self.row}] Retry Facebook OK")
                self.done.emit(self.row, True, "")
                return
        except Exception as efb:
            if _is_canceled(efb):
                self.status.emit(self.row, "Canceled")
                self.log.emit(f"[{self.row}] Canceled by user")
                self.done.emit(self.row, False, "Canceled")
                return
            self.log.emit(f"[{self.row}] Retry(Facebook) failed: {efb!r}")

        # --- Retry Reddit generic ---
        try:
            from urllib.parse import urlparse
            host = urlparse(self.url or "").netloc.lower()
            is_rd = ("reddit.com" in host) or ("v.redd.it" in host) or ("old.reddit.com" in host) or ("redd.it" in host)
            if is_rd:
                self.status.emit(self.row, "Retry(Reddit)")
                opts3 = self._ydl_opts()
                opts3["format"] = "bv*+ba/best"
                hdrs = (opts3.get("http_headers") or {}).copy()
                hdrs["Referer"] = "https://www.reddit.com/"
                opts3["http_headers"] = hdrs
                opts3["force_generic_extractor"] = True
                with YoutubeDL(opts3) as y3:
                    y3.download([self.url])
                self.progress.emit(self.row, 100)
                self.status.emit(self.row, "Bong")
                self.log.emit(f"[{self.row}] Retry Reddit OK")
                self.done.emit(self.row, True, "")
                return
        except Exception as e3:
            if _is_canceled(e3):
                self.status.emit(self.row, "Canceled")
                self.log.emit(f"[{self.row}] Canceled by user")
                self.done.emit(self.row, False, "Canceled")
                return
            self.log.emit(f"[{self.row}] Reddit retry failed: {e3!r}\n{traceback.format_exc()}")

        # --- Retry Instagram với Instaloader ---
        try:
            from urllib.parse import urlparse
            host = urlparse(self.url or "").netloc.lower()
            is_ig = "instagram.com" in host
            if is_ig:
                self.status.emit(self.row, "Retry(Instagram/instaloader)")
                self.log.emit(f"[{self.row}] Trying instaloader for Instagram...")
                
                # Parse Instagram URL to get shortcode
                import re
                shortcode_match = re.search(r'instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)', self.url)
                if shortcode_match:
                    shortcode = shortcode_match.group(1)
                    
                    # Use instaloader
                    import subprocess
                    instaloader_cmd = [
                        sys.executable, "-m", "instaloader",
                        "--no-captions", "--no-metadata-json",
                        "--dirname-pattern", str(self.out_dir),
                        "--filename-pattern", "{shortcode}",
                        f"--post={shortcode}"
                    ]
                    
                    # Add cookies if available
                    if COOKIE_FILE.exists():
                        instaloader_cmd.extend(["--cookies", str(COOKIE_FILE)])
                    
                    result = subprocess.run(
                        instaloader_cmd,
                        capture_output=True,
                        text=True,
                        timeout=300,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                    )
                    
                    if result.returncode == 0:
                        self.progress.emit(self.row, 100)
                        self.status.emit(self.row, "Bong")
                        self.log.emit(f"[{self.row}] Instagram download (instaloader) OK")
                        self.done.emit(self.row, True, "")
                        return
                    else:
                        self.log.emit(f"[{self.row}] Instaloader failed: {result.stderr}")
                else:
                    self.log.emit(f"[{self.row}] Could not extract shortcode from Instagram URL")
        except Exception as e_insta:
            if _is_canceled(e_insta):
                self.status.emit(self.row, "Canceled")
                self.log.emit(f"[{self.row}] Canceled by user")
                self.done.emit(self.row, False, "Canceled")
                return
            self.log.emit(f"[{self.row}] Retry(Instagram/instaloader) failed: {e_insta!r}")

        # --- Retry Instagram với gallery-dl ---
        try:
            from urllib.parse import urlparse
            host = urlparse(self.url or "").netloc.lower()
            is_ig = "instagram.com" in host
            if is_ig:
                self.status.emit(self.row, "Retry(Instagram/gallery-dl)")
                self.log.emit(f"[{self.row}] Trying gallery-dl for Instagram...")
                
                import subprocess
                gallery_cmd = [
                    sys.executable, "-m", "gallery_dl",
                    "--dest", str(self.out_dir),
                    "--filename", "{category}_{post_shortcode}.{extension}",
                    self.url
                ]
                
                # Add cookies if available
                if COOKIE_FILE.exists():
                    gallery_cmd.extend(["--cookies", str(COOKIE_FILE)])
                
                result = subprocess.run(
                    gallery_cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                )
                
                if result.returncode == 0:
                    self.progress.emit(self.row, 100)
                    self.status.emit(self.row, "Bong")
                    self.log.emit(f"[{self.row}] Instagram download (gallery-dl) OK")
                    self.done.emit(self.row, True, "")
                    return
                else:
                    self.log.emit(f"[{self.row}] gallery-dl failed: {result.stderr}")
        except Exception as e_gallery:
            if _is_canceled(e_gallery):
                self.status.emit(self.row, "Canceled")
                self.log.emit(f"[{self.row}] Canceled by user")
                self.done.emit(self.row, False, "Canceled")
                return
            self.log.emit(f"[{self.row}] Retry(Instagram/gallery-dl) failed: {e_gallery!r}")

        # --- Retry YouTube với ios/android client (có cookies) ---
        try:
            from urllib.parse import urlparse
            host = (urlparse(self.url or "").netloc or "").lower()
            is_yt = ("youtube.com" in host) or ("youtu.be" in host)
            if is_yt and COOKIE_FILE.exists():
                # ✅ Có cookie: thử ios/android client (bypass nsig/SABR tốt hơn)
                self.status.emit(self.row, "Retry(YouTube/ios+cookie)")
                opts_ios = self._ydl_opts()
                opts_ios["format"] = "best"
                opts_ios["extractor_args"] = {
                    "youtube": {
                        "player_client": ["ios", "android"],
                        "player_skip": ["web", "web_creator", "web_embedded", "tv", "tv_embedded", "mediaconnect"]
                    }
                }
                with YoutubeDL(opts_ios) as yios:
                    yios.download([self.url])
                self.progress.emit(self.row, 100)
                self.status.emit(self.row, "Bong")
                self.log.emit(f"[{self.row}] Retry YouTube (ios+cookie) OK")
                self.done.emit(self.row, True, "")
                return
        except Exception as e_ios:
            if _is_canceled(e_ios):
                self.status.emit(self.row, "Canceled")
                self.log.emit(f"[{self.row}] Canceled by user")
                self.done.emit(self.row, False, "Canceled")
                return
            self.log.emit(f"[{self.row}] Retry(YouTube-ios) failed: {e_ios!r}")
        
        # --- Retry YouTube: format đơn giản (fallback cuối) ---
        try:
            from urllib.parse import urlparse
            host = (urlparse(self.url or "").netloc or "").lower()
            is_yt = ("youtube.com" in host) or ("youtu.be" in host)
            if is_yt:
                # ✅ Retry với format đơn giản (best)
                self.status.emit(self.row, "Retry(YouTube/simple)")
                opts_simple = self._ydl_opts()
                opts_simple["format"] = "best"
                with YoutubeDL(opts_simple) as ysimple:
                    ysimple.download([self.url])
                self.progress.emit(self.row, 100)
                self.status.emit(self.row, "Bong")
                self.log.emit(f"[{self.row}] Retry YouTube (simple) OK")
                self.done.emit(self.row, True, "")
                return
        except Exception as e_simple:
            if _is_canceled(e_simple):
                self.status.emit(self.row, "Canceled")
                self.log.emit(f"[{self.row}] Canceled by user")
                self.done.emit(self.row, False, "Canceled")
                return
            self.log.emit(f"[{self.row}] Retry(YouTube-simple) failed: {e_simple!r}")

        # --- Retry cuối: ép recode h264/aac ---
        try:
            self.status.emit(self.row, "Retry(recode h264/aac)")
            opts4 = self._ydl_opts()
            # TẮT thumbnail ở nhánh retry để tránh lỗi convert
            opts4.pop("writethumbnail", None)
            # đảm bảo có convert
            if "postprocessors" not in opts4:
                opts4["postprocessors"] = []
            opts4["postprocessors"].append({"key": "FFmpegVideoConvertor", "preferedformat": "mp4"})
            opts4.setdefault("postprocessor_args", []).extend([
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-movflags", "+faststart"
            ])
            with YoutubeDL(opts4) as y4:
                y4.download([self.url])
            self.progress.emit(self.row, 100)
            self.status.emit(self.row, "Bong")
            self.log.emit(f"[{self.row}] Retry recode OK")
            self.done.emit(self.row, True, "")
            return
        except Exception as e4:
            if _is_canceled(e4):
                self.status.emit(self.row, "Canceled")
                self.log.emit(f"[{self.row}] Canceled by user")
                self.done.emit(self.row, False, "Canceled")
                return
            self.log.emit(f"[{self.row}] Recode retry failed: {e4!r}\n{traceback.format_exc()}")

        # Hết cách
        self.status.emit(self.row, "Error")
        self.log.emit(f"[{self.row}] ERROR: cannot download after retries")
        self.done.emit(self.row, False, "Cannot download after retries")

# ------------------------ Themes (rút gọn cho ngắn) ------------------------
DARK_QSS = """
QWidget#Root { background-color: #0b1220; }
QTabBar::tab { background: #0f172a; color: #e5e7eb; padding: 6px 12px; border: 1px solid rgba(255,255,255,0.08); border-bottom: 0; border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 4px; }
QTabBar::tab:selected { background: #111827; }
QTabWidget::pane { border: 1px solid rgba(255,255,255,0.08); top: -1px; border-radius: 10px; }
QTableWidget { background: rgba(17,24,39,210); color: #e5e7eb; border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; gridline-color: rgba(255,255,255,0.06); }
QHeaderView::section { background: rgba(30,41,59,230); color: #e5e7eb; padding: 8px; border: 0; border-right: 1px solid rgba(255,255,255,0.06); }
QLineEdit, QLabel { color: #e5e7eb; }
QLineEdit { background: #0f172a; border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; padding: 6px 10px; }
QLineEdit:focus { border: 1px solid #38bdf8; }
QComboBox, QSpinBox { background: #0f172a; color: #e5e7eb; border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; padding: 4px 8px; }
/* ✅ Đã xóa QProgressBar CSS - dùng QLabel thay thế để tăng performance */
QPlainTextEdit { background: #0f172a; color: #e5e7eb; border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; }

/* Buttons: base */
QPushButton {
    color: #e5e7eb;
    background: #1f2937;
    border: 1px solid rgba(255,255,255,0.14);
    padding: 8px 12px;
    border-radius: 10px;
    font-weight: 600;
}
QPushButton:hover { border-color: #38bdf8; }
QPushButton:pressed { transform: translateY(1px); }

/* Variants (cũ) */
QPushButton[kind="primary"] { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #2563eb, stop:1 #0ea5e9); border-color: #60a5fa; color: white; }
QPushButton[kind="primary"]:hover { background: #2563eb; }
QPushButton[kind="success"] { background: #16a34a; border-color: #22c55e; color: white; }
QPushButton[kind="success"]:hover { background: #15803d; }
QPushButton[kind="warning"] { background: #d97706; border-color: #f59e0b; color: #111827; }
QPushButton[kind="warning"]:hover { background: #b45309; color: #f9fafb; }
QPushButton[kind="danger"] { background: #dc2626; border-color: #f87171; color: white; }
QPushButton[kind="danger"]:hover { background: #b91c1c; }
QPushButton[kind="info"] { background: #0891b2; border-color: #22d3ee; color: white; }
QPushButton[kind="info"]:hover { background: #0e7490; }
QPushButton[kind="ghost"] { background: transparent; color: #e5e7eb; border-color: rgba(255,255,255,0.22); }
QPushButton[kind="ghost"]:hover { border-color: #38bdf8; }

/* NEW: Pause / Resume */
QPushButton[kind="pause"]  { background: #f59e0b; color: #111827; border: 1px solid #fbbf24; }
QPushButton[kind="pause"]:hover  { background: #d97706; color: #f9fafb; }

QPushButton[kind="resume"] { background: #10b981; color: #052e16; border: 1px solid #34d399; }
QPushButton[kind="resume"]:hover { background: #059669; color: #ecfdf5; }
"""

# LIGHT_QSS: bỏ các block QMenuBar/QMenu/QToolBar/QStatusBar
LIGHT_QSS = """
QWidget#Root { background-color: #f5f7fb; }
QTabBar::tab { background: #ffffff; color: #111827; padding: 6px 12px; border: 1px solid #e5e7eb; border-bottom: 0; border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 4px; }
QTabBar::tab:selected { background: #f8fafc; }
QTabWidget::pane { border: 1px solid #e5e7eb; top: -1px; border-radius: 10px; }
QTableWidget { background: #ffffff; color: #111827; border: 1px solid #e5e7eb; border-radius: 10px; gridline-color: #e5e7eb; }
QHeaderView::section { background: #f3f4f6; color: #111827; padding: 8px; border: 0; border-right: 1px solid #e5e7eb; }
QLineEdit, QLabel { color: #111827; }
QLineEdit { background: #ffffff; border: 1px solid #d1d5db; border-radius: 8px; padding: 6px 10px; }
QLineEdit:focus { border: 1px solid #2563eb; }
QComboBox, QSpinBox { background: #ffffff; color: #111827; border: 1px solid #d1d5db; border-radius: 8px; padding: 4px 8px; }
/* ✅ Đã xóa QProgressBar CSS - dùng QLabel thay thế để tăng performance */
QPlainTextEdit { background: #ffffff; color: #111827; border: 1px solid #d1d5db; border-radius: 8px; }

/* Buttons: base */
QPushButton {
    color: #111827;
    background: #ffffff;
    border: 1px solid #d1d5db;
    padding: 8px 12px;
    border-radius: 10px;
    font-weight: 600;
}
QPushButton:hover { border-color: #2563eb; }
QPushButton:pressed { transform: translateY(1px); }

/* Variants (cũ) */
QPushButton[kind="primary"] { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #3b82f6, stop:1 #60a5fa); border-color: #93c5fd; color: white; }
QPushButton[kind="primary"]:hover { background: #2563eb; }
QPushButton[kind="success"] { background: #22c55e; border-color: #86efac; color: #052e16; }
QPushButton[kind="success"]:hover { background: #16a34a; color: white; }
QPushButton[kind="warning"] { background: #f59e0b; border-color: #fcd34d; color: #111827; }
QPushButton[kind="warning"]:hover { background: #d97706; color: #f9fafb; }
QPushButton[kind="danger"] { background: #ef4444; border-color: #fecaca; color: white; }
QPushButton[kind="danger"]:hover { background: #dc2626; }
QPushButton[kind="info"] { background: #06b6d4; border-color: #a5f3fc; color: white; }
QPushButton[kind="info"]:hover { background: #0891b2; }
QPushButton[kind="ghost"] { background: transparent; color: #111827; border-color: #cbd5e1; }
QPushButton[kind="ghost"]:hover { border-color: #2563eb; }

/* NEW: Pause / Resume */
QPushButton[kind="pause"]  { background: #f59e0b; color: #111827; border: 1px solid #fbbf24; }
QPushButton[kind="pause"]:hover  { background: #d97706; color: #f9fafb; }

QPushButton[kind="resume"] { background: #22c55e; color: #052e16; border: 1px solid #86efac; }
QPushButton[kind="resume"]:hover { background: #16a34a; color: white; }
"""

# đặt trước phần MainWindow hoặc sau helpers
class LicenseDialog(QDialog):
    """Dialog yêu cầu dán token khi license chưa hợp lệ.
       Hiển thị Device ID (có nút Copy) + vùng nhập token rõ ràng.
    """
    def __init__(self, reason: str, parent=None, device_id: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("License required")
        self.setModal(True)
        self.resize(700, 360)

        # ---- Styles: tăng tương phản, font monospace cho token ---
        self.setStyleSheet("""
        QDialog { background: #F7F8FA; }
        QLabel#Title    { color:#0F172A; font-weight:700; font-size:14px; }
        QLabel#Reason   { color:#374151; font-size:13px; }
        QLabel#DidTitle { color:#111827; font-weight:600; }
        QLineEdit[readOnly="true"]{
            background:#FFFFFF; color:#111827; border:1px solid #CBD5E1;
            padding:8px 10px; border-radius:8px; font-size:13px;
        }
        QTextEdit{
            background:#FFFFFF; color:#111827; border:1px solid #CBD5E1;
            padding:10px; border-radius:10px; font-size:13px;
            font-family: Consolas, 'Courier New', monospace;
        }
        QPushButton {
            background:#111827; color:white; border:0; border-radius:10px;
            padding:8px 14px; font-weight:600;
        }
        QPushButton:hover { filter: brightness(1.08); }
        QPushButton#Secondary { background:#4B5563; }
        """)

        # ---- lấy Device ID ----
        did = device_id
        if not did:
            try:
                from license_check import get_device_id as _get_did  # type: ignore
                did = _get_did()
            except Exception:
                did = None
        if not did:
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as k:
                    v, _ = winreg.QueryValueEx(k, "MachineGuid")
                did = str(v).strip()
            except Exception:
                did = "(unknown)"

        # ================= UI =================
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        lbl_title = QLabel("🔐 License required")
        lbl_title.setObjectName("Title")
        root.addWidget(lbl_title)

        lbl_reason = QLabel(
            "License chưa hợp lệ.\n"
            "Contact for :hungse17002@gmail.com"
        )
        lbl_reason.setObjectName("Reason")
        lbl_reason.setWordWrap(True)
        root.addWidget(lbl_reason)

        # Device ID row (ô readonly + nút Copy)
        row_did = QHBoxLayout(); row_did.setSpacing(8)
        lbl_did = QLabel("Device ID:")
        lbl_did.setObjectName("DidTitle")
        self.le_did = QLineEdit(did)
        self.le_did.setReadOnly(True)
        btn_copy = QPushButton("Copy")
        btn_copy.setToolTip("Copy Device ID vào clipboard")
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.le_did.text()))
        row_did.addWidget(lbl_did)
        row_did.addWidget(self.le_did, 1)
        row_did.addWidget(btn_copy)
        root.addLayout(row_did)

        # Token box
        self.txt = QTextEdit()
        self.txt.setPlaceholderText("Contact for :hungse17002@gmail.com")
        self.txt.setAcceptRichText(False)
        root.addWidget(self.txt, 1)

        # Buttons
        row_btn = QHBoxLayout()
        btn_save = QPushButton("Lưu token")
        btn_cancel = QPushButton("Thoát")
        btn_cancel.setObjectName("Secondary")
        row_btn.addWidget(btn_save)
        row_btn.addStretch(1)
        row_btn.addWidget(btn_cancel)
        root.addLayout(row_btn)

        btn_save.clicked.connect(self._on_save)
        btn_cancel.clicked.connect(self.reject)

    def _on_save(self):
        t = (self.txt.toPlainText() or "").replace("\r", "").replace("\n", "").strip()
        if not t:
            QMessageBox.information(self, "Info", "Chưa có token.")
            return
        try:
            save_token_text(t)
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
class QtLogHandler(logging.Handler):
    """Đẩy log vào hàm append_fn (ví dụ: self._append_log)."""
    def __init__(self, append_fn):
        super().__init__()
        self.append_fn = append_fn
    def emit(self, record):
        try:
            msg = self.format(record)
            self.append_fn(msg)
        except Exception:
            pass

class StreamToLogger(io.TextIOBase):
    """Chuyển mọi print/traceback sang logging."""
    def __init__(self, logger, level=logging.INFO):
        self.logger = logger
        self.level = level
        self._buf = ""
    def write(self, b):
        s = str(b)
        self._buf += s
        # flush theo dòng
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if line:
                self.logger.log(self.level, line)
        return len(s)
    def flush(self):
        if self._buf.strip():
            self.logger.log(self.level, self._buf.strip())
            self._buf = ""

# ------------------------ MainWindow ------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Mỹ Duyên"); self.resize(820, 600)

        self.quality = "1080p"
        self.concurrency = 10
        self.max_retries = 3  # Số lần retry mặc định
        self.out_dir = APP_DIR / "Output"; self.out_dir.mkdir(parents=True, exist_ok=True)

        self.pending_rows = queue.Queue()
        self.active = {}        # row -> worker
        self.active_rows = set()
        self.row_retries = {}   # row -> số lần đã retry
        self.max_workers = 5  # Giảm từ 10 xuống 5 để giảm lag UI
        self.is_running = False

        self.settings = QSettings(str(APP_DIR / "ui_prefs.ini"), QSettings.IniFormat)
        self.theme = self.settings.value("theme", "dark")
        self.row_filename = {}
        self.row_meta = {}  
        self.row_url = {}  
        self.is_paused = False
        # ✅ Throttle progress updates để tăng performance
        self._progress_cache = {}  # row -> (last_percent, last_update_time)
        self._progress_throttle_ms = 1000  # Tăng để giảm lag hơn
        self._build_ui()
        self._setup_logging()
        self.concurrency = self.spin_threads.value()
        self._apply_background()
        self.apply_theme(self.theme)

        # ✅ NHẮC CẬP NHẬT yt-dlp (hữu ích khi dính nsig/SABR)
        try:
            import yt_dlp
            # ✅ Fix: yt-dlp lưu version ở yt_dlp.version.__version__
            try:
                from yt_dlp import version as yt_ver
                ver = yt_ver.__version__
            except Exception:
                ver = getattr(yt_dlp, "__version__", "unknown")
            
            self.logger.info(f"yt-dlp version: {ver}")
            
            try:
                from packaging.version import Version
                if ver != "unknown" and "nightly" not in ver and Version(ver) < Version("2024.12.1"):
                    self._toast("Khuyên cập nhật yt-dlp: pip install -U yt-dlp", 3500)
            except Exception:
                # packaging có thể chưa có → bỏ qua yên lặng
                pass
        except Exception:
            pass

        # --- Kiểm tra license khi khởi động ---
        from license_check import check_license, APP_LICENSE_FILE
        st = check_license()
        if not st.ok:
            # Cho phép người dùng dán token và lưu, sau đó kiểm tra lại 1 lần
            dlg = LicenseDialog(st.reason, self)
            if dlg.exec() != QDialog.Accepted:
                QMessageBox.critical(self, "License", "Ứng dụng cần license để chạy.")
                sys.exit(1)
            st2 = check_license()
            if not st2.ok:
                QMessageBox.critical(self, "License", f"Token không hợp lệ:\n{st2.reason}")
                sys.exit(1)

        # Bạn có thể hiển thị chủ sở hữu & hạn dùng ở tiêu đề hoặc About
        try:
            self.setWindowTitle(f"Mỹ Duyên — Licensed to {st.owner} (exp {st.exp})")
        except Exception:
            pass

    # ===== Pause / Resume / Stop =====
    def pause_all(self):
        """Tạm dừng mọi job đang chạy, và không khởi động job mới."""
        if not self.is_running:
            return
        self.is_paused = True
        for r, w in list(self.active.items()):
            try: w.pause()
            except Exception: pass
        # Không gọi _start_next khi pause
        self._toast("Paused all", 1500)
    def remove_success(self):
        """Xoá tất cả hàng có trạng thái 'Bong' (đã tải xong)."""
        removed = 0
        for r in range(self.tbl.rowCount() - 1, -1, -1):
            if r in self.active:
                continue
            st = self.tbl.item(r, 4).text() if self.tbl.item(r, 4) else ""
            if st == "Bong":
                self.tbl.removeRow(r)
                self.active_rows.discard(r)
                removed += 1
        if removed:
            self._reindex_after_row_change()
            self._toast(f"Đã xoá {removed} hàng thành công.", 2200)
        else:
            self._toast("Không có hàng 'Bong' để xoá.", 1800)

    def remove_selected(self):
        rows = sorted({idx.row() for idx in self.tbl.selectedIndexes()}, reverse=True)
        if not rows: return
        for r in rows:
            if r in self.active: 
                continue
            self.tbl.removeRow(r)
            self.active_rows.discard(r)
        self._reindex_after_row_change()
        self._renumber()

    def resume_all(self):
        """Tiếp tục các job đang chạy và cho phép khởi động job mới từ hàng đợi."""
        if not self.is_running:
            return
        self.is_paused = False
        for r, w in list(self.active.items()):
            try: w.resume()
            except Exception: pass
        # Lấp chỗ trống tiếp
        for _ in range(max(1, self.max_workers - len(self.active))):
            self._start_next()
        self._toast("Resumed", 1500)

    def stop_selected(self):
        """Hủy các hàng đang được chọn: nếu đang chạy → stop; nếu đang đợi → xóa khỏi hàng đợi."""
        rows = sorted({idx.row() for idx in self.tbl.selectedIndexes()})
        if not rows: 
            return
        # 1) Hủy các worker đang chạy
        for r in rows:
            w = self.active.get(r)
            if w:
                try:
                    w.stop()
                except Exception:
                    pass
                continue

        # 2) Loại khỏi hàng đợi
        self._requeue_excluding(set(rows))

        # 3) Cập nhật trạng thái hiển thị
        for r in rows:
            if r not in self.active:
                self._set_status(r, "Canceled")
                self._set_progress(r, 0)

        self._update_stats()
        # Nếu còn slot trống và không pause → tiếp tục
        if self.is_running and not self.is_paused:
            for _ in range(max(1, self.max_workers - len(self.active))):
                self._start_next()

    def stop_all(self):
        """Hủy toàn bộ tải hiện tại và xóa sạch hàng đợi."""
        for r, w in list(self.active.items()):
            try: w.stop()
            except Exception: pass
        self._requeue_excluding(set(range(self.tbl.rowCount())))
        self.is_running = False
        self.btn_start.setEnabled(True)
        self._toast("Stopped all", 1500)

    def _requeue_excluding(self, banned_rows: set[int]):
        """Tạo lại hàng đợi, bỏ các row trong banned_rows."""
        try:
            new_q = queue.Queue()
            while True:
                r = self.pending_rows.get_nowait()
                if r not in banned_rows:
                    new_q.put(r)
        except queue.Empty:
            pass
        self.pending_rows = new_q

    def _toast(self, text: str, ms: int = 2500):
        try:
            QToolTip.showText(
                self.mapToGlobal(self.rect().center()),
                text, self, self.rect(), ms
            )
        except Exception:
            pass
    
    def _import_cookie(self):
        """Import cookies.txt file for YouTube or Instagram authentication."""
        # Ask user which platform
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QRadioButton, QDialogButtonBox
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Import Cookies")
        layout = QVBoxLayout()
        
        label = QLabel("Chọn nền tảng để import cookies:")
        layout.addWidget(label)
        
        radio_youtube = QRadioButton("🎥 YouTube (cookies.txt)")
        radio_youtube.setChecked(True)
        layout.addWidget(radio_youtube)
        
        radio_instagram = QRadioButton("📷 Instagram (instagram_cookies.txt)")
        layout.addWidget(radio_instagram)
        
        info_label = QLabel(
            "\n💡 Hướng dẫn export cookies:\n"
            "1. Cài extension: 'Get cookies.txt LOCALLY'\n"
            "2. Đăng nhập YouTube/Instagram trên browser\n"
            "3. Click extension → Export cookies\n"
            "4. Import file vào đây"
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        dialog.setLayout(layout)
        
        if dialog.exec() != QDialog.Accepted:
            return
        
        is_youtube = radio_youtube.isChecked()
        platform_name = "YouTube" if is_youtube else "Instagram"
        target_file = COOKIE_FILE if is_youtube else INSTAGRAM_COOKIE_FILE
        
        # Select cookie file
        path, _ = QFileDialog.getOpenFileName(
            self, 
            f"Select {platform_name} cookies.txt file", 
            str(APP_DIR), 
            "Text Files (*.txt);;All Files (*.*)"
        )
        if not path:
            return
        
        try:
            # Copy the selected cookie file to the app directory
            import shutil
            shutil.copy(path, str(target_file))
            
            # Verify the file exists and has content
            if target_file.exists() and target_file.stat().st_size > 0:
                help_text = ""
                if is_youtube:
                    help_text = (
                        f"💡 **YouTube cookies help with:**\n"
                        f"• Fix SABR streaming errors ✅\n"
                        f"• Fix nsig challenge errors ✅\n"
                        f"• Access age-restricted videos ✅\n"
                        f"• Unlock more formats/quality ✅"
                    )
                else:
                    help_text = (
                        f"💡 **Instagram cookies help with:**\n"
                        f"• Bypass login required ✅\n"
                        f"• Fix rate-limit errors ✅\n"
                        f"• Access private/restricted content ✅"
                    )
                
                QMessageBox.information(
                    self, 
                    f"{platform_name} Cookie Import Success! 🍪", 
                    f"✅ **Cookie file imported successfully!**\n\n"
                    f"📍 Location: {target_file}\n"
                    f"📊 Size: {target_file.stat().st_size} bytes\n\n"
                    f"🎯 **Next steps:**\n"
                    f"1. Click '🔄 Retry Fail' to retry failed downloads\n"
                    f"2. Or add new URLs and click '▶ Start'\n\n"
                    f"{help_text}"
                )
                self.logger.info(f"{platform_name} cookie file imported: {target_file}")
                self._toast(f"{platform_name} cookie imported! Click '🔄 Retry Fail' ✅", 4000)
            else:
                QMessageBox.warning(
                    self, 
                    "Cookie Import", 
                    "Cookie file is empty or invalid."
                )
        except Exception as e:
            QMessageBox.critical(
                self, 
                "Cookie Import Failed", 
                f"Failed to import cookie file:\n{e}"
            )
            self.logger.error(f"Cookie import failed: {e}")
    
    def retry_failed(self):
        """Retry all downloads with 'Error' status and start downloading immediately."""
        # Đếm số lượng downloads bị lỗi
        retry_count = 0
        for r in range(self.tbl.rowCount()):
            st_item = self.tbl.item(r, 4)  # Status column
            if st_item and st_item.text() == "Error":
                retry_count += 1
        
        if retry_count == 0:
            self._toast("No failed downloads to retry.", 2000)
            return
        
        # Nếu chưa chạy, khởi tạo và bắt đầu tải xuống ngay
        if not self.is_running:
            # Reset các downloads bị lỗi
            for r in range(self.tbl.rowCount()):
                st_item = self.tbl.item(r, 4)  # Status column
                if st_item and st_item.text() == "Error":
                    self._set_status(r, "Pending")
                    self._set_progress(r, -1)
                    self.row_retries.pop(r, None)  # Reset retry count
            
            # Tự động bắt đầu tải xuống
            self._toast(f"Retrying {retry_count} failed download(s)...", 3000)
            self.logger.info(f"Retry failed: starting {retry_count} downloads")
            self.start_download()  # Bắt đầu tải xuống ngay
        else:
            # Nếu đang chạy, thêm các row error vào queue ngay
            for r in range(self.tbl.rowCount()):
                st_item = self.tbl.item(r, 4)  # Status column
                if st_item and st_item.text() == "Error":
                    # Reset status, retry count và thêm vào queue
                    self._set_status(r, "Queued")
                    self._set_progress(r, -1)
                    self.row_retries.pop(r, None)  # Reset retry count
                    self.pending_rows.put(r)
            
            # Cập nhật stats và bắt đầu download ngay các row retry
            self._update_stats()
            for _ in range(min(retry_count, self.max_workers - len(self.active))):
                self._start_next()
            
            self._toast(f"Retrying {retry_count} failed download(s)...", 3000)
            self.logger.info(f"Retry failed: added {retry_count} downloads to queue")
    def _show_about(self):
        QMessageBox.information(
            self, "About",
            "Mỹ Duyên Downloader\n"
            "• PySide6 + yt-dlp\n"
            "• Google Sheets import\n"
            "• License verification\n\n"
            "© 11LABS"
        )

    def _show_shortcuts(self):
        QMessageBox.information(
            self, "Shortcuts",
            "Ctrl+T  — Toggle theme\n"
            "Context menu (Right-click on table) — Paste URLs / Explode / Remove"
        )
    
    def _show_help(self):
        """Mở guide xử lý lỗi các nền tảng."""
        guide_path = APP_DIR / "YOUTUBE_ERRORS_GUIDE.md"
        if not guide_path.exists():
            QMessageBox.information(
                self, 
                "Common Errors & Fixes",
                "🔴 **YouTube Errors:**\n"
                "• SABR/nsig/PO Token/403 Forbidden\n"
                "  → 1) Click '🍪 Import Cookie'\n"
                "  → 2) Cài Node.js\n"
                "  → 3) pip install -U yt-dlp\n\n"
                "• Members-only\n"
                "  → Import cookies từ tài khoản có membership\n\n"
                "• Format not available\n"
                "  → 1) Click '🍪 Import Cookie'\n"
                "  → 2) Hoặc thử giảm quality (720p/480p)\n\n"
                "🟣 **Instagram Errors:**\n"
                "• Login required / Rate-limit\n"
                "  → Click '🍪 Import Cookie' chọn Instagram\n"
                "  → Export cookies từ Instagram browser\n"
                "  → App sẽ tự động dùng cookies khi tải\n\n"
                "🟢 **TikTok Errors:**\n"
                "• Video not available\n"
                "  → pip install 'yt-dlp[default]'\n"
                "  → pip install -U yt-dlp\n\n"
                "🔧 **FFmpeg Missing:**\n"
                "• Download: https://ffmpeg.org/download.html\n"
                "• Thêm vào PATH (Environment Variables)\n\n"
                "**Quick Fix:**\n"
                "1. Click '🍪 Import Cookie'\n"
                "   - YouTube: Fix SABR/nsig/403\n"
                "   - Instagram: Fix login/rate-limit\n"
                "2. Click '🔄 Retry Fail' (auto start)\n"
                "3. pip install -U yt-dlp (update)"
            )
            return
        
        # Mở file guide
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(guide_path))
            elif sys.platform == "darwin":
                os.system(f'open "{guide_path}"')
            else:
                os.system(f'xdg-open "{guide_path}"')
            self._toast("Opened YouTube Errors Guide", 2500)
        except Exception as e:
            QMessageBox.warning(self, "Open Guide", f"Cannot open guide:\n{e}")
    def _add_row(self, url, quality, filename_base=None, stt_text=None, from_collection=False):
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)

        # Cột 0: checkbox Sel
        sel_item = QTableWidgetItem()
        sel_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
        sel_item.setCheckState(Qt.Checked)  # mặc định chọn để tải
        self.tbl.setItem(r, 0, sel_item)

        # Cột 1..5 như mô tả
        stt_val = stt_text if stt_text else str(r + 1)
        self.tbl.setItem(r, 1, QTableWidgetItem(stt_val))
        self.tbl.setItem(r, 2, QTableWidgetItem(url))
        self.tbl.setItem(r, 3, QTableWidgetItem(quality))
        self.tbl.setItem(r, 4, QTableWidgetItem("Pending"))
        # ✅ Dùng QLabel thay QProgressBar để tăng performance (không có animation)
        lbl = QLabel("—"); lbl.setAlignment(Qt.AlignCenter); lbl.setFixedHeight(16)
        self.tbl.setCellWidget(r, 5, lbl)

        self.row_filename[r] = filename_base
        self.row_url[r] = url

        # phân loại main/preventive/sound theo STT
        stt_lower = (stt_val or "").lower()
        kind = "main"
        if stt_lower.endswith("_preventive"):
            kind = "preventive"
        elif stt_lower.endswith("_sound"):
            kind = "sound"

        group = stt_lower
        for suf in ("_preventive", "_sound"):
            if group.endswith(suf):
                group = group[: -len(suf)]
        # Lưu metadata
        self.row_meta[r] = {
            "group": group, 
            "kind": kind, 
            "from_collection": bool(from_collection),
            "url": url
        }

        self._update_stats()

    def _import_gsheet(self):
        # hỏi URL sheet
        sheet_url, ok = QInputDialog.getText(self, "Google Sheet", "Paste Google Sheet URL:")
        if not ok or not sheet_url.strip():
            return
        # chọn credentials.json nếu chưa có
        cred_file = ensure_embedded_credentials()
        cred_path = resource_path("credentials.json")
        if not Path(cred_path).exists():
            path, _ = QFileDialog.getOpenFileName(self, "Chọn credentials.json", "", "JSON (*.json)")
            if not path:
                QMessageBox.warning(self, "Google Sheet", "Thiếu credentials.json")
                return
            cred_path = path

        try:
            self._set_status_all("Reading Sheet…")
            values = gs_get_values_from_url(sheet_url.strip(), cred_path)
        except Exception as e:
            QMessageBox.critical(self, "Google Sheet", f"Lỗi đọc sheet:\n{e}")
            return

        if not values or len(values) < 2:
            QMessageBox.information(self, "Google Sheet", "Không có dữ liệu (cần >= 2 hàng).")
            return

        # ✅ Map URL -> dict với stt_list
        url_stt_map: dict[str, dict] = {}
        last_stt = ""

        # Giả định: Cột F = STT (index 5), Cột C = link video (index 2)
        for i in range(1, len(values)):
            row = values[i]
            stt = (row[5].strip() if len(row) > 5 and row[5] else "")  # F
            link_cell = (row[2] if len(row) > 2 else "")               # C

            if stt:
                last_stt = stt
            else:
                stt = last_stt

            reg, prev, snd = parse_cell_content(link_cell)

            for u in reg:
                if is_valid_video_url(u):
                    # ✅ Sanitize URL để loại bỏ tham số thời gian
                    u = _sanitize_yt_watch_url(u)
                    if u not in url_stt_map:
                        url_stt_map[u] = {"url": u, "stt_list": [], "kind": "main"}
                    if stt and stt not in url_stt_map[u]["stt_list"]:
                        url_stt_map[u]["stt_list"].append(stt)

            for u in prev:
                if is_valid_video_url(u):
                    # ✅ Sanitize URL để loại bỏ tham số thời gian
                    u = _sanitize_yt_watch_url(u)
                    key = u + "_Preventive"
                    if key not in url_stt_map:
                        url_stt_map[key] = {"url": u, "stt_list": [], "kind": "preventive"}
                    if stt and stt not in url_stt_map[key]["stt_list"]:
                        url_stt_map[key]["stt_list"].append(stt)

            for u in snd:
                if is_valid_video_url(u):
                    # ✅ Sanitize URL để loại bỏ tham số thời gian
                    u = _sanitize_yt_watch_url(u)
                    key = u + "_Sound"
                    if key not in url_stt_map:
                        url_stt_map[key] = {"url": u, "stt_list": [], "kind": "sound"}
                    if stt and stt not in url_stt_map[key]["stt_list"]:
                        url_stt_map[key]["stt_list"].append(stt)

        # Đẩy vào bảng: mỗi URL = 1 hàng riêng
        added = 0
        qual = self.cbo_quality.currentText()
        for key, data in url_stt_map.items():
            url = data.get("url", "")
            stt_list = data.get("stt_list", [])
            kind = data.get("kind", "main")

            if not is_valid_video_url(url):
                continue

            # STT hiển thị & tên file: lấy đúng từ cột F, ghép hậu tố lowercase nếu có
            base_id = "_".join([s for s in stt_list if s]) if stt_list else f"{added + 1}"
            stt_display = base_id
            if kind == "preventive":
                stt_display += "_preventive"
            elif kind == "sound":
                stt_display += "_sound"

            # Nếu là playlist/kênh → explode như thường, nhưng vẫn giữ nguyên STT cho từng video
            if looks_like_playlist_or_channel(url):
                for v in expand_url_to_videos(url):
                    self._add_row(v, qual, filename_base=stt_display, stt_text=stt_display)
                    added += 1
            else:
                self._add_row(url, qual, filename_base=stt_display, stt_text=stt_display)
                added += 1

        if added:
            # KHÔNG renumber để giữ nguyên STT (tên từ Sheet)
            QMessageBox.information(self, "Google Sheet", f"Đã nhập {added} video từ Sheet.")
        else:
            QMessageBox.information(self, "Google Sheet", "Không tìm thấy URL hợp lệ.")


    def _set_status_all(self, text: str):
        for r in range(self.tbl.rowCount()):
            self._set_status(r, text)
    def _append_log(self, msg: str):
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_view.appendPlainText(f"[{ts}] {msg}")

    def _setup_logging(self):
        # logger gốc
        self.logger = logging.getLogger("app")
        self.logger.setLevel(logging.INFO)

        # formatter chung
        fmt = logging.Formatter("%(asctime)s %(levelname)s: %(message)s", "%Y-%m-%d %H:%M:%S")

        # ghi file
        log_path = APP_DIR / "app.log"
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.INFO); fh.setFormatter(fmt)
        self.logger.addHandler(fh)

        # đẩy vào UI
        uh = QtLogHandler(self._append_log)
        uh.setLevel(logging.INFO); uh.setFormatter(fmt)
        self.logger.addHandler(uh)

        # ✅ Phát hiện nếu đang chạy trong IDLE/Python Shell
        running_in_idle = 'idlelib' in sys.modules or 'IDLE' in sys.executable
        
        # chuyển stdout/stderr → logger (CHỈ khi KHÔNG chạy trong IDLE)
        if not running_in_idle:
            sys.stdout = StreamToLogger(self.logger, logging.INFO)
            sys.stderr = StreamToLogger(self.logger, logging.ERROR)
        else:
            # Trong IDLE: chỉ log warning, không redirect stdout/stderr
            self.logger.warning("Running in IDLE - stdout/stderr redirection disabled")

        # log mọi exception không bắt (CHỈ khi KHÔNG chạy trong IDLE)
        if not running_in_idle:
            def _excepthook(exctype, value, tb):
                self.logger.error("Uncaught exception", exc_info=(exctype, value, tb))
                # vẫn hiển thị messagebox nhẹ nhàng nếu muốn:
                try:
                    QMessageBox.critical(self, "Lỗi không bắt", f"{exctype.__name__}: {value}")
                except Exception:
                    pass
            sys.excepthook = _excepthook

    def _open_log_file(self):
        """Mở file app.log và báo toast."""
        p = str(APP_DIR / "app.log")
        try:
            if sys.platform.startswith("win"):
                os.startfile(p)
            elif sys.platform == "darwin":
                os.system(f'open "{p}"')
            else:
                os.system(f'xdg-open "{p}"')
            self._toast(f"Opened: {p}", 3000)
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Open Log", str(e))

        # ---------- UI ----------
    def _build_ui(self):
        from PySide6.QtWidgets import QCheckBox
        central = QWidget(objectName="Root")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        # Toggle theme (Ctrl+T)
        act_toggle = QAction(self)
        act_toggle.setShortcut(QKeySequence("Ctrl+T"))
        act_toggle.triggered.connect(self.toggle_theme)
        self.addAction(act_toggle)

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)

        # ---------------- TAB: DOWNLOADER ----------------
        main_page = QWidget()
        root = QVBoxLayout(main_page)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # Row A: Sheet / URL Input
        rowA = QHBoxLayout(); rowA.setSpacing(8)
        btn_gsheet = QPushButton("Sheet"); btn_gsheet.clicked.connect(self._import_gsheet); btn_gsheet.setProperty("kind", "info")

        self.edt_url = PasteOnClickLineEdit()
        self.edt_url.setPlaceholderText("Paste link video / playlist / channel… (click để paste từ Clipboard)")
        self.edt_url.pastedMany.connect(self._bulk_add_from_list)
        self.edt_url.pastedOne.connect(lambda u: self._bulk_add_from_list([u]))

        btn_add    = QPushButton("Add Link");  btn_add.clicked.connect(self._add_single); btn_add.setProperty("kind", "success")
        btn_import = QPushButton("Import TXT"); btn_import.clicked.connect(self._import_txt); btn_import.setProperty("kind", "primary")
        btn_cookie = QPushButton("🍪 Import Cookie"); btn_cookie.clicked.connect(self._import_cookie); btn_cookie.setProperty("kind", "warning")
        btn_cookie.setToolTip("Import cookies for YouTube (SABR, nsig, age-restrict) or Instagram (login, rate-limit)")
        
        btn_help = QPushButton("❓ Help"); btn_help.clicked.connect(self._show_help); btn_help.setProperty("kind", "info")
        btn_help.setToolTip("Troubleshooting guide for YouTube errors")

        self.btn_theme = QPushButton("🌙"); self.btn_theme.setFixedWidth(36)
        self.btn_theme.setToolTip("Toggle Dark/Light  (Ctrl+T)")
        self.btn_theme.clicked.connect(self.toggle_theme)
        self.btn_theme.setProperty("kind", "ghost")
        act_toggle = QAction(self); act_toggle.setShortcut(QKeySequence("Ctrl+T"))
        act_toggle.triggered.connect(self.toggle_theme); self.addAction(act_toggle)

        rowA.addWidget(btn_gsheet)
        rowA.addWidget(self.edt_url)
        rowA.addWidget(btn_add); rowA.addWidget(btn_import); rowA.addWidget(btn_cookie); rowA.addWidget(btn_help)
        rowA.addWidget(self.btn_theme)
        root.addLayout(rowA)

        # Table: 6 cột ["Sel","STT","URL","Quality","Status","Progress"]
        self.tbl = QTableWidget(0, 6)
        self.tbl.setHorizontalHeaderLabels(["Sel", "STT", "URL", "Quality", "Status", "Progress"])
        hh = self.tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # Sel
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # STT
        hh.setSectionResizeMode(2, QHeaderView.Stretch)           # URL
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # Quality
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # Status
        hh.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # Progress
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tbl.customContextMenuRequested.connect(self._table_menu)
        self.tbl.setItemDelegate(GlowDelegate(self.tbl, lambda: self.active_rows))
        root.addWidget(self.tbl)

        # Row B: Output / Quality / Threads / Options
        rowB = QHBoxLayout()
        rowB.addWidget(QLabel("Output:"))
        self.lbl_out = QLabel(str(self.out_dir)); self.lbl_out.setTextInteractionFlags(Qt.TextSelectableByMouse)
        btn_browse = QPushButton("Browse"); btn_browse.clicked.connect(self._choose_out); btn_browse.setProperty("kind", "ghost")
        rowB.addWidget(self.lbl_out); rowB.addWidget(btn_browse)
        rowB.addStretch(1)

        rowB.addWidget(QLabel("Quality:"))
        self.cbo_quality = QComboBox(); self.cbo_quality.addItems(QUALITY_OPTIONS); self.cbo_quality.setCurrentText("1080p")
        self.cbo_quality.currentTextChanged.connect(lambda x: setattr(self, "quality", x))
        rowB.addWidget(self.cbo_quality)

        rowB.addWidget(QLabel("Threads:"))
        self.spin_threads = QSpinBox(); self.spin_threads.setRange(1, 1000)
        self.spin_threads.setValue(self.concurrency if hasattr(self, "concurrency") else 10)
        self.spin_threads.valueChanged.connect(lambda v: setattr(self, "concurrency", v))
        rowB.addWidget(self.spin_threads)

        rowB.addWidget(QLabel("Auto Retry:"))
        self.spin_retries = QSpinBox(); self.spin_retries.setRange(0, 10)
        self.spin_retries.setValue(self.max_retries if hasattr(self, "max_retries") else 3)
        self.spin_retries.valueChanged.connect(lambda v: setattr(self, "max_retries", v))
        self.spin_retries.setToolTip("Số lần tự động retry khi download lỗi (0 = không retry)")
        rowB.addWidget(self.spin_retries)

        # Options
        self.chk_folder_thumb = QCheckBox("Thumbnail + Folder"); self.chk_folder_thumb.setChecked(False)
        self.chk_h264 = QCheckBox("H.264 (convert AV1)"); self.chk_h264.setChecked(False)

        # NEW: CheckAll / UncheckAll button for "Sel" column
        btn_check_all = QPushButton("Check All"); btn_check_all.setProperty("kind","ghost")
        btn_uncheck_all = QPushButton("Uncheck All"); btn_uncheck_all.setProperty("kind","ghost")
        btn_check_all.clicked.connect(lambda: self._set_all_checked(True))
        btn_uncheck_all.clicked.connect(lambda: self._set_all_checked(False))

        rowB.addWidget(self.chk_folder_thumb)
        rowB.addWidget(self.chk_h264)
        rowB.addSpacing(12)
        rowB.addWidget(btn_check_all)
        rowB.addWidget(btn_uncheck_all)
        root.addLayout(rowB)

        # Row C: Controls
        rowC = QHBoxLayout()
        self.btn_start  = QPushButton("▶ Start");   self.btn_start.clicked.connect(self.start_all);   self.btn_start.setProperty("kind", "primary")
        self.btn_pause  = QPushButton("⏸ Pause");   self.btn_pause.clicked.connect(self.pause_all);    self.btn_pause.setProperty("kind", "pause")
        self.btn_resume = QPushButton("⏵ Resume");  self.btn_resume.clicked.connect(self.resume_all);  self.btn_resume.setProperty("kind", "resume")
        self.btn_stop   = QPushButton("⏹ Stop Sel");self.btn_stop.clicked.connect(self.stop_selected); self.btn_stop.setProperty("kind", "danger")
        btn_retry_fail  = QPushButton("🔄 Retry Fail"); btn_retry_fail.clicked.connect(self.retry_failed); btn_retry_fail.setProperty("kind", "warning")
        btn_retry_fail.setToolTip("Retry all failed downloads and start immediately")

        btn_remove   = QPushButton("🗑 Remove Selected"); btn_remove.clicked.connect(self.remove_selected); btn_remove.setProperty("kind", "danger")
        btn_del_ok   = QPushButton("🧽 Delete Success");  btn_del_ok.clicked.connect(self.remove_success); btn_del_ok.setProperty("kind", "danger")
        btn_clear    = QPushButton("🧹 Clear (idle only)"); btn_clear.clicked.connect(self.clear_all); btn_clear.setProperty("kind", "warning")
        btn_clear_all= QPushButton("🧯 Clear ALL (Force)");  btn_clear_all.clicked.connect(self.clear_all_force); btn_clear_all.setProperty("kind", "danger")
        btn_open     = QPushButton("📂 Open Output");     btn_open.clicked.connect(self._open_out);       btn_open.setProperty("kind", "success")

        rowC.addWidget(self.btn_start)
        rowC.addWidget(self.btn_pause)
        rowC.addWidget(self.btn_resume)
        rowC.addWidget(self.btn_stop)
        rowC.addWidget(btn_retry_fail)
        rowC.addWidget(btn_remove)
        rowC.addWidget(btn_del_ok)
        rowC.addWidget(btn_clear)
        rowC.addWidget(btn_clear_all)
        rowC.addStretch(1)
        rowC.addWidget(btn_open)
        root.addLayout(rowC)

        # Row D: Stats
        rowD = QHBoxLayout()
        def _make_stat(label_text):
            box = QHBoxLayout()
            lb = QLabel(label_text); lb.setStyleSheet("font-weight:600;")
            val = QLabel("0")
            box.addWidget(lb); box.addWidget(val)
            wrap = QWidget(); wrap.setLayout(box)
            return wrap, val
        w_total, self.lbl_stat_total = _make_stat("Tổng:")
        w_ok,    self.lbl_stat_ok    = _make_stat("Thành công:")
        w_fail,  self.lbl_stat_fail  = _make_stat("Thất bại:")
        w_act,   self.lbl_stat_active= _make_stat("Đang tải:")

        rowD.addWidget(w_total); rowD.addSpacing(16)
        rowD.addWidget(w_ok);    rowD.addSpacing(16)
        rowD.addWidget(w_fail);  rowD.addSpacing(16)
        rowD.addWidget(w_act);   rowD.addStretch(1)
        root.addLayout(rowD)

        self.tabs.addTab(main_page, "Downloader")

        # ---------------- TAB: LOGS ----------------
        logs_page = QWidget()
        logs_layout = QVBoxLayout(logs_page); logs_layout.setContentsMargins(10,10,10,10)

        self.log_view = QPlainTextEdit(); self.log_view.setReadOnly(True); self.log_view.setMaximumBlockCount(8000)
        logs_layout.addWidget(self.log_view)

        rowL = QHBoxLayout()
        btn_open_log = QPushButton("📄 Open app.log"); btn_open_log.clicked.connect(lambda: self._open_log_file()); btn_open_log.setProperty("kind", "info")
        btn_clear_log = QPushButton("Clear view");  btn_clear_log.clicked.connect(lambda: self.log_view.clear()); btn_clear_log.setProperty("kind", "ghost")
        rowL.addWidget(btn_open_log); rowL.addWidget(btn_clear_log); rowL.addStretch(1)
        logs_layout.addLayout(rowL)

        self.tabs.addTab(logs_page, "Logs")

        for btn in (btn_gsheet, btn_add, btn_import, btn_cookie, btn_help, self.btn_theme,
                    self.btn_start, self.btn_pause, self.btn_resume, self.btn_stop, btn_retry_fail,
                    btn_remove, btn_del_ok, btn_clear, btn_clear_all, btn_open, btn_browse,
                    btn_open_log, btn_clear_log):
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _update_stats(self):
        """Cập nhật thống kê: Tổng, Thành công, Thất bại, Đang tải và hiển thị toast ngắn."""
        total = self.tbl.rowCount()
        ok = fail = downloading = 0
        downloading_states = {
            "Starting", "Downloading", "Merging",
            "Retry(best)", "Retry(TikTok/best)",
            "Retry(Reddit)", "Retry(YouTube-TV)",
            "Retry(recode h264/aac)"
        }

        for r in range(total):
            it = self.tbl.item(r, 4)   # <-- Status ở cột 4
            st = it.text() if it else ""
            if st == "Bong":
                ok += 1
            elif st == "Error":
                fail += 1
            if st in downloading_states:
                downloading += 1

        if hasattr(self, "lbl_stat_total"): self.lbl_stat_total.setText(str(total))
        if hasattr(self, "lbl_stat_ok"):    self.lbl_stat_ok.setText(str(ok))
        if hasattr(self, "lbl_stat_fail"):  self.lbl_stat_fail.setText(str(fail))
        if hasattr(self, "lbl_stat_active"):self.lbl_stat_active.setText(str(downloading))

        self._toast(f"Total: {total} | OK: {ok} | Fail: {fail} | Active: {downloading}", 1800)



    def _apply_background(self):
        for p in (APP_DIR/"bg.jpg", APP_DIR/"bg.png"):
            if p.exists():
                css = f"""QWidget#Root {{
                    background-image: url("{p.as_posix()}");
                    background-position: center; background-repeat: no-repeat;
                    background-attachment: fixed; background-origin: content; background-clip: border; }}"""
                self.findChild(QWidget, "Root").setStyleSheet(css); break

    # Theme
    def apply_theme(self, name: str):
        """Đổi theme (light/dark), lưu vào QSettings và báo toast."""
        # chọn QSS
        self.setStyleSheet(LIGHT_QSS if str(name).lower() == "light" else DARK_QSS)

        # cập nhật icon/nội bộ
        self.theme = "light" if str(name).lower() == "light" else "dark"
        self.settings.setValue("theme", self.theme)
        try:
            # nút toggle nếu có
            if hasattr(self, "btn_theme") and self.btn_theme:
                self.btn_theme.setText("☀️" if self.theme == "light" else "🌙")
        except Exception:
            pass

        self._toast(f"Theme: {self.theme.capitalize()}", 2200)


    def toggle_theme(self):
        self.apply_theme("light" if self.theme == "dark" else "dark")
    # Menu
    def _table_menu(self, pos):
        menu = QMenu(self)
        actPaste = QAction("Paste URLs from clipboard", self)
        actPaste.triggered.connect(lambda: self._bulk_add_from_list(split_urls(QApplication.clipboard().text())))
        menu.addAction(actPaste)

        actExplode = QAction("Explode selected (playlist/channel → nhiều video)", self)
        def _explode_sel():
            rows = sorted({idx.row() for idx in self.tbl.selectedIndexes()}, reverse=True)
            if not rows: return
            # lấy URL từ các hàng chọn (chỉ những hàng là video đơn/playlist/kênh)
            srcs = [self.tbl.item(r,1).text() for r in rows]
            vids = []
            for u in srcs:
                if looks_like_playlist_or_channel(u):
                    vids.extend(expand_url_to_videos(u))
                else:
                    vids.append(u)
            # thay các hàng cũ bằng list video
            for r in rows:
                self.tbl.removeRow(r)
            self._add_many_rows(vids, self.cbo_quality.currentText())
            self._renumber()
        actExplode.triggered.connect(_explode_sel)
        menu.addAction(actExplode)

        actRemove = QAction("Remove selected", self); actRemove.triggered.connect(self.remove_selected); menu.addAction(actRemove)
        actToggle = QAction("Toggle Dark/Light  (Ctrl+T)", self); actToggle.triggered.connect(self.toggle_theme); menu.addAction(actToggle)
        menu.exec(QCursor.pos())
    # ---------- Add / Import ----------
    def _add_single(self):
        urls = split_urls(self.edt_url.text())
        if not urls: return
        self._bulk_add_from_list(urls); self.edt_url.clear()

    def _import_txt(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn file .txt", "", "Text Files (*.txt)")
        if not path: return
        try: text = Path(path).read_text(encoding="utf-8", errors="ignore")
        except Exception: text = Path(path).read_text(encoding="latin-1", errors="ignore")
        self._bulk_add_from_list(split_urls(text))

    def _add_many_rows(self, urls: List[str], quality: str):
        if not urls: return
        seen = {self.tbl.item(i,2).text() for i in range(self.tbl.rowCount()) if self.tbl.item(i,2)}
        added = 0
        for u in urls:
            if u in seen: 
                continue
            seen.add(u)
            r = self.tbl.rowCount(); self.tbl.insertRow(r)

            # Sel
            sel_item = QTableWidgetItem()
            sel_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            sel_item.setCheckState(Qt.Checked)
            self.tbl.setItem(r, 0, sel_item)

            self.tbl.setItem(r, 1, QTableWidgetItem(str(r+1)))         # STT
            self.tbl.setItem(r, 2, QTableWidgetItem(u))                 # URL
            self.tbl.setItem(r, 3, QTableWidgetItem(quality))           # Quality
            self.tbl.setItem(r, 4, QTableWidgetItem("Pending"))         # Status
            pb = QProgressBar(); pb.setMinimum(0); pb.setMaximum(0); pb.setTextVisible(False); pb.setFixedHeight(16)
            self.tbl.setCellWidget(r, 5, pb)                            # Progress
            added += 1

            # chống đơ UI khi add số lượng lớn
            if (added % 20) == 0:
                self._yield_ui()
        self._update_stats()


    def _bulk_add_from_list(self, urls: List[str]):
        if not urls: return
        qual = self.cbo_quality.currentText()
        added = 0
        
        # ✅ Batch lấy title để tránh lag khi import nhiều link
        # Chỉ lấy title cho link lẻ (không phải playlist/channel)
        single_urls = []
        for u in urls:
            u = _sanitize_yt_watch_url(u)
            if not looks_like_playlist_or_channel(u):
                single_urls.append(u)
        
        # Lấy title cho tất cả link lẻ cùng lúc (nhanh hơn)
        titles_map = {}
        if single_urls:
            # ✅ Giới hạn số lượng link lấy title cùng lúc để tránh lag
            batch_size = 5  # Giảm xuống 5 để nhanh hơn
            for i in range(0, len(single_urls), batch_size):
                batch = single_urls[i:i+batch_size]
                for u in batch:
                    try:
                        title = get_video_title(u)
                        if title:
                            titles_map[u] = title
                    except Exception:
                        pass
                # Bỏ yield UI trong batch để tránh lag
        
        # Thêm vào bảng
        for u in urls:
            # ✅ Sanitize URL để loại bỏ tham số thời gian
            u = _sanitize_yt_watch_url(u)
            
            if looks_like_playlist_or_channel(u):
                for v in expand_url_to_videos(u):
                    self._add_row(v, qual, filename_base=None, stt_text=None, from_collection=True)
                    added += 1
            else:
                # Lấy title từ map đã fetch
                title = titles_map.get(u)
                
                # Dùng title làm filename_base và stt_text nếu có
                filename_base = title
                stt_text = title if title else None
                self._add_row(u, qual, filename_base=filename_base, stt_text=stt_text, from_collection=False)
                added += 1
                
                # ✅ Yield UI mỗi 10 link để tránh lag
                if added % 10 == 0:
                    self._yield_ui()
        self._renumber()
    def _yield_ui(self, steps: int = 1):
        """Nhường CPU cho UI 'steps' lần để tránh cảm giác đơ khi add nhiều hàng."""
        for _ in range(max(1, steps)):
            QApplication.processEvents()


    # ---------- Table ops ----------
    def _renumber(self):
        for i in range(self.tbl.rowCount()):
            it = self.tbl.item(i, 1)
            if it:
                it.setText(str(i+1))
    def _set_all_checked(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        for r in range(self.tbl.rowCount()):
            it = self.tbl.item(r, 0)
            if it:
                it.setCheckState(state)
    def clear_all_force(self):
        """Dừng toàn bộ worker, huỷ hàng đợi và xoá sạch bảng (kể cả đang chạy)."""
        # 1) Dừng tất cả worker hiện tại
        for r, w in list(self.active.items()):
            try:
                w.stop()
            except Exception:
                pass
        # 2) Đợi nhẹ 1 nhịp để thread nhận stop (không block lâu)
        self._yield_ui(8)
        # 3) Reset tất cả
        self.is_running = False
        self.pending_rows = queue.Queue()
        self.active.clear()
        self.active_rows.clear()
        self.row_filename.clear()
        self.row_meta.clear()
        self.row_url.clear()
        self.tbl.setRowCount(0)
        self.btn_start.setEnabled(True)
        self._update_stats()
        self._toast("Cleared ALL (force).", 1800)


    def _reindex_after_row_change(self):
        """Sau khi xóa/di chuyển hàng: đánh lại số thứ tự + rebuild row_meta/row_filename từ cột STT."""
        self._renumber()
        new_row_meta = {}
        new_row_filename = {}
        new_row_url = {}

        for i in range(self.tbl.rowCount()):
            stt_val = self.tbl.item(i, 1).text() if self.tbl.item(i, 1) else ""
            stt_lower = (stt_val or "").lower()

            kind = "main"
            if stt_lower.endswith("_preventive"):
                kind = "preventive"
            elif stt_lower.endswith("_sound"):
                kind = "sound"

            group = stt_lower
            for suf in ("_preventive", "_sound"):
                if group.endswith(suf):
                    group = group[: -len(suf)]

            url = self.row_meta.get(i, {}).get("url", "")
            new_row_meta[i] = {"group": group, "kind": kind, "from_collection": False, "url": url}
            new_row_filename[i] = stt_val if stt_val else None
            new_row_url[i] = url

        self.row_meta = new_row_meta
        self.row_filename = new_row_filename
        self.row_url = new_row_url
        self._update_stats()

    def clear_all(self):
        if self.is_running:
            self._toast("Đang chạy — dùng 'Clear ALL (Force)' nếu muốn xoá hết.", 2500)
            return
        self.tbl.setRowCount(0)
        self.pending_rows = queue.Queue()
        self.active.clear()
        self.active_rows.clear()
        self.row_filename.clear()
        self.row_meta.clear()
        self.row_url.clear()
        self._update_stats()


    # ---------- Start / Scheduler ----------
    def start_all(self):
        if self.is_running or self.tbl.rowCount()==0:
            return

        if not hasattr(self, "row_meta"):
            self.row_meta = {}

        self.is_running = True
        self.btn_start.setEnabled(False)
        # ✅ Giới hạn max_workers để tránh lag, nhưng vẫn cho phép nhiều link
        self.max_workers = min(int(self.concurrency), 20)  # Tối đa 20 workers
        self.pending_rows = queue.Queue(); self.active.clear(); self.active_rows.clear()
        self.row_retries.clear()  # Reset retry count khi bắt đầu mới

        # chỉ queue những hàng: (được tick) và (không phải preventive)
        for r in range(self.tbl.rowCount()):
            sel = self.tbl.item(r, 0)
            is_checked = (sel and sel.checkState() == Qt.Checked)
            meta = self.row_meta.get(r, {"kind": "main"})
            if not is_checked:
                self._set_status(r, "Skipped (unchecked)")
                self._set_progress(r, 0)
                continue
            if meta.get("kind") == "preventive":
                self._set_status(r, "Waiting (preventive)")
                self._set_progress(r, 0)
            else:
                self._set_status(r, "Queued")
                self._set_progress(r, -1)
                self.pending_rows.put(r)

        self._update_stats()
        for _ in range(self.max_workers):
            self._start_next()

    def _start_next(self):
        if not self.is_running or self.is_paused:
            return
        if len(self.active) >= self.max_workers:
            return
        try:
            r = self.pending_rows.get_nowait()
        except queue.Empty:
            if not self.active:
                self._all_done()
            return

        url_item  = self.tbl.item(r, 2)  # URL
        qual_item = self.tbl.item(r, 3)  # Quality
        url = self.row_meta.get(r, {}).get("url", url_item.text() if url_item else "")
        qual = qual_item.text() if qual_item else self.quality

        platform = detect_platform(url)
        if platform == "yt":
            url = _sanitize_yt_watch_url(url)

        fmt = build_format(qual, platform)

        fname = self.row_filename.get(r)
        meta  = self.row_meta.get(r, {})
        from_collection = bool(meta.get("from_collection", False))

        per_folder  = bool(self.chk_folder_thumb.isChecked())
        convert_av1 = bool(self.chk_h264.isChecked())
        audio_only  = (meta.get("kind") == "sound")

        worker = DownloadWorker(
            row=r,
            url=url,
            out_dir=self.out_dir,
            fmt=fmt,
            filename_base=fname,
            per_folder=per_folder,
            from_collection=from_collection,
            audio_only=audio_only,
            convert_av1=convert_av1,
        )
        worker.log.connect(self._append_log)
        worker.progress.connect(self._on_progress)
        worker.status.connect(self._on_status)
        worker.done.connect(self._on_done)

        self.active[r] = worker
        self._set_status(r, "Starting")
        self.active_rows.add(r)
        self._update_stats()
        worker.start()


    def _on_progress(self, row, percent):
        now = time.time() * 1000  # ms
        last_pct, last_time = self._progress_cache.get(row, (None, 0))
        if last_pct == percent and now - last_time < self._progress_throttle_ms:
            return  # skip update
        self._progress_cache[row] = (percent, now)
        self._set_progress(row, percent)

    def _on_status(self, row, text):
        self._set_status(row, text)
        if text in ("Starting", "Downloading", "Merging", "Retry(best)", "Retry(TikTok/best)"):
            self.active_rows.add(row)
        else:
            self.active_rows.discard(row)
        self._update_stats()


    def _on_done(self, row, ok, err):
        self.active.pop(row, None)

        meta = self.row_meta.get(row, {})
        kind = meta.get("kind")
        group = meta.get("group")

        if ok:
            self._set_status(row, "Bong"); self._set_progress(row, 100)
            # Reset retry count khi thành công
            self.row_retries.pop(row, None)
            if kind == "main" and group:
                # bỏ qua các preventive cùng nhóm
                for r2, m2 in self.row_meta.items():
                    if m2.get("group") == group and m2.get("kind") == "preventive":
                        self._set_status(r2, "Skipped (main OK)")
                        self._set_progress(r2, 0)
        else:
            # ✅ Auto-retry logic
            current_retries = self.row_retries.get(row, 0)
            
            # Lưu lỗi vào tooltip của cột Quality (cột 3)
            qual_item = self.tbl.item(row, 3)
            if qual_item:
                qual_item.setToolTip(err)
            
            if current_retries < self.max_retries:
                # Còn retry, tự động thêm lại vào queue
                self.row_retries[row] = current_retries + 1
                self._set_status(row, f"Retry {current_retries + 1}/{self.max_retries}")
                self._set_progress(row, -1)
                self.pending_rows.put(row)
                try:
                    self.logger.info(f"[{row}] Auto-retry {current_retries + 1}/{self.max_retries}: {err[:100]}")
                except Exception:
                    pass
            else:
                # Hết retry, set Error
                self._set_status(row, "Error")
                self._set_progress(row, 0)
                self.row_retries.pop(row, None)  # Reset count
                
                # Queue preventive nếu main fail (logic cũ)
                if kind == "main" and group:
                    for r2, m2 in self.row_meta.items():
                        if m2.get("group") == group and m2.get("kind") == "preventive":
                            st_item = self.tbl.item(r2, 4)
                            st = st_item.text() if st_item else ""
                            if st.startswith("Waiting") or st == "Pending" or st == "Error":
                                self._set_status(r2, "Queued (preventive)")
                                self._set_progress(r2, -1)
                                self.pending_rows.put(r2)
                                try:
                                    self.logger.info(f"[{r2}] Main URL failed → Auto-queue preventive URL")
                                except Exception:
                                    pass

        self.active_rows.discard(row)
        self._update_stats()
        self._start_next()
        
    def _all_done(self):
        self.is_running = False
        self.btn_start.setEnabled(True)

    # ---------- Small setters ----------
    def _set_status(self, row, text):
        it = self.tbl.item(row, 4)
        if it: it.setText(text)

    def _set_progress(self, row, percent):
        # ✅ Dùng QLabel thay QProgressBar - chỉ hiển thị text % (không animation)
        lbl = self.tbl.cellWidget(row, 5)
        if not lbl: return
        if percent < 0:
            lbl.setText("—")  # Indeterminate
        else:
            pct = max(0, min(100, percent))
            lbl.setText(f"{pct}%")


    # ---------- Misc ----------
    def _choose_out(self):
        path = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu", str(self.out_dir))
        if not path: return
        self.out_dir = Path(path); self.out_dir.mkdir(parents=True, exist_ok=True)
        self.lbl_out.setText(str(self.out_dir))

    def _open_out(self):
        p = str(self.out_dir)
        try:
            if sys.platform.startswith("win"):
                os.startfile(p)
            elif sys.platform == "darwin":
                os.system(f'open "{p}"')
            else:
                os.system(f'xdg-open "{p}"')
            self._toast(f"Opened: {p}", 3000)
        except Exception as e:
            QMessageBox.warning(self, "Open Output", str(e))


# ------------------------ main ------------------------
def main():
    ensure_embedded_credentials()
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(resource_path("icon.ico")))
    w = MainWindow(); w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
