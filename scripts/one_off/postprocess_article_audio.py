#!/usr/bin/env python3
"""Post-process existing Spanish article narrations from their stored segments.

Since #547 generation applies this processing itself
(``app/services/narration_postprocess.py``); this script is kept only for a
deliberate back-catalogue rerun after a tuning change, and imports the same
code.

Background (2026-09-04). The per-paragraph ElevenLabs narrations
(``app/services/article_audio.py``) are good overall but carry four
defects often enough to degrade the read, all measurable on the raw
per-segment MP3s that every narration already stores under a content-hash key
on Spaces:

1. **Tail blips** — a 2–70 ms burst at the very end of a clip, after a
   200–1200 ms silence: the start of the *next* segment's first word, which
   ``next_text`` request-stitching lets the model begin before the clip is
   cut. Occasionally longer (up to ~1 s).
2. **Uneven loudness** — integrated loudness spans ~10 dB across the segments
   of one article (-29 to -19 LUFS on a 226-segment sample), headings loudest.
3. **Uneven pace** — paragraph speech rate is 126 wpm median, but ~11 % of
   paragraphs run >15 % faster (short dramatic lines, and the newest articles
   as a group). Paragraph *length* does not predict it (r = -0.05 after
   removing the fixed trailing-silence overhead).
4. **The "Continuar leyendo" CTA was narrated** in 36 older series members
   whose content carries the block without a ``---`` in front of it.

This script rebuilds each narration from the **raw** stored segments — it
never overwrites the hash-keyed segment objects, so a later ``mode="changed"``
regeneration still mixes cleanly and this script can simply be re-run after
it. Per segment: drop CTA segments; trim leading silence and the tail
(removing end-of-file blips); measure integrated loudness with ffmpeg's
EBU R128 meter and apply a gain to a common target, with a true-peak limiter
only when the gain would clip; correct pace outliers with a pitch-preserving
tempo change (paragraphs only, bounded factor); pad to a fixed trailing
silence so pauses are uniform. Then concatenate with the module's own silence
constants, upload a fresh timestamped MP3 and persist the manifest with the
new per-segment durations (the public player derives its displayed length
from them).

Segment text is recovered from the saved Spanish content by hash; when an
article's manifest no longer matches its text but the segment *count* does
(the Ngäbe → Nobe pronunciation edits on articles 26/27/29/58/59), segments
are paired positionally so word counts still line up.

Safety. Dry-run by default: downloads the raw segments over the public CDN,
writes ``<out-dir>/<article_id>.mp3`` plus a JSON report per article, and
touches neither Spaces nor the DB. ``--no-dry-run`` additionally needs
``--confirm-prod`` and Spaces write credentials in the environment (i.e. run
inside the production backend container).

Usage.
    # listen first (from the repo root; needs the backend image's ffmpeg+pydub)
    DATABASE_URL=... python scripts/one_off/postprocess_article_audio.py --articles 30,235 --out-dir /scr/out
    # for real, inside pic-backend on the droplet
    python scripts/one_off/postprocess_article_audio.py --no-dry-run --confirm-prod
"""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import re
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

# Make ``app.*`` importable from a checkout (scripts/one_off/ → ../../backend)
# or from wherever the file was copied inside the backend container (cwd /app).
for _candidate in (*[p / "backend" for p in Path(__file__).resolve().parents], Path.cwd()):
    if (_candidate / "app").is_dir():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

