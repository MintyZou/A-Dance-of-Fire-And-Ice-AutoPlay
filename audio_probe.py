"""用歌曲音频校验谱面时间轴（可选工具，不影响自动演奏主流程）。

原理：谱面是照着音乐节拍写的。把脚本算出的击打时刻平移一个常数 C 后，
应与音频的起音(onset)峰值对齐。C 应等于 settings.offset/1000。
若某段（尤其 Pause 之后）需要另一个 C，说明该段存在整数拍级的错位。

用法：
    python audio_probe.py                       # 用 config.json 里的谱面
    python audio_probe.py --level X.adofai --song X.ogg --around 75

依赖：numpy、soundfile（pip install numpy soundfile）。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import parse
import timeline

ROOT = Path(__file__).resolve().parent


def onset_envelope(song: Path):
    import numpy as np
    import soundfile as sf

    y, sr = sf.read(str(song))
    if y.ndim > 1:
        y = y.mean(axis=1)
    y = y.astype(np.float32)
    n, hop = 1024, 256
    win = np.hanning(n).astype(np.float32)
    nf = 1 + (len(y) - n) // hop
    frames = y[np.arange(n)[None, :] + hop * np.arange(nf)[:, None]] * win
    spec = np.abs(np.fft.rfft(frames, axis=1))
    flux = np.r_[0, np.maximum(np.diff(spec, axis=0), 0).sum(1)]
    smooth = np.convolve(flux, np.ones(24) / 24, mode="same")
    env = np.maximum(flux - smooth, 0)
    env /= env.std() + 1e-9
    return env, sr, hop, len(y) / sr


def main() -> None:
    ap = argparse.ArgumentParser(description="用音频校验谱面时间轴")
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    ap.add_argument("--level", default=None)
    ap.add_argument("--song", default=None, help="默认取谱面同目录下 settings.songFilename")
    ap.add_argument("--around", type=int, default=None, help="列出该砖附近的击打与音频起音峰")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    level = Path(args.level or cfg["level"])
    data = parse.load(level)
    song = Path(args.song) if args.song else level.parent / data["settings"]["songFilename"]

    env, sr, hop, dur = onset_envelope(song)
    tl = timeline.build(data)
    import numpy as np
    hits = np.array([h.at_s for h in tl.hits])
    c = tl.offset_ms / 1000.0

    print(f"谱面 {level.name}")
    print(f"歌曲 {song.name}  {dur:.3f}s")
    print(f"脚本击打 {len(hits)} 次，跨度 {hits[-1]:.3f}s")
    print(f"对齐常数 C = offset = {c:.3f}s，脚本末端 ≈ {hits[-1] + c:.3f}s")

    def best_lag(lo, hi, span=2.0):
        hs = hits[(hits >= lo) & (hits <= hi)]
        if len(hs) < 4:
            return None, 0, len(hs)
        lags = np.arange(c - span / 2, c + span / 2, 0.002)
        scores = np.array([
            env[np.clip(((hs + L) * sr / hop).astype(int), 0, len(env) - 1)].mean()
            for L in lags
        ])
        j = int(np.argmax(scores))
        return lags[j], scores[j], len(hs)

    print("\n分段对齐（全曲最优 lag 应稳定在 C 附近）:")
    bounds = [0.0] + [h.at_s for h in tl.hits if any(
        a.get("floor") == h.tile and a.get("eventType") == "Pause" for a in data["actions"]
    )] + [hits[-1]]
    bounds = sorted(set(round(b, 3) for b in bounds))
    for i in range(len(bounds) - 1):
        lo, hi = bounds[i] + 0.3, bounds[i + 1] - 0.3
        if hi - lo < 1:
            continue
        lag, sc, n = best_lag(lo, hi)
        if lag is not None:
            print(f"  [{lo:7.2f},{hi:7.2f}] n={n:3d}  lag={lag:7.3f}s "
                  f"(偏离 C {lag - c:+.3f}s)  score={sc:.2f}")

    if args.around is not None:
        print(f"\n=== tile {args.around} 附近：音频起音峰 vs 脚本击打(+C) ===")
        t0, t1 = hits[max(0, args.around - 12)] + c - 0.5, hits[min(len(hits) - 1, args.around + 12)] + c + 0.5
        rows = []
        i0, i1 = int(t0 * sr / hop), int(t1 * sr / hop)
        for i in range(max(2, i0), min(len(env) - 2, i1)):
            t = i * hop / sr
            if env[i] > 1.5 and env[i] > env[i - 1] and env[i] >= env[i + 1]:
                rows.append((t, f"AUDIO {env[i]:5.2f}"))
        for h in tl.hits:
            t = h.at_s + c
            if t0 <= t <= t1:
                rows.append((t, f"hit#{h.index:<4d} tile{h.tile:<4d} rel{h.angle:>5.0f}"))
        for t, label in sorted(rows):
            print(f"  {t:9.4f}  {label}")


if __name__ == "__main__":
    main()
