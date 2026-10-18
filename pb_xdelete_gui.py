#==============================================================#
# File       :   pb_xdelete_gui.py
# Ctime      :   2025-10-16
# Mtime      :   2026-10-18
# Version    :   PB XDelete GUI（调用同目录 pb_xdelete.exe）
# Version    :   V106
# Author     :   www.linkleading.com
# Copyright (C) 2025-2026
#==============================================================#
# -*- coding: utf-8 -*-
"""
PB XDelete 图形化启动器 (PyQt6)
后台调用删除引擎 pb_xdelete.exe（与 C++ 工程编译产物同名）
支持：拖拽路径、深海暗夜主题、系统托盘
"""
import sys
import os
import re
import codecs
import queue
import logging
import ctypes
import subprocess
import threading
import tempfile
import uuid
import winreg 
import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__) 

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                             QListWidget, QTextEdit, QFileDialog, QMessageBox, 
                             QFrame, QSplitter, QAbstractItemView, 
                             QSystemTrayIcon, QMenu, QCheckBox, QProgressBar, QComboBox, QProgressDialog)
from PyQt6.QtCore import Qt, QTimer, QEvent, QMetaObject, QAbstractNativeEventFilter, pyqtSlot
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QFont

# ==========================================
#  配置区
# ==========================================
ICON_NAME = "lflogo.ico"
# 注意：单元素必须写成 ("pb_xdelete.exe",) 带尾逗号；否则是 str 不是 tuple，会按字符遍历导致永远找不到引擎
ENGINE_CANDIDATES = ("pb_xdelete.exe", "fast_delete_v39.2.exe", "fast_delete_core.exe")
TASK_FILE = "task_list.tmp"  # 仅用于清理旧固定名；新任务用随机 pbxd_task_*.list
TASK_LIST_PREFIX = "pbxd_task_"
LOG_FILE = "realtime_log.tmp"       
HISTORY_FILE = "pb_xdelete_history.log"

REG_KEY_NAME = "PB_XDelete"
MENU_NAME = "⚡ 极速粉碎 (PB_XDelete)"

# 唯一标识符，用于检测是否重复运行
app_instance_id = "PB_XDelete_Gui_Unique_Instance_V1"

# 平滑停止：须与 pb_xdelete.exe 内 STOP_EVENT_NAME 完全一致（Local\ 会话命名空间）
STOP_EVENT_NAME = "Local\\PB_XDelete_StopEvent"
_stop_event_handle = None  # GUI 持有命名事件句柄，保证生命周期覆盖子进程

COPYRIGHT_TEXT = (
    "PB XDelete GUI V108\n"
    "Copyright © 2025-2026 Wuhan Linkleading\n"
    "All rights reserved."
)

# 扩展保护路径（含 System32、桌面可选保护）
def _norm_protect(p):
    try:
        return os.path.abspath(p).lower().rstrip("\\/")
    except Exception:
        return (p or "").lower().rstrip("\\/")

# 系统路径：禁止自身及其子路径
SYSTEM_PROTECTED_PATHS = [
    _norm_protect(os.environ.get("SystemRoot", "C:\\Windows")),
    _norm_protect(os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32")),
    _norm_protect(os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "SysWOW64")),
    _norm_protect(os.environ.get("ProgramFiles", "C:\\Program Files")),
    _norm_protect(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")),
]
# 用户敏感根：仅禁止「整目录」本身，允许删其下的普通文件/子文件夹
USER_ROOT_PROTECTED = []
_up = os.environ.get("USERPROFILE")
if _up:
    USER_ROOT_PROTECTED.extend([
        _norm_protect(_up),
        _norm_protect(os.path.join(_up, "Desktop")),
        _norm_protect(os.path.join(_up, "Documents")),
        _norm_protect(os.path.join(_up, "AppData")),
    ])
_ad = os.environ.get("APPDATA")
if _ad:
    USER_ROOT_PROTECTED.append(_norm_protect(_ad))
_lad = os.environ.get("LOCALAPPDATA")
if _lad:
    USER_ROOT_PROTECTED.append(_norm_protect(_lad))

# --- 1. 绝对路径锚定 ---
def get_base_paths():
    try:
        real_exe_path = os.path.abspath(sys.argv[0])
        base_dir = os.path.dirname(real_exe_path)
        os.chdir(base_dir) 
        return base_dir, real_exe_path
    except Exception as e:
        logger.debug("get_base_paths: %s", e)
        return os.getcwd(), sys.executable

BASE_DIR, REAL_EXE_PATH = get_base_paths()

# 打包时 pb_xdelete.exe / 图标路径：PyInstaller 用 _MEIPASS；Nuitka onefile 用解压临时目录
def _resource_dir():
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            return meipass
        # Nuitka onefile：--include-data-files 的资源在解压临时目录，主模块 __file__ 可能指向 exe 目录
        # 使用 PyQt6.__file__ 定位解压根目录（与 pb_xdelete.exe 同级），并验证候选 exe 存在
        try:
            d = os.path.dirname(os.path.dirname(os.path.abspath(__import__("PyQt6").__file__)))
            if d and os.path.isdir(d) and any(os.path.isfile(os.path.join(d, n)) for n in ENGINE_CANDIDATES):
                return d
        except Exception:
            pass
        try:
            d = os.path.dirname(os.path.abspath(__file__))
            if d:
                return d
        except NameError:
            pass
        return BASE_DIR
    return BASE_DIR

# IPC / 任务临时文件：用户可写目录，避免 Program Files 无写权限
PENDING_PATHS_DIR = os.path.join(tempfile.gettempdir(), "PB_XDelete_IPC")
# 历史日志：LocalAppData，避免写安装目录
_APP_DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()), "PB_XDelete")

# 已知非路径参数，写入 pending_paths 时需过滤
KNOWN_NON_PATH_ARGS = frozenset(("--reg-menu", "--help", "-h"))

def _engine_path():
    """pb_xdelete.exe 路径：打包时在 _resource_dir；开发时在 BASE_DIR；按 ENGINE_CANDIDATES 顺序探测"""
    bases = []
    if getattr(sys, 'frozen', False):
        bases.append(_resource_dir())
    bases.append(BASE_DIR)
    try:
        bases.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        pass
    seen = set()
    for base in bases:
        if not base:
            continue
        ab = os.path.abspath(base)
        if ab in seen:
            continue
        seen.add(ab)
        for name in ENGINE_CANDIDATES:
            p = os.path.join(ab, name)
            if os.path.exists(p):
                return p
    return os.path.join(BASE_DIR, ENGINE_CANDIDATES[0])

ENGINE_PATH = _engine_path()
try:
    os.makedirs(PENDING_PATHS_DIR, exist_ok=True)
except Exception:
    pass
try:
    os.makedirs(_APP_DATA_DIR, exist_ok=True)
except Exception:
    pass
TASK_FILE_PATH = os.path.join(PENDING_PATHS_DIR, TASK_FILE)
LOG_FILE_PATH = os.path.join(PENDING_PATHS_DIR, LOG_FILE)
HISTORY_FILE_PATH = os.path.join(_APP_DATA_DIR, HISTORY_FILE)

def get_icon_path():
    if getattr(sys, 'frozen', False):
        p = os.path.join(_resource_dir(), ICON_NAME)
        if os.path.exists(p): return p
    path = os.path.join(BASE_DIR, ICON_NAME)
    if os.path.exists(path): return path
    try: return os.path.join(os.path.dirname(__file__), ICON_NAME)
    except Exception as e: logger.debug("get_icon_path: %s", e); return path

FINAL_ICON_PATH = get_icon_path()

def is_admin():
    try: return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception as e: logger.debug("is_admin: %s", e); return False

def _long_path(path):
    """为存在性检查加 \\?\\ 前缀（勿用于 SHFileOperation）"""
    if not path:
        return path
    abs_path = os.path.abspath(path.replace("/", "\\"))
    if abs_path.startswith("\\\\?\\"):
        return abs_path
    if abs_path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + abs_path[2:]
    return "\\\\?\\" + abs_path

def is_dir_safe(path):
    """突破 Windows 260 字符限制的目录判断"""
    if not path:
        return False
    try:
        return os.path.isdir(_long_path(path))
    except Exception as e:
        logger.debug("is_dir_safe: %s", e)
        return False

def is_file_safe(path):
    """长路径安全的普通文件判断"""
    if not path:
        return False
    try:
        return os.path.isfile(_long_path(path))
    except Exception as e:
        logger.debug("is_file_safe: %s", e)
        return False

def is_path_exists_safe(path):
    """目录或文件均可（引擎已支持单文件）"""
    return is_dir_safe(path) or is_file_safe(path)

