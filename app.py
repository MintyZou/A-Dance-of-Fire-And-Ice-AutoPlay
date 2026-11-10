"""冰与火之舞 · 自动演奏（封面选择器版）

  ← → / 滚轮 / 点击   切换封面（只切，不读谱）
  左键 / 回车         载入当前封面（读谱 + 算时间轴）
  F1 / F2             开始 / 停止（全局热键，切到游戏窗口后按）
  顶部按钮            在「社区谱（创意工坊）」与「官方谱（游戏内置）」间切换

启动时自动找到《冰与火之舞》的创意工坊目录（也可以写在 config.json 的
workshop_dir 里），用每张谱自己的 `previewImage` 当封面。
官方谱则从游戏安装目录的 resources.assets 里提取，缓存到程序旁的 official/。
"""
from __future__ import annotations

import ctypes
import json
import queue
import re
import sys
import threading
import time
import tkinter as tk
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog

from PIL import Image, ImageOps, ImageTk

import input_win
import official
import parse
import player
import timeline

APP_NAME = "冰与火之舞 · 自动演奏"
APP_VER = "0.20.1"
GAME_DIRNAME = "A Dance of Fire and Ice"
WORKSHOP_APPID = "977950"

BG = "#12141a"
PANEL = "#181b23"
ENTRY_BG = "#1e222c"
FG = "#e8eaf0"
DIM = "#8b90a0"
ACCENT = "#4ea1ff"
BORDER = "#2a2f3a"

COVER_MAX = 340          # 居中封面最大边长
COVER_MIN_SCALE = 0.44   # 最远端缩放
BASE_PX = 400            # 缓存用的基准图边长
CACHE_MAX = 96           # 缩放后的封面缓存条数

MIN_W, MIN_H = 640, 480

FONT = "Microsoft YaHei UI"
_TAG = re.compile(r"</?[a-zA-Z][^>]*>")

DEFAULT_CFG = {
    "level": "",
    "key": "space",
    "start_hotkey": "f1",
    "stop_hotkey": "f2",
    "offset_ms": 0.0,
    "speed_scale": 1.0,
    "lead_in_s": 0.0,
    "press_s": 0.02,
    "pause_taps": 0,
    "chart_source": "community",
}

