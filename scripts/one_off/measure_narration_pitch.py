#!/usr/bin/env python3
"""Measure the pitch of every stored article-narration segment (#600).

Background (2026-10-06). On article 246 two paragraphs came back from
ElevenLabs in a voice that did not sound like Diego: higher pitch, felt
faster. Median F0 of those raw clips was 197.5 / 183.9 Hz against 134-159 Hz
for every other paragraph; post-processing was ruled out (pitch identical raw
vs final, ``atempo`` preserves pitch). A pitch QA step for the narration job
(#601) wants to retry such clips automatically, and this script supplies the
numbers it needs: the normal within-article spread, the outlier tail, a
deviation threshold, the shortest segment that measures reliably, and how
many articles would cross a "10 % of segments" cutoff.

Measurement only: it reads the raw hash-keyed segments over the public CDN
and never writes to Spaces or the DB.

Two halves, because the backend image has the DB access and the operator's
machine has the room:

``--export`` runs **inside pic-backend** (read-only DB query) and prints one
JSON document: every article with an ``audio_manifest_es``, its manifest, and
the text of each segment whose hash still matches the saved ``content_es``
(``None`` where the article was edited after narration, so wpm is skipped).

The default mode measures that export. It needs ffmpeg and numpy only (no app
imports), downloads each segment to a temp file, decodes it to 16 kHz mono and
deletes it, so memory stays at one clip. Don't run it on the droplet (4 GB, no
swap, shared with other projects).

F0 estimator: 40 ms frames / 10 ms hop, normalized autocorrelation (FFT) over
60-300 Hz; a frame is voiced when its AC peak > 0.6 and its RMS > 0.3 x the
clip RMS. The segment value is the median over voiced frames. ``--pyin N``
re-measures the N most-deviant segments with librosa's pYIN as a cross-check
of the simple estimator.

Usage:
    # 1. export (read-only) from prod
    ssh wanderindev@<droplet-ip> 'docker exec -i pic-backend python - --export' \\
        < scripts/one_off/measure_narration_pitch.py > /scr/narrations.json
    # 2. measure locally, e.g. in the backend test image (has ffmpeg)
    docker run --rm -u 0 -v $PWD:/w -v /scr:/scr --entrypoint sh pic-backend-test:latest \\
        -c "pip install -q numpy librosa && python /w/scripts/one_off/measure_narration_pitch.py \\
            --export-file /scr/narrations.json --out-dir /scr/pitch --pyin 15"
    # re-analyse without re-downloading
    python scripts/one_off/measure_narration_pitch.py --export-file ... --out-dir /scr/pitch --reuse
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

CDN = "https://panamaincontext.nyc3.digitaloceanspaces.com/"
SR = 16000
FRAME = int(0.04 * SR)
HOP = int(0.01 * SR)
F0_MIN, F0_MAX = 60, 300
AC_VOICED = 0.6
RMS_REL = 0.3
SPEECH_DBFS = -40
# Mirror of article_audio's concat silences, for timestamps in the final read.
PARAGRAPH_SILENCE_MS = 1000
HEADING_SILENCE_MS = 1400
THRESHOLDS = (8, 10, 12, 15, 20, 25)


# --------------------------------------------------------------------------- export
def export() -> None:
    sys.path.insert(0, "/app")
    from app.core.database import SessionLocal
    from app.models.article import Article
    from app.services.article_audio import segment_article, segment_hash

    db = SessionLocal()
    out = []
    try:
        rows = (
            db.query(Article)
            .filter(Article.audio_manifest_es.isnot(None))
            .order_by(Article.id)
            .all()
        )
        for a in rows:
            texts = {}
            if a.content_es:
                for text, _ in segment_article(a.title_es or "", a.content_es):
                    texts[segment_hash(text)] = text
            out.append(
                {
                    "id": a.id,
                    "audio_url_es": a.audio_url_es,
                    "segments": [
                        {**e, "text": texts.get(e["hash"])} for e in a.audio_manifest_es
                    ],
                }
            )
    finally:
        db.close()
    json.dump(out, sys.stdout, ensure_ascii=False)


# --------------------------------------------------------------------------- measure
def decode(path: str):
    import numpy as np

    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "quiet",
            "-i",
            path,
            "-ac",
            "1",
            "-ar",
            str(SR),
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def f0_frames(x):
    """Per-frame F0 (Hz) of the voiced frames of ``x``."""
    import numpy as np

    if len(x) < FRAME:
        return np.array([])
    n = 1 + (len(x) - FRAME) // HOP
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    frames = x[idx].astype(np.float64)
    frames -= frames.mean(axis=1, keepdims=True)
    rms = np.sqrt((frames**2).mean(axis=1))
    clip_rms = math.sqrt(float((x.astype(np.float64) ** 2).mean()))
    keep = rms >= RMS_REL * clip_rms
    frames = frames[keep]
    if not len(frames):
        return np.array([])
    spec = np.fft.rfft(frames, n=2 * FRAME, axis=1)
    ac = np.fft.irfft(np.abs(spec) ** 2, axis=1)[:, :FRAME]
    ac /= ac[:, :1] + 1e-12
    lo, hi = SR // F0_MAX, SR // F0_MIN
    lag = lo + np.argmax(ac[:, lo:hi], axis=1)
    peak = ac[np.arange(len(ac)), lag]
    return SR / lag[peak > AC_VOICED]


def speech_seconds(x) -> float:
    """First-to-last frame above SPEECH_DBFS, like the post-process trim."""
    import numpy as np

    n = len(x) // HOP
    if not n:
        return 0.0
    e = np.sqrt((x[: n * HOP].reshape(n, HOP).astype(np.float64) ** 2).mean(axis=1))
    voiced = np.where(20 * np.log10(e + 1e-9) > SPEECH_DBFS)[0]
    return (voiced[-1] - voiced[0]) / 100 if len(voiced) else 0.0


def window_stability(f0, windows=(0.5, 1, 2, 3, 5, 8)):
    """|median of a contiguous run of N voiced seconds - whole-clip median|, in %.

    Long clips stand in for short ones: how far off would the estimate be if
    only N seconds of voicing were available?
    """
    import numpy as np

    out = {}
    if len(f0) < 200:
        return out
    full = float(np.median(f0))
    for w in windows:
        k = int(w * 100)  # voiced frames are 10 ms apart
        if k >= len(f0):
            continue
        devs = [
            abs(float(np.median(f0[s : s + k])) / full - 1) * 100
            for s in range(0, len(f0) - k, max(1, k // 2))
        ]
        out[str(w)] = devs
    return out


def measure_one(seg: dict, out_dir: str, reuse: bool) -> dict:
    import numpy as np

    # Keyed by the object key, not the text hash: identical text in two
    # articles is two different clips.
    cache = os.path.join(out_dir, "cache", seg["key"].replace("/", "_") + ".json")
    if reuse and os.path.exists(cache):
        with open(cache) as fh:
            return json.load(fh)
    with tempfile.NamedTemporaryFile(suffix=".mp3") as tmp:
        for attempt in range(3):
            try:
                tmp.seek(0)
                tmp.truncate()
                with urllib.request.urlopen(CDN + seg["key"], timeout=60) as r:
                    tmp.write(r.read())
                break
            except Exception as exc:
                if attempt == 2:
                    return {"error": f"download: {exc}"}
                time.sleep(2)
        tmp.flush()
        t0 = time.monotonic()
        try:
            x = decode(tmp.name)
        except subprocess.CalledProcessError as exc:
            return {"error": f"decode: {exc}"}
        f0 = f0_frames(x)
        elapsed = time.monotonic() - t0
    res = {
        "clip_s": round(len(x) / SR, 2),
        "speech_s": round(speech_seconds(x), 2),
        "voiced_s": round(len(f0) / 100, 2),
        "f0": round(float(np.median(f0)), 1) if len(f0) else None,
        "f0_p10": round(float(np.percentile(f0, 10)), 1) if len(f0) else None,
        "f0_p90": round(float(np.percentile(f0, 90)), 1) if len(f0) else None,
        "estimator_s": round(elapsed, 3),
        "stability": window_stability(f0),
    }
    tmp_cache = f"{cache}.{os.getpid()}.{id(res)}"
    with open(tmp_cache, "w") as fh:
        json.dump(res, fh)
    os.replace(tmp_cache, cache)  # atomic: a --reuse never reads a torn file
    return res


def pct(values, q):
    import numpy as np

    return float(np.percentile(values, q)) if len(values) else float("nan")


def pyin_f0(url: str) -> float | None:
    import librosa
    import numpy as np

    with tempfile.NamedTemporaryFile(suffix=".mp3") as tmp:
        with urllib.request.urlopen(url, timeout=60) as r:
            tmp.write(r.read())
        tmp.flush()
        y = decode(tmp.name)
    f0, flag, _ = librosa.pyin(y, fmin=F0_MIN, fmax=F0_MAX, sr=SR, frame_length=1024)
    v = f0[flag & ~np.isnan(f0)]
    return round(float(np.median(v)), 1) if len(v) else None


def measure(args) -> None:
    import numpy as np

    with open(args.export_file) as fh:
        articles = json.load(fh)
    if args.articles:
        wanted = {int(a) for a in args.articles.split(",")}
        articles = [a for a in articles if a["id"] in wanted]
    os.makedirs(os.path.join(args.out_dir, "cache"), exist_ok=True)

    rows = []
    for art in articles:
        t = 0
        for pos, seg in enumerate(art["segments"]):
            if pos:
                t += HEADING_SILENCE_MS if seg["is_heading"] else PARAGRAPH_SILENCE_MS
            rows.append({"article": art["id"], "pos": pos, "start_ms": t, **seg})
            t += int(seg.get("duration_ms") or 0)
    print(f"{len(articles)} articles, {len(rows)} segments", file=sys.stderr)

    done = 0
    with ThreadPoolExecutor(args.workers) as pool:
        futures = [pool.submit(measure_one, r, args.out_dir, args.reuse) for r in rows]
        for r, fut in zip(rows, futures, strict=True):
            r.update(fut.result())
            done += 1
            if done % 250 == 0:
                print(f"  {done}/{len(rows)}", file=sys.stderr)

    errors = [r for r in rows if r.get("error")]
    for r in rows:
        r["words"] = len(r["text"].split()) if r.get("text") else None
        r["wpm"] = (
            round(r["words"] / r["speech_s"] * 60, 1)
            if r["words"] and r.get("speech_s") and not r["is_heading"]
            else None
        )

    # Article baseline: median over paragraphs with enough voicing.
    by_art: dict[int, list[dict]] = {}
    for r in rows:
        by_art.setdefault(r["article"], []).append(r)
    for segs in by_art.values():
        base_pool = [
            r["f0"]
            for r in segs
            if not r["is_heading"] and r.get("f0") and r["voiced_s"] >= args.min_voiced
        ]
        base = statistics.median(base_pool) if base_pool else None
        for r in segs:
            r["article_f0"] = round(base, 1) if base else None
            if base and r.get("f0"):
                r["dev_pct"] = round((r["f0"] / base - 1) * 100, 1)
                r["dev_st"] = round(12 * math.log2(r["f0"] / base), 2)
            r["measurable"] = bool(
                not r["is_heading"]
                and r.get("f0")
                and r["voiced_s"] >= args.min_voiced
                and base
            )

    with open(os.path.join(args.out_dir, "segments.csv"), "w", newline="") as fh:
        cols = [
            "article", "pos", "start_ms", "is_heading", "words", "clip_s", "speech_s",
            "voiced_s", "f0", "f0_p10", "f0_p90", "article_f0", "dev_pct", "dev_st",
            "wpm", "measurable", "estimator_s", "hash", "key",
        ]  # fmt: skip
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    # ---------------------------------------------------------------- report
    meas = [r for r in rows if r["measurable"]]
    heads = [r for r in rows if r["is_heading"]]
    lines = []
    p = lines.append
    p(f"## Corpus\n\n{len(by_art)} articles, {len(rows)} segments ({len(heads)} headings), "
      f"{len(errors)} download/decode errors.")  # fmt: skip
    p(
        f"Measurable paragraphs (non-heading, >= {args.min_voiced}s voiced): {len(meas)}.\n"
    )

    bases = [s[0]["article_f0"] for s in by_art.values() if s[0]["article_f0"]]
    p("## Article baselines (median of paragraph medians)\n")
    p(f"min {min(bases):.1f} / p10 {pct(bases, 10):.1f} / median {pct(bases, 50):.1f} / "
      f"p90 {pct(bases, 90):.1f} / max {max(bases):.1f} Hz\n")  # fmt: skip

    devs = np.array([r["dev_pct"] for r in meas])
    p("## Deviation of paragraphs from their article baseline (%)\n")
    p("| p1 | p5 | p25 | p50 | p75 | p95 | p99 | max |")
    p("|---|---|---|---|---|---|---|---|")
    p("| " + " | ".join(f"{pct(devs, q):+.1f}" for q in (1, 5, 25, 50, 75, 95, 99))
      + f" | {devs.max():+.1f} |\n")  # fmt: skip
    p("Histogram (2 % bins, |dev| >= 6 % only):\n")
    p("```")
    for lo in range(-40, 60, 2):
        n = int(((devs >= lo) & (devs < lo + 2)).sum())
        if n and abs(lo) >= 6:
            p(f"{lo:+4d}..{lo + 2:+4d}%  {n:5d}  {'#' * min(n, 80)}")
    p("```\n")

    p("## Outliers per threshold\n")
    p("| ±threshold | outlier paragraphs | rate | above / below | articles with any "
      "| mean per article | max per article | articles > 10 % |")  # fmt: skip
    p("|---|---|---|---|---|---|---|---|")
    for th in THRESHOLDS:
        per = []
        over10 = 0
        for segs in by_art.values():
            m = [r for r in segs if r["measurable"]]
            k = sum(abs(r["dev_pct"]) > th for r in m)
            per.append(k)
            if m and k / len(m) > 0.10:
                over10 += 1
        out = [r for r in meas if abs(r["dev_pct"]) > th]
        up = sum(r["dev_pct"] > 0 for r in out)
        p(f"| {th} % | {len(out)} | {len(out) / len(meas) * 100:.1f} % | {up} / {len(out) - up} "
          f"| {sum(k > 0 for k in per)} | {statistics.mean(per):.2f} | {max(per)} | {over10} |")  # fmt: skip
    p("")

    p("## Estimate stability vs voiced length (|window median - clip median|, %)\n")
    p("From paragraphs with >= 2 s voiced: contiguous N-second voiced windows.\n")
    p("| voiced s | windows | p50 | p90 | p95 | p99 |")
    p("|---|---|---|---|---|---|")
    pooled: dict[str, list[float]] = {}
    for r in meas:
        for w, d in (r.get("stability") or {}).items():
            pooled.setdefault(w, []).extend(d)
    for w in sorted(pooled, key=float):
        d = pooled[w]
        p(f"| {w} | {len(d)} | {pct(d, 50):.1f} | {pct(d, 90):.1f} | {pct(d, 95):.1f} "
          f"| {pct(d, 99):.1f} |")  # fmt: skip
    short = [
        r
        for r in rows
        if not r["is_heading"] and r.get("f0") and r["voiced_s"] < args.min_voiced
    ]
    p(f"\nNon-heading segments below the {args.min_voiced}s cut: {len(short)}.")
    hv = [r["voiced_s"] for r in heads if r.get("voiced_s") is not None]
    if hv:
        p(f"Heading voiced seconds: median {pct(hv, 50):.1f}, p90 {pct(hv, 90):.1f}.\n")

    wp = [r for r in meas if r.get("wpm")]
    if len(wp) > 10:
        dv = np.array([r["dev_pct"] for r in wp])
        wm = np.array([r["wpm"] for r in wp])
        p("## Pitch vs pace\n")
        p(f"{len(wp)} paragraphs with matching text. Pearson r(dev %, wpm) = "
          f"{np.corrcoef(dv, wm)[0, 1]:.2f}; r(|dev %|, wpm) = {np.corrcoef(abs(dv), wm)[0, 1]:.2f}.")  # fmt: skip
        hi = wm[dv > 15]
        p(f"Median wpm: all {np.median(wm):.0f}; paragraphs > +15 % pitch {np.median(hi):.0f} "
          f"(n={len(hi)}).\n" if len(hi) else "")  # fmt: skip

    est = [r["estimator_s"] for r in rows if r.get("estimator_s") is not None]
    clip = sum(r["clip_s"] for r in rows if r.get("clip_s"))
    p("## Estimator cost\n")
    p(f"Decode + F0: median {pct(est, 50) * 1000:.0f} ms / segment, p95 {pct(est, 95) * 1000:.0f} ms; "
      f"{sum(est) / clip * 1000:.1f} ms per second of audio.\n")  # fmt: skip

    worst = sorted(meas, key=lambda r: -abs(r["dev_pct"]))[: args.spot]
    if args.pyin:
        print("pYIN cross-check...", file=sys.stderr)
        for r in worst[: args.pyin]:
            try:
                r["pyin"] = pyin_f0(CDN + r["key"])
            except Exception as exc:  # cross-check only
                r["pyin"] = f"err {exc}"
    p(f"## Spot list: {len(worst)} most-deviant paragraphs\n")
    p(
        "Timestamps are in the article's *current* narration (manifest order + concat silences).\n"
    )
    p(
        "| article | pos | at | voiced s | F0 Hz | baseline | dev | wpm | pYIN Hz | text |"
    )
    p("|---|---|---|---|---|---|---|---|---|---|")
    for r in worst:
        s = r["start_ms"] // 1000
        txt = (r.get("text") or "(text changed since narration)")[:60].replace("|", "/")
        p(f"| {r['article']} | {r['pos']} | {s // 60}:{s % 60:02d} | {r['voiced_s']} | {r['f0']} "
          f"| {r['article_f0']} | {r['dev_pct']:+.1f} % | {r.get('wpm') or ''} "
          f"| {r.get('pyin', '')} | {txt} |")  # fmt: skip

    report = "\n".join(lines)
    with open(os.path.join(args.out_dir, "report.md"), "w") as fh:
        fh.write(report + "\n")
    print(report)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--export", action="store_true", help="dump manifests (run inside pic-backend)"
    )
    ap.add_argument("--export-file", help="JSON produced by --export")
    ap.add_argument("--out-dir", default="pitch-out")
    ap.add_argument("--articles", help="comma-separated ids to restrict to")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument(
        "--min-voiced",
        type=float,
        default=2.0,
        help="voiced s for a paragraph to count",
    )
    ap.add_argument("--spot", type=int, default=20)
    ap.add_argument(
        "--pyin", type=int, default=0, help="cross-check the N worst with pYIN"
    )
    ap.add_argument(
        "--reuse", action="store_true", help="reuse cached per-segment results"
    )
    args = ap.parse_args()
    if args.export:
        export()
    elif args.export_file:
        measure(args)
    else:
        ap.error("--export or --export-file is required")


if __name__ == "__main__":
    main()
