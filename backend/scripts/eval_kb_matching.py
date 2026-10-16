"""Replay historical claims through both KB candidate selectors (#628, epic #618).

``match_claims_against_kb`` picks the KB rows Haiku is asked to confirm either
by word overlap (#378, the default) or, with ``kb_vector_matching_enabled``, by
nearest ``VERIFIED_CLAIM`` chunks above a cosine-similarity floor. This script
measures both on the same claims, **without writing anything**, and reports per
selector: candidates per claim, Haiku-confirmed matches, hit rate, verdict
agreement, Haiku tokens / cost and latency. The vector selector is run once per
``--floors`` value (the retrieval is shared, the Haiku pass is real per floor).

**Which claims.** Historical ``fact_check_claims`` rows of the ``--subjects``
types (default SUGGESTION, RESEARCH, EDU_RESEARCH, ARTICLE), de-duplicated by
normalized text (``claim_key``) and sampled with ``--seed`` down to
``--limit``.

**No self-matching.** Most resolved claims *produced* a KB row — the one whose
``claim_key`` equals their own — so a replay would match each of them to
itself. Every claim's own-key KB row is therefore removed from **both**
selectors' candidates (leave-one-out), and the exact-key stage, which can only
ever find that same row, is reported separately rather than counted as a hit.
The hit rate is thus the fuzzy stage's: "found a *different* KB row asserting
the same fact". That is lower than a production hit rate (which includes exact
hits), so it is not comparable to the ~26% the edu fact-check epic recorded.

**Verdict agreement** is a precision proxy: among matched claims whose own
verdict is reusable (VERIFIED / INACCURATE / DISPUTED), the share whose matched
KB row carries the same verdict.

Read-only by construction: the session runs on a ``postgresql_readonly``
engine, is rolled back before every network phase and closed at the end, and
the script never adds or commits. Haiku calls use ``feature="eval_kb_matching"``;
Voyage calls ``feature="eval_kb_matching"``.

Usage (from ``backend/``; on the droplet ``docker exec pic-backend python -m ...``):
    python -m scripts.eval_kb_matching --dry-run                # sample + cost estimate only
    python -m scripts.eval_kb_matching --limit 200              # -> docs/portfolio/rag/results/<date>-kb-matching.{md,json}
    python -m scripts.eval_kb_matching --out /tmp/kb-matching.md --floors 0.5,0.6,0.7,0.8
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.models.enums import FactCheckSubject
from app.models.fact_check import FactCheckClaim
from app.models.verified_claim import VerifiedClaim
from app.services import embeddings
from app.services.fact_check import (
    _KB_MATCH_PROMPT,
    _KB_REUSABLE_VERDICTS,
    KB_CANDIDATES_PER_CLAIM,
    KB_MATCH_BATCH_SIZE,
    ExtractedClaim,
    _kb_candidates,
    _normalize,
    confirm_kb_candidates,
    kb_vector_hits,
    select_vector_candidates,
)
from app.services.llm import HAIKU_MODEL
from sqlalchemy.orm import Session

FEATURE = "eval_kb_matching"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "portfolio" / "rag" / "results"
DEFAULT_SUBJECTS = (
    FactCheckSubject.SUGGESTION.value,
    FactCheckSubject.RESEARCH.value,
    FactCheckSubject.EDU_RESEARCH.value,
    FactCheckSubject.ARTICLE.value,
)
DEFAULT_FLOORS = (0.5, 0.6, 0.7, 0.8)
DEFAULT_LIMIT = 200
DEFAULT_SEED = 628
# Haiku 5.5 list price, USD per million tokens (prompts up to 100K tokens).
HAIKU_INPUT_PRICE_PER_MTOK = 0.10
HAIKU_OUTPUT_PRICE_PER_MTOK = 0.50
# The match call's max_tokens: the output ceiling for the cost estimate.
HAIKU_MAX_OUTPUT_TOKENS = 4000
_CHARS_PER_TOKEN = 3
OLD = "word_overlap"


@dataclass(frozen=True)
class ReplayClaim:
    id: int
    subject_type: str
    text: str
    key: str
    verdict: str


@dataclass(frozen=True)
class KbRow:
    """A detached KB snapshot; ``_kb_candidates`` reads ``id`` / ``claim_text``."""

    id: int
    claim_key: str
    claim_text: str
    verdict: str


@dataclass
class SelectorRun:
    name: str
    candidates: list[list[int]]
    selection_ms: float
    usage: list[tuple[int, int]] = field(default_factory=list)
    haiku_ms: float = 0.0
    matches: dict[int, int] = field(default_factory=dict)


def load_replay_claims(
    db: Session,
    *,
    subjects=DEFAULT_SUBJECTS,
    limit: int = DEFAULT_LIMIT,
    seed: int = DEFAULT_SEED,
) -> list[ReplayClaim]:
    """Distinct-by-key historical claims of ``subjects``, sampled to ``limit``, id order."""
    rows = (
        db.query(
            FactCheckClaim.id,
            FactCheckClaim.subject_type,
            FactCheckClaim.claim_text,
            FactCheckClaim.verdict,
        )
        .filter(FactCheckClaim.subject_type.in_(list(subjects)))
        .order_by(FactCheckClaim.id)
        .all()
    )
    seen: set[str] = set()
    claims: list[ReplayClaim] = []
    for row in rows:
        key = _normalize(row.claim_text or "")
        if not key or key in seen:
            continue
        seen.add(key)
        claims.append(ReplayClaim(row.id, row.subject_type, row.claim_text, key, row.verdict))
    if limit and len(claims) > limit:
        claims = sorted(random.Random(seed).sample(claims, limit), key=lambda c: c.id)
    return claims


def load_kb(db: Session) -> list[KbRow]:
    rows = (
        db.query(
            VerifiedClaim.id,
            VerifiedClaim.claim_key,
            VerifiedClaim.claim_text,
            VerifiedClaim.verdict,
        )
        .filter(VerifiedClaim.verdict.in_(_KB_REUSABLE_VERDICTS))
        .order_by(VerifiedClaim.id)
        .all()
    )
    return [KbRow(r.id, r.claim_key, r.claim_text, r.verdict) for r in rows]


def _self_ids(claims: list[ReplayClaim], kb: list[KbRow]) -> list[int | None]:
    by_key = {row.claim_key: row.id for row in kb}
    return [by_key.get(c.key) for c in claims]


def old_candidates(
    claims: list[ReplayClaim], kb: list[KbRow], self_ids: list[int | None]
) -> SelectorRun:
    """The word-overlap selector, each claim's own-key row left out."""
    start = time.perf_counter()
    out = []
    for claim, self_id in zip(claims, self_ids, strict=True):
        pool = kb if self_id is None else [row for row in kb if row.id != self_id]
        out.append([row.id for row in _kb_candidates(ExtractedClaim(claim.text, "OTHER"), pool)])
    return SelectorRun(OLD, out, (time.perf_counter() - start) * 1000)


