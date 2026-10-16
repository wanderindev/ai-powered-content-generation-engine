"""Command line for the RAG eval harness (#627, epic #617). See ``scripts/eval_rag/__init__.py``."""

from __future__ import annotations

import argparse
import hashlib
import logging
import shlex
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

from app.core.config import get_settings
from app.services import ask_pic, embeddings, llm
from app.services.knowledge import retrieval

from . import (
    answers,
    answers_report,
    drafting,
    golden,
    memory_report,
    memory_run,
    memory_seed,
    reader,
    report,
    runner,
    selection,
    tables,
    twin_keys,
)

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval" / "rag"
DRAFTS_DIR = EVAL_DIR / "drafts"


def _session():
    from app.core.database import SessionLocal

    return SessionLocal()


def _guarded_session():
    """A session on a database that is not production (or exit 2)."""
    db = _session()
    try:
        tables.assert_session_not_production(db, get_settings().environment)
    except tables.ProductionRefusedError as exc:
        db.close()
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    return db


def _quiet() -> None:
    # The per-call INFO lines (knowledge search / voyage / anthropic) would drown the output.
    logging.getLogger("app").setLevel(logging.WARNING)


# --- draft-questions ---------------------------------------------------------------


def cmd_draft_questions(args) -> int:
    counts = {
        "factual": args.factual,
        "cross-lingual": args.cross_lingual,
        "multi-article": args.multi_article,
        "unanswerable": args.unanswerable,
    }
    out = Path(args.out or DRAFTS_DIR / f"{date.today().isoformat()}-drafts.json")
    if out.exists() and not args.force and not args.dry_run:
        print(f"{out} exists; pass --force to overwrite it.", file=sys.stderr)
        return 1
    db = _guarded_session()
    try:
        chunks = drafting.load_public_chunks(db)
        titles = drafting.public_titles(db)
    finally:
        db.rollback()
        db.close()  # nothing below touches the database
    if not chunks:
        print(
            "No public ARTICLE chunks in knowledge_chunks. Seed the local DB "
            "(scripts/seed_local_db.py) or run scripts.reindex_knowledge --all first.",
            file=sys.stderr,
        )
        return 1
    sampled = drafting.sample(chunks, counts, seed=args.seed)
    batches = drafting.build_batches(sampled, titles, counts)
    estimate = drafting.estimate_cost(batches)
    print(
        f"Sampled from {len(chunks)} public chunks ({len(titles)} articles): "
        + ", ".join(f"{t}={len(v)}" for t, v in sampled.items())
        + f", unanswerable={counts['unanswerable']} requested"
    )
    print(
        f"Sonnet ({drafting.MODEL}): {estimate['calls']} calls, ~{estimate['input_tokens_est']} "
        f"input tokens, <= {estimate['output_tokens_cap']} output tokens; estimated "
        f"${estimate['cost_usd_min']:.4f}–${estimate['cost_usd_max']:.4f} "
        f"(at ${drafting.SONNET_INPUT_PRICE_PER_MTOK}/${drafting.SONNET_OUTPUT_PRICE_PER_MTOK} "
        "per 1M; TODO confirm Sonnet 5.5 prices). Voyage: none."
    )
    if args.dry_run:
        print("Dry run: no model call, nothing written.")
        return 0
    if not get_settings().anthropic_api_key:
        print("ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        return 2
    llm.reset_session_usage()
    drafts, stats = drafting.run_batches(batches)
    usage = llm.get_session_usage()._asdict()
    document = drafting.build_document(
        drafts, seed=args.seed, sampled_chunks=len(chunks), stats=stats, usage=usage
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(drafting.dumps(document), encoding="utf-8")
    by_type: dict[str, int] = {}
    for d in document["drafts"]:
        by_type[d["type"]] = by_type.get(d["type"], 0) + 1
    print(
        f"Wrote {len(document['drafts'])} drafts to {out} ({by_type}); "
        f"{stats['failed_calls']} failed call(s), {stats['dropped_items']} malformed item(s) "
        f"dropped; usage {usage}."
    )
    print("Next: set every draft's status to keep / edit / drop, then run `golden`.")
    return 0


# --- draft-reader (#693) ------------------------------------------------------------


def cmd_draft_reader(args) -> int:
    out = Path(args.out or DRAFTS_DIR / f"{date.today().isoformat()}-reader.json")
    if out.exists() and not args.force and not args.dry_run:
        print(f"{out} exists; pass --force to overwrite it.", file=sys.stderr)
        return 1
    db = _memory_session(args.read_only_prod)  # read-only, checked
    try:
        articles = reader.load_public_articles(db)
    finally:
        db.rollback()
        db.close()  # nothing below touches the database
    if not articles:
        print("No public articles in the database.", file=sys.stderr)
        return 1
    drafted_from = reader.pick_articles(articles, args.articles, args.seed)
    batches = reader.build_draft_batches(drafted_from, articles)
    ubatch = reader.build_unanswerable_batch(articles, args.unanswerable)
    est = reader.estimate_cost(batches + ([ubatch] if ubatch else []))
    n_verify = 2 * len(drafted_from) + len(reader.OPERATOR_SEEDS)
    print(
        f"{len(articles)} public articles; drafting from {len(drafted_from)} "
        f"(2 questions each, EN + ES) + {len(reader.OPERATOR_SEEDS)} operator questions + "
        f"{args.unanswerable} unanswerable."
    )
    print(
        f"Opus ({reader.MODEL}, effort {reader.EFFORT}) drafting: {est['calls']} calls, "
        f"~{est['input_tokens_est']} input tokens, <= {est['output_tokens_cap']} output tokens; "
        f"${est['cost_usd_min']:.2f}–${est['cost_usd_max']:.2f} at list price. Verification "
        f"follows: ~{-(-n_verify // reader.DRAFTS_PER_VERIFY_CALL) + 1} more calls on <= "
        f"{n_verify} drafts (excerpts only), of similar size."
    )
    if args.dry_run:
        print("Dry run: no model call, nothing written.")
        return 0
    if not get_settings().anthropic_api_key:
        print("ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        return 2
    items, stats, spend = reader.run(
        articles,
        drafted_from=drafted_from,
        n_unanswerable=args.unanswerable,
        progress=lambda line: print(f"  {line}", flush=True),
    )
    document = reader.build_document(items, stats=stats, spend=spend, seed=args.seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(reader.dumps(document), encoding="utf-8")
    s = document["summary"]
    print(
        f"Wrote {out}: drafted {s['drafted']}, kept {s['kept']}, fixed {s['fixed']}, "
        f"dropped {s['dropped']} (+{s['dropped_before_verification']} malformed before "
        f"verification); {stats.get('failed_calls', 0)} failed call(s), "
        f"{stats.get('api_errors', 0)} of them API errors; "
        f"spend ${document['spend']['total_cost_usd']:.2f}."
    )
    print("Next: python -m scripts.eval_rag golden --drafts <that file> --append")
    return 0


# --- golden -------------------------------------------------------------------------


def _append_reader(doc: dict, drafts_path: Path, out: Path, curation_md: str | None) -> int:
    """Merge a verified reader-drafts file into an existing golden set (#693)."""
    import json

    try:
        existing = golden.load_golden(out)
        added, counts = golden.drafts_to_golden(doc, drafts_file=drafts_path.name)
    except (golden.GoldenError, FileNotFoundError) as exc:
        print(f"Cannot append: {exc}", file=sys.stderr)
        return 1
    clash = {q.id for q in existing.questions} & {q.id for q in added.questions}
    if clash:
        print(f"Cannot append: ids already in {out}: {sorted(clash)}", file=sys.stderr)
        return 1
    summary = doc.get("summary") or {}
    curation = dict(existing.curation or {})
    curation["reader"] = {
        "drafts_file": drafts_path.name,
        "drafts_generated_at": doc.get("generated_at"),
        "method": "drafted and verified by Opus (#693); no per-item manual review",
        "model": doc.get("model"),
        "drafted": summary.get("drafted"),
        "kept": summary.get("kept"),
        "fixed": summary.get("fixed"),
        "dropped": summary.get("dropped"),
        "dropped_before_verification": summary.get("dropped_before_verification"),
        "verdicts": summary.get("verdicts"),
        "questions_added": len(added.questions),
        "by_type": counts.by_type,
        "by_language": counts.by_language,
        "cost_usd": (doc.get("spend") or {}).get("total_cost_usd"),
    }
    merged = golden.GoldenSet(
        questions=existing.questions + added.questions,
        description=existing.description,
        curation=curation,
    )
    golden.validate_questions([q.to_dict() for q in merged.questions])
    out.write_text(
        json.dumps(merged.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if curation_md:
        Path(curation_md).parent.mkdir(parents=True, exist_ok=True)
        Path(curation_md).write_text(
            reader.render_curation_markdown(doc, drafts_path.name), encoding="utf-8"
        )
        print(f"Wrote {curation_md}.")
    print(
        f"Appended {len(added.questions)} questions to {out} ({counts.by_type}; "
        f"{counts.by_language}); now {len(merged.questions)}."
    )
    return 0


def cmd_golden(args) -> int:
    import json

    drafts_path = Path(args.drafts)
    out = Path(args.out or golden.DEFAULT_GOLDEN_PATH)
    try:
        doc = json.loads(drafts_path.read_text(encoding="utf-8"))
        if args.append:
            return _append_reader(doc, drafts_path, out, args.curation_md)
        result, counts = golden.drafts_to_golden(
            doc, drafts_file=drafts_path.name, description=args.description
        )
    except (golden.GoldenError, json.JSONDecodeError) as exc:
        problems = exc.errors if isinstance(exc, golden.GoldenError) else [str(exc)]
        print("Cannot build the golden set:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(
        f"Drafted {counts.drafted}: kept {counts.kept}, edited {counts.edited}, "
        f"dropped {counts.dropped} -> {len(result.questions)} questions "
        f"(by type {counts.by_type}; by language {counts.by_language})."
    )
    unanswerable = counts.by_type.get("unanswerable", 0)
    if unanswerable < 8:
        print(f"WARNING: only {unanswerable} unanswerable question(s); the ticket wants >= 8.")
    if out.exists() and not args.force:
        print(f"{out} exists; pass --force to overwrite it.", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {out}.")
    return 0


# --- retrieval ----------------------------------------------------------------------


def cmd_retrieval(args, argv: list[str]) -> int:
    golden_path = Path(args.golden or golden.DEFAULT_GOLDEN_PATH)
    try:
        gold = golden.load_golden(golden_path)
    except (golden.GoldenError, FileNotFoundError) as exc:
        print(f"Cannot load {golden_path}: {exc}", file=sys.stderr)
        return 1
    if args.live:
        return _retrieval_live(args, argv, gold, golden_path)
    if args.read_only_prod:
        print(
            "--read-only-prod needs --live (eval tables are never built on production).",
            file=sys.stderr,
        )
        return 2
    models, modes = list(args.models), list(args.modes)
    needs_embeddings = any(runner.uses_embeddings(m) for m in modes)
    db = _guarded_session()
    try:
        plans = {model: tables.plan_sync(db, model, reuse=not args.no_reuse) for model in models}
        db.rollback()
        avg_tokens = max((p.avg_chunk_tokens for p in plans.values()), default=0.0)
        index_cost = sum(p.estimated_cost for p in plans.values())
        query = runner.estimate_query_cost(gold, models, modes, avg_chunk_tokens=avg_tokens)
        print(
            f"{len(gold.questions)} questions x {len(runner.runs(models, modes))} runs "
            f"({', '.join(f'{m}/{x}' for m, x in runner.runs(models, modes))})"
        )
        for plan in plans.values():
            s = plan.summary()
            print(
                f"  index {s['table']}: {s['source_rows']} source rows, keep {s['kept']}, "
                f"copy {s['copied_vectors']}, embed {s['to_embed']} "
                f"(~{s['embed_tokens_est']} tokens, ${s['embed_cost_usd_est']:.4f}), "
                f"delete {s['deleted']}, hash mismatches {s['hash_mismatches']}"
            )
        print(
            f"Estimated Voyage cost: indexing ${index_cost:.4f} + queries "
            f"${query['cost_usd_est']:.4f} (~{query['query_embed_tokens_est']} embed + "
            f"~{query['rerank_tokens_est']} rerank tokens). Anthropic: none."
        )
        if not any(p.source_rows for p in plans.values()):
            print(
                "knowledge_chunks is empty: seed the local DB or run "
                "scripts.reindex_knowledge --all first.",
                file=sys.stderr,
            )
            return 1
        if args.dry_run:
            print("Dry run: no Voyage call, nothing written.")
            return 0
        needs_index = any(p.embed for p in plans.values())
        if (needs_embeddings or needs_index) and not embeddings.embeddings_enabled():
            print(
                "VOYAGE_API_KEY is not set (needed to index and to embed queries).", file=sys.stderr
            )
            return 2
        embeddings.reset_usage_totals()
        table_meta = []
        for model, plan in plans.items():
            tables.apply_sync(db, plan)
            table_meta.append(
                {
                    "model": model,
                    "table": plan.table,
                    "sync": plan.summary(),
                    "counts": tables.corpus_counts(db, tables.eval_table(model)),
                }
            )
            db.rollback()
        index_usage = embeddings.get_usage_totals()
        records = runner.run_eval(
            db,
            gold,
            models=models,
            modes=modes,
            table_for=tables.eval_table,
            k=args.k,
            candidates=args.candidates,
            progress=lambda line: print(f"  {line}"),
        )
    finally:
        db.rollback()
        db.close()
    summary = runner.summarize(records)
    settings = get_settings()
    meta = {
        "date": args.date or date.today().isoformat(),
        "command": "python -m scripts.eval_rag " + " ".join(shlex.quote(a) for a in argv),
        "golden_path": str(golden_path),
        "golden_sha256": hashlib.sha256(golden_path.read_bytes()).hexdigest(),
        "questions": len(gold.questions),
        "answerable": sum(q.answerable for q in gold.questions),
        "unanswerable": sum(not q.answerable for q in gold.questions),
        "curation": gold.curation,
        "models": models,
        "modes": modes,
        "k": args.k,
        "candidates": args.candidates,
        "rerank_model": settings.rerank_model,
        "rerank_top_n": retrieval.RERANK_TOP_N,
        "tables": table_meta,
        "index_usage_tokens": {f"{e}:{m}": n for (e, m), n in index_usage.items()},
    }
    md_path, json_path = report.write_results(
        meta, summary, records, out_dir=Path(args.out_dir or report.DEFAULT_OUT_DIR)
    )
    print(f"Wrote {md_path} and {json_path}.")
    return 0


def _retrieval_live(args, argv: list[str], gold, golden_path: Path) -> int:
    """Retrieval eval on the live ``knowledge_chunks`` (#693): read-only, no eval table.

    The index is whatever the database holds, embedded with
    ``settings.embedding_model``; nothing is synced or written, so with
    ``--read-only-prod`` it measures production's own index.
    """
    from app.models.knowledge_chunk import KnowledgeChunk

    settings = get_settings()
    model, modes = settings.embedding_model, list(args.modes)
    db = _memory_session(args.read_only_prod)  # read-only, checked
    try:
        counts = tables.corpus_counts(db, KnowledgeChunk.__table__)
        db.rollback()
        print(
            f"{len(gold.questions)} questions x {len(runner.runs([model], modes))} runs on the "
            f"live knowledge_chunks ({counts.get('total', 0)} rows, model {model})."
        )
        if args.dry_run:
            print("Dry run: no Voyage call, nothing written.")
            return 0
        if any(runner.uses_embeddings(m) for m in modes) and not embeddings.embeddings_enabled():
            print("VOYAGE_API_KEY is not set (needed to embed queries).", file=sys.stderr)
            return 2
        records = runner.run_eval(
            db,
            gold,
            models=[model],
            modes=modes,
            table_for=lambda _model: None,  # retrieval.search's default: the live table
            k=args.k,
            candidates=args.candidates,
            progress=lambda line: print(f"  {line}"),
        )
    finally:
        db.rollback()
        db.close()
    meta = {
        "date": args.date or date.today().isoformat(),
        "command": "python -m scripts.eval_rag " + " ".join(shlex.quote(a) for a in argv),
        "golden_path": str(golden_path),
        "golden_sha256": hashlib.sha256(golden_path.read_bytes()).hexdigest(),
        "questions": len(gold.questions),
        "answerable": sum(q.answerable for q in gold.questions),
        "unanswerable": sum(not q.answerable for q in gold.questions),
        "curation": gold.curation,
        "database": "production, read-only (--read-only-prod)"
        if args.read_only_prod
        else "non-production, read-only",
        "models": [model],
        "modes": modes,
        "k": args.k,
        "candidates": args.candidates,
        "rerank_model": settings.rerank_model,
        "rerank_top_n": retrieval.RERANK_TOP_N,
        "tables": [
            {
                "model": model,
                "table": "knowledge_chunks (live)",
                "sync": {},
                "counts": counts,
            }
        ],
    }
    md_path, json_path = report.write_results(
        meta,
        runner.summarize(records),
        records,
        out_dir=Path(args.out_dir or report.DEFAULT_OUT_DIR),
    )
    print(f"Wrote {md_path} and {json_path}.")
    return 0


def cmd_drop_tables(args) -> int:
    db = _guarded_session()
    try:
        for model in args.models:
            tables.drop_eval_table(db, model)
            print(f"Dropped {tables.eval_table_name(model)} (if it existed).")
    finally:
        db.close()
    return 0


# --- memory (#632) --------------------------------------------------------------------


def _readonly_session():
    """A session on a ``postgresql_readonly`` engine: any INSERT / DDL is refused by Postgres."""
    from app.core.database import engine
    from sqlalchemy.orm import Session

    return Session(bind=engine.execution_options(postgresql_readonly=True), autoflush=False)


def _memory_session(read_only_prod: bool):
    """The read-only session ``memory`` runs on (or exit 2).

    Always read-only. Without ``--read-only-prod`` the production refusal
    applies as for every other command; with it the refusal is waived, which
    is safe only because the session cannot write (checked here).
    """
    from sqlalchemy import text

    db = _readonly_session()
    try:
        if not read_only_prod:
            tables.assert_session_not_production(db, get_settings().environment)
        if db.execute(text("SHOW transaction_read_only")).scalar() != "on":
            raise tables.ProductionRefusedError("the memory eval session is not read-only")
        db.rollback()
    except tables.ProductionRefusedError as exc:
        db.close()
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    return db


def cmd_memory_seed(args) -> int:
    counts = {
        "perturbed": args.perturbed,
        "paraphrase": args.paraphrase,
        "near_duplicate": args.near_duplicate,
        "novel": args.novel,
    }
    out = Path(args.out or memory_seed.DEFAULT_SEED_PATH)
    if out.exists() and not args.force and not args.dry_run:
        print(f"{out} exists; pass --force to overwrite it.", file=sys.stderr)
        return 1
    db = _memory_session(args.read_only_prod)
    try:
        claims = memory_seed.load_public_claims(db)
        articles = memory_seed.load_public_articles(db)
        titles = memory_seed.covered_titles(db)
        db.rollback()  # no connection held through the model calls
        need = counts["perturbed"] + counts["paraphrase"]
        if len(claims) < need or len(articles) < counts["near_duplicate"]:
            print(
                f"Not enough indexed public content: {len(claims)} fact chunks (need {need}), "
                f"{len(articles)} indexed articles (need {counts['near_duplicate']}).",
                file=sys.stderr,
            )
            return 1
        sampled = memory_seed.sample(claims, articles, counts, seed=args.seed)
        batches = memory_seed.build_batches(sampled, titles, counts)
        est = memory_seed.estimate_cost(batches, counts["novel"])
        print(
            f"Sampled {need} of {len(claims)} public fact chunks, {counts['near_duplicate']} of "
            f"{len(articles)} indexed public articles; {len(titles)} covered titles in the "
            f"novel-topic prompt. Requested {counts}."
        )
        print(
            f"Sonnet ({memory_seed.MODEL}): {est['calls']} calls, ~{est['input_tokens_est']} input "
            f"tokens, <= {est['output_tokens_cap']} output tokens; estimated "
            f"${est['cost_usd_min']:.4f}–${est['cost_usd_max']:.4f} (the max is the output cap; "
            f"placeholder Sonnet prices). Voyage novelty check: ~{est['voyage_tokens_est']} "
            f"tokens, ${est['voyage_cost_usd_est']:.6f}."
        )
        if args.dry_run:
            print("Dry run: no model call, nothing written.")
            return 0
        settings = get_settings()
        if not settings.anthropic_api_key or not embeddings.embeddings_enabled():
            print("ANTHROPIC_API_KEY and VOYAGE_API_KEY must both be set.", file=sys.stderr)
            return 2
        llm.reset_session_usage()
        items, stats = memory_seed.run_batches(batches)
        novel_texts = memory_seed.novelty_texts(items)
        vectors = (
            embeddings.embed_documents(novel_texts, feature=memory_seed.NOVELTY_FEATURE)
            if novel_texts
            else []
        )
        memory_seed.annotate_novelty(db, items, vectors, settings.suggestion_dedup_min_similarity)
        db.rollback()
    finally:
        db.rollback()
        db.close()
    usage = llm.get_session_usage()._asdict()
    document = memory_seed.build_document(
        items, seed=args.seed, stats=stats, usage=usage, counts=counts
    )
    errors = memory_seed.validate_seed(document)
    if errors:  # pragma: no cover - parse_batch only emits valid items
        print("Generated seed is invalid: " + "; ".join(errors), file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(memory_seed.dumps(document), encoding="utf-8")
    by_type: dict[str, int] = {}
    for item in document["contradictions"] + document["duplicates"]:
        key = f"{item['type']}/{item['language']}"
        by_type[key] = by_type.get(key, 0) + 1
    suspects = sum(bool(i.get("suspect")) for i in document["duplicates"])
    print(
        f"Wrote {out}: {by_type}; {stats['failed_calls']} failed call(s), "
        f"{stats['dropped_items']} malformed item(s) dropped; {suspects} novel topic(s) "
        f"suspect; usage {usage}."
    )
    print("Next: sanity-review the novel topics (review: pending), then `memory run --seed`.")
    return 0


def cmd_memory_run(args, argv: list[str]) -> int:
    seed_path = Path(args.seed)
    try:
        seed = memory_seed.load_seed(seed_path)
    except (memory_seed.SeedError, FileNotFoundError) as exc:
        problems = exc.errors if isinstance(exc, memory_seed.SeedError) else [str(exc)]
        print(f"Cannot load {seed_path}:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    suites = set(args.suites)
    contra = memory_seed.active(seed["contradictions"]) if "contradictions" in suites else []
    dups = memory_seed.active(seed["duplicates"]) if "duplicates" in suites else []
    settings = get_settings()
    est = memory_run.estimate(
        {"contradictions": contra, "duplicates": dups},
        top_k=settings.consistency_top_k,
        batch_pairs=settings.consistency_batch_pairs,
    )
    print(
        f"{len(contra)} contradiction items, {len(dups)} duplicate items (review drops removed). "
        f"Judge: <= {est['pairs_max']} pairs in <= {est['judge_calls_max']} Sonnet calls, "
        f"~${est['judge_cost_usd_est']:.4f} (placeholder prices). Voyage: "
        f"~{est['voyage_tokens_est']} tokens, ${est['voyage_cost_usd_est']:.6f}."
    )
    db = _memory_session(args.read_only_prod)
    try:
        if args.dry_run:
            print("Dry run: no Voyage or Sonnet call, nothing written.")
            return 0
        if not embeddings.embeddings_enabled():
            print("VOYAGE_API_KEY is not set.", file=sys.stderr)
            return 2
        if contra and not settings.anthropic_api_key:
            print("ANTHROPIC_API_KEY is not set (the B4 judge needs it).", file=sys.stderr)
            return 2
        embeddings.reset_usage_totals()
        b3 = (
            memory_run.run_duplicates(
                db, dups, thresholds=list(args.thresholds), min_precision=args.min_precision
            )
            if dups
            else None
        )
        b4 = (
            memory_run.run_contradictions(
                db,
                contra,
                floors=list(args.floors),
                min_precision=args.min_precision,
                shuffle_seed=args.shuffle_seed,
            )
            if contra
            else None
        )
    finally:
        db.rollback()
        db.close()
    voyage = embeddings.get_usage_totals()
    voyage_cost = sum(embeddings.estimate_cost(m, n) for (_, m), n in voyage.items())
    rec = memory_run.recommend(b3, b4)
    meta = {
        "date": args.date or date.today().isoformat(),
        "command": "python -m scripts.eval_rag " + " ".join(shlex.quote(a) for a in argv),
        "seed_path": str(seed_path),
        "seed_sha256": hashlib.sha256(seed_path.read_bytes()).hexdigest(),
        "contradiction_items": len(contra),
        "duplicate_items": len(dups),
        "read_only_prod": bool(args.read_only_prod),
        "embedding_model": settings.embedding_model,
        "settings": {
            "suggestion_dedup_min_similarity": settings.suggestion_dedup_min_similarity,
            "consistency_min_similarity": settings.consistency_min_similarity,
            "consistency_top_k": settings.consistency_top_k,
            "consistency_batch_pairs": settings.consistency_batch_pairs,
            "consistency_max_claims": settings.consistency_max_claims,
        },
        "min_precision": args.min_precision,
        "sonnet_prices": [
            drafting.SONNET_INPUT_PRICE_PER_MTOK,
            drafting.SONNET_OUTPUT_PRICE_PER_MTOK,
        ],
        "voyage_tokens": {f"{e}:{m}": n for (e, m), n in voyage.items()},
        "voyage_cost_usd": voyage_cost,
        "cost_usd_total": voyage_cost + (b4["judge_cost_usd"] if b4 else 0.0),
    }
    md, js = memory_report.write_results(
        meta, b3, b4, rec, out_dir=Path(args.out_dir or memory_report.DEFAULT_OUT_DIR)
    )
    for key in ("b4", "b3"):
        if key in rec:
            print(f"{key.upper()}: {rec[key]['text']}")
    print(f"Wrote {md} and {js}.")
    return 0


# --- answers (#637) -------------------------------------------------------------------


def cmd_answers_rescore(args, argv: list[str]) -> int:
    """Recompute scores, summary and report from a results JSON: no API call, no DB."""
    import json

    path = Path(args.rescore)
    doc = json.loads(path.read_text(encoding="utf-8"))
    meta, records = doc["meta"], answers.rescore(doc["records"])
    # --label / --date name the re-scored files; never overwrite the input (#708).
    if args.label:
        meta["label"] = args.label
    if args.date:
        meta["date"] = args.date
    out_dir = Path(args.out_dir or path.parent)
    stem = f"{meta['date']}-answers" + (f"-{meta['label']}" if meta.get("label") else "")
    if (out_dir / f"{stem}.json").resolve() == path.resolve():
        print(
            f"Refusing to overwrite {path}: pass --label (or --date / --out-dir).",
            file=sys.stderr,
        )
        return 2
    settings = get_settings()
    summary = answers.summarize(records, meta["models"])
    rec = answers.recommend(
        summary,
        meta["settings"]["ask_pic_model"],
        daily_budget_usd=meta["settings"]["ask_pic_daily_budget_usd"],
    )
    meta["rescored"] = (
        f"`python -m scripts.eval_rag {' '.join(shlex.quote(a) for a in argv)}` "
        f"(scoring as of this checkout; ask_pic_model now {settings.ask_pic_model})"
    )
    md, js = answers_report.write_results(meta, summary, records, rec, out_dir=out_dir)
    print(rec["text"])
    print(f"Wrote {md} and {js}.")
    return 0


def cmd_answers(args, argv: list[str]) -> int:
    from app.core.database import engine
    from sqlalchemy.orm import sessionmaker

    if args.rescore:
        return cmd_answers_rescore(args, argv)
    settings = get_settings()
    golden_path = Path(args.golden or golden.DEFAULT_GOLDEN_PATH)
    injection_path = Path(args.injection or answers.DEFAULT_INJECTION_PATH)
    try:
        gold = golden.load_golden(golden_path) if "golden" in args.suites else None
        inj = answers.load_injection(injection_path) if "injection" in args.suites else None
    except (golden.GoldenError, answers.InjectionError, FileNotFoundError) as exc:
        print(f"Cannot load the question sets: {exc}", file=sys.stderr)
        return 1
    if args.ids:
        wanted = set(args.ids)
        if gold:
            gold.questions = [q for q in gold.questions if q.id in wanted]
        if inj:
            inj = [q for q in inj if q.id in wanted]
    if gold:
        gold.questions = answers.stratified(gold.questions, lambda q: q.type, args.limit)
    if inj:
        inj = answers.stratified(inj, lambda q: (q.form, q.language), args.limit)
    n = len(gold.questions if gold else []) + len(inj or [])
    models = list(args.models)
    rewrite = rewrite_from_args(args)
    apply_bare_rerank(args)
    apply_derive_twin_keys(args)
    try:
        selection = selection_from_args(args)
        bound = answers.estimate_upper_bound(n, models, selection.max_passages, rewrite=rewrite)
    except ValueError as exc:  # an unpriced model
        print(str(exc), file=sys.stderr)
        return 1
    print(
        f"{len(gold.questions) if gold else 0} golden + {len(inj or [])} injection questions "
        f"x {len(models)} model(s) ({', '.join(models)}); judge {answers.JUDGE_MODEL}."
    )
    print(
        f"Worst-case ceiling (every prompt at its largest, every output at its cap): "
        f"${bound['total_usd']:.2f}. Real spend is far lower; a --limit 3 run measures it. "
        f"The run stops once it has spent --max-usd ${args.max_usd:.2f}."
    )
    db = _memory_session(args.read_only_prod)  # read-only, checked; same guard as `memory`
    db.close()
    if args.dry_run:
        print("Dry run: no Voyage or model call, nothing written.")
        return 0
    if not settings.anthropic_api_key or not embeddings.embeddings_enabled():
        print("ANTHROPIC_API_KEY and VOYAGE_API_KEY must both be set.", file=sys.stderr)
        return 2
    # Every retrieval session the pipeline opens is read-only too.
    readonly = sessionmaker(
        bind=engine.execution_options(postgresql_readonly=True), autoflush=False
    )
    llm.reset_session_usage()
    records, stopped = answers.run_eval(
        golden=gold,
        injection=inj,
        models=models,
        search_session=readonly,
        max_usd=args.max_usd,
        progress=lambda line: print(f"  {line}", flush=True),
        selection=selection,
        rewrite=rewrite,
    )
    summary = answers.summarize(records, models)
    rec = answers.recommend(
        summary, settings.ask_pic_model, daily_budget_usd=settings.ask_pic_daily_budget_usd
    )
    meta = {
        "date": args.date or date.today().isoformat(),
        "command": "python -m scripts.eval_rag " + " ".join(shlex.quote(a) for a in argv),
        "golden_path": str(golden_path),
        "golden_sha256": hashlib.sha256(golden_path.read_bytes()).hexdigest(),
        "golden_questions": len(gold.questions) if gold else 0,
        "injection_path": str(injection_path),
        "injection_sha256": hashlib.sha256(injection_path.read_bytes()).hexdigest(),
        "injection_questions": len(inj or []),
        "database": "production, read-only (--read-only-prod)"
        if args.read_only_prod
        else "non-production, read-only",
        "models": models,
        "label": args.label,
        "selection": asdict(selection),
        "rewrite": rewrite,
        "rerank_text": rerank_text_label(args),
        "twin_keys": twin_keys_label(args),
        "max_tokens": ask_pic.MAX_TOKENS,
        "effort": ask_pic.EFFORT,
        "judge_model": answers.JUDGE_MODEL,
        "judge_effort": answers.JUDGE_EFFORT,
        "settings": {
            "ask_pic_model": settings.ask_pic_model,
            "ask_pic_min_rerank_score": settings.ask_pic_min_rerank_score,
            "ask_pic_min_vector_similarity": settings.ask_pic_min_vector_similarity,
            "ask_pic_daily_budget_usd": settings.ask_pic_daily_budget_usd,
            "embedding_model": settings.embedding_model,
            "rerank_model": settings.rerank_model,
        },
        "max_usd": args.max_usd,
        "stopped_by_budget": stopped,
        "total_cost_usd": sum(r["cost_usd"] + r["judge_cost_usd"] for r in records),
    }
    md, js = answers_report.write_results(
        meta, summary, records, rec, out_dir=Path(args.out_dir or answers_report.DEFAULT_OUT_DIR)
    )
    print(rec["text"])
    for m in models:
        s = summary[m]
        print(
            f"{m}: golden malformed {_pct_or_dash(s['malformed_share'])}, "
            f"tolerant parse {_pct_or_dash(s['tolerant_share'])}, "
            f"parse paths {s['parse_paths']}"
        )
    print(f"Spent ${meta['total_cost_usd']:.4f}. Wrote {md} and {js}.")
    return 0


def cmd_selection(args, argv: list[str]) -> int:
    """Passage-selection eval (#695): retrieve once per question, apply each variant."""
    from app.core.database import engine
    from sqlalchemy.orm import sessionmaker

    settings = get_settings()
    golden_path = Path(args.golden or golden.DEFAULT_GOLDEN_PATH)
    try:
        gold = golden.load_golden(golden_path)
    except (golden.GoldenError, FileNotFoundError) as exc:
        print(f"Cannot load the golden set: {exc}", file=sys.stderr)
        return 1
    if args.ids:
        gold.questions = [q for q in gold.questions if q.id in set(args.ids)]
    unknown = [v for v in args.variants if v not in selection.VARIANTS]
    if unknown:
        print(f"Unknown variants {unknown}; known: {list(selection.VARIANTS)}", file=sys.stderr)
        return 1
    variants = {v: selection.VARIANTS[v] for v in args.variants}
    rewrite = rewrite_from_args(args)
    apply_bare_rerank(args)
    deriver = apply_derive_twin_keys(args)
    print(
        f"{len(gold.questions)} questions x {len(variants)} selection variants; "
        + ("one query-rewrite call per question (#696)." if rewrite else "no model call.")
    )
    db = _memory_session(args.read_only_prod)  # read-only, checked; same guard as `memory`
    db.close()
    if args.dry_run:
        print("Dry run: no Voyage call, nothing written.")
        return 0
    if not embeddings.embeddings_enabled():
        print("VOYAGE_API_KEY must be set.", file=sys.stderr)
        return 2
    if rewrite and not settings.anthropic_api_key:
        print("--rewrite needs ANTHROPIC_API_KEY.", file=sys.stderr)
        return 2
    readonly = sessionmaker(
        bind=engine.execution_options(postgresql_readonly=True), autoflush=False
    )
    records, spent = selection.run(
        gold,
        readonly,
        variants=variants,
        progress=lambda line: print(f"  {line}", flush=True),
        rewrite=rewrite,
    )
    summary = selection.summarize(records, variants)
    floors = selection.floor_report(records)
    meta = {
        "date": args.date or date.today().isoformat(),
        "label": args.label,
        "command": "python -m scripts.eval_rag " + " ".join(shlex.quote(a) for a in argv),
        "golden_path": str(golden_path),
        "golden_sha256": hashlib.sha256(golden_path.read_bytes()).hexdigest(),
        "questions": len(gold.questions),
        "database": "production, read-only (--read-only-prod)"
        if args.read_only_prod
        else "non-production, read-only",
        "variants": selection.variants_meta(variants),
        "rewrite": rewrite,
        "rerank_text": rerank_text_label(args),
        "twin_keys": twin_keys_label(args),
        "latency_ms": selection.latency_meta(records),
        "settings": {
            "ask_pic_min_rerank_score": settings.ask_pic_min_rerank_score,
            "embedding_model": settings.embedding_model,
            "rerank_model": settings.rerank_model,
        },
        "total_cost_usd": spent,
    }
    md, js = selection.write_results(
        meta,
        summary,
        floors,
        records,
        out_dir=Path(args.out_dir or selection.DEFAULT_OUT_DIR),
    )
    if deriver is not None:
        print(f"Twin keys derived for {deriver.filled} hits; {deriver.unmatched} unmatched.")
    print(f"Spent ${spent:.4f}. Wrote {md} and {js}.")
    return 0


def rewrite_from_args(args) -> bool:
    """The #696 query rewrite a run measures: ``--rewrite`` / ``--no-rewrite``,
    else the ``ask_pic_query_rewrite_enabled`` setting (what the endpoint runs)."""
    value = getattr(args, "rewrite", None)
    return get_settings().ask_pic_query_rewrite_enabled if value is None else value


def apply_bare_rerank(args) -> None:
    """``--bare-rerank``: rerank on the chunk text alone, the pre-#697 input (ablation)."""
    if getattr(args, "bare_rerank", False):
        retrieval.rerank_text = lambda hit: hit.content


def apply_derive_twin_keys(args) -> twin_keys.TwinKeyDeriver | None:
    """``--derive-twin-keys``: compute the #713 twin key in memory (see ``twin_keys``)."""
    if getattr(args, "derive_twin_keys", False):
        return twin_keys.install()
    return None


def twin_keys_label(args) -> str:
    if getattr(args, "derive_twin_keys", False):
        return "derived in memory from the current chunker (--derive-twin-keys)"
    return "as stored in knowledge_chunks.meta"


def _add_derive_twin_keys_arg(p) -> None:
    p.add_argument(
        "--derive-twin-keys",
        action="store_true",
        help="Re-chunk each retrieved article and use the current twin key (#713), "
        "for a database indexed before it",
    )


def rerank_text_label(args) -> str:
    if getattr(args, "bare_rerank", False):
        return "chunk only (--bare-rerank)"
    return "title > heading + situating sentence + chunk (#697)"


def _add_bare_rerank_arg(p) -> None:
    p.add_argument(
        "--bare-rerank",
        action="store_true",
        help="Rerank on the chunk text alone, as before #697 (ablation)",
    )


def _add_rewrite_arg(p) -> None:
    p.add_argument(
        "--rewrite",
        action=argparse.BooleanOptionalAction,
        help="Query rewriting before retrieval (#696); default: the setting",
    )


def _pct_or_dash(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


def selection_from_args(args) -> ask_pic.PassageSelection:
    """The passage selection a run measures (#695): the endpoint's default,
    with any ``--max-passages`` / ``--per-article-cap`` / ``--language-dedup``
    / ``--skip-covered-claims`` / ``--paragraph-replaces-claims`` override applied (``--per-article-cap 0`` = no cap)."""
    default = ask_pic.DEFAULT_SELECTION
    cap = default.per_article_cap
    if getattr(args, "per_article_cap", None) is not None:
        cap = args.per_article_cap or None
    flags = {}
    for name in (
        "language_dedup",
        "skip_covered_claims",
        "paragraph_replaces_claims",
        "twin_by_index",
    ):
        value = getattr(args, name, None)
        flags[name] = getattr(default, name) if value is None else value
    max_passages = getattr(args, "max_passages", None)
    return ask_pic.PassageSelection(
        max_passages=default.max_passages if max_passages is None else max_passages,
        per_article_cap=cap,
        **flags,
    )


def _positive_int(value: str) -> int:
    """An argparse type: an int >= 1. ``--max-passages 0`` used to fall back to
    the default silently (#708); now argparse rejects it (exit 2)."""
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from None
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {n}")
    return n


def _add_selection_args(p) -> None:
    """Passage-selection overrides (#695); omitted = the endpoint's default."""
    p.add_argument("--max-passages", type=_positive_int, help="Passages sent to the model (>= 1)")
    p.add_argument("--per-article-cap", type=int, help="Max passages per article (0 = no cap)")
    p.add_argument(
        "--language-dedup",
        action=argparse.BooleanOptionalAction,
        help="Keep one of an EN/ES twin pair (the question's language)",
    )
    p.add_argument(
        "--skip-covered-claims",
        action=argparse.BooleanOptionalAction,
        help="Skip a claim chunk when a paragraph of its article is selected",
    )
    p.add_argument(
        "--paragraph-replaces-claims",
        action=argparse.BooleanOptionalAction,
        help="A selected paragraph drops its article's claim chunks (and skips later ones)",
    )
    p.add_argument(
        "--twin-by-index",
        action=argparse.BooleanOptionalAction,
        help="Pair EN/ES twins by chunk_index (default); --no-twin-by-index pairs by "
        "the #713 twin key",
    )


def _add_answers_parser(sub) -> None:
    a = sub.add_parser("answers", help="Ask PIC answer eval: golden + injection, judged (#637)")
    a.add_argument("--golden", help="Golden file (default eval/rag/golden.json)")
    a.add_argument("--injection", help="Injection file (default eval/rag/injection.json)")
    a.add_argument("--suites", nargs="+", choices=answers.SUITES, default=list(answers.SUITES))
    a.add_argument("--models", nargs="+", default=list(answers.DEFAULT_MODELS))
    a.add_argument("--limit", type=int, help="Questions per suite (stratified), for a smoke run")
    a.add_argument("--ids", nargs="+", help="Only these question ids")
    a.add_argument(
        "--max-usd", type=float, default=5.0, help="Stop once this much is spent (default 5)"
    )
    a.add_argument("--out-dir", help="Results dir (default docs/portfolio/rag/results)")
    a.add_argument("--date", help="Date stamp for the file names (default today)")
    a.add_argument("--label", help="Suffix for the file names: <date>-answers-<label>.md")
    _add_selection_args(a)
    _add_rewrite_arg(a)
    _add_bare_rerank_arg(a)
    _add_derive_twin_keys_arg(a)
    a.add_argument("--dry-run", action="store_true", help="Print the plan and cost ceiling only")
    a.add_argument(
        "--rescore",
        metavar="RESULTS_JSON",
        help="Recompute scores and the report from a results JSON (no API call, no DB)",
    )
    a.add_argument(
        "--read-only-prod",
        action="store_true",
        help="Waive the production-host refusal (the session is read-only regardless)",
    )


def _add_selection_parser(sub) -> None:
    s = sub.add_parser(
        "selection", help="Ask PIC passage-selection eval: one retrieval, every variant (#695)"
    )
    s.add_argument("--golden", help="Golden file (default eval/rag/golden.json)")
    s.add_argument("--ids", nargs="+", help="Only these question ids")
    s.add_argument("--variants", nargs="+", default=list(selection.DEFAULT_VARIANTS))
    s.add_argument("--out-dir", help="Results dir (default docs/portfolio/rag/results)")
    s.add_argument("--date", help="Date stamp for the file names (default today)")
    s.add_argument("--label", help="Suffix for the file names: <date>-selection-<label>.md")
    _add_rewrite_arg(s)
    _add_bare_rerank_arg(s)
    _add_derive_twin_keys_arg(s)
    s.add_argument("--dry-run", action="store_true", help="Print the plan only")
    s.add_argument(
        "--read-only-prod",
        action="store_true",
        help="Waive the production-host refusal (the session is read-only regardless)",
    )


def _add_memory_parser(sub) -> None:
    m = sub.add_parser("memory", help="Pipeline-memory eval: B3 duplicates + B4 contradictions")
    msub = m.add_subparsers(dest="memory_command", required=True)
    read_only_help = (
        "Waive the production-host refusal (the session is read-only regardless): "
        "for reading the production index"
    )

    s = msub.add_parser("seed", help="Generate the seed set with Sonnet")
    s.add_argument("--out", help="Seed file (default eval/rag/memory_seed.json)")
    s.add_argument("--seed", type=int, default=632, help="Sampling seed")
    for name, default in memory_seed.DEFAULT_COUNTS.items():
        s.add_argument(
            f"--{name.replace('_', '-')}",
            type=int,
            default=default,
            help=f"Items (default {default})",
        )
    s.add_argument("--dry-run", action="store_true", help="Sample and estimate cost only")
    s.add_argument("--force", action="store_true", help="Overwrite an existing seed file")
    s.add_argument("--read-only-prod", action="store_true", help=read_only_help)

    r = msub.add_parser("run", help="Run B3 + B4 over a seed file and write the results")
    r.add_argument("--seed", default=str(memory_seed.DEFAULT_SEED_PATH), help="Seed file")
    r.add_argument(
        "--suites",
        nargs="+",
        choices=("contradictions", "duplicates"),
        default=["contradictions", "duplicates"],
    )
    r.add_argument("--floors", nargs="+", type=float, default=list(memory_run.DEFAULT_FLOORS))
    r.add_argument(
        "--thresholds", nargs="+", type=float, default=list(memory_run.DEFAULT_THRESHOLDS)
    )
    r.add_argument("--min-precision", type=float, default=memory_run.MIN_PRECISION)
    r.add_argument("--shuffle-seed", type=int, default=632)
    r.add_argument("--out-dir", help="Results dir (default docs/portfolio/rag/results)")
    r.add_argument("--date", help="Date stamp for the file names (default today)")
    r.add_argument("--dry-run", action="store_true", help="Print the plan and cost only")
    r.add_argument("--read-only-prod", action="store_true", help=read_only_help)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.eval_rag", description="RAG eval harness (#627)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    d = sub.add_parser("draft-questions", help="Draft candidate questions with Sonnet")
    d.add_argument("--out", help="Draft file (default eval/rag/drafts/YYYY-MM-DD-drafts.json)")
    d.add_argument("--seed", type=int, default=627)
    for name, default in drafting.DEFAULT_COUNTS.items():
        d.add_argument(f"--{name}", type=int, default=default, help=f"Drafts (default {default})")
    d.add_argument("--dry-run", action="store_true", help="Sample and estimate cost only")
    d.add_argument("--force", action="store_true", help="Overwrite an existing draft file")

    g = sub.add_parser("golden", help="Turn a curated draft file into golden.json")
    g.add_argument("--drafts", required=True)
    g.add_argument("--out", help="Golden file (default eval/rag/golden.json)")
    g.add_argument("--description")
    g.add_argument("--force", action="store_true", help="Overwrite an existing golden file")
    g.add_argument(
        "--append",
        action="store_true",
        help="Merge a verified draft-reader file into the existing golden file (#693)",
    )
    g.add_argument(
        "--curation-md",
        help="With --append: also write the curation summary (fixes and drops) to this path",
    )

    dr = sub.add_parser(
        "draft-reader", help="Draft reader-style questions with Opus and verify them (#693)"
    )
    dr.add_argument("--out", help="Output (default eval/rag/drafts/YYYY-MM-DD-reader.json)")
    dr.add_argument("--seed", type=int, default=693, help="Sampling seed for --articles")
    dr.add_argument("--articles", type=int, help="Draft from a sample of N articles (default all)")
    dr.add_argument(
        "--unanswerable",
        type=int,
        default=reader.DEFAULT_UNANSWERABLE,
        help=f"Unanswerable reader questions (default {reader.DEFAULT_UNANSWERABLE})",
    )
    dr.add_argument("--dry-run", action="store_true", help="Load and estimate cost only")
    dr.add_argument("--force", action="store_true", help="Overwrite an existing output file")
    dr.add_argument(
        "--read-only-prod",
        action="store_true",
        help="Waive the production-host refusal (the session is read-only regardless)",
    )

    r = sub.add_parser("retrieval", help="Run the retrieval eval and write the results")
    r.add_argument("--golden", help="Golden file (default eval/rag/golden.json)")
    r.add_argument("--models", nargs="+", default=list(runner.DEFAULT_MODELS))
    r.add_argument("--modes", nargs="+", choices=runner.MODES, default=list(runner.MODES))
    r.add_argument("--k", type=int, default=retrieval.DEFAULT_K)
    r.add_argument("--candidates", type=int, default=retrieval.DEFAULT_CANDIDATES)
    r.add_argument("--out-dir", help="Results dir (default docs/portfolio/rag/results)")
    r.add_argument("--date", help="Date stamp for the file names (default today)")
    r.add_argument("--no-reuse", action="store_true", help="Re-embed even same-model vectors")
    r.add_argument("--dry-run", action="store_true", help="Print the plan and cost only")
    r.add_argument(
        "--live",
        action="store_true",
        help="Search the live knowledge_chunks read-only (no eval table; settings model only)",
    )
    r.add_argument(
        "--read-only-prod",
        action="store_true",
        help="With --live: waive the production-host refusal (the session is read-only)",
    )

    x = sub.add_parser("drop-tables", help="Drop eval-scoped chunk tables")
    x.add_argument("--models", nargs="+", default=list(runner.DEFAULT_MODELS))

    _add_memory_parser(sub)
    _add_answers_parser(sub)
    _add_selection_parser(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    app_logger = logging.getLogger("app")
    level = app_logger.level
    _quiet()
    try:
        if args.command == "draft-questions":
            return cmd_draft_questions(args)
        if args.command == "golden":
            return cmd_golden(args)
        if args.command == "draft-reader":
            return cmd_draft_reader(args)
        if args.command == "retrieval":
            return cmd_retrieval(args, argv)
        if args.command == "answers":
            return cmd_answers(args, argv)
        if args.command == "selection":
            return cmd_selection(args, argv)
        if args.command == "memory":
            if args.memory_command == "seed":
                return cmd_memory_seed(args)
            return cmd_memory_run(args, argv)
        return cmd_drop_tables(args)
    finally:
        app_logger.setLevel(level)  # main() is also called in-process by the tests
