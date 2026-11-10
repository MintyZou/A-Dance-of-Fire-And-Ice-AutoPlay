"""从本机《冰与火之舞》安装目录提取官方内置谱面。

官方谱不是磁盘上的独立 .adofai 文件，而是以 Unity TextAsset 的形式
存放在 <游戏目录>/A Dance of Fire and Ice_Data/resources.assets 里：
文本内容就是标准 .adofai JSON，前面带一个 4 字节小端长度前缀。

本模块只读游戏文件，提取结果缓存到应用目录的 official/ 下（含 meta.json
与生成的封面），不随仓库分发、不联网、不修改游戏文件。
"""
from __future__ import annotations

import hashlib
import json
import mmap
import re
import struct
from pathlib import Path

import parse

_MAX_TEXT = 3_000_000
_START = re.compile(rb'\{\s*"(?:pathData|angleData)"')
_TAG = re.compile(r"</?[a-zA-Z][^>]*>|\[\[[^\]]*\]\]")
_FONTS = ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf")

# 「J 变体」关卡（愚人节 / 加长 / 被诅咒版）的本地化键形如 [[1J-X.title]]。
# 这些谱与游戏内同名的正常关卡不是同一份：前半段相同、中途插入段落，
# 实机演奏会在插入点开始整体偏半拍。必须标出来，避免当成正常关卡误选。
_VARIANT_TITLE = re.compile(r"\[\[\s*[0-9A-Za-z]*J-[0-9A-Za-z]+\.title\s*\]\]")


def _is_variant(settings: dict, song_raw: str) -> bool:
    """判断该 TextAsset 是否为 J 变体谱。"""
    if _VARIANT_TITLE.search(song_raw or ""):
        return True
    tags = str(settings.get("levelTags") or "").lower()
    return "cursed" in tags or "meme" in tags


def clean_title(s: str) -> str:
    """去掉富文本标签与 [[本地化键]] 占位，压平空白。"""
    t = _TAG.sub("", s or "").replace("?", "")
    return re.sub(r"\s+", " ", t).strip()


def iter_level_texts(asset: Path):
    """逐块产出 resources.assets 里的 .adofai 文本（原始字节）。"""
    with open(asset, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        for m in _START.finditer(mm):
            p = m.start()
            if p < 4:
                continue
            length = struct.unpack_from("<I", mm, p - 4)[0]
            if not (0 < length < _MAX_TEXT) or p + length > mm.size():
                continue
            seg = bytes(mm[p:p + length]).rstrip(b"\x00")
            if seg.rstrip().endswith(b"}") and b'"settings"' in seg and b'"actions"' in seg:
                yield seg


def _font(size: int):
    from PIL import ImageFont
    for path in _FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _write_cover(path: Path, title: str) -> bool:
    """给没有封面图的官方谱画一张带标题的占位封面。"""
    try:
        from PIL import Image, ImageDraw
        h = int(hashlib.md5(title.encode("utf-8")).hexdigest()[:6], 16)
        im = Image.new("RGB", (400, 400),
                       (26 + h % 26, 30 + (h >> 8) % 24, 44 + (h >> 16) % 34))
        d = ImageDraw.Draw(im)
        d.text((22, 20), "ADOFAI · 官方谱", fill=(150, 190, 255), font=_font(18))
        lines, cur = [], ""
        for ch in title:
            cur += ch
            if len(cur) >= 11:
                lines.append(cur)
                cur = ""
        if cur:
            lines.append(cur)
        y = 150
        for line in lines[:5]:
            d.text((22, y), line, fill=(236, 238, 246), font=_font(28))
            y += 40
        im.save(path)
        return True
    except Exception:
        return False


def extract(asset: Path, cache: Path) -> list[dict]:
    """提取并缓存全部官方谱，返回 meta 列表。"""
    cache.mkdir(parents=True, exist_ok=True)
    meta: list[dict] = []
    seen: set[str] = set()
    for raw in iter_level_texts(asset):
        digest = hashlib.md5(raw).hexdigest()
        if digest in seen:                 # 同一段谱在文件里可能重复出现
            continue
        seen.add(digest)
        try:
            data = json.loads(parse.sanitize_json(
                raw.decode("utf-8-sig", errors="replace")))
        except Exception:
            continue
        settings = data.get("settings")
        if not isinstance(settings, dict) or "actions" not in data:
            continue
        angles = data.get("angleData") or data.get("pathData")
        if not angles:
            continue
        idx = len(meta)
        song_raw = str(settings.get("song") or "")
        song_filename = str(settings.get("songFilename") or "")
        song = clean_title(song_raw)
        if not song:                # 部分 DLC / 额外关卡的曲名走本地化，用音频名兜底
            song = clean_title(Path(song_filename).stem)
        title = song or f"官方谱 #{idx + 1:03d}（{len(angles)} 砖）"
        variant = _is_variant(settings, song_raw)
        if variant:
            title = "【变体】" + title
        try:
            bpm = float(settings.get("bpm", 0) or 0)
        except (TypeError, ValueError):
            bpm = 0.0
        fname = f"{idx:04d}.adofai"
        (cache / fname).write_bytes(raw)
        cover = ""
        if _write_cover(cache / f"{idx:04d}.png", title):
            cover = f"{idx:04d}.png"
        meta.append({
            "file": fname,
            "title": title,
            "artist": clean_title(str(settings.get("artist") or "")),
            "bpm": bpm,
            "tiles": len(angles),
            "cover": cover,
            "variant": variant,
            "song_raw": clean_title(song_raw),
            "song_filename": song_filename,
        })
    # 同名谱（不同练习段 / 变体）用砖数区分，避免封面流里一堆一样的标题
    counts: dict[str, int] = {}
    for m in meta:
        counts[m["title"]] = counts.get(m["title"], 0) + 1
    for m in meta:
        if counts[m["title"]] > 1:
            m["title"] = f"{m['title']}（{m['tiles']} 砖）"
    (cache / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def load_cache(cache: Path) -> list[dict]:
    """读取已缓存的官方谱 meta（缺文件的条目会被丢掉）。"""
    p = cache / "meta.json"
    if not p.is_file():
        return []
    try:
        items = json.loads(p.read_text(encoding="utf-8"))
        return [m for m in items if isinstance(m, dict)
                and (cache / m.get("file", "")).is_file()]
    except Exception:
        return []