def _usage(message) -> tuple[int, int]:
    usage = getattr(message, "usage", None)
    values = []
    for name in ("input_tokens", "output_tokens"):
        value = getattr(usage, name, 0)
        values.append(value if isinstance(value, int) else 0)
    return values[0], values[1]


def confirm(run: SelectorRun, claims: list[ReplayClaim], kb_text: dict[int, str]) -> None:
    """The production Haiku pass over ``run``'s candidates (records tokens and time)."""
    fuzzy_ids = [(i, ids) for i, ids in enumerate(run.candidates) if ids]
    start = time.perf_counter()
    run.matches = confirm_kb_candidates(
        [c.text for c in claims],
        fuzzy_ids,
        kb_text,
        feature=FEATURE,
        on_message=lambda message: run.usage.append(_usage(message)),
    )
    run.haiku_ms = (time.perf_counter() - start) * 1000


def estimate_haiku(run: SelectorRun, claims: list[ReplayClaim], kb_text: dict[int, str]) -> dict:
    """Input tokens from the real prompts (chars / 3); output bounded by max_tokens."""
    fuzzy_ids = [(i, ids) for i, ids in enumerate(run.candidates) if ids]
    calls = 0
    input_tokens = 0
    for start in range(0, len(fuzzy_ids), KB_MATCH_BATCH_SIZE):
        batch = fuzzy_ids[start : start + KB_MATCH_BATCH_SIZE]
        union = dict.fromkeys(kb_id for _, ids in batch for kb_id in ids)
        prompt = _KB_MATCH_PROMPT.format(
            claims="\n".join(f"{j + 1}. {claims[i].text}" for j, (i, _) in enumerate(batch)),
            kb="\n".join(f"[{kb_id}] {kb_text[kb_id]}" for kb_id in union),
        )
        calls += 1
        input_tokens += len(prompt) // _CHARS_PER_TOKEN + 1
    return {
        "calls": calls,
        "input_tokens": input_tokens,
        "cost_usd_min": round(input_tokens * HAIKU_INPUT_PRICE_PER_MTOK / 1e6, 4),
        "cost_usd_max": round(
            input_tokens * HAIKU_INPUT_PRICE_PER_MTOK / 1e6
            + calls * HAIKU_MAX_OUTPUT_TOKENS * HAIKU_OUTPUT_PRICE_PER_MTOK / 1e6,
            4,
        ),
    }


