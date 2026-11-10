"""逐砖对照：把游戏 scrLevelMaker.CalculateFloorEntryTimes 原样移植过来，
和 timeline.build 的击打时刻做差，用来定位「某一段时序不对」的谱面。

    python verify_timing.py "path/to/level.adofai"
    python verify_timing.py "D:/path/to/chart.adofai"
    python verify_timing.py                         # 默认扫描自动探测到的工坊目录

原理：游戏里每块砖（floor）的 entryTime 就是该砖的击打时刻，
  * entryangle/exitangle 由 angleData 递推（`(90 - a) * pi/180`，999 不变）
  * 每段行程 = mod(exit - entry - 逆角/拍, 2π) / π 拍
  * angleMoved 为 0（转角翻面）时强制 2 拍
  * floor.speed 来自 SetSpeed 累积，Pause/FreeRoam 记 extraBeats，Hold 加 2*duration 拍
移植和 timeline.build 独立实现，两边都 0 偏差才说明我们的口径与游戏一致。
"""
from __future__ import annotations

import math
import sys

import parse
import timeline

PI = math.pi
TWO_PI = 2 * math.pi


def _mod(x: float, m: float) -> float:
    return (x % m + m) % m


def _frame(a: list[float]):
    """angleData -> (entry, exit, midspin, isCCW)，下标即游戏 floor 号。"""
    n = len(a)
    entry = [0.0] * (n + 1)
    exit_ = [0.0] * (n + 1)
    mid = [False] * (n + 1)
    entry[0] = 4.71238899230957
    cur = 0
    for j, x in enumerate(a):
        exit_[cur] = entry[cur] if x == 999.0 else (90.0 - x) * (PI / 180.0)
        entry[j + 1] = (exit_[cur] + PI) % TWO_PI
        if x == 999.0:
            mid[cur] = True
        cur = j + 1
    exit_[cur] = entry[cur] + PI
    return entry, exit_, mid


def _t(entry: float, exit_: float, speed: float, bpm: float, is_cw: bool) -> float:
    num = 1.0 if is_cw else -1.0
    return _mod((exit_ - entry) * num, TWO_PI) / PI * ((60.0 / bpm) / speed)


def game_hit_times(data: dict) -> tuple[list[tuple[int, float]], list[float]]:
    """返回 ([(游戏 floor, 相对第一次击打的秒数)], 每砖角度行程)。"""
    a = parse.get_angle_data(data)
    s = data["settings"]
    bpm = float(s.get("bpm", 100))
    pitch = float(s.get("pitch", 100)) / 100.0
    n = len(a)
    actions = timeline.expand_repeat_events(data["actions"])[0]

    entry, exit_, mid = _frame(a)
    bpm_tile = timeline._bpm_by_tile(n, actions, bpm)
    planets = timeline._planets_by_tile(n, actions)
    speed = [b / bpm for b in bpm_tile]

    # Twirl 从该 floor 起翻转 isCCW（scnGame.cs:1278）
    tw = [0] * (n + 2)
    for ev in actions:
        if ev.get("eventType") == "Twirl":
            f = int(ev.get("floor", 0))
            if 0 <= f <= n:
                tw[f] += 1
    isccw = [False] * (n + 1)
    state = False
    for i in range(n + 1):
        if tw[i] % 2:
            state = not state
        isccw[i] = state

    extra = [0.0] * (n + 2)
    hold = [-1] * (n + 2)
    for ev in actions:
        kind = ev.get("eventType")
        f = int(ev.get("floor", 0))
        if not (0 <= f <= n):
            continue
        if kind == "Pause" and not mid[f]:
            extra[f] += float(ev.get("duration", 0) or 0)
        elif kind == "Hold":
            hold[f] = int(float(ev.get("duration", 0) or 0))

    times = [0.0] * (n + 1)
    times[1] = _t(entry[0], exit_[0], speed[0], bpm, True)
    angles = [0.0] * (n + 1)
    t = times[1]
    for i in range(1, n):
        num3 = PI * (planets[i] - 2) / planets[i] * (1 if not isccw[i] else -1)
        if mid[i]:
            num3 = 0.0
        if mid[i - 1] and planets[i] > 2:
            num3 -= (TWO_PI + PI * (planets[i] - 2) / planets[i]) * (1 if not isccw[i] else -1)
        e1 = entry[i] + num3
        e2 = exit_[i] + (num3 if mid[i] else 0.0)
        dt = _t(e1, e2, speed[i], bpm, not isccw[i])
        moved = _mod((e2 - e1) * (1 if not isccw[i] else -1), TWO_PI)
        one_beat = _t(0.0, PI, speed[i], bpm, False)
        if moved <= 1e-6 or moved >= TWO_PI - 2e-7:
            dt = 0.0 if mid[i] else 2.0 * one_beat
        angles[i] = moved / PI
        t += dt
        if hold[i] > 0:
            t += hold[i] * 2.0 * one_beat
        t += extra[i] * one_beat
        times[i + 1] = t

    hits = []
    t0 = times[1]
    for k in range(1, n + 1):
        if not mid[k - 1] and a[k - 1] != 999.0:
            hits.append((k, (times[k] - t0) / pitch))
    return hits, angles


def compare(path: str, tol_ms: float = 1.0, quiet: bool = False) -> float:
    data = parse.load(path)
    # 这份对照是「游戏算法一致性」检查，用默认口径（翻面砖不提前）
    tl = timeline.build(data)
    ghits, _ = game_hit_times(data)
    # AutoPlayTiles 段由游戏自己打，timeline 会剔除，这里同步剔除再比
    a = parse.get_angle_data(data)
    auto = timeline._auto_tiles(a, timeline.expand_repeat_events(data["actions"])[0])
    if auto:
        ghits = [(k, s) for k, s in ghits if (k - 1) not in auto]
    if len(ghits) != len(tl.hits):
        print(f"[砖数不一致] {path}: 游戏 {len(ghits)} / 我们 {len(tl.hits)}")
        return 9e9
    worst = (0.0, None)
    for (k, gt), h in zip(ghits, tl.hits):
        d = abs(gt - h.at_s) * 1000.0
        if d > worst[0]:
            worst = (d, (k, h.tile, gt, h.at_s))
    if not quiet or worst[0] > tol_ms:
        k, tile, gt, ours = worst[1]
        print(f"{path}\n  最大偏差 {worst[0]:.2f}ms @ floor{k} tile{tile} "
              f"(游戏 {gt:.4f}s / 我们 {ours:.4f}s)")
    return worst[0]


def main() -> None:
    paths = sys.argv[1:]
    if not paths:
        try:
            from app import find_workshop_dir
            ws = find_workshop_dir()
        except Exception:
            ws = None
        paths = [str(ws / "*" / "main.adofai")] if ws else []
    import glob
    files: list[str] = []
    for p in paths:
        files += glob.glob(p) if any(ch in p for ch in "*?") else glob.glob(p + "/*.adofai")
        if not files:
            files.append(p)
    bad = 0
    for f in files:
        try:
            if compare(f, quiet=True) > 1.0:
                bad += 1
        except Exception as e:
            print(f"[失败] {f}: {e}")
            bad += 1
    print(f"共 {len(files)} 个，偏差>1ms 的 {bad} 个")


if __name__ == "__main__":
    main()