def move_to_recycle_bin(paths):
    """SHFileOperation 移入回收站。注意：Shell API 不支持 \\?\\ 前缀。"""
    if not paths:
        return True
    try:
        def _prep(p):
            p = os.path.abspath(p.replace("/", "\\").strip())
            # 去掉引擎/长路径前缀，SHFileOperation 只认普通绝对路径
            if p.startswith("\\\\?\\UNC\\"):
                p = "\\" + p[7:]  # \\?\UNC\server\share -> \\server\share
            elif p.startswith("\\\\?\\"):
                p = p[4:]
            return p
        shell32 = ctypes.windll.shell32
        FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_NOERRORUI = 0x0003, 0x0040, 0x0010, 0x0400
        # 双 NUL 终止的 PCZZWSTR：不可用 c_wchar_p（会在首个 \\0 截断）
        pFrom = "\0".join(_prep(p) for p in paths) + "\0\0"
        pFrom_buf = ctypes.create_unicode_buffer(pFrom)
        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", ctypes.c_void_p),
                ("wFunc", ctypes.c_uint),
                ("pFrom", ctypes.c_void_p),
                ("pTo", ctypes.c_void_p),
                ("fFlags", ctypes.c_uint16),
                ("fAnyOperationsAborted", ctypes.c_int),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", ctypes.c_void_p),
            ]
        op = SHFILEOPSTRUCTW()
        op.hwnd = 0
        op.wFunc = FO_DELETE
        op.pFrom = ctypes.addressof(pFrom_buf)
        op.pTo = 0
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI
        op.fAnyOperationsAborted = 0
        op.hNameMappings = 0
        op.lpszProgressTitle = 0
        return shell32.SHFileOperationW(ctypes.byref(op)) == 0
    except Exception as e:
        logger.debug("move_to_recycle_bin: %s", e)
        return False

def _bind_kernel32_event_apis(kernel32):
    """64 位 Windows：显式声明 HANDLE，避免 ctypes 默认 c_int 截断句柄高位"""
    from ctypes import wintypes
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.SetEvent.restype = wintypes.BOOL
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.ResetEvent.restype = wintypes.BOOL
    kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def ensure_engine_stop_event(reset=True):
    """Popen 前由 GUI 创建/持有停止事件并复位，避免引擎端 ResetEvent 抹掉早期停止信号"""
    global _stop_event_handle
    try:
        kernel32 = ctypes.windll.kernel32
        _bind_kernel32_event_apis(kernel32)
        if not _stop_event_handle:
            # bManualReset=True, bInitialState=False
            h = kernel32.CreateEventW(None, True, False, STOP_EVENT_NAME)
            if not h:
                return False
            _stop_event_handle = h
        if reset and _stop_event_handle:
            kernel32.ResetEvent(_stop_event_handle)
        return True
    except Exception as e:
        logger.debug("ensure_engine_stop_event: %s", e)
        return False


def signal_engine_graceful_stop():
    """向 pb_xdelete.exe 发送平滑停止（64 位 HANDLE 安全；优先用已持有句柄）"""
    try:
        kernel32 = ctypes.windll.kernel32
        _bind_kernel32_event_apis(kernel32)
        EVENT_MODIFY_STATE = 0x0002
        h = _stop_event_handle
        owned = bool(h)
        if not h:
            h = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, STOP_EVENT_NAME)
        if h:
            kernel32.SetEvent(h)
            if not owned:
                kernel32.CloseHandle(h)
            return True
    except Exception as e:
        logger.debug("signal_engine_graceful_stop: %s", e)
    return False

WM_DROPFILES = 0x0233
WM_COPYDATA = 0x004A
WM_COPYGLOBALDATA = 0x0049


def _uipi_allow_drop_messages(hwnd=None):
    """放行低完整性→高完整性的拖放相关消息（Admin 必需）。"""
    try:
        user32 = ctypes.windll.user32
        MSGFLT_ALLOW = 1
        for msg in (WM_DROPFILES, WM_COPYDATA, WM_COPYGLOBALDATA):
            try:
                user32.ChangeWindowMessageFilter(ctypes.c_uint(msg), MSGFLT_ALLOW)
            except Exception:
                pass
            if hwnd:
                try:
                    user32.ChangeWindowMessageFilterEx(
                        ctypes.c_void_p(int(hwnd)), ctypes.c_uint(msg), MSGFLT_ALLOW, None
                    )
                except Exception:
                    pass
    except Exception as e:
        logger.debug("_uipi_allow_drop_messages: %s", e)


def _drag_accept_files(hwnd, accept=True):
    """启用经典 WS_EX_ACCEPTFILES 拖放（OLE 在 Admin 下不可用，必须走这条）。"""
    try:
        if not hwnd:
            return
        ctypes.windll.shell32.DragAcceptFiles(ctypes.c_void_p(int(hwnd)), ctypes.c_bool(accept))
    except Exception as e:
        logger.debug("_drag_accept_files: %s", e)


def _hdrop_to_paths(hdrop):
    """HDROP → 本地路径列表"""
    shell32 = ctypes.windll.shell32
    shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
    shell32.DragQueryFileW.restype = ctypes.c_uint
    shell32.DragFinish.argtypes = [ctypes.c_void_p]
    count = shell32.DragQueryFileW(ctypes.c_void_p(hdrop), 0xFFFFFFFF, None, 0)
    paths = []
    buf = ctypes.create_unicode_buffer(32768)
    for i in range(count):
        n = shell32.DragQueryFileW(ctypes.c_void_p(hdrop), i, buf, 32768)
        if n > 0:
            p = buf.value.replace("/", "\\")
            if p and is_path_exists_safe(p):
                paths.append(p)
    shell32.DragFinish(ctypes.c_void_p(hdrop))
    return paths


class _WinDropFilesFilter(QAbstractNativeEventFilter):
    """拦截 WM_DROPFILES：管理员窗口从资源管理器拖入的唯一可靠方式。"""

    def __init__(self, on_paths):
        super().__init__()
        self._on_paths = on_paths

    def nativeEventFilter(self, eventType, message):
        try:
            et = bytes(eventType).decode("latin1", errors="ignore") if eventType is not None else ""
            if et not in ("windows_generic_MSG", "windows_dispatcher_MSG"):
                return False
            # MSG layout (x64)
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            class MSG(ctypes.Structure):
                _fields_ = [
                    ("hwnd", ctypes.c_void_p),
                    ("message", ctypes.c_uint),
                    ("wParam", ctypes.c_void_p),
                    ("lParam", ctypes.c_void_p),
                    ("time", ctypes.c_uint),
                    ("pt", POINT),
                ]

            msg = MSG.from_address(int(message))
            if msg.message != WM_DROPFILES:
                return False
            paths = _hdrop_to_paths(int(msg.wParam) if msg.wParam else 0)
            if paths and self._on_paths:
                self._on_paths(paths)
            return True
        except Exception as e:
            logger.debug("nativeEventFilter WM_DROPFILES: %s", e)
            return False


class PathDropListWidget(QListWidget):
    """列表拖放：保留 OLE 作为非 Admin 兜底；Admin 依赖主窗口 WM_DROPFILES。"""

    def __init__(self, on_paths, parent=None):
        super().__init__(parent)
        self._on_paths = on_paths
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.setDropAction(Qt.DropAction.CopyAction)
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.setDropAction(Qt.DropAction.CopyAction)
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        if not e.mimeData().hasUrls():
            e.ignore()
            return
        paths = []
        for url in e.mimeData().urls():
            local = url.toLocalFile()
            if local and is_path_exists_safe(local):
                paths.append(local.replace("/", "\\"))
        e.setDropAction(Qt.DropAction.CopyAction)
        e.acceptProposedAction()
        if self._on_paths:
            self._on_paths(paths)