from app.core.config import get_settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.article import Article  # noqa: E402
from app.services.article_audio import (  # noqa: E402
    HEADING_SILENCE_MS,
    PARAGRAPH_SILENCE_MS,
    _clean,
    segment_article,
    segment_hash,
    store_article_audio,
)
from app.services.narration_postprocess import _ffmpeg  # noqa: E402
from app.services.narration_postprocess import (  # noqa: E402
    process_segment as _process_raw_segment,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("postprocess_article_audio")

# Tuning and the per-segment DSP live in app/services/narration_postprocess.py
# since #547 — generation runs them on every narration, and this script only
# rebuilds the back catalogue with the same code after a parameter change.

CTA_TEXT = re.compile(r"^(Continuar leyendo|¿Listo para la siguiente parte|Continue Reading)", re.I)


@dataclass
class SegmentReport:
    idx: int
    hash: str
    is_heading: bool
    text: str
    words: int | None
    blips_removed: int
    gain_db: float | None
    limited: bool
    wpm: float | None
    tempo: float
    final_ms: int
    dropped: bool = False
    reason: str = ""


def process_segment(
    mp3: bytes,
    idx: int,
    text_hash: str,
    is_heading: bool,
    text: str,
    words: int | None,
    out_wav: str,
) -> SegmentReport:
    """Drop a narrated CTA segment, else run the pipeline's DSP into ``out_wav``.

    The CTA check is the one step generation does not need: the segmenter
    stops at the "Continuar leyendo" block, but 36 older manifests carry it.
    """
    if CTA_TEXT.match(text):
        return SegmentReport(
            idx, text_hash, is_heading, text[:80], words, 0, None, False, None, 1.0, 0,
            dropped=True, reason="cta",
        )  # fmt: skip
    seg = _process_raw_segment(mp3, text=text, is_heading=is_heading, out_wav=out_wav)
    return SegmentReport(
        idx=idx,
        hash=text_hash,
        is_heading=is_heading,
        text=text[:80],
        words=words,
        blips_removed=seg.blips_removed,
        gain_db=seg.gain_db,
        limited=seg.limited,
        wpm=seg.wpm,
        tempo=seg.tempo,
        final_ms=seg.duration_ms,
    )


def _recover_texts(article: Article) -> dict[int, tuple[str, int]]:
    """Map manifest index → (text, words), by hash, else positionally.

    The segmenter no longer emits the CTA block, but the manifests of the 36
    older narrations still carry it, so every cleaned paragraph block of the
    content is hashed too — that is what lets the CTA entries be recognised
    (and dropped) by text.
    """
    plan = segment_article(article.title_es or "", article.content_es or "")
    manifest = article.audio_manifest_es or []
    by_hash = {segment_hash(t): t for t, _ in plan}
    for block in re.split(r"\n\s*\n", article.content_es or ""):
        text = _clean(block)
        if text:
            by_hash.setdefault(segment_hash(text), text)
    out: dict[int, tuple[str, int]] = {}
    n_cta = sum(1 for e in manifest if CTA_TEXT.match(by_hash.get(e["hash"], "")))
    positional = len(plan) + n_cta == len(manifest)
    for i, entry in enumerate(manifest):
        text = by_hash.get(entry["hash"])
        if text is None and positional and i < len(plan):
            text = plan[i][0]
        if text is not None:
            out[i] = (text, len(text.split()))
    return out


def _fetch_segment(cdn_url: str, key: str, attempts: int = 4) -> bytes:
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(f"{cdn_url}/{key}", timeout=60) as resp:  # noqa: S310
                return resp.read()
        except (OSError, http.client.HTTPException) as exc:
            if attempt == attempts - 1:
                raise
            logger.warning("fetch %s failed (%s); retrying", key, exc)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


def process_article(
    article: Article, cdn_url: str, workers: int, out_mp3: str
) -> tuple[list[dict], list[SegmentReport]]:
    """Rebuild one narration into ``out_mp3``; returns (manifest, reports).

    Streams through disk: each worker downloads, processes and writes one WAV,
    then ffmpeg's concat demuxer stitches WAVs and the two silence files into
    the MP3. Peak memory is a couple of decoded segments, not the article.
    """
    manifest = article.audio_manifest_es or []
    texts = _recover_texts(article)
    missing = [i for i in range(len(manifest)) if i not in texts]
    if missing:
        logger.warning(
            "Article %s: %d segments have no recoverable text (no pace correction for them)",
            article.id,
            len(missing),
        )

    with tempfile.TemporaryDirectory() as tmp:

        def _work(i_entry):
            i, entry = i_entry
            text, words = texts.get(i, ("", None))
            mp3 = _fetch_segment(cdn_url, entry["key"])
            return process_segment(
                mp3,
                i,
                entry["hash"],
                bool(entry.get("is_heading")),
                text,
                words,
                f"{tmp}/{i:04d}.wav",
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            reports = list(pool.map(_work, enumerate(manifest)))

        for name, ms in (("para", PARAGRAPH_SILENCE_MS), ("head", HEADING_SILENCE_MS)):
            _ffmpeg(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=44100:cl=mono",
                    "-t",
                    f"{ms / 1000:.3f}",
                    f"{tmp}/silence_{name}.wav",
                ]
            )

        new_manifest: list[dict] = []
        lines: list[str] = []
        for entry, rep in zip(manifest, reports, strict=True):
            if rep.dropped:
                continue
            if new_manifest:
                lines.append(f"file 'silence_{'head' if rep.is_heading else 'para'}.wav'")
            lines.append(f"file '{rep.idx:04d}.wav'")
            new_manifest.append({**entry, "duration_ms": rep.final_ms})
        Path(f"{tmp}/concat.txt").write_text("\n".join(lines) + "\n")
        _ffmpeg(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                f"{tmp}/concat.txt",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                out_mp3,
            ]
        )
    return new_manifest, reports