def summarize(
    run: SelectorRun,
    claims: list[ReplayClaim],
    kb_verdict: dict[int, str],
    *,
    reference: dict[int, int] | None = None,
) -> dict:
    n = len(claims)
    counts = [len(ids) for ids in run.candidates]
    with_candidates = sum(1 for c in counts if c)
    input_tokens = sum(u[0] for u in run.usage)
    output_tokens = sum(u[1] for u in run.usage)
    resolved = [i for i in run.matches if claims[i].verdict in _KB_REUSABLE_VERDICTS]
    agree = sum(1 for i in resolved if kb_verdict.get(run.matches[i]) == claims[i].verdict)
    by_subject: dict[str, dict] = {}
    for i, claim in enumerate(claims):
        entry = by_subject.setdefault(claim.subject_type, {"claims": 0, "matches": 0})
        entry["claims"] += 1
        entry["matches"] += int(i in run.matches)
    for entry in by_subject.values():
        entry["hit_rate"] = round(entry["matches"] / entry["claims"], 4)
    summary = {
        "selector": run.name,
        "claims": n,
        "claims_with_candidates": with_candidates,
        "candidates_per_claim": round(sum(counts) / n, 3) if n else 0.0,
        "candidate_pairs": sum(counts),
        "haiku_calls": len(run.usage),
        "haiku_input_tokens": input_tokens,
        "haiku_output_tokens": output_tokens,
        "haiku_cost_usd": round(
            input_tokens * HAIKU_INPUT_PRICE_PER_MTOK / 1e6
            + output_tokens * HAIKU_OUTPUT_PRICE_PER_MTOK / 1e6,
            6,
        ),
        "selection_ms_per_claim": round(run.selection_ms / n, 2) if n else 0.0,
        "haiku_ms": round(run.haiku_ms, 1),
        "matches": len(run.matches),
        "hit_rate": round(len(run.matches) / n, 4) if n else 0.0,
        "verdict_agreement": round(agree / len(resolved), 4) if resolved else None,
        "verdict_agreement_n": len(resolved),
        "by_subject": dict(sorted(by_subject.items())),
    }
    if reference is not None:
        mine, ref = set(run.matches), set(reference)
        summary["overlap_vs_word_overlap"] = {
            "both": len(mine & ref),
            "only_this": len(mine - ref),
            "only_word_overlap": len(ref - mine),
            "same_kb_row": sum(1 for i in mine & ref if run.matches[i] == reference[i]),
        }
    return summary