# --- 2. 深海暗夜 极致皮肤 ---
DEEP_OCEAN_STYLE = """
    QMainWindow, QWidget#MainWidget { background-color: #1e1e1e; font-family: "Microsoft YaHei UI", "Segoe UI"; }
    
    QFrame#HeaderFrame { 
        background-color: #252526; 
        border: none; 
        border-bottom: 2px solid #007acc; 
    }
    
    QFrame#LeftPanel, QFrame#RightPanel { background-color: #252526; border: 1px solid #333333; border-radius: 6px; }
    
    QLabel { color: #e0e0e0; font-weight: bold; font-size: 13px; }
    QLabel#TitleLabel { font-size: 18px; color: #ffffff; font-weight: 900; }

    /* 输入框样式 */
    QLineEdit { background-color: #1e1e1e; border: 1px solid #3e3e42; border-radius: 4px; color: #d4d4d4; padding: 4px; font-size: 12px;}
    QLineEdit:focus { border: 1px solid #007acc; background-color: #2d2d2d; }

    /* 列表框样式：增强选中反馈 */
    QListWidget { 
        background-color: #1e1e1e; 
        border: 1px solid #3e3e42; 
        border-radius: 4px; 
        color: #d4d4d4; 
        font-size: 13px;
        outline: 0; 
    }
    QListWidget::item { 
        padding: 6px 4px; 
        margin: 1px;
        border-radius: 3px; 
    }
    QListWidget::item:hover { 
        background-color: #2a2d2e; 
    }
    QListWidget::item:selected { 
        background-color: #007acc; /* 选中时：鲜艳蓝底 */
        color: white;              /* 选中时：白字 */
        border: none;
    }
    QListWidget::item:selected:!active { 
        background-color: #005a9e; 
        color: white;
    }
    
    QPushButton { background-color: #333333; border: 1px solid #454545; border-radius: 4px; color: #cccccc; padding: 6px; font-weight: bold;}
    QPushButton:hover { background-color: #3e3e3e; color: white; border-color: #666; }
    QPushButton:pressed { background-color: #007acc; border-color: #007acc; }
    
    QPushButton#BtnBrowse { background-color: transparent; border: 1px solid #007acc; color: #007acc; }
    QPushButton#BtnBrowse:hover { background-color: #007acc; color: white; }
    
    /* 默认启动按钮样式 (蓝色) */
    QPushButton#BtnStart { background-color: #0e639c; color: white; font-weight: bold; border: none; padding: 10px; border-radius: 4px; font-size: 14px;}
    QPushButton#BtnStart:hover { background-color: #1177bb; }
    QPushButton#BtnStart:disabled { background-color: #3a3d41; color: #666; }
    
    QProgressBar { background-color: #1e1e1e; border: 1px solid #3e3e42; border-radius: 4px; text-align: center; }
    QProgressBar::chunk { background-color: #007acc; border-radius: 3px; }
    
    QTextEdit { background-color: #1e1e1e; color: #4ec9b0; border: none; border-radius: 4px; font-family: "Consolas"; font-size: 11px; }
    
    QMenuBar { background-color: #1e1e1e; border-bottom: 1px solid #333; color: #ccc; }
    QMenuBar::item { background: transparent; }
    QMenuBar::item:selected { background: #333; }
    QMenu { background-color: #252526; color: #ccc; border: 1px solid #444; }
    QMenu::item:selected { background-color: #007acc; color: white; }
    
    QLabel#StatusBar { color: #888; background: #252526; border-top: 1px solid #333; padding: 2px 5px; font-size: 10px; }
    
    QComboBox { background-color: #1e1e1e; border: 1px solid #3e3e42; border-radius: 4px; color: #d4d4d4; padding: 4px; min-width: 80px; }
    QComboBox:hover { border-color: #007acc; }
    QComboBox::drop-down { border: none; }
    
    QCheckBox { color: #e0e0e0; font-weight: bold; spacing: 8px; }
    QCheckBox::indicator { width: 18px; height: 18px; border: 1px solid #666; background: #1e1e1e; border-radius: 3px; }
    QCheckBox::indicator:hover { border-color: #007acc; }
    QCheckBox::indicator:checked { 
        background-color: #007acc; 
        border-color: #007acc;
        image: url('data:image/svg+xml;utf8,<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z" fill="white"/></svg>');
    }
    
    QMessageBox { background-color: #252526; }
    QMessageBox QLabel { color: #f0f0f0; }
    QMessageBox QPushButton { background-color: #3e3e42; color: #f0f0f0; border: 1px solid #555; width: 60px; }
"""

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PB XDelete") 
        self.resize(800, 600)
        self.center_window()
        self.force_quit = False 
        
        # --- 状态控制 ---
        self.is_running = False      # 标记是否正在运行
        self.current_process = None  # 当前 pb_xdelete.exe 子进程
        
        if os.path.exists(FINAL_ICON_PATH): self.setWindowIcon(QIcon(FINAL_ICON_PATH))
        self.init_tray_icon()

        for f in [TASK_FILE_PATH, LOG_FILE_PATH]:
            if os.path.exists(f): 
                try: os.remove(f)
                except Exception as e: logger.debug("init remove %s: %s", f, e)
        
        self.setup_ui()
        
        self.log_timer = QTimer()
        self.log_timer.setInterval(30) 
        self.log_timer.timeout.connect(self.read_log)
        self.log_queue = queue.Queue()
        self.total_batches = 1
        self.current_batch_index = 0
        self.failed_paths = []
        self.log_file_lock = threading.Lock()
        self.estimated_files = 0
        self.estimated_dirs = 0
        self._current_job_handle = None
        self._engine_returncode = None  # pb_xdelete.exe 子进程退出码，run_engine_thread 中设置

    def setup_ui(self):
        self.setStyleSheet(DEEP_OCEAN_STYLE)
        
        main_widget = QWidget()
        main_widget.setObjectName("MainWidget")
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        layout.setContentsMargins(10, 10, 10, 5)
        layout.setSpacing(8)
        
        self.setup_menubar()
        
        # Header
        self.header_frame = QFrame(); self.header_frame.setObjectName("HeaderFrame")
        self.header_frame.setFixedHeight(60)
        hl = QHBoxLayout(self.header_frame); hl.setContentsMargins(0, 0, 0, 0)
        hl.addStretch()
        title_lbl = QLabel("🚀 PB XDelete"); title_lbl.setObjectName("TitleLabel")
        hl.addWidget(title_lbl)
        hl.addStretch()
        layout.addWidget(self.header_frame)
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Left Panel
        self.left_panel = QFrame(); self.left_panel.setObjectName("LeftPanel")
        ll = QVBoxLayout(self.left_panel); ll.setContentsMargins(10, 10, 10, 10)
        
        l_top = QHBoxLayout()
        l_top.addWidget(QLabel("📁 任务队列（可拖入文件/文件夹）:"))
        btn_browse_dir = QPushButton("📂 加文件夹"); btn_browse_dir.setObjectName("BtnBrowse")
        btn_browse_dir.clicked.connect(self.browse_folder)
        btn_browse_file = QPushButton("📄 加文件"); btn_browse_file.setObjectName("BtnBrowseFile")
        btn_browse_file.clicked.connect(self.browse_file)
        l_top.addWidget(btn_browse_dir)
        l_top.addWidget(btn_browse_file)
        ll.addLayout(l_top)
        
        self.list_widget = PathDropListWidget(self.handle_dropped_paths)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # 左侧整块面板也可拖放，扩大落点区域
        self.left_panel.setAcceptDrops(True)
        self.left_panel.installEventFilter(self)
        ll.addWidget(self.list_widget)
        
        l_btns = QHBoxLayout()
        btn_rm = QPushButton("移除"); btn_rm.clicked.connect(self.remove_items)
        btn_cl = QPushButton("清空"); btn_cl.clicked.connect(self.clear_items)
        l_btns.addWidget(btn_rm); l_btns.addWidget(btn_cl)
        ll.addLayout(l_btns)
        
        l_mode = QHBoxLayout()
        l_mode.addWidget(QLabel("删除模式:"))
        self.combo_delete_mode = QComboBox()
        self.combo_delete_mode.addItems(["☢️ 永久粉碎", "🗑️ 移到回收站"])
        self.combo_delete_mode.setCurrentIndex(0)
        l_mode.addWidget(self.combo_delete_mode)
        ll.addLayout(l_mode)

        l_post = QHBoxLayout()
        l_post.addWidget(QLabel("任务完成后:"))
        self.combo_post_action = QComboBox()
        self.combo_post_action.addItems(["无", "⏳ 关机", "🔄 重启", "💤 待机"])
        self.combo_post_action.setCurrentIndex(0)
        l_post.addWidget(self.combo_post_action)
        ll.addLayout(l_post)

        self.check_auto_clear = QCheckBox("✅ 完成后自动清空队列")
        self.check_auto_clear.setChecked(False)
        ll.addWidget(self.check_auto_clear)
        
        # 按钮连接到 toggle_process
        self.btn_start = QPushButton("☢️ 启动删除"); self.btn_start.setObjectName("BtnStart")
        self.btn_start.clicked.connect(self.toggle_process)
        ll.addWidget(self.btn_start)
        
        # Right Panel
        self.right_panel = QFrame(); self.right_panel.setObjectName("RightPanel")
        rl = QVBoxLayout(self.right_panel); rl.setContentsMargins(10, 10, 10, 10)
        
        rl.addWidget(QLabel("💻 实时日志 (Native Output):"))
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p% - 等待 pb_xdelete...")
        rl.addWidget(self.progress_bar)
        self.log_text = QTextEdit(); self.log_text.setReadOnly(True)
        self.log_text.document().setMaximumBlockCount(4000)
        self.log_text.setText("Ready...\n")
        rl.addWidget(self.log_text)
        
        splitter.addWidget(self.left_panel)
        splitter.addWidget(self.right_panel)
        splitter.setStretchFactor(0, 4); splitter.setStretchFactor(1, 6)
        layout.addWidget(splitter)
        
        self.status_bar = QLabel("系统就绪 · 可将文件/文件夹拖入左侧队列"); self.status_bar.setObjectName("StatusBar")
        self.status_bar.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.status_bar.setFixedHeight(22)
        layout.addWidget(self.status_bar)

    # --- 核心交互逻辑 ---
    def showEvent(self, e):
        super().showEvent(e)
        # Admin 下 Qt OLE 拖放无效：改用 Win32 DragAcceptFiles + WM_DROPFILES
        if not getattr(self, "_win_drop_enabled", False):
            self._win_drop_enabled = True
            try:
                widgets = [self, self.list_widget, self.list_widget.viewport(), self.left_panel]
                for w in widgets:
                    hwnd = int(w.winId())
                    _uipi_allow_drop_messages(hwnd)
                    _drag_accept_files(hwnd, True)
                if not getattr(self, "_win_drop_filter", None):
                    self._win_drop_filter = _WinDropFilesFilter(self.handle_dropped_paths)
                    app = QApplication.instance()
                    if app:
                        app.installNativeEventFilter(self._win_drop_filter)
                self.status_bar.setText("系统就绪 · 可从资源管理器拖入文件/文件夹到本窗口")
            except Exception as ex:
                logger.debug("showEvent Win32 drop enable: %s", ex)

    def eventFilter(self, obj, e):
        """左侧面板空白处也可接收拖放"""
        if obj is self.left_panel:
            if e.type() == QEvent.Type.DragEnter:
                if e.mimeData().hasUrls():
                    e.setDropAction(Qt.DropAction.CopyAction)
                    e.acceptProposedAction()
                    return True
            elif e.type() == QEvent.Type.DragMove:
                if e.mimeData().hasUrls():
                    e.setDropAction(Qt.DropAction.CopyAction)
                    e.acceptProposedAction()
                    return True
            elif e.type() == QEvent.Type.Drop:
                if e.mimeData().hasUrls():
                    paths = []
                    for url in e.mimeData().urls():
                        local = url.toLocalFile()
                        if local and is_path_exists_safe(local):
                            paths.append(local.replace("/", "\\"))
                    e.setDropAction(Qt.DropAction.CopyAction)
                    e.acceptProposedAction()
                    self.handle_dropped_paths(paths)
                    return True
        return super().eventFilter(obj, e)

    def handle_dropped_paths(self, paths):
        """拖放进队列：支持多选文件与文件夹"""
        if not paths:
            QMessageBox.information(self, "提示", "请拖入文件夹或文件。")
            return
        if self.is_running:
            QMessageBox.warning(self, "提示", "任务运行中，请先停止后再添加路径。")
            return
        if len(paths) >= 10:
            pd = QProgressDialog("正在添加路径...", None, 0, len(paths), self)
            pd.setWindowModality(Qt.WindowModality.WindowModal)
            pd.setMinimumDuration(0)
            for i, p in enumerate(paths):
                pd.setValue(i)
                pd.setLabelText(f"正在添加 ({i+1}/{len(paths)})...")
                QApplication.processEvents()
                self.add_path(p)
            pd.setValue(len(paths))
            pd.close()
        else:
            for p in paths:
                self.add_path(p)
        self.status_bar.setText(f"队列: {self.list_widget.count()} 个（可继续拖入）")

    def add_path(self, path):
        norm_path = os.path.normpath(os.path.abspath(path.replace("/", "\\")))
        existing_items = [os.path.normpath(os.path.abspath(self.list_widget.item(i).text().replace("/", "\\"))) 
                         for i in range(self.list_widget.count())]
        if norm_path.lower() in [p.lower() for p in existing_items]:
            return
        abs_path = norm_path
        lower_path = _norm_protect(abs_path)

        # 磁盘根目录（C:\）：硬拦截
        if len(abs_path) <= 3 and abs_path.endswith(":\\"):
            QMessageBox.critical(self, "危险操作拦截", f"❌ 禁止删除磁盘根目录：\n{abs_path}")
            return

        for protected in SYSTEM_PROTECTED_PATHS:
            if not protected:
                continue
            if lower_path == protected or lower_path.startswith(protected + os.sep):
                QMessageBox.critical(self, "危险操作拦截", f"❌ 系统保护路径禁止删除：\n{abs_path}")
                return
            # 反向：待删路径是系统保护目录的父级
            if protected.startswith(lower_path + os.sep):
                QMessageBox.critical(self, "危险操作拦截", f"❌ 该路径包含系统保护目录，禁止删除：\n{abs_path}")
                return

        # 用户主目录/桌面/AppData 根：仅禁止整目录，允许删其下文件
        for protected in USER_ROOT_PROTECTED:
            if protected and lower_path == protected:
                QMessageBox.critical(self, "危险操作拦截", f"❌ 禁止删除用户敏感根目录：\n{abs_path}")
                return

        self.list_widget.addItem(norm_path)
        self.status_bar.setText(f"队列: {self.list_widget.count()} 个")


    # --- 核心交互逻辑 ---

    def toggle_process(self):
        """控制任务的开始与停止"""
        if self.is_running:
            self.stop_process()
        else:
            self.start_process()

    def stop_process(self):
        """用户点击停止时的处理（回收站模式无法取消，仅提示）"""
        if self.is_running and self.current_process is None:
            QMessageBox.information(self, "提示", "移到回收站操作无法中途取消，请等待完成。")
            return
        if QMessageBox.question(self, "停止", "⚠️ 确定要停止任务吗？\n将优先尝试平滑停止（等待当前批次完成），超时后强制终止。",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.kill_engine()
            self.btn_start.setText("⏳ 正在停止...")
            self.btn_start.setEnabled(False)

    def _kill_engine_sync(self):
        """同步击杀：用于 closeEvent 强退。直接 kill 并等待子进程退出，避免 daemon 线程被主进程强杀导致僵尸进程"""
        self.is_running = False
        proc = self.current_process
        if not proc:
            return
        self.current_process = None
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception as e:
            logger.debug("_kill_engine_sync: %s", e)

    def kill_engine(self):
        """核心：优先平滑停止，超时后强制 kill（异步执行，避免 UI 阻塞）"""
        self.is_running = False
        proc = self.current_process
        if not proc:
            return
        self.current_process = None

        def _wait_and_kill():
            try:
                if signal_engine_graceful_stop():
                    proc.wait(timeout=2)
                else:
                    raise RuntimeError("OpenEvent failed")
            except (subprocess.TimeoutExpired, RuntimeError):
                try:
                    proc.kill()
                except Exception as e:
                    logger.debug("kill_engine kill: %s", e)
            except Exception as e:
                logger.debug("kill_engine: %s", e)
                try:
                    proc.kill()
                except Exception:
                    pass
            QMetaObject.invokeMethod(self, "_on_kill_done", Qt.ConnectionType.QueuedConnection)

        self.log_text.append("\n>>> 🛑 已发送平滑停止信号，等待 pb_xdelete.exe 退出...")
        threading.Thread(target=_wait_and_kill, daemon=True).start()

    def _run_recycle_bin_mode(self):
        """移到回收站模式：使用 SHFileOperation，不调用 pb_xdelete.exe"""
        self.is_running = True
        self.list_widget.setEnabled(False)
        self.combo_post_action.setEnabled(False)
        self.combo_delete_mode.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.btn_start.setText("🗑️ 正在移到回收站...")
        self.log_text.clear()
        self.log_text.append(f">>> 正在将 {len(self.targets)} 个项目移到回收站...")

        def _do_recycle():
            try:
                ctypes.windll.ole32.CoInitialize(None)
                try:
                    self._recycle_ok = move_to_recycle_bin(self.targets)
                finally:
                    ctypes.windll.ole32.CoUninitialize()
            except Exception as e:
                logger.debug("_do_recycle COM: %s", e)
                self._recycle_ok = move_to_recycle_bin(self.targets)
            QMetaObject.invokeMethod(self, "_on_recycle_done", Qt.ConnectionType.QueuedConnection)

        self._recycle_ok = False
        threading.Thread(target=_do_recycle, daemon=True).start()

    def _on_recycle_done(self):
        """回收站模式完成回调（含窗口存活检查，避免关闭竞态）"""
        if not self.isVisible() or not self.windowHandle():
            return
        self.is_running = False
        self.btn_start.setEnabled(True)
        self.btn_start.setText("☢️ 启动删除")
        self.list_widget.setEnabled(True)
        self.combo_post_action.setEnabled(True)
        self.combo_delete_mode.setEnabled(True)
        ok = getattr(self, "_recycle_ok", False)
        self.log_text.append("\n>>> ✅ 已全部移到回收站。" if ok else "\n>>> ⚠️ 部分或全部操作失败，请检查路径权限。")
        finish_msg = "已全部移到回收站！" if ok else "部分或全部操作失败，请检查路径权限。"
        # 回收站模式也写入历史日志
        targets_snapshot = getattr(self, "targets", [])
        if targets_snapshot:
            def _append_recycle_history():
                try:
                    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    content = f"\n{'='*50}\n[移到回收站] {ts} | {len(targets_snapshot)} 个目标 | 结果: {'成功' if ok else '失败'}\n"
                    for p in targets_snapshot[:50]:
                        content += f"  {p}\n"
                    if len(targets_snapshot) > 50:
                        content += f"  ... 还有 {len(targets_snapshot)-50} 个\n"
                    content += f"{'='*50}\n"
                    with open(HISTORY_FILE_PATH, "a", encoding="utf-8") as f:
                        f.write(content)
                    # 历史轮转（与 finish_work 一致）
                    HISTORY_MAX_BYTES = 5 * 1024 * 1024
                    if os.path.exists(HISTORY_FILE_PATH) and os.path.getsize(HISTORY_FILE_PATH) > HISTORY_MAX_BYTES:
                        with open(HISTORY_FILE_PATH, "r", encoding="utf-8", errors="ignore") as f:
                            full = f.read()
                        keep_from = max(0, len(full) - (HISTORY_MAX_BYTES // 2))
                        sep = "\n" + "=" * 50 + "\n"
                        idx = full.find(sep, keep_from)
                        full = full[idx + len(sep):] if idx >= 0 else full[keep_from:]
                        with open(HISTORY_FILE_PATH, "w", encoding="utf-8") as f:
                            f.write(full)
                except Exception as e:
                    logger.debug("_append_recycle_history: %s", e)
            threading.Thread(target=_append_recycle_history, daemon=True).start()
        if self.check_auto_clear.isChecked():
            self.list_widget.clear()
            self.status_bar.setText("队列已清空")
        else:
            self.status_bar.setText(f"队列: {self.list_widget.count()} 个")
        post_idx = self.combo_post_action.currentIndex()
        _no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        if post_idx == 1:
            subprocess.run(["shutdown", "/s", "/t", "60"], creationflags=_no_win, check=False)
            QMessageBox.warning(self, "完成", f"{finish_msg}\n\n系统将在 60 秒后关机。")
        elif post_idx == 2:
            subprocess.run(["shutdown", "/r", "/t", "60"], creationflags=_no_win, check=False)
            QMessageBox.warning(self, "完成", f"{finish_msg}\n\n系统将在 60 秒后重启。")
        elif post_idx == 3:
            QMessageBox.information(self, "完成", finish_msg)
            try:
                subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0", "1", "0"],
                              creationflags=subprocess.CREATE_NO_WINDOW, check=False)
            except Exception:
                pass
        else:
            QMessageBox.information(self, "完成", finish_msg)

    def _assign_job_object(self, proc):
        """将子进程绑定到 Windows Job Object，修复 64 位句柄截断与内核句柄泄漏"""
        from ctypes import wintypes
        try:
            kernel32 = ctypes.windll.kernel32

            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
            kernel32.SetInformationJobObject.restype = wintypes.BOOL
            kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]
            kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
            kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

            if self._current_job_handle:
                kernel32.CloseHandle(self._current_job_handle)
                self._current_job_handle = None

            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                err = kernel32.GetLastError()
                msg = f">>> WARN: CreateJobObject 失败 (err={err})，强退保底可能减弱"
                logger.warning(msg)
                try:
                    self.log_queue.put(msg)
                except Exception:
                    pass
                return

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32), ("SchedulingClass", ctypes.c_uint32)
                ]

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_uint64), ("WriteOperationCount", ctypes.c_uint64),
                    ("OtherOperationCount", ctypes.c_uint64), ("ReadTransferCount", ctypes.c_uint64),
                    ("WriteTransferCount", ctypes.c_uint64), ("OtherTransferCount", ctypes.c_uint64)
                ]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)
                ]

            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
            limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

            if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                err = kernel32.GetLastError()
                msg = f">>> WARN: JobObject SetInformation 失败 (err={err})，强退保底可能减弱"
                logger.warning(msg)
                try:
                    self.log_queue.put(msg)
                except Exception:
                    pass
                kernel32.CloseHandle(job)
                return

            # 仅使用 Popen 创建时持有的进程句柄，避免 OpenProcess(PROCESS_ALL_ACCESS) 在受限环境失败
            handle = getattr(proc, "_handle", None)
            if not handle:
                msg = ">>> WARN: 无进程句柄，跳过 Job Object 绑定"
                logger.warning(msg)
                try:
                    self.log_queue.put(msg)
                except Exception:
                    pass
                kernel32.CloseHandle(job)
                return
            if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(handle)):
                err = kernel32.GetLastError()
                msg = f">>> WARN: AssignProcessToJobObject 失败 (err={err})，强退保底可能减弱"
                logger.warning(msg)
                try:
                    self.log_queue.put(msg)
                except Exception:
                    pass
                kernel32.CloseHandle(job)
                return

            self._current_job_handle = job
        except Exception as e:
            logger.warning("Job Object failed: %s", e)
            try:
                self.log_queue.put(f">>> WARN: Job Object 异常: {e}")
            except Exception:
                pass

    @pyqtSlot()
    def _on_kill_done(self):
        """kill 完成后在主线程恢复按钮状态（finish_work 由 LOG_END_MARKER 触发）"""
        self.btn_start.setEnabled(True)
        self.btn_start.setText("☢️ 启动删除")
        self.btn_start.setStyleSheet("")
        self.list_widget.setEnabled(True)
        self.combo_post_action.setEnabled(True)
        self.combo_delete_mode.setEnabled(True)

    def start_process(self):
        """启动任务"""
        count = self.list_widget.count()
        if count == 0: QMessageBox.warning(self, "提示", "队列为空！"); return

        is_recycle = self.combo_delete_mode.currentIndex() == 1

        warn_text = (
            f"移到回收站 {count} 个项目（文件/文件夹）？"
            if is_recycle else
            f"永久粉碎 {count} 个项目（文件/文件夹）？"
        )
        post_idx = self.combo_post_action.currentIndex()
        if post_idx == 1: warn_text += "\n\n⚠️ 注意：任务完成后将自动关机！"
        elif post_idx == 2: warn_text += "\n\n⚠️ 注意：任务完成后将自动重启！"
        elif post_idx == 3: warn_text += "\n\n⚠️ 注意：任务完成后将进入待机！"

        if QMessageBox.question(self, "确认", warn_text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.No:
            return

        self.targets = [self.list_widget.item(i).text() for i in range(count)]
        self.total_batches = 1

        if not is_recycle:
            eng = _engine_path()
            if not os.path.isfile(eng):
                QMessageBox.critical(self, "错误", f"找不到 pb_xdelete.exe（或兼容旧名）：\n{eng}\n请与本程序放在同一目录或打包进资源。")
                return

        if is_recycle:
            self._run_recycle_bin_mode()
            return

        # --- 切换界面到运行状态（永久粉碎） ---
        self.is_running = True
        self.btn_start.setText("🛑 停止任务")
        # 红色警告样式
        self.btn_start.setStyleSheet("background-color: #c53030; color: white; font-weight: bold; border: none; padding: 10px; border-radius: 4px; font-size: 14px;")
        
        self.list_widget.setEnabled(False)
        self.combo_post_action.setEnabled(False)
        self.combo_delete_mode.setEnabled(False)
        self.log_text.clear()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("0% - 等待 pb_xdelete...")
        self._last_progress_pct = -1
        self.last_summary = {"ok": 0, "fail": 0, "dirs": 0, "dirs_fail": 0}
        self.failed_paths = []
        self._engine_returncode = None
        
        self.log_text.append(">>> 🚀 正在启动 pb_xdelete.exe（--list 清单模式）...")
        
        while not self.log_queue.empty():
            try: self.log_queue.get_nowait()
            except queue.Empty: break
        threading.Thread(target=self.run_engine_thread, daemon=True).start()
        self.log_timer.start()

    def _emit_pipe_line(self, line, log_file, q):
        """单行逻辑输出（兼容 C++ \\r 刷新）；队列与磁盘日志分流"""
        if not line:
            return
        q.put(line)
        if not log_file:
            return
        s = line.strip()
        if s.startswith("PB_XDELETE_PROTOCOL:"):
            return
        is_dashboard = s.startswith("[File]") or s.startswith("[Dirs]")
        if is_dashboard and "PROGRESS:" not in s and "SUMMARY:" not in s and "FAILED" not in s:
            return
        with self.log_file_lock:
            log_file.write(line + "\n")
            log_file.flush()

    def _pipe_reader(self, pipe, log_file, q):
        """二进制流 + UTF-8 增量解码；遇 \\r/\\n 即产出一段，避免 readline 卡在无换行缓冲"""
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        buf_chars = []
        try:
            while True:
                raw = pipe.read(8192)
                if not raw:
                    for ch in decoder.decode(b"", final=True):
                        if ch in "\r\n":
                            if buf_chars:
                                self._emit_pipe_line("".join(buf_chars), log_file, q)
                                buf_chars = []
                        else:
                            buf_chars.append(ch)
                    if buf_chars:
                        self._emit_pipe_line("".join(buf_chars), log_file, q)
                    break
                for ch in decoder.decode(raw):
                    if ch in "\r\n":
                        if buf_chars:
                            self._emit_pipe_line("".join(buf_chars), log_file, q)
                            buf_chars = []
                    else:
                        buf_chars.append(ch)
        except Exception as e:
            logger.debug("_pipe_reader: %s", e)
        finally:
            pipe.close()

    def run_engine_thread(self):
        """后台线程：写入随机清单，调用 pb_xdelete.exe --list；结束后删除清单"""
        task_path = None
        try:
            os.makedirs(PENDING_PATHS_DIR, exist_ok=True)
            # 随机名 + .list：避免固定 task_list.tmp 被覆盖；不与 IPC *.tmp 混淆
            task_path = os.path.join(
                PENDING_PATHS_DIR,
                f"{TASK_LIST_PREFIX}{os.getpid()}_{uuid.uuid4().hex}.list",
            )
            with open(task_path, "w", encoding="utf-8-sig") as f:
                for p in self.targets:
                    f.write(p + "\n")
            with open(LOG_FILE_PATH, "w", encoding="utf-8") as log_file:
                with self.log_file_lock:
                    log_file.write(f"--- 任务开始: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {len(self.targets)} 个目标 ---\n")
                    for p in self.targets[:20]:
                        log_file.write(f"  [目标] {p}\n")
                    if len(self.targets) > 20:
                        log_file.write(f"  ... 还有 {len(self.targets) - 20} 个目标\n")
                    log_file.write("-" * 50 + "\n")
                    log_file.flush()
                if not self.is_running:
                    with self.log_file_lock:
                        log_file.write("\n>>> USER_ABORTED: 任务已被用户手动停止。\n")
                    self.log_queue.put(">>> USER_ABORTED: 任务已被用户手动停止。")
                else:
                    eng = _engine_path()
                    self.log_queue.put(f">>> 已写入任务清单 ({len(self.targets)} 个)，启动 pb_xdelete.exe...")
                    cmd_args = [eng, "--list", task_path]
                    ensure_engine_stop_event(reset=True)
                    self.current_process = subprocess.Popen(
                        cmd_args,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        cwd=BASE_DIR,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        bufsize=0,
                    )
                    self._assign_job_object(self.current_process)
                    reader = threading.Thread(
                        target=self._pipe_reader,
                        args=(self.current_process.stdout, log_file, self.log_queue),
                        daemon=True
                    )
                    reader.start()
                    proc = self.current_process
                    if proc:
                        self._engine_returncode = proc.wait()
                        try:
                            with self.log_file_lock:
                                log_file.write(f"\nEXIT_CODE:{self._engine_returncode}\n")
                                log_file.flush()
                        except Exception:
                            pass
                    else:
                        self._engine_returncode = None
                    self.current_process = None
                    reader.join(timeout=2)
            with open(LOG_FILE_PATH, "a", encoding="utf-8") as log_file:
                with self.log_file_lock:
                    log_file.write("\nLOG_END_MARKER\n")
            self.log_queue.put("LOG_END_MARKER")
        except Exception as e:
            logger.debug("run_engine_thread: %s", e)
            self._engine_returncode = -1
            try:
                with open(LOG_FILE_PATH, "a", encoding="utf-8") as log_file:
                    with self.log_file_lock:
                        log_file.write(f"\nFATAL: {str(e)}\nEXIT_CODE:-1\nLOG_END_MARKER\n")
            except Exception:
                pass
            self.log_queue.put("LOG_END_MARKER")
        finally:
            if task_path:
                try:
                    os.remove(task_path)
                except Exception:
                    pass

    def read_log(self):
        """从 PIPE 队列读取日志（替代轮询临时文件）"""
        try:
            while True:
                try:
                    line = self.log_queue.get_nowait()
                except queue.Empty:
                    break
                clean_line = line.strip() if isinstance(line, str) else str(line).strip()
                if not clean_line:
                    continue
                if "LOG_END_MARKER" in clean_line:
                    self.log_text.append("\n>>> ✅ 任务处理流程结束。")
                    self.finish_work()
                    return
                # 扫描阶段：引擎无总文件数，无法用 0–100% 表示；用不确定进度条避免“一直 0%”的错觉
                m = re.search(r"PROGRESS:SCANNING:(\d+)", clean_line)
                if m:
                    tot = int(m.group(1))
                    self.progress_bar.setRange(0, 0)
                    self.progress_bar.setFormat(f"扫描中...（已删 {tot:,}）")
                    continue
                # 删除阶段：整数百分比，可能数秒停在同一格；用 search 兼容行首非 PROGRESS 的异常合并行
                m = re.search(r"PROGRESS:(\d+)%", clean_line)
                if m:
                    pct = int(m.group(1))
                    if pct > 100:
                        pct = 100
                    if pct < self._last_progress_pct:
                        pct = self._last_progress_pct
                    else:
                        self._last_progress_pct = pct
                    self.progress_bar.setRange(0, 100)
                    self.progress_bar.setValue(pct)
                    self.progress_bar.setFormat(f"{pct}% - 删除中...")
                    continue
                m = re.search(r"SUMMARY:\s*Files_OK=(\d+)\s*\|\s*Files_Fail=(\d+)\s*\|\s*Dirs=(\d+)(?:\s*\|\s*Bytes_Freed=(\d+))?(?:\s*\|\s*Dirs_Fail=(\d+))?", clean_line)
                if m:
                    self.last_summary["ok"] = self.last_summary.get("ok", 0) + int(m.group(1))
                    self.last_summary["fail"] = self.last_summary.get("fail", 0) + int(m.group(2))
                    self.last_summary["dirs"] = self.last_summary.get("dirs", 0) + int(m.group(3))
                    if m.group(4) is not None:
                        self.last_summary["bytes_freed"] = self.last_summary.get("bytes_freed", 0) + int(m.group(4))
                    if m.group(5) is not None:
                        self.last_summary["dirs_fail"] = self.last_summary.get("dirs_fail", 0) + int(m.group(5))
                if clean_line == "FAILED_PATHS_BEGIN":
                    continue
                if clean_line.startswith("FAILED: "):
                    self.failed_paths.append(clean_line[8:].strip())
                    continue
                if clean_line == "FAILED_PATHS_END":
                    continue
                if clean_line.startswith("PB_XDELETE_PROTOCOL:"):
                    continue
                self.log_text.append(clean_line)
                sb = self.log_text.verticalScrollBar()
                sb.setValue(sb.maximum())
        except Exception as e:
            logger.debug("read_log: %s", e)

    def finish_work(self):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.progress_bar.setFormat("100% - 任务处理完成")
        self._last_progress_pct = -1
        self.log_timer.stop()

        if self._current_job_handle:
            try:
                ctypes.windll.kernel32.CloseHandle(self._current_job_handle)
            except Exception:
                pass
            self._current_job_handle = None

        # 恢复状态和 UI
        self.is_running = False
        self.current_process = None
        
        self.btn_start.setEnabled(True)
        self.btn_start.setText("☢️ 启动删除")
        # 清空样式表，使其恢复到全局的 DEEP_OCEAN_STYLE (蓝色)
        self.btn_start.setStyleSheet("") 
        
        self.list_widget.setEnabled(True)
        self.combo_post_action.setEnabled(True)
        self.combo_delete_mode.setEnabled(True)

        if self.check_auto_clear.isChecked():
            self.list_widget.clear()
            self.status_bar.setText("队列已清空")
        else:
            self.status_bar.setText(f"队列: {self.list_widget.count()} 个")
        
        def _append_history():
            try:
                if os.path.exists(LOG_FILE_PATH):
                    with open(LOG_FILE_PATH, "r", encoding="utf-8", errors="ignore") as src:
                        raw_lines = src.readlines()
                    
                    # [日志优化] 核心清洗逻辑：剔除无用的刷屏信息
                    clean_lines = []
                    for line in raw_lines:
                        s = line.strip()
                        if not s or s == "LOG_END_MARKER": continue
                        if s.startswith("PROGRESS:"): continue            # 剔除进度条刷屏
                        if s.startswith("[File]") or s.startswith("[Dirs]"): continue # 剔除单行刷新指标
                        if s.startswith("PB_XDELETE_PROTOCOL:"): continue # 剔除内部通信协议头
                        if s.startswith(">>>"): continue                  # 剔除 GUI 界面产生的运行时提示文字
                        
                        clean_lines.append(line)
                        
                    content = "".join(clean_lines).strip()
                    
                    HISTORY_MAX_BYTES = 5 * 1024 * 1024  # 5MB 轮转
                    new_block = f"\n{'='*50}\n{content}\n{'='*50}\n"
                    
                    if os.path.exists(HISTORY_FILE_PATH) and os.path.getsize(HISTORY_FILE_PATH) + len(new_block.encode("utf-8")) > HISTORY_MAX_BYTES:
                        with open(HISTORY_FILE_PATH, "r", encoding="utf-8", errors="ignore") as f:
                            full = f.read()
                        keep_from = max(0, len(full) - (HISTORY_MAX_BYTES // 2))
                        sep = "\n" + "=" * 50 + "\n"
                        idx = full.find(sep, keep_from)
                        full = full[idx + len(sep):] if idx >= 0 else full[keep_from:]
                        with open(HISTORY_FILE_PATH, "w", encoding="utf-8") as f:
                            f.write(full)
                            
                    with open(HISTORY_FILE_PATH, "a", encoding="utf-8") as dest:
                        dest.write(new_block)
            except Exception as e:
                logger.debug("finish_work history: %s", e)

        threading.Thread(target=_append_history, daemon=True).start()

        last_log = self.log_text.toPlainText()
        if "USER_ABORTED" in last_log or "用户已发送终止信号" in last_log or "平滑停止" in last_log:
            QMessageBox.information(
                self,
                "停止",
                "任务已手动停止。\n引擎会尽力清理已扫到的空文件夹；若仍有残留请检查后手动删除。",
            )
            return

        summary_msg = ""
        if self.last_summary:
            s = self.last_summary
            summary_msg = f"\n\n📊 结果汇总：\n  文件成功: {s.get('ok', 0)} | 文件失败: {s.get('fail', 0)} | 目录: {s.get('dirs', 0)}"
            if s.get("dirs_fail", 0) > 0:
                summary_msg += f" | 目录失败: {s['dirs_fail']}"
            bf = s.get("bytes_freed", 0)
            if bf > 0:
                gb = bf / (1024 ** 3)
                mb = bf / (1024 ** 2)
                summary_msg += f"\n  ✅ 共释放空间: {gb:.2f} GB" if gb >= 1 else f"\n  ✅ 共释放空间: {mb:.2f} MB"
            if self.failed_paths:
                summary_msg += f"\n\n⚠️ 失败路径（共 {len(self.failed_paths)} 条，最多展示 500 条）：\n"
                for p in self.failed_paths[:20]:
                    summary_msg += f"  • {p}\n"
                if len(self.failed_paths) > 20:
                    summary_msg += f"  ... 还有 {len(self.failed_paths) - 20} 条，详见日志\n"

        rc = getattr(self, "_engine_returncode", None)
        if rc is not None:
            # V104+：0=ok 1=参数错误 2=用户中止 3=部分失败（兼容旧引擎 rc==1 当中止）
            if rc == 0:
                pass
            elif rc == -1:
                summary_msg += "\n\n⚠️ 启动或运行子进程时发生异常，详见日志。"
            elif rc == 2 or (rc == 1 and ("USER_ABORTED" in last_log or "平滑停止" in last_log)):
                summary_msg += "\n\n⚠️ 用户已中止删除（退出码 2）。" if rc == 2 else "\n\n⚠️ 用户已中止删除。"
            elif rc == 1:
                summary_msg += "\n\n⚠️ 进程退出码 1（参数错误或旧版中止）。"
            elif rc == 3:
                if "WARN:EMPTY_RUN" in last_log or (
                    self.last_summary
                    and self.last_summary.get("ok", 0) == 0
                    and self.last_summary.get("dirs", 0) == 0
                    and self.last_summary.get("fail", 0) == 0
                ):
                    summary_msg += "\n\n⚠️ 未删除任何内容（退出码 3：清单无效/目标不存在/空跑）。"
                else:
                    summary_msg += "\n\n⚠️ 部分路径删除失败（退出码 3），请查看失败列表/日志。"
            else:
                summary_msg += f"\n\n⚠️ 进程退出码: {rc}（非 0 表示未正常完成）。"

        post_idx = self.combo_post_action.currentIndex()
        _no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        if post_idx == 1:  # 关机
            subprocess.run(["shutdown", "/s", "/t", "60"], creationflags=_no_win, check=False)
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("自动关机")
            msg.setText("任务已完成！系统将在 60 秒后自动关机。" + summary_msg + "\n\n需要取消关机吗？")
            btn_cancel = msg.addButton("🛑 取消关机", QMessageBox.ButtonRole.ActionRole)
            msg.addButton("👌 知道了", QMessageBox.ButtonRole.AcceptRole)
            msg.exec()
            if msg.clickedButton() == btn_cancel:
                subprocess.run(["shutdown", "/a"], creationflags=_no_win, check=False)
                self.log_text.append("\n>>> 🚫 用户取消关机。")
            else:
                self.log_text.append("\n>>> ⏳ 系统即将关机...")
        elif post_idx == 2:  # 重启
            subprocess.run(["shutdown", "/r", "/t", "60"], creationflags=_no_win, check=False)
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("自动重启")
            msg.setText("任务已完成！系统将在 60 秒后自动重启。" + summary_msg + "\n\n需要取消重启吗？")
            btn_cancel = msg.addButton("🛑 取消重启", QMessageBox.ButtonRole.ActionRole)
            msg.addButton("👌 知道了", QMessageBox.ButtonRole.AcceptRole)
            msg.exec()
            if msg.clickedButton() == btn_cancel:
                subprocess.run(["shutdown", "/a"], creationflags=_no_win, check=False)
                self.log_text.append("\n>>> 🚫 用户取消重启。")
            else:
                self.log_text.append("\n>>> ⏳ 系统即将重启...")
        elif post_idx == 3:  # 待机
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setWindowTitle("进入待机")
            msg.setText("任务已完成！" + summary_msg + "\n\n点击确定后系统将进入待机。")
            msg.addButton("👌 确定", QMessageBox.ButtonRole.AcceptRole)
            msg.exec()
            self.log_text.append("\n>>> 💤 系统进入待机...")
            try:
                subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0", "1", "0"], 
                              creationflags=subprocess.CREATE_NO_WINDOW, check=False)
            except Exception:
                pass
        else:
            QMessageBox.information(self, "完成", "所有任务执行完毕！" + summary_msg)

    def center_window(self):
        screen = QApplication.primaryScreen().geometry()
        size = self.geometry()
        self.move((screen.width()-size.width())//2, (screen.height()-size.height())//2)

    def browse_folder(self):
        """仅选择文件夹"""
        path = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if path:
            self.add_path(path.replace("/", "\\"))

    def browse_file(self):
        """仅选择单个文件"""
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", "所有文件 (*.*)")
        if path:
            self.add_path(path.replace("/", "\\"))

    def remove_items(self):
        for item in self.list_widget.selectedItems(): self.list_widget.takeItem(self.list_widget.row(item))
        self.status_bar.setText(f"队列: {self.list_widget.count()} 个")
    
    def clear_items(self):
        self.list_widget.clear()
        self.estimated_files = 0
        self.estimated_dirs = 0
        self.status_bar.setText("队列为空")

    def setup_menubar(self):
        menubar = self.menuBar()
        tool_menu = menubar.addMenu("🛠️ 工具")
        tool_menu.addAction("✅ 添加右键菜单", self.reg_menu)
        tool_menu.addAction("🗑️ 移除右键菜单", self.unreg_menu)
        tool_menu.addSeparator()
        tool_menu.addAction("📜 查看历史日志", self.open_history_log)
        
        about_menu = menubar.addMenu("ℹ️ 关于")
        about_menu.addAction("©️ 版权信息", self.show_copyright)

    def open_history_log(self):
        if not os.path.exists(HISTORY_FILE_PATH):
            QMessageBox.information(self, "提示", "暂无历史日志。")
            return
        try: os.startfile(HISTORY_FILE_PATH)
        except Exception as e: QMessageBox.warning(self, "错误", f"无法打开文件:\n{e}")

    def show_copyright(self):
        QMessageBox.information(self, "关于软件", COPYRIGHT_TEXT)

    def reg_menu(self):
        if not is_admin():
            # 提权后携带 --reg-menu，新进程将自动执行注册并退出，无需二次点击
            extra = ["--reg-menu"] if "--reg-menu" not in sys.argv else []
            params = subprocess.list2cmdline(sys.argv[1:] + extra)
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, BASE_DIR, 1)
            return
        try:
            do_reg_menu()
            QMessageBox.information(self, "成功", "右键菜单已添加！")
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    def unreg_menu(self):
        if not is_admin(): QMessageBox.warning(self, "权限", "需管理员权限"); return
        try:
            subprocess.run(f'reg delete "HKCR\\Directory\\shell\\{REG_KEY_NAME}" /f', shell=True, stdout=subprocess.DEVNULL)
            subprocess.run(f'reg delete "HKCR\\*\\shell\\{REG_KEY_NAME}" /f', shell=True, stdout=subprocess.DEVNULL)
            QMessageBox.information(self, "成功", "已移除")
        except Exception as e:
            logger.debug("unreg_menu: %s", e)

    def init_tray_icon(self):
        self.tray = QSystemTrayIcon(self)
        if os.path.exists(FINAL_ICON_PATH): self.tray.setIcon(QIcon(FINAL_ICON_PATH))
        else: self.tray.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon))
        menu = QMenu()
        menu.addAction("显示", self.showNormal)
        menu.addAction("退出", self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.showNormal() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def quit_app(self): 
        # 调用 self.close() 而不是直接 quit，这样会触发 closeEvent 进行安全检查
        self.close()

    def closeEvent(self, e):
        # 1. 检查是否有任务正在运行
        if self.is_running:
            reply = QMessageBox.warning(
                self, 
                "退出确认", 
                "⚠️ 危险：任务正在运行中！\n\n强制退出可能会导致文件删除不彻底。\n确定要终止任务并退出吗？", 
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                self._kill_engine_sync()  # 同步击杀，避免 daemon 线程被主进程退出时强杀导致僵尸进程
                self.force_quit = True
                e.accept()
            else:
                e.ignore()
        else:
            self.force_quit = True
            e.accept()

    def changeEvent(self, e):
        if e.type() == QEvent.Type.WindowStateChange and (self.windowState() & Qt.WindowState.WindowMinimized):
            e.ignore()
            self.hide()
            self.tray.showMessage("PB XDelete", "已最小化到托盘", QSystemTrayIcon.MessageIcon.Information, 1000)

def cleanup_pending_paths_old_files():
    """启动时清理 IPC 目录（PENDING_PATHS_DIR）中超过 1 小时的旧 .tmp 及孤立 .writing 文件"""
    if not os.path.isdir(PENDING_PATHS_DIR):
        return
    try:
        now = datetime.datetime.now()
        for name in os.listdir(PENDING_PATHS_DIR):
            is_task_list = name.startswith(TASK_LIST_PREFIX) and name.endswith(".list")
            if not (name.endswith(".tmp") or name.endswith(".writing") or is_task_list):
                continue
            if name in (TASK_FILE, LOG_FILE):
                continue
            fpath = os.path.join(PENDING_PATHS_DIR, name)
            try:
                mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fpath))
                if (now - mtime).total_seconds() > 3600:
                    os.remove(fpath)
            except Exception:
                pass
    except Exception as e:
        logger.debug("cleanup_pending_paths_old_files: %s", e)

