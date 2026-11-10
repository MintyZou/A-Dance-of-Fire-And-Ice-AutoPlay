"""高精度击打调度。

时间基准用 time.perf_counter，等待策略是「先粗睡、临近 2ms 忙等」，
避免 time.sleep 的调度抖动累积。停止条件：停止热键 / 鼠标移到左上角。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import input_win


@dataclass
class Event:
    at: float        # 距启动的秒数
    kind: str        # "down" / "up"
    vk: int


# 长按的松手点（尾判）略微晚 2ms：早松会被判没按满，晚一点只是精度稍差。
HOLD_RELEASE_EPS_S = 0.002
# 尾判砖本身又是长按砖时，松手后需要立刻重新按下（极罕见）。
REHOLD_GAP_S = 0.002
# Multitap 砖的连按间隔：游戏只判定最后一按，且提前按会算到上一块砖，
# 所以所有按下都排在该砖时刻之后，最后一下会比谱面晚 (taps-1)*间隔。
MULTITAP_GAP_S = 0.035
# 0.1°「伪转面」砖会把多块砖压到同一瞬间（间隔 <1ms）。游戏一次按下只推进
# 一块砖，而亚毫秒内连发的 SendInput 会被系统/游戏合并成一次按下，同组后面的
# 砖就漏判 miss。把同组击打拉开到约一帧，保证每次按下都被单独接收；本谱最大
# 组 4 块，最后一下最多晚 48ms，仍在 Perfect 判定窗（234~250BPM 下 60~65ms）内。
STACK_GAP_S = 0.016
STACK_WINDOW_S = 0.005


def _spread_stacked(times: list[float], speed_scale: float) -> None:
    """把间隔过小的连续击打拉开，保证每次按键都被游戏单独接收。"""
    gap = STACK_GAP_S * speed_scale
    window = STACK_WINDOW_S * speed_scale
    original = list(times)
    for i in range(1, len(times)):
        if 0 <= original[i] - original[i - 1] < window:
            times[i] = max(original[i], times[i - 1] + gap)


def build_schedule(hits, vk: int, *, press_s: float = 0.02,
                   speed_scale: float = 1.0, offset_ms: float = 0.0,
                   lead_in_s: float = 0.0, limit: int | None = None) -> list[Event]:
    base = offset_ms / 1000.0 + lead_in_s
    chosen = hits if limit is None else hits[:limit]
    times = [h.at_s * speed_scale + base for h in chosen]
    _spread_stacked(times, speed_scale)
    events: list[Event] = []
    for i, (h, t) in enumerate(zip(chosen, times)):
        hold = float(getattr(h, "hold_s", 0.0) or 0.0) * speed_scale
        release = bool(getattr(h, "release", False))
        taps = max(1, int(getattr(h, "taps", 1) or 1))
        nxt = chosen[i + 1] if i + 1 < len(chosen) else None
        nxt_is_release = bool(nxt is not None and getattr(nxt, "release", False))

        if release:
            # 长按的尾判砖：这一刻松手就是击打，不要再按下。
            events.append(Event(t + HOLD_RELEASE_EPS_S, "up", vk))
            if hold > 0:
                # 极罕见：尾判砖自身也是长按砖，松手后立刻重新按住。
                events.append(Event(t + HOLD_RELEASE_EPS_S + REHOLD_GAP_S,
                                    "down", vk))
            continue

        if taps > 1 and hold <= 0:
            # Multitap 砖：前 taps-1 下不判定（游戏直接吞掉），最后一下才判定。
            # 全部排在该砖时刻之后 —— 提前按会被算到上一块砖上。
            gap = MULTITAP_GAP_S * speed_scale
            press = min(press_s, gap * 0.5)
            for k in range(taps):
                events.append(Event(t + k * gap, "down", vk))
                events.append(Event(t + k * gap + press, "up", vk))
            continue

        events.append(Event(t, "down", vk))
        if hold > 0:
            # 长按砖：一直按到下一块砖（尾判砖）再松手，这里不排 up。
            # 只有被 limit 截断、后面没有尾判砖时才自行按时长松开。
            if nxt_is_release:
                continue
            events.append(Event(t + hold, "up", vk))
        elif i + 1 < len(times):
            # 必须在下一次按下之前松开，否则游戏只会看到「一直按住」而漏键。
            # 用间隔的 60% 封顶，给两边都留出余量。
            events.append(Event(min(t + press_s, t + (times[i + 1] - t) * 0.6),
                                "up", vk))
        else:
            events.append(Event(t + press_s, "up", vk))
    events.sort(key=lambda e: e.at)
    return events


def wait_hotkey(vk: int, label: str) -> None:
    """等待热键「按下」的瞬间即返回（这一刻就是第一次击打的时刻）。

    先等按键松开，避免用户一直按着导致立即触发。
    """
    print(f"等待 {label} ...（在第一次击打的瞬间按下）", flush=True)
    while input_win.is_down(vk):
        time.sleep(0.005)
    while not input_win.is_down(vk):
        time.sleep(0.005)


def _stop_monitor(stop_vk: int, flag: threading.Event,
                  done: threading.Event) -> None:
    """独立线程每 1ms 轮询 F2 / 鼠标急停。

    主循环在两次击打之间会粗睡很久，短按 F2 很容易落在两次轮询之间被漏掉，
    所以把停止检测放到独立线程里，检测到就置 flag 让主循环立刻退出。
    """
    born = time.perf_counter()
    while not done.is_set() and not flag.is_set():
        if time.perf_counter() - born > 0.05:      # 忽略启动时残留的按键
            if (stop_vk and input_win.is_down(stop_vk)) or input_win.cursor_at_corner():
                flag.set()
                return
        time.sleep(0.001)


def _wait_until(target: float, stop_vk: int, abort=None,
                stop_flag: threading.Event | None = None) -> bool:
    """返回 False 表示应中止。abort 是可选的「是否退出」回调（供 GUI 关窗用）。"""
    while True:
        remain = target - time.perf_counter()
        if remain <= 0:
            return True
        if stop_flag is not None and stop_flag.is_set():
            return False
        if abort is not None and abort():
            return False
        if input_win.is_down(stop_vk) or input_win.cursor_at_corner():
            if stop_flag is not None:
                stop_flag.set()
            return False
        if remain > 0.003:
            time.sleep(remain - 0.002)
        # 剩余 3ms 以内用忙等，换取亚毫秒精度


def play(events: list[Event], *, stop_vk: int, release_vk: int,
         dry_run: bool = False, abort=None) -> tuple[str, list[float]]:
    """返回 (状态, 每个事件的时间误差秒数列表)。"""
    start = time.perf_counter()
    errors: list[float] = []
    input_win.set_timer_resolution(1)
    stop_flag = threading.Event()
    done = threading.Event()
    threading.Thread(target=_stop_monitor, args=(stop_vk, stop_flag, done),
                     daemon=True).start()
    try:
        for ev in events:
            if not _wait_until(start + ev.at, stop_vk, abort, stop_flag):
                return "stopped", errors
            errors.append((time.perf_counter() - start) - ev.at)
            if not dry_run:
                if ev.kind == "down":
                    input_win.key_down(ev.vk)
                else:
                    input_win.key_up(ev.vk)
        return "done", errors
    finally:
        done.set()
        input_win.restore_timer_resolution(1)
        if not dry_run:
            input_win.key_up(release_vk)
