"""把 .adofai 谱面换算成击打时间轴。

时序依据（游戏源码 scrMisc / scrLevelMaker 的等价实现，另见社区库
SharpFAI 的 FloorTimingCalculator 与 adofaipy 的 getAnglesRelative；
并用关卡 OGG 实际时长校验过）：

  * 行星匀速绕砖旋转，一拍(crotchet)转 180°，拍长 = 60 / BPM 秒
  * 相对角度 rel = (prev - cur + 180) mod 360，范围 [0, 360)；
    恰好 0°（转面）按游戏判为绕满一圈 360°（2 拍），接近 0° 的微小角度
    则保持原值（高难谱常用的 179.9°「伪转面」砖是近乎瞬时的击打）
  * 间隔拍数 = rel / 180；间隔秒数 = 拍数 * 60 / 有效BPM
  * 有效BPM = settings.bpm * pitch/100 * 该砖累计的 SetSpeed 倍率
  * Twirl 翻转旋转方向：等价为「以 a[F-1] 为轴镜像反射之后的绝对角度」
  * angleData 中的 999 是 midspin：+180° 翻转后续角度，且不产生独立击打
  * actions 的 floor 为 0 基，直接对应 angleData 下标（已用歌曲时长验证）
  * Pause（中途 3-2-1 倒数）：在该砖出块后插入 duration 拍的停顿，
    按该砖当时的有效曲速换算；countdownTicks 只是视觉倒数，不占时间。
  * Hold（长按）：落在该砖后，游戏实际按住 duration*2 拍
    （source：`f.HoldLength = (int)Duration`，`time += HoldLength * 2 * 一拍`），
    一拍按该砖有效曲速换算；因此后续所有击打整体后移，且按键要一直按住。
  * MultiPlanet（多球）：3 球时 `GetInverseAnglePerBeatMultiplanet(3) = π/3`，
    每段行程角度减 60°（等价于行星绕内角转）；60° 的砖会被强制绕满一圈。
  * Multitap（连按）：事件 floor=F 的砖（我们的 tile F-1）需要按 taps 次。
    游戏里第 1..N-1 次按下只累加计数、不判定，第 N 次才判定，
    所以脚本在同一块砖上连按，只有最后一按被计时。
  * AutoPlayTiles（自动砖）：事件生效后该段砖由游戏自己击打，脚本不该按键，
    这些砖直接从 hits 里剔除。

注意：hits[i].at_s 以「第一次击打」为 0 点，即自动演奏脚本在第一次击打时
启动即可；关卡开头的前奏（rel[0]）单独放在 lead_in_s，供校准时参考。

实机校准旋钮：
  * pause_offset_ms          —— 每个 Pause 固定加减毫秒
  * pause_offset_per_beat_ms —— 按该 Pause 的 duration 拍数线性加减毫秒
  * pause_offset_beats       —— 每个 Pause 固定加减「拍」，按该砖有效 BPM 换算成秒
    默认 -0.5 拍：由实机反推（填 -90ms 时第 1 个 Pause 能过、第 2 个又太慢，
    说明每个 Pause 残留约 140~170ms 且逐个累积；180BPM 下半拍正好 166.7ms，
    90BPM 下 333ms，符合「和曲速有关」的观察）。
三者可叠加。游戏设置里的 Input Offset 不在这里补偿 —— 它已由「按 F1 启动的时机」
自动吸收（原谱在同样设置下实机全部精准即为证据）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import parse

MIDSPIN = 999.0

# 会影响击打时序、但当前尚未实机验证的事件（出现时给出提示）
UNVERIFIED_GAMEPLAY_EVENTS = {"FreeRoam"}


@dataclass
class Hit:
    index: int          # 第几次击打（0 基）
    tile: int           # 原始 angleData 下标
    angle: float        # 相对角度（度）
    at_s: float         # 距第一次击打的秒数
    bpm: float          # 该段有效 BPM
    hold_s: float = 0.0 # 长按砖：从这次按下一直按住到下一块砖的秒数
    release: bool = False  # 长按的「尾判砖」：这一次不按下，而是在此刻松手
    taps: int = 1       # Multitap 砖需要按几次（多余的那几次不判定，最后一次才判定）


@dataclass
class Timeline:
    bpm: float
    pitch: float
    offset_ms: float
    tile_count: int
    midspin_count: int
    pause_count: int
    pause_beats: float
    pause_seconds: float
    pause_offset_ms: float
    pause_offset_per_beat_ms: float
    pause_offset_beats: float
    pause_taps: object
    hold_count: int
    hold_beats: float
    hold_seconds: float
    multiplanet_count: int
    three_planet_seconds: float
    hits: list[Hit]
    lead_in_s: float        # 关卡开头前奏（第一次击打之前）
    play_seconds: float     # 第一次击打 -> 最后一次击打
    total_beats: float
    total_seconds: float    # 从关卡起点算（含前奏与 Pause 停顿）
    event_counts: dict
    multitap_count: int = 0     # 需要连按的砖（事件数）
    auto_tile_count: int = 0    # 由游戏自动完成的砖（已从 hits 里剔除）
    freeroam_count: int = 0     # 自由移动段（无法自动演奏）
    freeroam_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)


def _apply_twirls(a: list[float], twirl_floors: list[int]) -> None:
    """按 floor 从大到小，把 F 之后的绝对角度以 a[F-1] 为轴镜像反射。

    两次反射（两个 Twirl）自然抵消，等价于方向翻回来。
    """
    for f in sorted(twirl_floors, reverse=True):
        if f <= 0 or f > len(a):
            continue
        pivot = a[f - 1]
        if pivot == MIDSPIN:
            continue
        for i in range(f, len(a)):
            if a[i] != MIDSPIN:
                a[i] = (2 * pivot - a[i]) % 360


def _apply_midspins(a: list[float]) -> None:
    """每个 midspin 让其后的绝对角度 +180°（等价于翻转入射方向）。"""
    for i in range(len(a) - 1, -1, -1):
        if a[i] != MIDSPIN:
            continue
        for j in range(i + 1, len(a)):
            if a[j] != MIDSPIN:
                a[j] = (a[j] + 180) % 360


def _relative_angles(a: list[float]) -> tuple[list[float], list[int]]:
    """折叠 midspin 后计算相对角度，返回 (rel, keep)。

    keep[i] 是 rel[i] 对应的原始 angleData 下标。
    相对角度取 ``(prev - cur + 180) % 360``，与游戏的行星行程一致：
      * 恰好 0°（转面砖）游戏判为绕满一圈，按 360°（2 拍）算；
      * 接近 0° 的微小角度保持原值 —— 高难谱常用 179.9° 之类的
        「伪转面」砖制造近乎瞬时的击打，若把它归到 360° 会每砖多算 2 拍。
    """
    keep = [i for i, x in enumerate(a) if x != MIDSPIN]
    b = [a[i] for i in keep]
    rel: list[float] = []
    for idx, x in enumerate(b):
        prev = 0.0 if idx == 0 else b[idx - 1]
        r = (prev - x + 180) % 360
        if r < 1e-4 or r > 360 - 1e-4:
            r = 360.0
        rel.append(float(r))
    return rel, keep


def _bpm_by_tile(n: int, actions: list[dict], base_bpm: float) -> list[float]:
    """逐砖 BPM：SetSpeed 在 floor F 生效，并累乘延续到后续砖。

    Multiplier 为「相对当前速度的倍率」（本谱 1/6 -> 6 正好还原，
    且累计后与歌曲时长吻合）；Bpm 为绝对 BPM。
    """
    events: dict[int, list[tuple[str, float]]] = {}
    for ev in actions:
        if ev.get("eventType") != "SetSpeed":
            continue
        f = int(ev.get("floor", 0))
        if ev.get("speedType") == "Bpm":
            events.setdefault(f, []).append(("set", float(ev.get("beatsPerMinute", base_bpm))))
        else:
            events.setdefault(f, []).append(("mul", float(ev.get("bpmMultiplier", 1))))

    bpm = [base_bpm] * (n + 2)
    cur = base_bpm
    for i in range(n + 2):
        for kind, v in events.get(i, []):
            cur = v if kind == "set" else cur * v
        bpm[i] = cur
    return bpm


def _pause_seconds(a: list[float], actions: list[dict],
                   bpm_tile: list[float], pitch: float,
                   offset_ms: float = 0.0,
                   per_beat_ms: float = 0.0,
                   offset_beats: float = 0.0) -> tuple[int, float, dict[int, float]]:
    """统计中途 Pause 的停顿，返回 (个数, 总拍数, {floor: 停顿秒数})。

    游戏源码：`case Pause: f.ExtraBeats += Duration`，随后
    `time += ExtraBeats * timeBetweenAngles(0, Pi, ...)`，也就是
    duration 拍 × 该砖的一拍秒数。countdownTicks 仅供 3-2-1 显示，不计时。
    最后一砖与 midspin 砖上的 Pause 会被游戏忽略，这里对齐。
    floor=F 的停顿落在「进入第 F 块砖」那一段（我们的 hit[F-1] → hit[F] 之间）。
    offset_ms / per_beat_ms 是实机校准量：前者对每个 Pause 固定加减，
    后者按该 Pause 的 duration 拍数线性加减（应对「越长越偏」的情况）。
    """
    n = len(a)
    midspin_tiles = {i for i, x in enumerate(a) if x == MIDSPIN}
    by_floor: dict[int, float] = {}
    count = 0
    beats_total = 0.0
    for ev in actions:
        if ev.get("eventType") != "Pause":
            continue
        count += 1
        f = int(ev.get("floor", 0))
        beats = float(ev.get("duration", 0))
        beats_total += beats
        if f >= n or f in midspin_tiles:
            continue
        eff = bpm_tile[min(f, n + 1)] * pitch
        sec = max(0.0, (beats + offset_beats) * 60.0 / eff
                  + (offset_ms + beats * per_beat_ms) / 1000.0)
        by_floor[f] = by_floor.get(f, 0.0) + sec
    return count, beats_total, by_floor


def _hold_events(a: list[float], actions: list[dict],
                 bpm_tile: list[float], pitch: float
                 ) -> tuple[int, float, dict[int, float], set[int]]:
    """统计 Hold（长按）砖，返回 (长按砖数, 额外循环拍数, {floor: 额外秒数}, 长按砖集合)。

    游戏实际机制（反编译 scrPlayer / scrLevelMaker 确认）：
      * 事件的 floor 索引的是游戏里的砖号，而游戏 listFloors[0] 是起点砖，
        所以 floor=F 的 Hold 长按在第 F 块砖（我们的 hit[F-1]）上按下，
        到下一块砖（我们的 hit[F]）松手；松手即尾判，那一块不再按下。
      * duration 只是额外循环：duration×2 拍（int 截断），按该砖有效曲速换算，
        加在「进入第 F 块砖」那一段里，使这次及之后的击打整体后移。
      * duration=0 依然是长按砖（按住整段行程即可，无额外循环）——
        SharpFAI 用 `HoldLength >= 0` 判定长按砖，别把它当普通砖。
    midspin(999) 砖没有独立击打，其 Hold 这里跳过并告警。
    """
    n = len(a)
    midspin_tiles = {i for i, x in enumerate(a) if x == MIDSPIN}
    by_floor: dict[int, float] = {}
    hold_floors: set[int] = set()
    count = 0
    beats_total = 0.0
    for ev in actions:
        if ev.get("eventType") != "Hold":
            continue
        f = int(ev.get("floor", 0))
        if f < 0 or f >= n or f in midspin_tiles:
            continue
        count += 1
        hold_floors.add(f)
        beats = int(float(ev.get("duration", 0))) * 2
        if beats <= 0:
            continue
        beats_total += beats
        eff = bpm_tile[min(f, n + 1)] * pitch
        by_floor[f] = by_floor.get(f, 0.0) + beats * 60.0 / eff
    return count, beats_total, by_floor, hold_floors


def _planets_by_tile(n: int, actions: list[dict]) -> list[int]:
    """逐砖的行星数量（2 或 3），MultiPlanet 在 floor F 生效并延续。

    游戏 scrMisc.GetInverseAnglePerBeatMultiplanet(P) = π(P-2)/P：
    3 球时每段行程的角度要减去 60°（等价于行星绕内角转），
    所以三球段的节奏比双球快。
    """
    by_floor: dict[int, int] = {}
    for ev in actions:
        if ev.get("eventType") != "MultiPlanet":
            continue
        p = ev.get("planets")
        by_floor[int(ev.get("floor", 0))] = 3 if p in (3, "ThreePlanets") else 2
    out = [2] * (n + 2)
    cur = 2
    for i in range(n + 2):
        cur = by_floor.get(i, cur)
        out[i] = cur
    return out


def _taps_by_tile(a: list[float], actions: list[dict]) -> dict[int, int]:
    """Multitap：事件 floor=F 落在我们 tile F-1 上，该砖需要按 taps 次。

    游戏里 tapsNeeded 是砖的属性（scrFloor.tapsNeeded）。scrPlanet 中第
    1..N-1 次按下只累加 tapsOnThisFloor 后直接 return（不判定、不算 miss），
    第 N 次才走正常判定 —— 所以脚本要在同一块砖上连按 N 下。
    """
    mid = {i for i, x in enumerate(a) if x == MIDSPIN}
    out: dict[int, int] = {}
    for ev in actions:
        if ev.get("eventType") != "Multitap":
            continue
        tile = int(ev.get("floor", 0)) - 1
        if tile < 0 or tile >= len(a) or tile in mid:
            continue
        out[tile] = max(1, int(float(ev.get("taps", 2) or 2)))
    return out


def _auto_tiles(a: list[float], actions: list[dict]) -> set[int]:
    """AutoPlayTiles：从该砖起（直到下一次事件关闭）由游戏自动击打。

    事件按 floor 生效并延续；脚本在这些砖上不能按键。
    """
    toggles: dict[int, bool] = {}
    for ev in actions:
        if ev.get("eventType") != "AutoPlayTiles":
            continue
        toggles[int(ev.get("floor", 0)) - 1] = bool(ev.get("enabled"))
    if not toggles:
        return set()
    out: set[int] = set()
    cur = toggles.pop(-1, False)
    for i in range(len(a)):
        if i in toggles:
            cur = toggles[i]
        if cur:
            out.add(i)
    return out


def expand_repeat_events(actions: list[dict]) -> tuple[list[dict], list[str]]:
    """展开 RepeatEvents：把带同名 eventTag 的事件复制到后续砖上。

    游戏语义（scnGame.cs:977 起）：RepeatEvents 只匹配**同一块砖上**带同名
    tag 的事件，重复 floorCount 次，每次把 floor 往后挪 gapLength 块
    （num=0 就是原事件本身）。Beat 型的重复靠 angleOffset 细分、只对带
    angleOffset 的 SetSpeed 有意义，这里不展开并提示。

    返回 (展开后的事件列表, 提示信息)。
    """
    repeats: dict[int, dict[str, tuple[int, float, int]]] = {}
    for ev in actions:
        if ev.get("eventType") != "RepeatEvents":
            continue
        by_beat = str(ev.get("repeatType", "Floor")) == "Beat"
        count = int(float(ev.get("repetitions" if by_beat else "floorCount", 1) or 0))
        interval = float(ev.get("interval", 1) or 0) if by_beat else 0.0
        gap = 0 if by_beat else int(float(ev.get("gapLength", 1) or 0))
        for tag in str(ev.get("tag", "") or "").split():
            repeats.setdefault(int(ev.get("floor", 0)), {})[tag] = (count, interval, gap)

    out = list(actions)
    notes: list[str] = []
    if not repeats:
        return out, notes
    # 这些事件会影响击打时序/按键，被重复时必须展开
    TIMING = {"SetSpeed", "Pause", "Twirl", "Hold", "Multitap"}
    beat_skipped = 0
    for ev in actions:
        tags = str(ev.get("eventTag", "") or "").split()
        if not tags:
            continue
        floor = int(ev.get("floor", 0))
        conf = repeats.get(floor)
        if not conf:
            continue
        hit = next((conf[t] for t in tags if t in conf), None)
        if hit is None:
            continue
        count, _interval, gap = hit
        if gap <= 0:
            if ev.get("eventType") in TIMING:
                beat_skipped += 1
            continue
        for k in range(1, count + 1):
            clone = dict(ev)
            clone["floor"] = floor + k * gap
            out.append(clone)
    if beat_skipped:
        notes.append(
            f"有 {beat_skipped} 个时序事件被 Beat 型 RepeatEvents 重复（按角度偏移细分），"
            "当前未展开，可能出现节奏偏差")
    return out, notes


def _tap_count(pause_taps, ticks: int, duration: float) -> int:
    """每个 Pause 期间补发的击打次数。

    pause_taps 可为：
      0 / "none"  —— 不补（整段空等，旧行为）
      正整数 N    —— 每处固定补 N 次
      "ticks"     —— 补 countdownTicks 次（3-2-1 每个刻度一次）
      "beats"     —— 补 duration 拍次（行星每转 180° 一次）
    """
    if isinstance(pause_taps, str):
        s = pause_taps.lower()
        if s in ("ticks", "countdown"):
            return max(0, int(ticks))
        if s in ("beats", "duration"):
            return max(0, int(round(duration)))
        if s in ("none", "off", ""):
            return 0
        try:
            return max(0, int(s))
        except ValueError:
            return 0
    return max(0, int(pause_taps))


def build(data: dict, pause_offset_ms: float = 0.0,
          pause_offset_per_beat_ms: float = 0.0,
          pause_offset_beats: float = 0.0,
          pause_taps=0) -> Timeline:
    settings = data["settings"]
    actions, repeat_notes = expand_repeat_events(data["actions"])
    base_bpm = float(settings.get("bpm", 100))
    pitch = float(settings.get("pitch", 100)) / 100.0
    offset_ms = float(settings.get("offset", 0))
    a = parse.get_angle_data(data)
    n = len(a)

    event_counts: dict[str, int] = {}
    for ev in actions:
        t = str(ev.get("eventType", "?"))
        event_counts[t] = event_counts.get(t, 0) + 1

    warnings: list[str] = list(repeat_notes)
    for t in UNVERIFIED_GAMEPLAY_EVENTS:
        if t in event_counts:
            warnings.append(f"含 {t} 事件 x{event_counts[t]}：该事件的击打语义尚未实机验证，可能导致漏键")

    twirl_floors = [int(ev.get("floor", 0)) for ev in actions if ev.get("eventType") == "Twirl"]
    bpm_tile = _bpm_by_tile(n, actions, base_bpm)
    planets_tile = _planets_by_tile(n, actions)
    taps_tile = _taps_by_tile(a, actions)
    auto_tiles = _auto_tiles(a, actions)
    midspin_count = sum(1 for x in a if x == MIDSPIN)

    # 三球与 midspin 交叠时游戏还有额外的循环修正（SharpFAI 里那段 num3 调整），
    # 这里没有实现；遇到时提示，避免静默错位。
    mid_three = [i for i, x in enumerate(a)
                 if x == MIDSPIN and planets_tile[min(i, n + 1)] >= 3]
    for ev in actions:
        if ev.get("eventType") != "MultiPlanet":
            continue
        f = int(ev.get("floor", 0))
        if 0 < f <= n and a[f - 1] == MIDSPIN:
            mid_three.append(f - 1)
    if mid_three:
        warnings.append(
            "三球段与 midspin 砖相邻（floor "
            + ", ".join(str(x) for x in sorted(set(mid_three))[:4])
            + "）：该处游戏有额外循环修正，当前未实现，遇到请实机确认")

    work = list(a)
    _apply_twirls(work, twirl_floors)
    _apply_midspins(work)
    rel, keep = _relative_angles(work)

    seconds: list[float] = []
    eff_bpms: list[float] = []
    eff_angles: list[float] = []
    three_planet_seconds = 0.0
    for i, r in enumerate(rel):
        # 该段速度取「落点砖」上生效的 BPM（与歌曲时长吻合的口径）
        tile = keep[i]
        eff = bpm_tile[min(tile, n + 1)] * pitch
        eff_bpms.append(eff)
        ang = r
        if planets_tile[min(tile, n + 1)] >= 3:
            # 三球：入口角度被内角推移 60°，等价于行程少 60°。
            # 恰好 60° 的砖游戏会判定为绕满一圈（2 拍）；更小的角度则绕 300°+ 回来。
            ang = r - 60.0
            if abs(ang) <= 1e-9:
                ang = 360.0
            elif ang < 0:
                ang += 360.0
        eff_angles.append(ang)
        sec = (ang / 180.0) * 60.0 / eff
        seconds.append(sec)
        if planets_tile[min(tile, n + 1)] >= 3:
            three_planet_seconds += sec

    pause_count, pause_beats, pause_by_floor = _pause_seconds(
        a, actions, bpm_tile, pitch,
        pause_offset_ms, pause_offset_per_beat_ms, pause_offset_beats)
    # 每个 Pause 的 countdownTicks / duration，用于决定补按次数。
    pause_meta: dict[int, tuple[int, float]] = {}
    for ev in actions:
        if ev.get("eventType") == "Pause":
            pause_meta[int(ev.get("floor", 0))] = (
                int(ev.get("countdownTicks", 0)), float(ev.get("duration", 0)))
    # 最后一砖之后的 Pause 不会落在任何两个击打之间，对演奏无影响，剔除掉，
    # 保证 total_seconds == lead_in_s + play_seconds。
    # （floor=F 的停顿影响「进入第 F 块砖」那一段，所以 F 等于最后一砖时仍有效）
    last_tile = keep[-1] if keep else 0
    pause_by_floor = {f: s for f, s in pause_by_floor.items() if f <= last_tile}
    pause_seconds = sum(pause_by_floor.values())
    lead_in_s = seconds[0] if seconds else 0.0

    # FreeRoam：游戏把停顿写成 extraBeats = max(该砖拍数, duration - 该砖拍数)，
    # 与 Pause 一样落在那块砖上。但这段是方向键自由移动，脚本无法自动通过，
    # 这里只把时间补对并给出提示。
    freeroam_count = 0
    freeroam_seconds = 0.0
    beats_by_tile = {keep[i]: r / 180.0 for i, r in enumerate(rel)}
    for ev in actions:
        if ev.get("eventType") != "FreeRoam":
            continue
        freeroam_count += 1
        f = int(ev.get("floor", 0))
        dur = float(ev.get("duration", 0) or 0)
        if dur < 2 or f <= 0 or f >= n:
            continue
        here = beats_by_tile.get(f, 0.0)
        extra = max(here, dur - here)
        sec = extra * 60.0 / (bpm_tile[min(f, n + 1)] * pitch)
        pause_by_floor[f] = pause_by_floor.get(f, 0.0) + sec
        freeroam_seconds += sec
    if freeroam_count:
        floors = ", ".join(str(int(e.get("floor", 0))) for e in actions
                           if e.get("eventType") == "FreeRoam")[:60]
        warnings.append(
            f"含 FreeRoam 自由移动段 x{freeroam_count}（floor {floors}）："
            f"该段要靠方向键自由移动，脚本只能按下不误、无法走位，实际会在该处失败")

    hold_count, hold_beats, hold_extra_by_floor, hold_floors = _hold_events(
        a, actions, bpm_tile, pitch)

    hits: list[Hit] = []
    t = 0.0
    for i in range(len(rel)):
        if i > 0:
            # 游戏里 listFloors[0] 是起点砖，事件 floor=F 落在「第 F 块砖」上，
            # 而我们的 hit[F] 对应第 F+1 块砖。所以 floor=F 的停顿/额外循环
            # 是加在「进入第 F 块砖」这一段里的 —— 也就是本题的 keep[i] == F，
            # 区间取 (上一块砖, 当前砖]。
            lo = keep[i - 1]
            hi = keep[i]
            # 长按期间必须一直按住，不能在中途补按，否则会提前松手。
            in_hold = hi in hold_floors
            # 停顿发生在刚落到上一块砖之后，所以补按/停顿放在本段最前面。
            for f, sec in pause_by_floor.items():
                if lo < f <= hi:
                    if not in_hold:
                        ticks, dur = pause_meta.get(f, (0, 0.0))
                        cnt = _tap_count(pause_taps, ticks, dur)
                        # 游戏自己会按 countdownTicks 播 sndHat 倒数音，
                        # 默认 cnt=0（不补按）；需要时可在停顿窗口内等分补发。
                        for k in range(1, cnt + 1):
                            hits.append(Hit(index=-1, tile=-1, angle=0.0,
                                           at_s=t + sec * k / (cnt + 1),
                                           bpm=eff_bpms[i]))
                    elif _tap_count(pause_taps, *pause_meta.get(f, (0, 0.0))):
                        warnings.append(
                            f"Pause@floor{f} 落在长按段内，已跳过倒数补按（不能中途松手）")
                    t += sec
            t += hold_extra_by_floor.get(hi, 0.0)
            t += seconds[i]
        hits.append(Hit(index=i, tile=keep[i], angle=eff_angles[i], at_s=t,
                        bpm=eff_bpms[i], taps=taps_tile.get(keep[i], 1)))

    # 长按：floor=F 的 Hold 从第 F 块砖（我们的 hit[F-1]）按下，
    # 到第 F+1 块砖（我们的 hit[F]）松手，松手即尾判，那一块不再按下。
    real = [h for h in hits if h.tile >= 0]
    pos_by_tile = {h.tile: k for k, h in enumerate(real)}
    hold_seconds = 0.0
    for f in hold_floors:
        k = pos_by_tile.get(f)
        if k is None:
            warnings.append(f"Hold@floor{f} 落在 midspin 砖上，无法按（已跳过）")
            continue
        if k == 0:
            warnings.append(f"Hold@floor{f} 在起点砖上，没有可按住的前一块（已跳过）")
            continue
        start = real[k - 1]
        end = real[k]
        end.release = True
        start.hold_s = max(0.0, end.at_s - start.at_s)
        hold_seconds += start.hold_s

    if any(h.tile < 0 for h in hits):
        hits.sort(key=lambda h: h.at_s)
        for idx, h in enumerate(hits):
            h.index = idx

    # AutoPlayTiles 段的砖由游戏自己击打，脚本按键反而会多按：整块剔除。
    auto_dropped = 0
    if auto_tiles:
        auto_dropped = sum(1 for h in hits if h.tile in auto_tiles)
        for h in hits:
            if h.tile in auto_tiles and h.release:
                warnings.append(
                    f"AutoPlayTiles 段覆盖了长按尾判砖 tile{h.tile}，该处松手不会发出（请实机确认）")
        hits = [h for h in hits if h.tile not in auto_tiles]
        for idx, h in enumerate(hits):
            h.index = idx
        if hits:
            t = hits[-1].at_s

    total_beats = sum(x / 180.0 for x in eff_angles) + pause_beats + hold_beats
    return Timeline(
        bpm=base_bpm,
        pitch=pitch,
        offset_ms=offset_ms,
        tile_count=n,
        midspin_count=midspin_count,
        pause_count=pause_count,
        pause_beats=pause_beats,
        pause_seconds=pause_seconds,
        pause_offset_ms=pause_offset_ms,
        pause_offset_per_beat_ms=pause_offset_per_beat_ms,
        pause_offset_beats=pause_offset_beats,
        pause_taps=pause_taps,
        hold_count=hold_count,
        hold_beats=hold_beats,
        hold_seconds=hold_seconds,
        multiplanet_count=event_counts.get("MultiPlanet", 0),
        three_planet_seconds=three_planet_seconds,
        hits=hits,
        lead_in_s=lead_in_s,
        play_seconds=t,
        total_beats=total_beats,
        total_seconds=lead_in_s + t,
        event_counts=event_counts,
        multitap_count=len(taps_tile),
        auto_tile_count=auto_dropped,
        freeroam_count=freeroam_count,
        freeroam_seconds=freeroam_seconds,
        warnings=warnings,
    )
