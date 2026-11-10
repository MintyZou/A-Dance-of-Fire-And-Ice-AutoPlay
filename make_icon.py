"""从游戏 exe 里抽出《冰与火之舞》的图标，存成 assets/icon.ico + icon.png。

    python make_icon.py                 # 自动找 Steam 安装的游戏
    python make_icon.py "D:/.../A Dance of Fire and Ice.exe"

PyInstaller 打包时用 --icon=assets/icon.ico，程序里用 icon.png 做窗口图标。
"""
from __future__ import annotations

import ctypes
import re
import sys
from ctypes import wintypes
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
GAME_EXE = "A Dance of Fire and Ice.exe"

def find_game_exe() -> Path:
    """从 Steam 注册表 + libraryfolders.vdf 里找游戏 exe。"""
    import winreg
    roots: list[Path] = []
    for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam")):
        try:
            with winreg.OpenKey(hive, key) as k:
                for name in ("SteamPath", "InstallPath"):
                    try:
                        p = Path(winreg.QueryValueEx(k, name)[0])
                        if p.is_dir() and p not in roots:
                            roots.append(p)
                    except OSError:
                        pass
        except OSError:
            continue
    for steam in roots:
        libs = [steam]
        try:
            text = (steam / "steamapps" / "libraryfolders.vdf").read_text(
                encoding="utf-8", errors="ignore")
            for m in re.finditer(r'"path"\s*"([^"]+)"', text):
                lib = Path(m.group(1).replace("\\\\", "\\"))
                if lib.is_dir() and lib not in libs:
                    libs.append(lib)
        except OSError:
            pass
        for lib in libs:
            exe = lib / "steamapps" / "common" / "A Dance of Fire and Ice" / GAME_EXE
            if exe.is_file():
                return exe
    # 找不到就返回一个相对路径，让 main() 给出"请手动指定"的提示
    return Path(GAME_EXE)


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long),
                ("bmHeight", ctypes.c_long), ("bmWidthBytes", ctypes.c_long),
                ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                ("bmBits", ctypes.c_void_p)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
                ("hbmColor", wintypes.HBITMAP)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def hicon_to_image(hicon) -> Image.Image:
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                                wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
                                wintypes.UINT]
    info = ICONINFO()
    if not user32.GetIconInfo(hicon, ctypes.byref(info)):
        raise OSError("GetIconInfo 失败")
    try:
        bmp = BITMAP()
        gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(bmp), ctypes.byref(bmp))
        w, h = int(bmp.bmWidth), int(bmp.bmHeight)
        hdr = BITMAPINFOHEADER()
        hdr.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        hdr.biWidth = w
        hdr.biHeight = -h                  # 负数 = 上到下
        hdr.biPlanes = 1
        hdr.biBitCount = 32
        hdr.biCompression = 0              # BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        hdc = user32.GetDC(None)
        try:
            got = gdi32.GetDIBits(hdc, info.hbmColor, 0, h, buf,
                                  ctypes.byref(hdr), 0)
        finally:
            user32.ReleaseDC(None, hdc)
        if not got:
            raise OSError("GetDIBits 失败")
        return Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1)
    finally:
        gdi32.DeleteObject(info.hbmColor)
        gdi32.DeleteObject(info.hbmMask)
        user32.DestroyIcon(hicon)


def extract(exe: Path) -> list[Image.Image]:
    """按尺寸把图标组里的每一档都取出来（PrivateExtractIcons 会自动缩放）。"""
    user32 = ctypes.windll.user32
    user32.PrivateExtractIconsW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.UINT),
        wintypes.UINT, wintypes.UINT]
    out: list[Image.Image] = []
    for size in (256, 128, 64, 48, 32, 16):
        h = wintypes.HICON()
        n = user32.PrivateExtractIconsW(str(exe), 0, size, size,
                                        ctypes.byref(h), None, 1, 0)
        if not n or not h:
            continue
        try:
            out.append(hicon_to_image(h))
        except OSError:
            user32.DestroyIcon(h)
    if not out:
        raise OSError(f"这个 exe 里没有图标：{exe}")
    return out


def main() -> None:
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else find_game_exe()
    if not exe.is_file():
        raise SystemExit(
            f"找不到游戏 exe：{exe}\n"
            '请手动指定：python make_icon.py "<游戏目录>/A Dance of Fire and Ice.exe"')
    imgs = [im for im in extract(exe) if im.width >= 32]
    if not imgs:
        raise SystemExit("没能解出可用的图标")
    big = max(imgs, key=lambda i: i.width)

    # 带 alpha 的方形画布，居中放原图（部分尺寸是 32bpp 带蒙版的）
    def square(im: Image.Image) -> Image.Image:
        side = max(im.width, im.height)
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
        return canvas

    ASSETS.mkdir(parents=True, exist_ok=True)
    png = square(big)
    png.save(ASSETS / "icon.png")
    sizes = sorted({(s, s) for s in (16, 24, 32, 48, 64, 128, 256)
                    if s <= png.width} | {(png.width, png.height)})
    png.save(ASSETS / "icon.ico", sizes=sizes)
    print(f"已写出 {ASSETS/'icon.png'} ({png.width}x{png.height})")
    print(f"已写出 {ASSETS/'icon.ico'}  尺寸 {sizes}")


if __name__ == "__main__":
    main()
