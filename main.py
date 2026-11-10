"""ADOFAI 自动演奏工具 —— 命令行入口。

    python main.py analyze                 # 分析谱面并校验时间轴
    python main.py hits --limit 30         # 打印击打时间轴
    python main.py play                    # 载入谱面、按启动热键开始自动演奏
    python main.py play --dry-run --limit 50  # 只跑调度、不发送按键（测抖动）
    python main.py analyze --pauses        # 列出每个中途倒数的位置与停顿
"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

import parse
import timeline

ROOT = Path(__file__).resolve().parent


def ogg_duration(path: Path) -> float | None:
    """纯标准库读取 OGG/Vorbis 时长（最后一页 granule / 采样率）。"""
    try:
        b = path.read_bytes()
    except OSError:
        return None
    offs: list[int] = []
    i = 0
    while True:
        j = b.find(b"OggS", i)
        if j < 0:
            break
        offs.append(j)
        i = j + 4
    if not offs:
        return None
    last = offs[-1]
    granule = struct.unpack_from("<q", b, last + 6)[0]
    first = offs[0]
    nseg = b[first + 26]
    p = first + 27 + nseg
    if b[p:p + 7] != b"\x01vorbis":
        return None
    rate = struct.unpack_from("<I", b, p + 12)[0]
    return granule / rate if rate else None


def load_config(args) -> dict:
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.level:
        cfg["level"] = args.level
    return cfg


def print_analysis(cfg: dict, tl: timeline.Timeline, data: dict,
                   level_path: Path, show_hits: int) -> None:
    print("=" * 62)
    print("谱面:", level_path)
    print("=" * 62)
    print(f"BPM {tl.bpm:g}   pitch x{tl.pitch:g}   offset {tl.offset_ms:g}ms")
    print(f"砖块 {tl.tile_count}   击打 {len(tl.hits)}   midspin(999) {tl.midspin_count}")
    if tl.pause_count:
        print(f"中途 Pause(321倒数) x{tl.pause_count}   "
              f"{tl.pause_beats:g} 拍 -> 停顿 {tl.pause_seconds:.2f}s（按各砖曲速）")
        if tl.pause_taps:
            extra = sum(1 for h in tl.hits if h.tile < 0)
            print(f"  Pause 期间补发击打: 模式={tl.pause_taps}，共补 {extra} 次"
                  f"（击打总数 {len(tl.hits)}）")
        if tl.pause_offset_ms or tl.pause_offset_per_beat_ms or tl.pause_offset_beats:
            print(f"  Pause 停顿校准: 每次 {tl.pause_offset_ms:+g}ms "
                  f" + 每拍 {tl.pause_offset_per_beat_ms:+g}ms "
                  f" + {tl.pause_offset_beats:+g} 拍(按曲速换算)")
    if tl.hold_count:
        presses = sum(1 for h in tl.hits if not h.release)
        print(f"Hold(长按) x{tl.hold_count} 处（按到下一块砖，松手=尾判，该砖不再按下）"
              f"  额外循环 {tl.hold_beats:g} 拍 → 共按住 {tl.hold_seconds:.2f}s")
        print(f"  实际按键次数 {presses}（{len(tl.hits)} 块砖，其中 "
              f"{len(tl.hits) - presses} 块靠松手判定）")
    if tl.multiplanet_count:
        print(f"MultiPlanet(多球) x{tl.multiplanet_count}   三球段共 {tl.three_planet_seconds:.2f}s"
              f"（行程角度 -60°，60° 砖绕满一圈）")
    if tl.multitap_count:
        print(f"Multitap(连按) {tl.multitap_count} 块 —— 每块在砖后连按 2 下"
              f"（前几下不判定，最后一按才判定）")
    if tl.auto_tile_count:
        print(f"AutoPlayTiles: {tl.auto_tile_count} 块由游戏自动完成，已从击打列表剔除")
    if tl.freeroam_count:
        print(f"FreeRoam(自由移动) x{tl.freeroam_count}：该段停顿 {tl.freeroam_seconds:.2f}s，"
              f"但需要方向键走位，脚本无法自动通过")
    print(f"总拍数 {tl.total_beats:.1f}   总时长 {tl.total_seconds:.2f}s")
    print(f"前奏(lead-in) {tl.lead_in_s:.3f}s   实际演奏跨度 {tl.play_seconds:.2f}s")
    print(f"平均 KPS {len(tl.hits) / tl.play_seconds:.2f}" if tl.play_seconds else "")

    # 用同目录歌曲校验
    song = data["settings"].get("songFilename")
    if song:
        dur = ogg_duration(level_path.parent / song)
        if dur:
            delta = tl.total_seconds - dur
            print(f"歌曲 {song}: {dur:.2f}s   关卡-歌曲差 {delta:+.2f}s"
                  f"（倒计时/offset/尾音会造成约 1~2s 偏差）")

    print("-" * 62)
    top = sorted(tl.event_counts.items(), key=lambda kv: -kv[1])[:12]
    print("事件统计 (前 12):", ", ".join(f"{k}={v}" for k, v in top))
    print(f"Twirl={tl.event_counts.get('Twirl', 0)}  "
          f"SetSpeed={tl.event_counts.get('SetSpeed', 0)}")

    if show_hits:
        print("-" * 62)
        print(f"{'#':>5} {'tile':>6} {'角度':>8} {'at(s)':>10} {'BPM':>9} {'按住s':>7}")
        for h in tl.hits[:show_hits]:
            print(f"{h.index:>5} {h.tile:>6} {h.angle:>8.1f} {h.at_s:>10.4f} "
                  f"{h.bpm:>9.1f} {h.hold_s:>7.3f}")
        if len(tl.hits) > show_hits:
            print(f"  ... 共 {len(tl.hits)} 次击打")
        last = tl.hits[-1]
        print(f"  末次: #{last.index} tile={last.tile} at={last.at_s:.4f}s")

    if tl.warnings:
        print("-" * 62)
        for w in tl.warnings:
            print("[提示]", w)
    print("=" * 62)


def print_pauses(tl: timeline.Timeline, data: dict) -> None:
    """列出每个中途 Pause 倒数的位置、停顿时长、以及它插在哪两次击打之间。

    用来对照游戏里 3-2-1 倒数出现的位置，确认脚本的停顿没有放错砖。
    """
    a = parse.get_angle_data(data)
    n = len(a)
    bpm_tile = timeline._bpm_by_tile(n, data["actions"], tl.bpm)
    mid = {i for i, x in enumerate(a) if x == timeline.MIDSPIN}
    last_tile = tl.hits[-1].tile if tl.hits else 0
    print("-" * 92)
    print(f"{'floor':>6} {'duration':>9} {'ticks':>6} {'有效BPM':>9} "
          f"{'停顿s':>8} {'累计s':>8} {'补按':>6}   停顿插在")
    cum = 0.0
    real = [h for h in tl.hits if h.tile >= 0]
    for ev in data["actions"]:
        if ev.get("eventType") != "Pause":
            continue
        f = int(ev.get("floor", 0))
        beats = float(ev.get("duration", 0))
        eff = bpm_tile[min(f, n + 1)] * tl.pitch
        ignored = f >= n or f in mid or f > last_tile
        sec = 0.0 if ignored else max(
            0.0, (beats + tl.pause_offset_beats) * 60.0 / eff
            + (tl.pause_offset_ms + beats * tl.pause_offset_per_beat_ms) / 1000.0)
        cum += sec
        prev = next((h for h in reversed(real) if h.tile < f), None)
        nxt = next((h for h in real if h.tile >= f), None)
        if ignored:
            where = "(被游戏忽略：末砖/midspin)"
        elif prev and nxt:
            where = f"击打 tile{prev.tile} -> tile{nxt.tile}"
        else:
            where = "(开头/结尾)"
        ticks = int(ev.get('countdownTicks', 0))
        cnt = timeline._tap_count(tl.pause_taps, ticks, beats)
        print(f"{f:>6} {beats:>9.2f} {ticks:>6} "
              f"{eff:>9.1f} {sec:>8.3f} {cum:>8.3f} {cnt:>6}   {where}")
    total_extra = sum(1 for h in tl.hits if h.tile < 0)
    print(f"合计停顿 {cum:.3f}s；补按模式={tl.pause_taps}，共补 {total_extra} 次")
    if tl.pause_taps:
        print("提示：补按在停顿窗口内等分。注意倒数的沙锤声是游戏自己播的（sndHat），"
              "通常不需要补按（pause_taps=0）。")
    print("-" * 92)


def run_play(cfg: dict, tl: timeline.Timeline, args) -> None:
    import input_win   # Windows 专用，延迟导入，保证 analyze 跨平台可用
    import player

    game_vk = input_win.to_vk(cfg["key"])
    start_vk = input_win.to_vk(cfg["start_hotkey"])
    stop_vk = input_win.to_vk(cfg["stop_hotkey"])
    lead_in = args.lead_in if args.lead_in is not None else float(cfg.get("lead_in_s", 0.0))
    press_s = args.press if args.press is not None else float(cfg.get("press_s", 0.02))
    events = player.build_schedule(
        tl.hits, game_vk,
        press_s=press_s,
        speed_scale=float(cfg.get("speed_scale", 1.0)),
        offset_ms=float(cfg.get("offset_ms", 0.0)),
        lead_in_s=lead_in,
        limit=args.limit,
    )
    n_hits = len(tl.hits) if args.limit is None else min(args.limit, len(tl.hits))

    print("=" * 62)
    print("谱面:", cfg["level"])
    print(f"击打 {n_hits} 次 / 共 {len(tl.hits)}   事件 {len(events)} 个   "
          f"末事件 {events[-1].at:.2f}s" if events else "无击打")
    print(f"按键 {cfg['key']}   启动 {cfg['start_hotkey']}   "
          f"停止 {cfg['stop_hotkey']} / 鼠标移到屏幕左上角")
    print(f"offset {cfg['offset_ms']}ms   speed_scale {cfg['speed_scale']}   "
          f"lead-in {lead_in}s   press {press_s * 1000:.0f}ms")
    if args.dry_run:
        print("[dry-run] 只运行调度，不会发送任何按键")
    else:
        print("!! 请确认《冰与火之舞》窗口已聚焦 !!")
    print("=" * 62)

    player.wait_hotkey(start_vk, cfg["start_hotkey"])
    print("开始演奏 ...", flush=True)
    status, errors = player.play(events, stop_vk=stop_vk,
                                 release_vk=game_vk, dry_run=args.dry_run)
    if errors:
        mx = max(errors) * 1000
        avg = sum(errors) / len(errors) * 1000
        print(f"调度误差: 平均 {avg:+.3f}ms  最大 {mx:+.3f}ms  ({len(errors)} 个事件)")
    print("结果:", "完成" if status == "done" else "已中止")


def main() -> None:
    ap = argparse.ArgumentParser(description="ADOFAI 谱面分析 / 自动演奏")
    ap.add_argument("command", nargs="?", default="analyze",
                    choices=["analyze", "hits", "play"])
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    ap.add_argument("--level", default=None, help="覆盖 config.json 里的 level 路径")
    ap.add_argument("--limit", type=int, default=None,
                    help="hits 打印条数 / play 只演奏前 N 次击打（默认全部）")
    ap.add_argument("--hits", type=int, default=10, help="analyze 附带打印的击打条数")
    ap.add_argument("--lead-in", type=float, default=None,
                    help="启动后到第一次击打的等待秒数（默认 config.lead_in_s）")
    ap.add_argument("--press", type=float, default=None, help="单次按键按住秒数")
    ap.add_argument("--dry-run", action="store_true", help="只运行调度，不发送按键")
    ap.add_argument("--pauses", action="store_true", help="analyze 时列出每个中途倒数的位置")
    ap.add_argument("--around", type=int, default=None,
                    help="hits 时只打印 tile 在该值 ±12 范围内的击打，便于对照游戏画面")
    args = ap.parse_args()

    cfg = load_config(args)
    level_path = Path(cfg["level"])
    data = parse.load(level_path)
    tl = timeline.build(
        data,
        pause_offset_ms=float(cfg.get("pause_offset_ms", 0.0)),
        pause_offset_per_beat_ms=float(cfg.get("pause_offset_per_beat_ms", 0.0)),
        pause_offset_beats=float(cfg.get("pause_offset_beats", 0.0)),
        pause_taps=cfg.get("pause_taps", 0),
    )

    if args.command == "hits":
        print(f"{'#':>5} {'tile':>6} {'角度':>8} {'at(s)':>10} {'BPM':>9} {'间隔s':>8} {'按住s':>7}")
        if args.around is not None:
            lo = args.around - 12
            hi = args.around + 12
            real = [h for h in tl.hits if lo <= h.tile <= hi]
            t_lo = min((h.at_s for h in real), default=-1.0)
            t_hi = max((h.at_s for h in real), default=1e9)
            chosen = [h for h in tl.hits
                      if lo <= h.tile <= hi or (h.tile < 0 and t_lo <= h.at_s <= t_hi)]
        else:
            chosen = tl.hits[:args.limit or 10]
        for h in chosen:
            gap = h.at_s - tl.hits[h.index - 1].at_s if h.index > 0 else 0.0
            tag = (" <松手>" if h.release else
                   (" <长按>" if h.hold_s else (" <补按>" if h.tile < 0 else "")))
            if getattr(h, "taps", 1) > 1:
                tag += f" <连按{h.taps}>"
            print(f"{h.index:>5} {h.tile:>6} {h.angle:>8.1f} {h.at_s:>10.4f} "
                  f"{h.bpm:>9.1f} {gap:>8.4f} {h.hold_s:>7.3f}{tag}")
        print(f"共 {len(tl.hits)} 次击打")
    elif args.command == "play":
        run_play(cfg, tl, args)
    else:
        print_analysis(cfg, tl, data, level_path, args.hits)
        if args.pauses:
            print_pauses(tl, data)


if __name__ == "__main__":
    main()
