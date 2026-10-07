"""Find the E.S. Agent chat window (an Edge app window) and bring it to the front."""
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
SW_RESTORE = 9
NAMES = ("E.S. Agent", "EsAgent")  # current and former window titles
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x2


def _title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def find_chat_window(include_minimized=True):
    """The chat window's title is "E.S. Agent", or "12:34 集中 – E.S. Agent" while the timer runs."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd) and (include_minimized or not user32.IsIconic(hwnd)) \
                and _class(hwnd) == "Chrome_WidgetWin_1":
            title = _title(hwnd)
            if title in NAMES or any(title.endswith("– " + n) for n in NAMES):
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(visit, 0)
    return found[0] if found else None


def chat_window_visible():
    """A matching chat app is open and not minimized, even when another app covers it."""
    return find_chat_window(include_minimized=False) is not None


def focus_chat_window():
    """Bring the chat window to the front. Returns False if it is not open."""
    hwnd = find_chat_window()
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    if user32.GetForegroundWindow() != hwnd:
        # Windows refuses focus changes from background processes. Share input with the current
        # foreground window's thread, and tap Alt, which both lift that lock.
        me = ctypes.windll.kernel32.GetCurrentThreadId()
        other = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        attached = other and other != me and user32.AttachThreadInput(me, other, True)
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        if attached:
            user32.AttachThreadInput(me, other, False)
    return True