HELP_TEXT = """【这是什么】

《冰与火之舞》(A Dance of Fire and Ice) 的单机自动演奏器。
它读取 .adofai 谱面，按谱面的击打时间轴把按键送进游戏，
用来自娱自乐地"看谱面本身"。谱面有两个来源，用程序顶部按钮切换：

  * 社区谱：Steam 创意工坊里下载好的谱面。
  * 官方谱：游戏内置关卡，从本机游戏安装目录自动提取。

它不读内存、不改游戏文件、不联网、不注入 DLL —— 只是模拟键盘。
官方谱只是「读取」本机游戏文件并缓存到程序旁边，不会写回游戏。


【第一次使用】

1. 想打社区谱：先在 Steam 订阅并下载好谱面（创意工坊）。
   想打官方谱：确保已安装《冰与火之舞》，程序会自动提取内置关卡。
2. 打开本程序，用顶部按钮在「社区谱（创意工坊）」和
   「官方谱（游戏内置）」之间切换。首次切到官方谱会扫描游戏目录的
   resources.assets 提取谱面（约几秒），结果缓存在程序旁的 official/
   文件夹，之后再切就直接读缓存。注意：部分官方 X 关（1-X、2-X、8-X
   等）本机拿不到谱面，暂不可用；详见文末【已知限制】。
3. 社区谱会自动找 .../steamapps/workshop/content/977950/ 下的谱面；
   没找到就点「选择 .adofai」手动选一个 main.adofai。
4. 用 ← → 或滚轮把想打的封面移到正中间。
5. 左键点一下封面（或按回车）载入 —— 这时候才会读谱、算时间轴。
   状态栏变成「待命 · F1 开始」才算准备好。
6. 在游戏里选好同一张谱、进入关卡，等倒计时/加载结束。
7. 让游戏窗口保持前台，然后按 F1。


【怎么开始演奏（重点，很多人卡在这里）】

核心规则：按下 F1 的那一瞬间，就是本工具发出的第一次击打。
本工具不会等游戏里的音乐或行星，它把"你按 F1 的时刻"当作时间零点。

所以正确做法是：

  A. 先把游戏窗口点一下、确保它有焦点；鼠标别放在屏幕左上角
     （那是急停区）。
  B. 盯着游戏，等倒数结束、行星第一次落到第一块砖的那一刻 ——
     也就是"正常玩的时候该按第一下"的那一瞬间 —— 按下 F1。
  C. 按一下就松手，不要提前按住、不要长按。
  D. 之后本工具会自己按完整首，窗口可以留在后台。

如果整首都偏早或偏晚，那不是谱面算错了，而是 F1 按的时机不对：
  * 按下 F1 早了 -> 整首提前，下次晚一点按 F1，或者把 offset(ms) 调大
  * 按下 F1 晚了 -> 整首拖后，下次早一点按 F1，或者把 offset(ms) 调小

想快速对齐：把「只打前N块」填 20，反复按 F1 试几秒，
听游戏判定是"太早"还是"太晚"，比整首重跑省事得多。

另外，「空跑」只跑调度、不真的发按键，用来看日志里的抖动统计，
这一点很重要：空跑不能用来对齐游戏（不会有任何按键进游戏）。


【按键】

  顶部按钮      切换「社区谱」/「官方谱」两个谱源
  ← →          上一张 / 下一张封面
  Home / End   第一张 / 最后一张
  滚轮         上下滚动切换
  左键点封面   选中并载入
  回车         载入当前居中的封面
  F1           开始演奏（全局热键）
  F2           停止演奏（全局热键）
  鼠标左上角   急停（和 F2 效果一样）

F1/F2 是全局物理按键检测，不需要本窗口有焦点。F2 由独立线程
每 1ms 轮询一次，短按也能停下来；万一还是不灵，把鼠标甩到
屏幕左上角即可中止。停止后这一轮就结束了，想重跑要重新按 F1。


【参数说明】

  按键        送进游戏的键，默认 space。改完记得游戏里也用同一个键。
  offset(ms)  整条击打时间轴整体平移：偏晚填负数、偏早填正数。
  速度倍率    练习用：0.5 = 慢放一半（游戏里也要相应减速才行）。
  只打前N块   只演奏前 N 次击打，用来快速验证某一段 / 对齐 F1 时机。
  空跑        只跑调度、不发按键，看日志里的"平均/最大误差"。

  注意 offset(ms) 只影响脚本内部的按键时刻，游戏设置里的
  Input Offset 已经由"按 F1 的时机"自动吸收，不要重复补偿。


【已知限制】

  * 含 FreeRoam（自由移动）的段落需要方向键走位，脚本无法通过，
    载入时会在日志里提示。
  * 官方谱不是全都能打。非 X 关（如 1-1、2-3）全部可用；X 关里只有
    Files 型世界的正常版可用（3-X、5-X、AR-X、XS-X、XI-X、CI-X、
    CE-X、CE-TX、EMOMOMO 等）。Mixed 型世界（1/2/4/6/7/8/9/10/11/12 等）
    的 X 关，游戏是从预构建场景而不是 resources.assets 加载的，本机
    拿不到完整谱面，所以 1-X、2-X、4-X、6-X、7-X、8-X、9-X、10-X、
    11-X、12-X、B-X、ML-X、MN-X、RJ-X… 等暂不可自动演奏。
    想打这些关，需要外部 .adofai：创意工坊里的官方关副本，或「选择
    .adofai」手动载入（官方关卡本身不能在游戏编辑器里另存导出）。
  * 官方谱里的「【变体】」谱（1J-X ~ 6J-X、BJ-X，共 7 张）是愚人节 /
    加长 / 被诅咒版，与游戏内同名的正常关卡不是同一份谱：前半段相同、
    中途插入段落，实机会从插入点开始偏半拍。它们已排在列表末尾并标注，
    请勿当成正常关卡选；要打变体请在游戏里选对应的 J 关卡。
  * 官方谱提取自本机游戏目录的 resources.assets，没有封面图，程序会
    按曲名生成占位封面。
  * 长按砖（Hold）的松手时机、带 3-2-1 倒数的 Pause 段，
    极端谱面下可能需要微调 offset。


【免责声明】

1. 本工具仅供单人、离线、自娱自乐地观赏谱面使用。
   请勿用于排行榜刷分、联机对战、比赛或任何形式的成绩提交。
2. 本工具与《冰与火之舞》的开发商 7th Beat Games 无任何关系，
   未经其授权或认可。游戏名称、图标等版权归其所有。
3. 使用本工具可能违反游戏的用户协议或社区规则，由此导致的
   存档异常、成绩失效、账号处罚等后果由使用者自行承担。
4. 本工具按"现状"提供，不附带任何明示或暗示的担保；作者不对
   使用本工具造成的任何直接或间接损失负责。
5. 若你不接受以上条款，请不要使用本程序。
6. 官方谱仅从使用者本机的游戏安装目录读取并缓存在本地；本程序与
   本仓库不内置、不分发任何官方谱面或音频文件。
"""


def app_dir() -> Path:
    # PyInstaller --onefile 时 __file__ 指向临时解压目录，配置要放在 exe 旁边
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource(rel: str) -> Path:
    # 打包后资源在 _MEIPASS，源码运行时在项目目录
    return Path(getattr(sys, "_MEIPASS", app_dir())) / rel


ROOT = app_dir()
CONFIG = ROOT / "config.json"


# --------------------------------------------------------------- 路径探测
def _steam_roots() -> list[Path]:
    """从注册表拿 Steam 安装目录（拿不到就返回空）。"""
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
    return roots


def _library_dirs(steam: Path) -> list[Path]:
    """解析 steamapps/libraryfolders.vdf 里的所有库目录。"""
    out = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    try:
        text = vdf.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    for m in re.finditer(r'"path"\s*"([^"]+)"', text):
        p = Path(m.group(1).replace("\\\\", "\\"))
        if p.is_dir() and p not in out:
            out.append(p)
    return out


def find_workshop_dir() -> Path | None:
    """找创意工坊里本游戏的内容目录；找不到返回 None。"""
    for steam in _steam_roots():
        for lib in _library_dirs(steam):
            ws = lib / "steamapps" / "workshop" / "content" / WORKSHOP_APPID
            if ws.is_dir() and any(ws.glob("*/main.adofai")):
                return ws
    return None


def find_game_data_dir() -> Path | None:
    """找游戏安装目录里含 resources.assets 的 *_Data 文件夹。"""
    data_name = f"{GAME_DIRNAME}_Data"
    for steam in _steam_roots():
        for lib in _library_dirs(steam):
            d = lib / "steamapps" / "common" / GAME_DIRNAME / data_name
            if (d / "resources.assets").is_file():
                return d
    return None


# --------------------------------------------------------------- 谱面扫描
@dataclass
class Chart:
    path: Path
    title: str
    artist: str
    bpm: float
    tiles: int
    cover: Path | None
    source: str = "workshop"
    variant: bool = False
    song_filename: str = ""

    @property
    def folder(self) -> str:
        return self.path.parent.name


