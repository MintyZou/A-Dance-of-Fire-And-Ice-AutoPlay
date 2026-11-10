"""Windows 按键注入：用 SendInput 发送硬件扫描码。

Unity 游戏对 PostMessage / 普通虚拟键事件有时不响应，扫描码方式最可靠。
只依赖标准库 ctypes，无第三方依赖。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
MAPVK_VK_TO_VSC = 0

_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", _PTR)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", _PTR)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]


_u = ctypes.WinDLL("user32", use_last_error=True)
_u.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
_u.SendInput.restype = wintypes.UINT
_u.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
_u.MapVirtualKeyW.restype = wintypes.UINT
_u.GetAsyncKeyState.argtypes = (ctypes.c_int,)
_u.GetAsyncKeyState.restype = ctypes.c_short
_u.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
_u.GetCursorPos.restype = wintypes.BOOL

# Windows 默认定时器粒度约 15.6ms，会让 time.sleep 醒得太晚；
# 提升到 1ms 后，调度器的粗睡才能压到毫秒级误差。
_winmm = ctypes.WinDLL("winmm", use_last_error=True)


def set_timer_resolution(ms: int = 1) -> None:
    _winmm.timeBeginPeriod(ms)


def restore_timer_resolution(ms: int = 1) -> None:
    _winmm.timeEndPeriod(ms)


VK = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09,
    "esc": 0x1B, "escape": 0x1B, "shift": 0x10, "ctrl": 0x11,
    "alt": 0x12, "backspace": 0x08,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
}
for _i, _ch in enumerate("abcdefghijklmnopqrstuvwxyz"):
    VK[_ch] = 0x41 + _i
for _i in range(10):
    VK[str(_i)] = 0x30 + _i
for _i in range(1, 13):
    VK[f"f{_i}"] = 0x6F + _i          # F1 = 0x70 ... F12 = 0x7B
_EXTENDED = {0x25, 0x26, 0x27, 0x28}


def to_vk(name: str) -> int:
    key = str(name).lower()
    if key not in VK:
        raise ValueError(f"不支持的按键 {name!r}")
    return VK[key]


def _send(vk: int, up: bool) -> None:
    scan = _u.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    if vk in _EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    inp = _INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki = _KEYBDINPUT(0, scan, flags, 0, 0)
    if _u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)) != 1:
        raise ctypes.WinError(ctypes.get_last_error())


def key_down(vk: int) -> None:
    _send(vk, False)


def key_up(vk: int) -> None:
    _send(vk, True)


def is_down(vk: int) -> bool:
    return bool(_u.GetAsyncKeyState(vk) & 0x8000)


def cursor_at_corner() -> bool:
    """鼠标移到屏幕左上角作为紧急停止（类似 pydirectinput 的 failsafe）。"""
    pt = wintypes.POINT()
    if not _u.GetCursorPos(ctypes.byref(pt)):
        return False
    return pt.x < 4 and pt.y < 4
