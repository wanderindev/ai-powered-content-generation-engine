"""RAG evaluation harness (#627, epic #617): golden set, drafts, retrieval eval.

Runs against the **dev stack** with a local copy of production content, never
in CI (CI only unit-tests the pure parts) and never against production: every
command that opens the database refuses ``ENVIRONMENT=production`` or a
DigitalOcean managed-Postgres host (``tables.assert_not_production``).

Usage (from ``backend/``, in the dev backend container):
    python -m scripts.eval_rag draft-questions --dry-run      # sample + Sonnet cost estimate
    python -m scripts.eval_rag draft-questions                # -> eval/rag/drafts/<date>-drafts.json
    python -m scripts.eval_rag golden --drafts eval/rag/drafts/<date>-drafts.json
    python -m scripts.eval_rag retrieval --dry-run            # index plan + Voyage cost estimate
    python -m scripts.eval_rag retrieval                      # -> docs/portfolio/rag/results/<date>-retrieval.{md,json}
    python -m scripts.eval_rag retrieval --models voyage-4-lite voyage-4 voyage-4-large
    python -m scripts.eval_rag drop-tables                    # remove the eval-scoped tables
    python -m scripts.eval_rag memory seed --dry-run          # #632: sample + Sonnet cost estimate
    python -m scripts.eval_rag memory seed                    # -> eval/rag/memory_seed.json
    python -m scripts.eval_rag memory run --seed eval/rag/memory_seed.json   # -> results/<date>-memory.{md,json}
    python -m scripts.eval_rag memory run ... --read-only-prod   # against the production index, read-only
    python -m scripts.eval_rag answers --read-only-prod --limit 3   # #637: smoke run, real spend
    python -m scripts.eval_rag answers --read-only-prod             # -> results/<date>-answers.{md,json}
    python -m scripts.eval_rag draft-reader --read-only-prod --dry-run   # #693: Opus cost estimate
    python -m scripts.eval_rag draft-reader --read-only-prod   # -> eval/rag/drafts/<date>-reader.json
    python -m scripts.eval_rag golden --drafts eval/rag/drafts/<date>-reader.json --append
    python -m scripts.eval_rag retrieval --live --read-only-prod --modes hybrid+rerank
    python -m scripts.eval_rag selection --read-only-prod   # #695: passage-selection variants, no model call
    python -m scripts.eval_rag answers --read-only-prod --label cap2 --per-article-cap 2   # one variant

**``draft-reader`` and ``retrieval --live`` (#693) are read-only too.**
``draft-reader`` reads the public articles on a ``postgresql_readonly``
session, closes it, then drafts reader-style questions with Opus and verifies
them with a second Opus pass (``reader``; no manual curation: the verdicts
travel in the drafts file and the counts in ``golden.json``). ``retrieval
--live`` searches the live ``knowledge_chunks`` with the configured embedding
model instead of building eval tables, so it can measure production's index.

**``memory``, ``answers`` and ``selection`` are the exceptions to "never
production"** (#632, #637, #695): they only ever open ``postgresql_readonly`` sessions (verified with
``SHOW transaction_read_only`` before use), create no table and write nothing
to the database, so ``--read-only-prod`` may waive the host refusal for them.
Every other command keeps the refusal and does not accept the flag.

``answers`` (#637) runs the golden set and ``eval/rag/injection.json`` through
``app.services.ask_pic.run_pipeline`` (the code behind ``POST /api/v1/ask``,
minus the ledger and limits: no ``ask_pic_queries`` row is written) for each
``--models`` entry, judges every answer with Sonnet (``feature="eval_judge"``)
and writes ``docs/portfolio/rag/results/<date>-answers.{md,json}`` with the
go / no-go for ``ask_pic_enabled``. ``--max-usd`` (default 5) stops a run
that has spent that much. Modules: ``answers`` (run, judge, score,
recommend), ``answers_report``. ``--label`` suffixes the file names and the
``--max-passages`` / ``--per-article-cap`` / ``--[no-]language-dedup`` /
``--[no-]skip-covered-claims`` flags override the passage selection (#695).

``selection`` (#695) retrieves each golden question once with
``ask_pic.search_passages`` and applies every named passage-selection variant
to the same ranked list: recall of the passages sent, key facts in their text,
prompt tokens, the abstain floor. Voyage only, no model call. Module:
``selection``. Modules:
``memory_seed`` (seed schema + Sonnet drafts), ``memory_run`` (B3 / B4 over
the seed, reusing ``suggestion_dedup`` / ``consistency_check``),
``memory_report``.

Modules: ``metrics`` (recall@k, MRR, nDCG, floor candidates), ``golden``
(schema, loader, drafts -> golden), ``drafting`` (Sonnet drafts),
``tables`` (production refusal, ``eval_knowledge_chunks_<model>`` tables),
``runner`` (the eval loop and aggregation), ``report`` (markdown + JSON).

``draft-questions`` reads the database and writes only the draft file (the
``eval/rag/drafts/`` directory is gitignored: drafts are working files, the
curation counts travel in ``golden.json``). ``retrieval`` writes only the
eval-scoped tables and the results files; ``knowledge_chunks`` is read-only.
"""