def _norm(p) -> str:
    """路径比较用（不同来源的正/反斜杠、大小写都算同一张）。"""
    try:
        return str(Path(p).resolve()).lower()
    except Exception:
        return str(p).lower()


def clean_title(s: str) -> str:
    t = _TAG.sub("", s or "")
    return re.sub(r"\s+", " ", t).strip()


def _natural_key(s: str):
    """自然排序：'2-X' < '10-5'；纯字母标题排在数字后面。"""
    return tuple((0, int(t)) if t.isdigit() else (1, t.lower())
                 for t in re.split(r"(\d+)", s))


def _find_cover(main: Path, preview: str) -> Path | None:
    folder = main.parent
    if preview:
        p = folder / preview
        if p.is_file():
            return p
    for name in ("cover.jpg", "cover.png", "Cover.jpg", "cover.jpeg"):
        p = folder / name
        if p.is_file():
            return p
    best, best_size = None, 0
    try:
        for p in folder.iterdir():
            if p.suffix.lower() in (".png", ".jpg", ".jpeg") and p.is_file():
                n = p.stat().st_size
                if n > best_size:
                    best, best_size = p, n
    except OSError:
        pass
    return best


def scan_charts(root: Path, source: str = "workshop") -> list[Chart]:
    """扫创意工坊目录，读每张谱自己的 song/artist/bpm/previewImage。"""
    out: list[Chart] = []
    if not root.is_dir():
        return out
    for main in sorted(root.glob("*/main.adofai")):
        try:
            data = parse.load(main)
        except Exception:
            continue
        s = data.get("settings", {})
        title = clean_title(str(s.get("song") or "")) or clean_title(main.parent.name)
        tiles = len(data.get("angleData") or data.get("pathData") or "")
        out.append(Chart(
            path=main,
            title=title or main.parent.name,
            artist=clean_title(str(s.get("artist") or "")),
            bpm=float(s.get("bpm", 0) or 0),
            tiles=tiles,
            cover=_find_cover(main, str(s.get("previewImage") or "")),
            source=source,
        ))
    out.sort(key=lambda c: c.title.lower())
    return out


def dark_titlebar(win: tk.Tk) -> None:
    """让标题栏跟随系统深色模式（Win10 1809+）。"""
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        value = ctypes.c_int(1)
        for attr in (20, 19):          # DWMWA_USE_IMMERSIVE_DARK_MODE
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


def open_help(parent: tk.Misc) -> tuple[tk.Toplevel, tk.Text]:
    """打开可滚动的说明 / 免责声明窗口，返回 (窗口, 文本控件)。"""
    win = tk.Toplevel(parent)
    win.title("使用说明 / 免责声明")
    win.geometry("760x600")
    win.minsize(420, 320)
    win.configure(bg=BG)
    try:
        win.iconbitmap(default=str(resource("assets/icon.ico")))
    except Exception:
        pass

    foot = tk.Frame(win, bg=BG)
    foot.pack(side="bottom", fill="x", padx=12, pady=(0, 10))
    tk.Button(foot, text="关闭", command=win.destroy, bg=ENTRY_BG, fg=FG,
              relief="flat", padx=14, bd=0).pack(side="right")
    wrap = tk.Frame(win, bg=BG)
    wrap.pack(fill="both", expand=True, padx=12, pady=(10, 6))
    bar = tk.Scrollbar(wrap)
    bar.pack(side="right", fill="y")
    txt = tk.Text(wrap, wrap="word", bg=PANEL, fg=FG, relief="flat",
                  font=(FONT, 10), padx=12, pady=10, insertbackground=FG,
                  yscrollcommand=bar.set, highlightthickness=0)
    txt.pack(side="left", fill="both", expand=True)
    bar.configure(command=txt.yview)
    txt.insert("1.0", HELP_TEXT)
    txt.configure(state="disabled")
    dark_titlebar(win)
    win.lift()
    return win, txt


