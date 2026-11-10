"""Thin ctypes layer: per-pixel-alpha window, DPI, tray icon, single-instance, autostart.
Only stdlib + the signed Python interpreter are involved, so Windows Smart App Control has nothing to block."""
import ctypes
import os
import sys
import winreg
from ctypes import wintypes as wt

from PIL import Image

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
HANDLE = ctypes.c_void_p

# ---- prototypes (64-bit safe) ----
user32.GetDC.argtypes = [HANDLE]; user32.GetDC.restype = HANDLE
user32.ReleaseDC.argtypes = [HANDLE, HANDLE]
user32.GetAncestor.argtypes = [HANDLE, wt.UINT]; user32.GetAncestor.restype = HANDLE
user32.GetWindowLongPtrW.argtypes = [HANDLE, ctypes.c_int]; user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [HANDLE, ctypes.c_int, ctypes.c_ssize_t]; user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [HANDLE, wt.UINT, wt.WPARAM, wt.LPARAM]; user32.DefWindowProcW.restype = LRESULT
user32.DestroyIcon.argtypes = [HANDLE]
user32.SetForegroundWindow.argtypes = [HANDLE]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
gdi32.CreateCompatibleDC.argtypes = [HANDLE]; gdi32.CreateCompatibleDC.restype = HANDLE
gdi32.SelectObject.argtypes = [HANDLE, HANDLE]; gdi32.SelectObject.restype = HANDLE
gdi32.DeleteObject.argtypes = [HANDLE]
gdi32.DeleteDC.argtypes = [HANDLE]
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]; kernel32.GetModuleHandleW.restype = HANDLE
kernel32.GetCurrentProcess.restype = HANDLE
kernel32.SetProcessWorkingSetSize.argtypes = [HANDLE, ctypes.c_size_t, ctypes.c_size_t]
kernel32.CreateMutexW.argtypes = [HANDLE, wt.BOOL, wt.LPCWSTR]; kernel32.CreateMutexW.restype = HANDLE
kernel32.CreateEventW.argtypes = [HANDLE, wt.BOOL, wt.BOOL, wt.LPCWSTR]; kernel32.CreateEventW.restype = HANDLE
kernel32.SetEvent.argtypes = [HANDLE]
kernel32.WaitForSingleObject.argtypes = [HANDLE, wt.DWORD]; kernel32.WaitForSingleObject.restype = wt.DWORD

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x80000
WS_EX_TOOLWINDOW = 0x80


# ------------------------------------------------------------------ DPI

def set_dpi_aware():
    try:
        user32.SetProcessDpiAwarenessContext(HANDLE(-4))          # per-monitor v2
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def get_dpi(hwnd):
    try:
        d = user32.GetDpiForWindow(HANDLE(hwnd))
        return d if d else 96
    except (AttributeError, OSError):
        return 96


# ------------------------------------------------------------------ layered window

def root_hwnd(tk_winfo_id):
    return user32.GetAncestor(HANDLE(tk_winfo_id), 2) or tk_winfo_id      # GA_ROOT


def make_layered(hwnd):
    ex = user32.GetWindowLongPtrW(HANDLE(hwnd), GWL_EXSTYLE)
    user32.SetWindowLongPtrW(HANDLE(hwnd), GWL_EXSTYLE, ex | WS_EX_LAYERED | WS_EX_TOOLWINDOW)


class _BMIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class _BLEND(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", wt.LONG), ("cy", wt.LONG)]


user32.UpdateLayeredWindow.argtypes = [HANDLE, HANDLE, ctypes.POINTER(wt.POINT), ctypes.POINTER(_SIZE), HANDLE,
                                       ctypes.POINTER(wt.POINT), wt.COLORREF, ctypes.POINTER(_BLEND), wt.DWORD]
user32.UpdateLayeredWindow.restype = wt.BOOL
gdi32.CreateDIBSection.argtypes = [HANDLE, ctypes.POINTER(_BMIH), wt.UINT, ctypes.POINTER(ctypes.c_void_p), HANDLE, wt.DWORD]
gdi32.CreateDIBSection.restype = HANDLE


def present(hwnd, img, x, y):
    """Show an RGBA PIL image (straight alpha) on a layered window at screen position (x, y)."""
    w, h = img.size
    r, g, b, a = img.convert("RGBa").split()                       # premultiplied alpha
    raw = Image.merge("RGBA", (b, g, r, a)).tobytes()              # BGRA, as Windows expects
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmi = _BMIH(ctypes.sizeof(_BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    ok = False
    try:
        if hbmp and bits.value:
            ctypes.memmove(bits, raw, len(raw))
            old = gdi32.SelectObject(mem, hbmp)
            sz, src, dst = _SIZE(w, h), wt.POINT(0, 0), wt.POINT(int(x), int(y))
            bf = _BLEND(0, 0, 255, 1)                              # AC_SRC_OVER, AC_SRC_ALPHA
            ok = bool(user32.UpdateLayeredWindow(HANDLE(hwnd), screen, ctypes.byref(dst), ctypes.byref(sz), mem,
                                                 ctypes.byref(src), 0, ctypes.byref(bf), 2))
            gdi32.SelectObject(mem, old)
    finally:
        if hbmp:
            gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)
    return ok


def trim_memory():
    try:
        kernel32.SetProcessWorkingSetSize(kernel32.GetCurrentProcess(), ctypes.c_size_t(-1), ctypes.c_size_t(-1))
    except Exception:
        pass


# ------------------------------------------------------------------ single instance

class SingleInstance:
    def __init__(self, name="LimitLamp"):
        self.mutex = kernel32.CreateMutexW(None, False, "Local\\%s.v2" % name)
        self.already = ctypes.get_last_error() == 183              # ERROR_ALREADY_EXISTS
        self.event = kernel32.CreateEventW(None, False, False, "Local\\%s.Show" % name)

    def signal_existing(self):
        kernel32.SetEvent(self.event)

    def poll_signal(self):
        return kernel32.WaitForSingleObject(self.event, 0) == 0


# ------------------------------------------------------------------ autostart

_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_on():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN) as k:
            winreg.QueryValueEx(k, "LimitLamp")
            return True
    except OSError:
        return False


def set_autostart(on):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                exe = sys.executable
                if exe.lower().endswith("python.exe"):
                    exe = exe[:-10] + "pythonw.exe"
                script = os.path.abspath(sys.argv[0])
                winreg.SetValueEx(k, "LimitLamp", 0, winreg.REG_SZ, '"%s" "%s"' % (exe, script))
            else:
                try:
                    winreg.DeleteValue(k, "LimitLamp")
                except FileNotFoundError:
                    pass
    except OSError:
        pass


# ------------------------------------------------------------------ tray icon

WM_APP_TRAY = 0x8001
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 1, 2, 4, 0x10
NIIF_WARNING = 2


class _NID(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", HANDLE), ("uID", wt.UINT), ("uFlags", wt.UINT),
                ("uCallbackMessage", wt.UINT), ("hIcon", HANDLE), ("szTip", wt.WCHAR * 128),
                ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
                ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", HANDLE)]


_WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HANDLE, wt.UINT, wt.WPARAM, wt.LPARAM)


class _WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", _WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", HANDLE), ("hIcon", HANDLE), ("hCursor", HANDLE), ("hbrBackground", HANDLE),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASS)]
user32.RegisterClassW.restype = wt.ATOM
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, HANDLE, HANDLE, HANDLE, HANDLE]
user32.CreateWindowExW.restype = HANDLE
user32.LoadImageW.argtypes = [HANDLE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.LoadImageW.restype = HANDLE
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(_NID)]
shell32.Shell_NotifyIconW.restype = wt.BOOL