def run_replay(
    db: Session,
    *,
    subjects=DEFAULT_SUBJECTS,
    limit: int = DEFAULT_LIMIT,
    seed: int = DEFAULT_SEED,
    floors=DEFAULT_FLOORS,
    top_k: int = 5,
    dry_run: bool = False,
) -> dict:
    """Read, select, confirm; returns ``{"meta": ..., "results": [...]}``. Never writes."""
    claims = load_replay_claims(db, subjects=subjects, limit=limit, seed=seed)
    kb = load_kb(db)
    db.rollback()  # read-only; release the connection before any network call
    self_ids = _self_ids(claims, kb)
    kb_text = {row.id: row.claim_text for row in kb}
    kb_verdict = {row.id: row.verdict for row in kb}
    meta = {
        "date": datetime.now(UTC).date().isoformat(),
        "subjects": list(subjects),
        "claims": len(claims),
        "kb_rows": len(kb),
        "claims_with_own_kb_row": sum(1 for s in self_ids if s is not None),
        "limit": limit,
        "seed": seed,
        "top_k": top_k,
        "floors": list(floors),
        "word_overlap_cap": KB_CANDIDATES_PER_CLAIM,
        "haiku_model": HAIKU_MODEL,
        "embedding_model": None,
        "dry_run": dry_run,
    }

    old = old_candidates(claims, kb, self_ids)
    runs: list[SelectorRun] = [old]
    embed_ms = search_ms = None
    if dry_run:
        # No Voyage call: the vector selector's candidates are assumed to be top_k each.
        assumed = [
            [kb[(i * top_k + j) % len(kb)].id for j in range(min(top_k, len(kb)))]
            for i in range(len(claims))
        ]
        estimate = {
            OLD: estimate_haiku(old, claims, kb_text),
            "vector_assumed_top_k": estimate_haiku(
                SelectorRun("vector", assumed, 0.0), claims, kb_text
            ),
        }
        per_floor = estimate["vector_assumed_top_k"]
        estimate["total_cost_usd_max"] = round(
            estimate[OLD]["cost_usd_max"] + per_floor["cost_usd_max"] * len(floors), 4
        )
        estimate["word_overlap_candidates_per_claim"] = summarize(old, claims, {})[
            "candidates_per_claim"
        ]
        return {"meta": meta, "estimate": estimate, "results": []}

    meta["embedding_model"] = embeddings.document_model()
    texts = [c.text for c in claims]
    start = time.perf_counter()
    vectors = embeddings.embed_queries(texts, feature=FEATURE) if texts else []
    embed_ms = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    hits = kb_vector_hits(db, texts, vectors)
    search_ms = (time.perf_counter() - start) * 1000
    db.rollback()
    for floor in floors:
        candidates = [
            select_vector_candidates(h, top_k=top_k, min_similarity=floor, exclude_id=s)
            for h, s in zip(hits, self_ids, strict=True)
        ]
        # Selection latency is the per-claim embed + vector search, shared by every floor.
        runs.append(
            SelectorRun(f"vector@{floor:g}", candidates, (embed_ms or 0.0) + (search_ms or 0.0))
        )
    meta["embed_ms"] = round(embed_ms, 1)
    meta["vector_search_ms"] = round(search_ms, 1)

    for run in runs:
        confirm(run, claims, kb_text)
    results = [summarize(old, claims, kb_verdict)]
    results += [summarize(r, claims, kb_verdict, reference=old.matches) for r in runs[1:]]
    return {"meta": meta, "results": results}


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def render_markdown(report: dict) -> str:
    meta = report["meta"]
    lines = [
        f"# KB matching: word overlap vs vector candidates ({meta['date']})",
        "",
        "Produced by `python -m scripts.eval_kb_matching` (#628). Read-only replay.",
        "",
        f"- Claims replayed: **{meta['claims']}** distinct-by-key historical "
        f"`fact_check_claims` of {', '.join(meta['subjects'])} (seed {meta['seed']}, "
        f"limit {meta['limit']}).",
        f"- KB: {meta['kb_rows']} reusable `verified_claims` rows; "
        f"{meta['claims_with_own_kb_row']} replayed claims have their own-key KB row.",
        "- Leave-one-out: each claim's own-key KB row is removed from both selectors, and "
        "the exact-key stage (which can only find that row) is not counted. Hit rate = "
        "Haiku-confirmed match to a *different* KB row / claims replayed. Not comparable "
        "to the ~26% edu-epic figure, which includes exact hits.",
        f"- Word overlap: 2+ shared significant words, cap {meta['word_overlap_cap']}. "
        f"Vector: top {meta['top_k']} `VERIFIED_CLAIM` chunks at or above the cosine floor "
        f"({meta['embedding_model'] or 'n/a'}). Confirmation: {meta['haiku_model']}, the "
        "production prompt.",
    ]
    if "embed_ms" in meta:
        lines.append(
            f"- Vector selection: one batched query embedding {meta['embed_ms']} ms + "
            f"vector searches {meta['vector_search_ms']} ms, shared by every floor."
        )
    lines += [""]
    if meta["dry_run"]:
        est = report["estimate"]
        lines += [
            "## Dry run (no Voyage, no Haiku)",
            "",
            f"- Word overlap: {est[OLD]['calls']} Haiku calls, ~{est[OLD]['input_tokens']} "
            f"input tokens, ${est[OLD]['cost_usd_min']}–${est[OLD]['cost_usd_max']} "
            f"({est['word_overlap_candidates_per_claim']} candidates/claim).",
            f"- Vector (assuming top_k candidates per claim), per floor: "
            f"{est['vector_assumed_top_k']['calls']} calls, "
            f"${est['vector_assumed_top_k']['cost_usd_min']}–"
            f"${est['vector_assumed_top_k']['cost_usd_max']}.",
            f"- Upper bound for the full run: ${est['total_cost_usd_max']}.",
            "",
        ]
    headers = [
        "Selector",
        "Cand./claim",
        "Claims w/ cand.",
        "Haiku calls",
        "Haiku in/out tok",
        "Haiku $",
        "Select ms/claim",
        "Haiku ms",
        "Matches",
        "Hit rate",
        "Verdict agree (n)",
    ]
    if not report["results"]:
        return "\n".join(lines) + "\n"
    lines += ["## Results", "", "| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    for r in report["results"]:
        agree = r["verdict_agreement"]
        lines.append(
            "| "
            + " | ".join(
                [
                    r["selector"],
                    f"{r['candidates_per_claim']:.2f}",
                    str(r["claims_with_candidates"]),
                    str(r["haiku_calls"]),
                    f"{r['haiku_input_tokens']}/{r['haiku_output_tokens']}",
                    f"{r['haiku_cost_usd']:.4f}",
                    f"{r['selection_ms_per_claim']:.1f}",
                    f"{r['haiku_ms']:.0f}",
                    str(r["matches"]),
                    _pct(r["hit_rate"]),
                    f"{_pct(agree)} ({r['verdict_agreement_n']})",
                ]
            )
            + " |"
        )
    overlaps = [r for r in report["results"] if "overlap_vs_word_overlap" in r]
    if overlaps:
        lines += [
            "",
            "## Overlap with word-overlap matches",
            "",
            "| Selector | Both | Only vector | Only word overlap | Same KB row |",
            "|---|---|---|---|---|",
        ]
        for r in overlaps:
            o = r["overlap_vs_word_overlap"]
            lines.append(
                f"| {r['selector']} | {o['both']} | {o['only_this']} | "
                f"{o['only_word_overlap']} | {o['same_kb_row']} |"
            )
    subjects = sorted({s for r in report["results"] for s in r["by_subject"]})
    if subjects:
        lines += ["", "## Hit rate by subject", ""]
        lines.append("| Selector | " + " | ".join(subjects) + " |")
        lines.append("|---|" + "|".join("---" for _ in subjects) + "|")
        for r in report["results"]:
            cells = []
            for s in subjects:
                e = r["by_subject"].get(s)
                cells.append("—" if e is None else f"{_pct(e['hit_rate'])} ({e['claims']})")
            lines.append(f"| {r['selector']} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def write_report(report: dict, out: Path) -> tuple[Path, Path]:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(report), encoding="utf-8")
    json_path = out.with_suffix(".json")
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return out, json_path


def _readonly_session() -> Session:
    from app.core.database import engine

    return Session(bind=engine.execution_options(postgresql_readonly=True), autoflush=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Claims to replay")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--subjects",
        default=",".join(DEFAULT_SUBJECTS),
        help="Comma-separated fact_check_claims subject types",
    )
    parser.add_argument(
        "--floors",
        default=",".join(f"{f:g}" for f in DEFAULT_FLOORS),
        help="Comma-separated cosine-similarity floors for the vector selector",
    )
    parser.add_argument("--top-k", type=int, default=None, help="Default: kb_vector_top_k")
    parser.add_argument(
        "--out", help="Markdown path (JSON alongside); default results/<date>-kb-matching.md"
    )
    parser.add_argument("--dry-run", action="store_true", help="Sample and estimate cost only")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    from app.core.config import get_settings

    top_k = args.top_k if args.top_k is not None else get_settings().kb_vector_top_k
    floors = [float(f) for f in args.floors.split(",") if f.strip()]
    subjects = [s.strip() for s in args.subjects.split(",") if s.strip()]
    if not args.dry_run and not embeddings.embeddings_enabled():
        print("VOYAGE_API_KEY is empty: the vector selector cannot run (use --dry-run).")
        return 2
    db = _readonly_session()
    try:
        report = run_replay(
            db,
            subjects=subjects,
            limit=args.limit,
            seed=args.seed,
            floors=floors,
            top_k=top_k,
            dry_run=args.dry_run,
        )
    finally:
        db.rollback()
        db.close()
    print(render_markdown(report))
    if not args.dry_run:
        out = (
            Path(args.out)
            if args.out
            else DEFAULT_OUT_DIR / f"{report['meta']['date']}-kb-matching.md"
        )
        md, js = write_report(report, out)
        print(f"Wrote {md} and {js}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