def write_pending_paths(paths):
    """副进程：将路径写入 PENDING_PATHS_DIR（%TEMP%\\PB_XDelete_IPC），供主进程轮询（右键多选 IPC）"""
    if not paths:
        return
    # 过滤已知非路径参数，避免将 --reg-menu 等写入
    filtered = [p.strip() for p in paths if p and p.strip() and p.strip() not in KNOWN_NON_PATH_ARGS]
    if not filtered:
        return
    try:
        os.makedirs(PENDING_PATHS_DIR, exist_ok=True)
        fname_final = os.path.join(PENDING_PATHS_DIR, f"_{os.getpid()}_{id(paths)}.tmp")
        fname_writing = fname_final + ".writing"
        # [核心修复] 先写入 .writing 临时文件，完毕后再原子重命名，杜绝主进程读取半截文件的竞态条件
        with open(fname_writing, "w", encoding="utf-8") as f:
            for p in filtered:
                f.write(p + "\n")
        os.rename(fname_writing, fname_final)
    except Exception as e:
        logger.debug("write_pending_paths: %s", e)

def process_pending_paths(window):
    """主进程：扫描 PENDING_PATHS_DIR；有新增时唤醒窗口。运行中不消费，避免篡改任务队列。"""
    if not os.path.isdir(PENDING_PATHS_DIR):
        return 0
    if getattr(window, "is_running", False):
        return 0
    added = 0
    try:
        for name in os.listdir(PENDING_PATHS_DIR):
            if not name.endswith(".tmp"):
                continue
            if name in (TASK_FILE, LOG_FILE):
                continue
            fpath = os.path.join(PENDING_PATHS_DIR, name)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        p = line.strip()
                        if p and is_path_exists_safe(p):
                            before = window.list_widget.count()
                            window.add_path(p)
                            if window.list_widget.count() > before:
                                added += 1
            except Exception as e:
                logger.debug("process_pending_paths read %s: %s", fpath, e)
            try:
                os.remove(fpath)
            except Exception:
                pass
    except Exception as e:
        logger.debug("process_pending_paths: %s", e)
    if added > 0:
        try:
            window.showNormal()
            window.activateWindow()
            window.raise_()
        except Exception:
            pass
    return added