class Tray:
    """Notification-area icon. Callbacks run inside Tk's message loop (same thread)."""

    def __init__(self, on_left, on_right, ico_path):
        self.on_left, self.on_right, self.ico_path = on_left, on_right, ico_path
        self.hicon = None
        self.ok = False
        try:
            hinst = kernel32.GetModuleHandleW(None)
            self._proc = _WNDPROC(self._wndproc)                   # keep a reference
            wc = _WNDCLASS()
            wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = self._proc, hinst, "LimitLampTray"
            user32.RegisterClassW(ctypes.byref(wc))
            self.hwnd = user32.CreateWindowExW(0, "LimitLampTray", "LimitLampTray", 0, 0, 0, 0, 0, HANDLE(-3), None, hinst, None)
            self.ok = bool(self.hwnd)
        except Exception:
            self.ok = False

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_APP_TRAY:
            ev = lparam & 0xFFFF
            try:
                if ev == 0x202:                                    # WM_LBUTTONUP
                    self.on_left()
                elif ev in (0x205, 0x7B):                          # WM_RBUTTONUP / WM_CONTEXTMENU
                    self.on_right()
            except Exception:
                pass
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _nid(self, flags):
        n = _NID()
        n.cbSize = ctypes.sizeof(_NID)
        n.hWnd, n.uID, n.uFlags, n.uCallbackMessage = self.hwnd, 1, flags, WM_APP_TRAY
        return n

    def _load_icon(self, pil_img):
        pil_img.save(self.ico_path, format="ICO", sizes=[(32, 32)])
        h = user32.LoadImageW(None, self.ico_path, 1, 0, 0, 0x10 | 0x40)         # IMAGE_ICON, LR_LOADFROMFILE|LR_DEFAULTSIZE
        old, self.hicon = self.hicon, h
        return h, old

    def set(self, pil_img, tip):
        if not self.ok:
            return
        try:
            h, old = self._load_icon(pil_img)
            n = self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
            n.hIcon = h
            n.szTip = tip[:127]
            first = not getattr(self, "_added", False)
            self.last_ok = bool(shell32.Shell_NotifyIconW(NIM_ADD if first else NIM_MODIFY, ctypes.byref(n)))
            self._added = True
            if old:
                user32.DestroyIcon(old)
        except Exception:
            pass

    def balloon(self, title, text):
        if not self.ok or not getattr(self, "_added", False):
            return
        n = self._nid(NIF_INFO)
        n.szInfoTitle, n.szInfo, n.dwInfoFlags = title[:63], text[:255], NIIF_WARNING
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(n))

    def remove(self):
        if self.ok and getattr(self, "_added", False):
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid(0)))
            self._added = False
        if self.hicon:
            user32.DestroyIcon(self.hicon)
            self.hicon = None


def cursor_pos():
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def foreground(hwnd):
    user32.SetForegroundWindow(HANDLE(hwnd))


# ------------------------------------------------------------------ screen helpers

user32.MonitorFromPoint.argtypes = [wt.POINT, wt.DWORD]
user32.MonitorFromPoint.restype = HANDLE


def on_some_monitor(x, y):
    return bool(user32.MonitorFromPoint(wt.POINT(int(x), int(y)), 0))      # MONITOR_DEFAULTTONULL


def work_area():
    r = wt.RECT()
    user32.SystemParametersInfoW(48, 0, ctypes.byref(r), 0)                  # SPI_GETWORKAREA
    return r.left, r.top, r.right, r.bottom