def _summarize(article_id: int, reports: list[SegmentReport], total_ms: int) -> str:
    kept = [r for r in reports if not r.dropped]
    dropped = [r for r in reports if r.dropped]
    blips = sum(r.blips_removed for r in kept)
    gains = [r.gain_db for r in kept if r.gain_db is not None]
    tempos = [r for r in kept if r.tempo != 1.0]
    limited = sum(1 for r in kept if r.limited)
    lines = [
        f"article {article_id}: {len(kept)} segments kept, {len(dropped)} dropped (CTA), "
        f"{total_ms / 60000:.1f} min",
        f"  blips removed: {blips} in {sum(1 for r in kept if r.blips_removed)} segments; "
        f"gain range {min(gains):+.1f}..{max(gains):+.1f} dB, limited {limited}",
        f"  tempo-corrected: {len(tempos)} "
        + ", ".join(f"#{r.idx} {r.wpm}wpm×{r.tempo}" for r in tempos[:12])
        + (" …" if len(tempos) > 12 else ""),
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--articles",
        help="comma-separated article ids (default: every narrated article)",
    )
    ap.add_argument("--out-dir", default="scratch/postprocessed", help="dry-run output directory")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument(
        "--cdn-url",
        help="public base URL the raw segments are fetched from (default: settings.do_spaces_cdn_url)",
    )
    ap.add_argument(
        "--no-dry-run",
        action="store_true",
        help="upload + persist instead of writing files",
    )
    ap.add_argument("--confirm-prod", action="store_true", help="required with --no-dry-run")
    args = ap.parse_args()

    dry_run = not args.no_dry_run
    if not dry_run and not args.confirm_prod:
        print("--no-dry-run requires --confirm-prod", file=sys.stderr)
        return 2
    settings = get_settings()
    if not dry_run and not (settings.do_spaces_key and settings.do_spaces_secret):
        print(
            "no Spaces write credentials in the environment; refusing --no-dry-run",
            file=sys.stderr,
        )
        return 2

    out_dir = Path(args.out_dir)
    if dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    work_dir = tempfile.mkdtemp(prefix="postprocess_")
    db = SessionLocal()
    try:
        query = db.query(Article).filter(Article.audio_manifest_es.isnot(None))
        if args.articles:
            query = query.filter(Article.id.in_([int(x) for x in args.articles.split(",")]))
        articles = query.order_by(Article.id).all()
        logger.info(
            "%d narrated article(s) selected (%s)",
            len(articles),
            "dry run" if dry_run else "PROD WRITE",
        )
        # Don't pin a pool connection across minutes of ffmpeg + network I/O.
        todo = [
            (a.id, a.title_es, a.content_es, a.audio_manifest_es, a.audio_url_es) for a in articles
        ]
        db.close()

        for article_id, title_es, content_es, manifest, old_url in todo:
            snapshot = Article(
                id=article_id,
                title_es=title_es,
                content_es=content_es,
                audio_manifest_es=manifest,
            )
            out_mp3 = (
                str(out_dir / f"{article_id}.mp3") if dry_run else f"{work_dir}/{article_id}.mp3"
            )
            new_manifest, reports = process_article(
                snapshot, args.cdn_url or settings.do_spaces_cdn_url, args.workers, out_mp3
            )
            total_ms = sum(e["duration_ms"] for e in new_manifest) + sum(
                HEADING_SILENCE_MS if e.get("is_heading") else PARAGRAPH_SILENCE_MS
                for e in new_manifest[1:]
            )
            print(_summarize(article_id, reports, total_ms))
            if dry_run:
                (out_dir / f"{article_id}.report.json").write_text(
                    json.dumps([asdict(r) for r in reports], ensure_ascii=False, indent=1)
                )
                continue

            url = store_article_audio(article_id, Path(out_mp3).read_bytes(), old_url)
            Path(out_mp3).unlink(missing_ok=True)
            db = SessionLocal()
            try:
                article = db.get(Article, article_id)
                if article is None:
                    logger.error("Article %s vanished; uploaded %s is orphaned", article_id, url)
                    continue
                article.audio_url_es = url
                article.audio_manifest_es = new_manifest
                db.commit()
                logger.info("Article %s → %s", article_id, url)
            finally:
                db.close()
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