def do_reg_menu():
    """执行右键菜单注册（需管理员权限）：文件夹 + 所有文件；MultiSelectModel=Player 避免多选 UAC 风暴"""
    for root_key in (f"Directory\\shell\\{REG_KEY_NAME}", f"*\\shell\\{REG_KEY_NAME}"):
        key = winreg.CreateKey(winreg.HKEY_CLASSES_ROOT, root_key)
        winreg.SetValue(key, "", winreg.REG_SZ, MENU_NAME)
        winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, REAL_EXE_PATH)
        winreg.SetValueEx(key, "MultiSelectModel", 0, winreg.REG_SZ, "Player")
        winreg.CloseKey(key)
        key_cmd = winreg.CreateKey(winreg.HKEY_CLASSES_ROOT, root_key + "\\command")
        winreg.SetValue(key_cmd, "", winreg.REG_SZ, f'"{REAL_EXE_PATH}" "%1"')
        winreg.CloseKey(key_cmd)

def try_single_instance_mutex():
    """使用 Windows Mutex 实现单实例（进程退出时 OS 自动释放；64 位 HANDLE 安全）"""
    from ctypes import wintypes
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        mutex_name = "Global\\" + app_instance_id
        mutex = kernel32.CreateMutexW(None, True, mutex_name)
        if not mutex:
            return False
        err = kernel32.GetLastError()
        if err == 0x000000B7:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(mutex)
            return False
        return True
    except Exception as e:
        logger.debug("try_single_instance_mutex: %s", e)
        return True