class CoverFlow(tk.Canvas):
    """横向封面流：居中最大，两侧按距离缩小平移，切换时缓动。

    为了帧率：canvas item 复用（不 delete/create）、缩放后的 PhotoImage 走
    LRU 缓存、尺寸量化到 6px，避免每帧都做一次 PIL 缩放 + Tk 图片上传。
    """

    def __init__(self, master, charts: list[Chart], on_change, on_click):
        super().__init__(master, bg=BG, highlightthickness=0, bd=0, height=140)
        self.charts = charts
        self.on_change = on_change
        self.on_click = on_click
        self.target = 0.0
        self.cur = 0.0
        self.loaded = -1          # 已载入的封面下标（画蓝框）
        self._base: dict[int, Image.Image] = {}
        self._cache: OrderedDict[tuple[int, int], ImageTk.PhotoImage] = OrderedDict()
        self._items: dict[int, dict] = {}
        self._queue: list[int] = []
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._on_click)
        self.bind("<MouseWheel>", self._on_wheel)

    # ---------------------------------------------------------------- 数据
    def _base_image(self, i: int) -> Image.Image:
        img = self._base.get(i)
        if img is not None:
            return img
        src = None
        cover = self.charts[i].cover
        if cover is not None:
            try:
                src = Image.open(cover)
                src.load()
            except Exception:
                src = None
        if src is None:
            src = Image.new("RGB", (BASE_PX, BASE_PX), (52, 56, 68))
        else:
            if src.mode in ("RGBA", "LA", "P"):
                rgba = src.convert("RGBA")
                bg = Image.new("RGBA", rgba.size, (18, 20, 26, 255))
                src = Image.alpha_composite(bg, rgba).convert("RGB")
            else:
                src = src.convert("RGB")
            src = ImageOps.fit(src, (BASE_PX, BASE_PX), Image.LANCZOS)
        self._base[i] = src
        return src

    def _photo(self, i: int, size: int) -> ImageTk.PhotoImage:
        q = max(24, int(round(size / 6.0)) * 6)
        key = (i, q)
        ph = self._cache.get(key)
        if ph is not None:
            self._cache.move_to_end(key)
            return ph
        ph = ImageTk.PhotoImage(self._base_image(i).resize((q, q), Image.BILINEAR))
        self._cache[key] = ph
        if len(self._cache) > CACHE_MAX:
            self._cache.popitem(last=False)
        return ph

    def preload(self, center: int, radius: int = 4) -> None:
        """后台把附近的封面解码好，避免滚到新封面时卡一下。"""
        lo = max(0, center - radius)
        hi = min(len(self.charts) - 1, center + radius)
        self._queue = [i for i in range(lo, hi + 1) if i not in self._base]
        self._pump()

    def _pump(self) -> None:
        if not self._queue:
            return
        if abs(self.target - self.cur) > 0.01:      # 动画中先别抢时间
            self.after(40, self._pump)
            return
        i = self._queue.pop(0)
        try:
            self._base_image(i)
        except Exception:
            pass
        self.after(12, self._pump)

    def set_charts(self, charts: list[Chart]) -> None:
        """换一整套谱面（切换谱源时清空封面缓存与 canvas item）。"""
        self.charts = charts
        self._base.clear()
        self._cache.clear()
        self._queue.clear()
        for it in self._items.values():
            for item in it.values():
                if item:
                    self.delete(item)
        self._items.clear()
        self.cur = self.target = 0.0
        self.loaded = -1
        self._draw()
        if charts:
            self.preload(0, 5)

    # ---------------------------------------------------------------- 交互
    @property
    def index(self) -> int:
        return int(round(self.target))

    def move(self, delta: int) -> None:
        self.select(self.index + delta)

    def select(self, i: int) -> None:
        if not self.charts:
            return
        i = max(0, min(len(self.charts) - 1, i))
        if i == self.index:
            return
        self.target = float(i)
        self.on_change(i)

    def _on_click(self, e) -> None:
        if not self.charts:
            return
        _w, _h, _box, gap = self._metrics()
        d = (e.x - self.winfo_width() / 2) / gap
        i = max(0, min(len(self.charts) - 1, int(round(self.cur + d))))
        if i != self.index:
            self.target = float(i)
            self.on_change(i)
        self.on_click(i)          # 左键 = 载入

    def _on_wheel(self, e) -> None:
        self.move(-1 if e.delta > 0 else 1)

    # ---------------------------------------------------------------- 绘制
    def start(self) -> None:
        self.after(16, self._tick)

    def _tick(self) -> None:
        if abs(self.target - self.cur) > 0.004:
            self.cur += (self.target - self.cur) * 0.3
            self._draw()
        elif self.cur != self.target:
            self.cur = self.target
            self._draw()
        self.after(16, self._tick)

    def _metrics(self) -> tuple[int, int, int, float]:
        w = max(self.winfo_width(), 1)
        h = max(self.winfo_height(), 1)
        box = int(max(96, min(w * 0.30, h * 0.68, COVER_MAX)))
        return w, h, box, box * 0.72

    def _draw(self) -> None:
        w, h, box, gap = self._metrics()
        if w < 60 or h < 60:
            return
        if not self.charts:
            self.delete("all")
            self._items.clear()
            self.create_text(w / 2, h / 2, text="没找到谱面，用「选择 .adofai」手动选",
                             fill=DIM, font=(FONT, 11))
            return
        cx, cy = w / 2, h / 2 - box * 0.03
        span = int(box / gap) + 2
        lo = max(0, int(self.cur) - span)
        hi = min(len(self.charts) - 1, int(self.cur) + span + 1)

        for i in list(self._items):
            if i < lo or i > hi:
                for item in self._items.pop(i).values():
                    if item:
                        self.delete(item)

        cap_font = (FONT, max(8, min(11, int(box * 0.034))))
        near_i = int(round(self.cur))
        for i in range(lo, hi + 1):
            d = i - self.cur
            ad = abs(d)
            scale = max(COVER_MIN_SCALE, 1.0 - 0.19 * ad)
            size = int(box * scale)
            x = cx + d * gap
            y = cy + min(ad, 3) * box * 0.03
            if x < -size or x > w + size:
                continue
            photo = self._photo(i, size)
            near = ad < 0.5
            armed = i == self.loaded
            outline = ACCENT if (near or armed) else BORDER

            it = self._items.get(i)
            if it is None:
                it = {
                    "rect": self.create_rectangle(0, 0, 0, 0, outline=BORDER, width=1),
                    "img": self.create_image(0, 0),
                    "text": self.create_text(0, 0, text=self.charts[i].title[:20],
                                             fill=DIM, font=cap_font,
                                             width=max(60, int(gap) - 14)),
                }
                self._items[i] = it
            self.coords(it["rect"], x - size / 2 - 2, y - size / 2 - 2,
                        x + size / 2 + 2, y + size / 2 + 2)
            self.itemconfig(it["rect"], outline=outline, width=2 if armed else 1)
            self.coords(it["img"], x, y)
            self.itemconfig(it["img"], image=photo)
            self.coords(it["text"], x, y + size / 2 + max(12, box * 0.05))
            self.itemconfig(it["text"], state="hidden" if near else "normal",
                            fill=ACCENT if armed else DIM)

        # 由远到近依次抬升，居中的封面最后抬起 = 在最上层
        for i in sorted(range(lo, hi + 1),
                        key=lambda k: abs(k - self.cur), reverse=True):
            it = self._items.get(i)
            if it:
                for key in ("rect", "img", "text"):
                    self.tag_raise(it[key])


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VER}")
        self.root.geometry("1100x690")
        self.root.minsize(MIN_W, MIN_H)
        self.root.configure(bg=BG)
        self._set_icon()

        try:
            self.cfg = dict(DEFAULT_CFG, **json.loads(CONFIG.read_text(encoding="utf-8")))
        except Exception:
            self.cfg = dict(DEFAULT_CFG)

        self.tl: timeline.Timeline | None = None
        self._data: dict | None = None
        self.events: list[player.Event] = []
        self.game_vk = input_win.to_vk(self.cfg.get("key", "space"))
        self.q: queue.Queue = queue.Queue()
        self.abort = threading.Event()
        self._dry = False
        self.charts: list[Chart] = []
        self._loaded: str = ""
        self._narrow: bool | None = None

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(80, self._drain)
        self.root.after(60, lambda: dark_titlebar(self.root))
        self.root.bind("<Configure>", self._on_resize)

        self.ws = self._workshop_path()
        self.ws_charts = scan_charts(self.ws, "workshop") if self.ws else []
        self._official_cache = ROOT / "official"
        self.off_charts = self._charts_from_meta(official.load_cache(self._official_cache))
        self.source = self.cfg.get("chart_source", "community")
        if self.source not in ("community", "official"):
            self.source = "community"
        self._extracting = False
        self._official_attempted = False
        self._variant_noted = False
        self._apply_source(initial=True)
        self.flow.start()
        self.log_line("← → 选曲 · 左键/回车 载入 · F1 开始 · F2 停止 · 顶部切换社区谱/官方谱")

        threading.Thread(target=self._worker, daemon=True).start()

    def _set_icon(self) -> None:
        try:
            self.root.iconbitmap(default=str(resource("assets/icon.ico")))
        except Exception:
            pass

    def _workshop_path(self) -> Path | None:
        cfg_dir = str(self.cfg.get("workshop_dir") or "")
        if cfg_dir and Path(cfg_dir).is_dir():
            return Path(cfg_dir)
        return find_workshop_dir()

    # ------------------------------------------------------------- 谱源切换
    def _charts_from_meta(self, meta: list[dict]) -> list[Chart]:
        """把 official/meta.json 还原成 Chart 列表。"""
        out: list[Chart] = []
        for m in meta:
            path = self._official_cache / m["file"]
            cover = self._official_cache / m["cover"] if m.get("cover") else None
            out.append(Chart(
                path=path,
                title=m.get("title") or path.stem,
                artist=m.get("artist", ""),
                bpm=float(m.get("bpm", 0) or 0),
                tiles=int(m.get("tiles", 0) or 0),
                cover=cover if cover and cover.is_file() else None,
                source="official",
                variant=bool(m.get("variant", False)),
                song_filename=m.get("song_filename", ""),
            ))
        # 变体谱排到末尾，避免和正常关卡混在一起被误选
        out.sort(key=lambda c: (c.variant, _natural_key(c.title)))
        return out

    def _apply_source(self, initial: bool = False) -> None:
        """把当前谱源的谱面装进封面流，并恢复上次选中的那张。"""
        charts = self.off_charts if self.source == "official" else self.ws_charts
        self.charts = charts
        self.flow.set_charts(charts)
        self._update_source_buttons()
        self.src_count_var.set(f"共 {len(charts)} 张")
        if self.source == "official" and not self._variant_noted:
            nv = sum(1 for c in charts if c.variant)
            if nv:
                self._variant_noted = True
                self.log_line(
                    f"官方谱含 {nv} 张变体谱（愚人节/加长版，与游戏内同名关卡不同，"
                    "已排在列表末尾并标注；打正常关卡请勿选它们）")
        if charts:
            start = 0
            last = self.cfg.get("level", "")
            for k, c in enumerate(charts):
                if last and _norm(c.path) == _norm(last):
                    start = k
                    break
            self.flow.cur = self.flow.target = float(start)
            self.flow._draw()
            self.select(start)
            if initial and last and _norm(charts[start].path) == _norm(last):
                self.arm(start)
        else:
            # 谱源清空时一并清掉上一谱源已载入的时间轴，避免误按 F1 放错谱
            self.tl = None
            self.events = []
            self._loaded = ""
            self._set_text(self.info, "")
            self.title_var.set("")
            self.meta_var.set("")
            if self.source == "official" and not self._extracting:
                self._ensure_official()
            elif self.source == "community":
                self.log_line("没找到创意工坊目录，可用「选择 .adofai」手动选谱面")
                self.q.put(("state", "待命 · 选择谱面"))

    def set_source(self, source: str) -> None:
        if source not in ("community", "official") or source == self.source:
            return
        self.source = source
        self.cfg["chart_source"] = source
        self._save_cfg()
        self._apply_source()

    def _update_source_buttons(self) -> None:
        for src, btn in (("community", self.src_community_btn),
                         ("official", self.src_official_btn)):
            active = self.source == src
            btn.configure(bg=ACCENT if active else ENTRY_BG,
                          fg="#10131a" if active else FG)

    def _ensure_official(self) -> None:
        """首次切到官方谱时，从游戏目录后台提取并缓存。"""
        if self.off_charts or self._extracting or self._official_attempted:
            return
        self._official_attempted = True
        data_dir = find_game_data_dir()
        if data_dir is None:
            self.log_line("未检测到游戏安装目录（resources.assets），官方谱暂不可用")
            self.q.put(("state", "官方谱不可用 · 未找到游戏目录"))
            return
        self._extracting = True
        self.q.put(("state", "正在提取官方谱面 ..."))
        self.log_line("首次使用官方谱：正在从游戏安装目录提取（约几秒）...")
        threading.Thread(target=self._extract_worker, args=(data_dir,),
                         daemon=True).start()

    def _extract_worker(self, data_dir: Path) -> None:
        try:
            meta = official.extract(data_dir / "resources.assets", self._official_cache)
            charts = self._charts_from_meta(meta)
            msg = f"官方谱提取完成：{len(charts)} 张（缓存在 {self._official_cache}）"
        except Exception as e:
            charts = []
            msg = f"官方谱提取失败：{e}"
        # Tk 不是线程安全的：结果走队列，由主线程的 _drain 处理
        self.q.put(("official_ready", (charts, msg)))

    def _on_official_ready(self, charts: list[Chart], msg: str) -> None:
        self._extracting = False
        self.off_charts = charts
        self.log_line(msg)
        if self.source == "official":
            self._apply_source()
            if not charts:
                self.q.put(("state", "官方谱不可用 · 见日志"))
        else:
            self._update_source_buttons()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        f = tk.Frame(self.root, bg=BG, padx=10, pady=8)
        f.pack(fill="both", expand=True)

        bottom = tk.Frame(f, bg=BG)
        bottom.pack(side="bottom", fill="x")

        topbar = tk.Frame(f, bg=BG)
        topbar.pack(side="top", fill="x", pady=(0, 6))
        tk.Label(topbar, text="谱源", bg=BG, fg=DIM, font=(FONT, 9)).pack(
            side="left", padx=(0, 8))
        btn_kw = dict(relief="flat", padx=12, font=(FONT, 9), bd=0)
        self.src_community_btn = tk.Button(
            topbar, text="社区谱（创意工坊）",
            command=lambda: self.set_source("community"),
            bg=ENTRY_BG, fg=FG, activebackground="#2a2f3a", **btn_kw)
        self.src_community_btn.pack(side="left", padx=(0, 6))
        self.src_official_btn = tk.Button(
            topbar, text="官方谱（游戏内置）",
            command=lambda: self.set_source("official"),
            bg=ENTRY_BG, fg=FG, activebackground="#2a2f3a", **btn_kw)
        self.src_official_btn.pack(side="left")
        self.src_count_var = tk.StringVar(value="")
        tk.Label(topbar, textvariable=self.src_count_var, bg=BG, fg=DIM,
                 font=(FONT, 9)).pack(side="right")

        self.flow = CoverFlow(f, [], self.select, self.arm)
        self.flow.pack(side="top", fill="both", expand=True)

        self.title_var = tk.StringVar(value="")
        self.meta_var = tk.StringVar(value="")
        tk.Label(bottom, textvariable=self.title_var, bg=BG, fg=FG, font=(FONT, 14, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(bottom, textvariable=self.meta_var, bg=BG, fg=DIM, font=(FONT, 9),
                 anchor="w").pack(fill="x")

        self.info = tk.Text(bottom, height=2, state="disabled", bg=PANEL, fg=FG,
                            relief="flat", font=(FONT, 9), padx=8, pady=5,
                            highlightthickness=0, wrap="word")
        self.info.pack(fill="x", pady=(4, 6))

        self.params = tk.Frame(bottom, bg=BG)
        self.params.pack(fill="x")
        self.key_var = tk.StringVar(value=self.cfg.get("key", "space"))
        self.offset_var = tk.StringVar(value=str(self.cfg.get("offset_ms", 0)))
        self.scale_var = tk.StringVar(value=str(self.cfg.get("speed_scale", 1.0)))
        self.limit_var = tk.StringVar(value="")
        self._cells = []
        for name, var in (("按键", self.key_var), ("offset(ms)", self.offset_var),
                          ("速度倍率", self.scale_var), ("只打前N块", self.limit_var)):
            lab = tk.Label(self.params, text=name, bg=BG, fg=DIM, font=(FONT, 9))
            ent = tk.Entry(self.params, textvariable=var, width=7, bg=ENTRY_BG, fg=FG,
                           insertbackground=FG, relief="flat", font=(FONT, 9))
            self._cells.append((lab, ent))
        self.dry_var = tk.BooleanVar(value=False)
        self.dry_btn = tk.Checkbutton(self.params, text="空跑", variable=self.dry_var,
                                      command=self._sync_dry, bg=BG, fg=DIM,
                                      selectcolor=ENTRY_BG, activebackground=BG,
                                      activeforeground=FG, font=(FONT, 9),
                                      highlightthickness=0, bd=0)
        self.browse_btn = tk.Button(self.params, text="选择 .adofai", command=self.choose_file,
                                    bg=ENTRY_BG, fg=FG, relief="flat", padx=10,
                                    font=(FONT, 9), activebackground="#2a2f3a",
                                    activeforeground=FG, bd=0)
        self.help_btn = tk.Button(self.params, text="使用说明 / 免责声明", command=self.show_help,
                                  bg=ENTRY_BG, fg=ACCENT, relief="flat", padx=10,
                                  font=(FONT, 9), activebackground="#2a2f3a",
                                  activeforeground=ACCENT, bd=0)
        for v in (self.key_var, self.offset_var, self.scale_var, self.limit_var):
            v.trace_add("write", lambda *_: self.rebuild())

        self.state_var = tk.StringVar(value="待命 · 左键选中的封面载入")
        tk.Label(bottom, textvariable=self.state_var, bg=BG, fg=ACCENT,
                 font=(FONT, 9)).pack(anchor="w", pady=(6, 0))

        self.log = tk.Text(bottom, height=3, state="disabled", bg=PANEL, fg=DIM,
                           relief="flat", font=(FONT, 9), padx=8, pady=4,
                           highlightthickness=0)
        self.log.pack(fill="x", pady=(4, 0))

        self._reflow(1100)
        for seq, fn in (("<Left>", lambda e: self._on_arrow(-1)),
                        ("<Right>", lambda e: self._on_arrow(1)),
                        ("<Home>", lambda e: self._on_arrow(-10 ** 6)),
                        ("<End>", lambda e: self._on_arrow(10 ** 6)),
                        ("<Return>", lambda e: self._on_enter()),
                        ("<KP_Enter>", lambda e: self._on_enter()),
                        ("<F1>", lambda e: None)):
            self.root.bind(seq, fn)

    def _reflow(self, width: int) -> None:
        """窄窗口把参数排成两行，宽窗口一行排开。"""
        narrow = width < 900
        if narrow == self._narrow:
            return
        self._narrow = narrow
        per_row = 2 if narrow else 4
        for idx, (lab, ent) in enumerate(self._cells):
            r, c = divmod(idx, per_row)
            lab.grid(row=r, column=c * 2, padx=(0, 4), pady=2, sticky="w")
            ent.grid(row=r, column=c * 2 + 1, padx=(0, 14), pady=2, sticky="w")
        rows = (len(self._cells) + per_row - 1) // per_row
        base_col = per_row * 2
        self.dry_btn.grid(row=0, column=base_col, padx=(0, 8), pady=2, sticky="w")
        self.browse_btn.grid(row=0, column=base_col + 1, padx=(0, 6), pady=2, sticky="w")
        self.help_btn.grid(row=0, column=base_col + 2, pady=2, sticky="w")
        if narrow:      # 窄的时候按钮放到参数下面一行
            self.dry_btn.grid(row=rows, column=0, padx=(0, 8), pady=(4, 2), sticky="w")
            self.browse_btn.grid(row=rows, column=1, padx=(0, 6), pady=(4, 2), sticky="w")
            self.help_btn.grid(row=rows, column=2, padx=(0, 6), pady=(4, 2), sticky="w")

    def _on_resize(self, e) -> None:
        if e.widget is self.root:
            self._reflow(e.width)

    def _on_arrow(self, delta: int) -> None:
        """方向键切封面；输入框里按方向键时不动（否则光标和选曲会一起变）。"""
        if self._typing():
            return
        if abs(delta) > 1000:
            self.flow.select(0 if delta < 0 else len(self.charts) - 1)
        else:
            self.flow.move(delta)

    def _on_enter(self) -> None:
        if not self._typing():
            self.arm(self.flow.index)

    def _typing(self) -> bool:
        w = self.root.focus_get()
        return w is not None and w.winfo_class() == "Entry"

    # ----------------------------------------------------------------- 说明
    def show_help(self) -> None:
        open_help(self.root)

    @staticmethod
    def _set_text(w: tk.Text, s: str) -> None:
        w.configure(state="normal")
        w.delete("1.0", "end")
        w.insert("1.0", s)
        w.configure(state="disabled")

    def _append_log(self, s: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", s + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def log_line(self, s: str) -> None:
        self.q.put(("log", s))

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "state":
                    self.state_var.set(payload)
                elif kind == "official_ready":
                    charts, msg = payload
                    self._on_official_ready(charts, msg)
                else:
                    self._append_log(payload)
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    def _sync_dry(self) -> None:
        self._dry = self.dry_var.get()

    # ------------------------------------------------------------- 谱面/参数
    def choose_file(self) -> None:
        initial = str(self.cfg.get("level", ""))
        initdir = str(Path(initial).parent) if initial and Path(initial).parent.exists() else (
            str(self.ws) if self.ws else None)
        kwargs = {"title": "选择 .adofai 谱面",
                  "filetypes": [("ADOFAI 谱面", "*.adofai"), ("所有文件", "*.*")]}
        if initdir:
            kwargs["initialdir"] = initdir
        path = filedialog.askopenfilename(**kwargs)
        if path:
            self.title_var.set(Path(path).parent.name)
            self.meta_var.set(str(path))
            self.load_level(Path(path))

    def select(self, i: int) -> None:
        """切换封面：只更新标题/基础信息，不读谱（所以滚动不卡）。"""
        if not self.charts:
            return
        c = self.charts[i]
        self.flow.preload(i, 4)
        self.title_var.set(c.title)
        src = "官方内置" if c.source == "official" else f"工坊 {c.folder}"
        if c.variant:
            src += " · 变体谱（与游戏内同名关卡不同）"
        if c.song_filename:
            src += f" · {c.song_filename}"
        self.meta_var.set(f"{c.artist or '—'}   ·   {c.bpm:g} BPM   ·   "
                          f"{c.tiles} 砖   ·   {src}")
        if not self._loaded or _norm(c.path) != _norm(self._loaded):
            # 还没载入这张：清掉上一张的时间轴，避免误按 F1 放错谱
            self.tl = None
            self.events = []
            self._set_text(self.info, "")
            self.state_var.set("未载入 · 左键或回车载入")

    def arm(self, i: int) -> None:
        """左键/回车：真正读谱并算时间轴。"""
        if not self.charts:
            return
        c = self.charts[i]
        self.flow.loaded = i
        self.flow._draw()
        if not self._loaded or _norm(c.path) != _norm(self._loaded):
            self.load_level(c.path)

    def load_level(self, path: Path) -> None:
        try:
            data = parse.load(path)
            self._data = data
            self.tl = timeline.build(data, pause_taps=self.cfg.get("pause_taps", 0))
            self._loaded = str(path)
            self.cfg["level"] = str(path)
            self._save_cfg()
            self._show_info()
            self.log_line(f"已载入：{path.parent.name}")
            for w in self.tl.warnings:
                self.log_line("[提示] " + w)
            self.rebuild()
        except Exception as e:
            self.tl = None
            self.events = []
            self.log_line(f"载入失败：{e}")

    def _show_info(self) -> None:
        t = self.tl
        if t is None:
            self._set_text(self.info, "")
            return
        bits = [f"{len(t.hits)} 击打", f"总时长 {t.total_seconds:.2f}s",
                f"Twirl {t.event_counts.get('Twirl', 0)}",
                f"变速 {t.event_counts.get('SetSpeed', 0)}"]
        if t.midspin_count:
            bits.append(f"midspin {t.midspin_count}")
        if t.pause_count:
            bits.append(f"Pause {t.pause_count}（停顿 {t.pause_seconds:.2f}s）")
        if t.hold_count:
            bits.append(f"长按 {t.hold_count}")
        if t.multitap_count:
            bits.append(f"连按砖 {t.multitap_count}")
        if t.auto_tile_count:
            bits.append(f"自动砖 {t.auto_tile_count}")
        if t.multiplanet_count:
            bits.append(f"三球 {t.multiplanet_count}")
        if t.freeroam_count:
            bits.append("含 FreeRoam（需手动走位）")
        self._set_text(self.info, "   ·   ".join(bits))

    def rebuild(self) -> None:
        if self.tl is None:
            self.events = []
            return
        try:
            key = self.key_var.get().strip() or "space"
            vk = input_win.to_vk(key)
            raw = self.limit_var.get().strip()
            limit = int(raw) if raw else None
            self.events = player.build_schedule(
                self.tl.hits, vk,
                press_s=float(self.cfg.get("press_s", 0.02)),
                speed_scale=float(self.scale_var.get()),
                offset_ms=float(self.offset_var.get()),
                lead_in_s=float(self.cfg.get("lead_in_s", 0.0)),
                limit=limit,
            )
            self.cfg["key"] = key
            self.cfg["offset_ms"] = float(self.offset_var.get())
            self.cfg["speed_scale"] = float(self.scale_var.get())
            self._save_cfg()
            self.game_vk = vk
            self.state_var.set("待命 · F1 开始")
        except Exception as e:
            self.events = []
            self.state_var.set(f"参数错误：{e}")

    def _save_cfg(self) -> None:
        CONFIG.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    # ------------------------------------------------------------- 热键循环
    def _worker(self) -> None:
        start_vk = input_win.to_vk(self.cfg.get("start_hotkey", "f1"))
        stop_vk = input_win.to_vk(self.cfg.get("stop_hotkey", "f2"))
        while not self.abort.is_set():
            # 等待 F1 的「按下」边沿：先等松开，再等按下
            while input_win.is_down(start_vk):
                if self.abort.is_set():
                    return
                time.sleep(0.005)
            while not input_win.is_down(start_vk):
                if self.abort.is_set():
                    return
                time.sleep(0.005)
            if self.abort.is_set():
                return
            if not self.events:
                self.log_line("没有可演奏的时间轴：先用左键载入谱面")
                continue

            self.q.put(("state", "运行中 · F2 停止"))
            self.log_line("开始演奏 ..." + ("（空跑）" if self._dry else ""))
            status, errs = player.play(
                self.events, stop_vk=stop_vk, release_vk=self.game_vk,
                dry_run=self._dry, abort=self.abort.is_set,
            )
            if errs:
                avg = sum(errs) / len(errs) * 1000
                mx = max(errs) * 1000
                self.log_line(f"结束({status})：{len(errs)} 事件，"
                              f"误差 平均 {avg:+.3f}ms / 最大 {mx:+.3f}ms")
            else:
                self.log_line(f"结束({status})")
            self.q.put(("state", "待命 · F1 开始"))

    def on_close(self) -> None:
        self.abort.set()
        self.root.after(200, self.root.destroy)


def selftest() -> None:
    """无窗口自检：给 --noconsole 的 exe 用，结果写到 selftest.txt。"""
    try:
        cfg = dict(DEFAULT_CFG, **json.loads(CONFIG.read_text(encoding="utf-8")))
        root = tk.Tk()          # 同时验证打包后 Tcl/Tk 运行时是否齐全
        root.withdraw()
        ws = Path(cfg["workshop_dir"]) if cfg.get("workshop_dir") else find_workshop_dir()
        charts = scan_charts(ws) if ws else []
        ok_cover = False
        for c in charts:
            if c.cover is None:
                continue
            try:
                im = Image.open(c.cover)
                im.load()
                ImageOps.fit(im.convert("RGB"), (64, 64)).resize((32, 32))
                ok_cover = True
            except Exception:
                ok_cover = False
            break
        n_hits = n_ev = 0
        if cfg.get("level"):
            data = parse.load(cfg["level"])
            tl = timeline.build(data, pause_taps=cfg.get("pause_taps", 0))
            n_hits = len(tl.hits)
            n_ev = len(player.build_schedule(tl.hits, input_win.to_vk(cfg["key"]),
                                             press_s=float(cfg.get("press_s", 0.02))))
        icon = resource("assets/icon.ico").is_file()
        official_n = len(official.load_cache(ROOT / "official"))
        game_data = 1 if find_game_data_dir() else 0
        win, txt = open_help(root)
        root.update_idletasks()
        help_len = len(txt.get("1.0", "end").strip())
        win.destroy()
        root.destroy()
        result = (f"OK  charts={len(charts)}  official={official_n}  "
                  f"game_data={game_data}  cover={ok_cover}  icon={icon}  "
                  f"help={help_len}  hits={n_hits}  events={n_ev}  "
                  f"hotkeys={cfg.get('start_hotkey')}/{cfg.get('stop_hotkey')}")
    except Exception as e:
        result = f"FAIL  {type(e).__name__}: {e}"
    (ROOT / "selftest.txt").write_text(result, encoding="utf-8")
    print(result)


def main() -> None:
    if "--selftest" in sys.argv:
        selftest()
        return
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