if __name__ == "__main__":
    # 勿在此调用 shcore.SetProcessDpiAwareness：会与 Qt6 默认的 PerMonitor V2 冲突，
    # 导致 SetProcessDpiAwarenessContext 失败（拒绝访问）及 qt.qpa.window 告警。

    # [1] 主实例已运行时：副进程只写 IPC 秒退，绝不弹 UAC（消灭右键多选风暴）
    # --reg-menu 跳过 mutex，否则主程序在跑时无法完成注册
    if "--reg-menu" not in sys.argv and not try_single_instance_mutex():
        if len(sys.argv) > 1:
            write_pending_paths(sys.argv[1:])
        sys.exit(0)

    # [2] 仅主进程首次启动（或 --reg-menu）才申请 Admin
    if not is_admin():
        if getattr(sys, 'frozen', False):
            params = subprocess.list2cmdline(sys.argv[1:])
        else:
            params = subprocess.list2cmdline(sys.argv)
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, BASE_DIR, 1)
        sys.exit(0)

    if "--reg-menu" in sys.argv:
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(True)
        app.setFont(QFont("Microsoft YaHei UI", 10))
        try:
            do_reg_menu()
            QMessageBox.information(None, "成功", "右键菜单已添加！")
        except Exception as e:
            QMessageBox.critical(None, "错误", str(e))
        sys.exit(0)
    try:
        os.makedirs(PENDING_PATHS_DIR, exist_ok=True)
        cleanup_pending_paths_old_files()
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = MainWindow()
    if len(sys.argv) > 1:
        argv_paths = [p for p in sys.argv[1:] if p and p != "--reg-menu" and is_path_exists_safe(p)]
        if len(argv_paths) >= 10:
            pd = QProgressDialog("正在添加路径...", None, 0, len(argv_paths), window)
            pd.setWindowModality(Qt.WindowModality.WindowModal)
            pd.setMinimumDuration(0)
            for i, p in enumerate(argv_paths):
                pd.setValue(i)
                pd.setLabelText(f"正在添加 ({i+1}/{len(argv_paths)})...")
                QApplication.processEvents()
                window.add_path(p)
            pd.setValue(len(argv_paths))
            pd.close()
        else:
            for p in argv_paths:
                window.add_path(p)
    process_pending_paths(window)
    # 常驻 IPC 轮询：主窗口运行期间持续接收右键/二次启动写入的路径
    window.ipc_timer = QTimer(window)
    window.ipc_timer.setInterval(500)
    window.ipc_timer.timeout.connect(lambda: process_pending_paths(window))
    window.ipc_timer.start()
    window.show()
    sys.exit(app.exec())