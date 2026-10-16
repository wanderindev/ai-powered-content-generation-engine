#!/usr/bin/env python3
"""Benchmark the grammar-apply meaning-check prompt against labelled pairs.

Replays stored ``grammar_check_fixes`` pairs (before = ``find_text``, after =
``replace_text``) through the verdict prompt and scores the verdicts against
hand labels. Used for the 2026-09-27 recalibration of
``app/services/grammar_verdicts.py``: 27 same-day reverts plus the 245 EN rows
from the first live Grammarly run (2026-09-20), plus 20 random same-day KEEPs
("KEEP?", not hand-verified). REVERT and REVIEW both count as catching a
REVERT-labelled pair; EITHER pairs are judgment calls and are only tallied.

Read-only on the database. It does call Sonnet (``feature=grammar_verdicts_eval``,
a few cents per run), so it runs where the Anthropic key is, i.e. the backend
container:
    docker cp scripts/one_off/eval_grammar_verdicts.py pic-backend:/tmp/
    docker exec -w /app pic-backend python /tmp/eval_grammar_verdicts.py --runs 3
    # compare a candidate prompt without deploying it:
    docker cp backend/app/services/grammar_verdicts.py pic-backend:/tmp/candidate.py
    docker exec -w /app pic-backend python /tmp/eval_grammar_verdicts.py \\
        --prompt-file /tmp/candidate.py --runs 3
"""

from __future__ import annotations

import argparse
import ast
import collections
import random
import sys
from pathlib import Path

if (Path.cwd() / "app").is_dir():
    sys.path.insert(0, str(Path.cwd()))

import app.services.grammar_verdicts as live  # noqa: E402
from app.core.database import engine  # noqa: E402
from app.services.llm import SONNET_MODEL, create_message, first_text  # noqa: E402
from sqlalchemy import text  # noqa: E402

R, K, J = "REVERT", "KEEP", "EITHER"
# grammar_check_fixes ids -> expected verdict (hand-labelled 2026-09-27).
LABELS: dict[int, str] = {
    # Same-day reverts that changed a claim.
    3218: R, 3244: R, 3248: R, 3278: R, 3301: R, 3305: R, 3347: R, 3356: R, 3359: R, 3374: R,
    # Same-day reverts over intensifiers, splits and synonyms.
    3241: K, 3257: K, 3259: K, 3285: K, 3299: K, 3330: K, 3372: K, 3381: K,
    # Judgment calls (voice, strength, would/could modality, added hedge).
    3252: J, 3254: J, 3256: J, 3281: J, 3302: J, 3303: J, 3332: J, 3352: J, 3379: J,
    # 245 EN, first live run: clean fixes and Clarity reorders ...
    1: K, 5: K, 6: K, 7: K, 8: K, 9: K, 12: K, 17: K, 18: K, 20: K, 22: K, 23: K, 24: K,
    25: K, 26: K, 27: K, 28: K,
    # ... broken punctuation, a dropped hedge, would -> did ...
    10: R, 11: R, 13: R, 19: R, 21: R,
    # ... and judgment calls.
    14: J, 15: J, 16: J,
}
BATCH = 8


def _prompt_from_file(path: str) -> str:
    tree = ast.parse(Path(path).read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "_VERDICT_PROMPT":
            return ast.literal_eval(node.value)
    raise SystemExit(f"no _VERDICT_PROMPT in {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--prompt-file", help="Python file to read _VERDICT_PROMPT from (default: deployed)")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--effort", default="low", choices=("low", "medium", "high"))
    parser.add_argument("--sample-keeps", type=int, default=20, help="random same-day KEEPs to add")
    args = parser.parse_args()
    prompt = _prompt_from_file(args.prompt_file) if args.prompt_file else live._VERDICT_PROMPT

    labels = dict(LABELS)
    with engine.connect() as c:
        kept = [r.id for r in c.execute(text(
            "select id from grammar_check_fixes where created_at::date = '2026-09-27' and applied order by id"
        ))]
        random.Random(531).shuffle(kept)
        for i in kept[: args.sample_keeps]:
            labels.setdefault(i, "KEEP?")
        rows = {r.id: (r.find_text, r.replace_text) for r in c.execute(
            text("select id, find_text, replace_text from grammar_check_fixes where id = any(:ids)"),
            {"ids": list(labels)},
        )}
    ids = sorted(i for i in labels if i in rows)

    for run in range(1, args.runs + 1):
        tally: collections.Counter = collections.Counter()
        misses: list[str] = []
        for start in range(0, len(ids), BATCH):
            batch = ids[start : start + BATCH]
            pairs = [live.ChangedPair(i, *rows[i]) for i in batch]
            message = create_message(
                feature="grammar_verdicts_eval",
                model=SONNET_MODEL,
                max_tokens=8000,
                system=prompt,
                messages=[{"role": "user", "content": live._render_pairs(pairs)}],
                output_config={"effort": args.effort},
            )
            by_pos: dict[int, list] = {}
            for v in live.parse_verdict_lines(first_text(message), len(pairs)):
                by_pos.setdefault(v.position, []).append(v)
            for pos, i in enumerate(batch):
                kinds = {v.kind for v in by_pos.get(pos, [])}
                kind = R if R in kinds else ("REVIEW" if "REVIEW" in kinds or not kinds else K)
                label = labels[i]
                if label == R:
                    ok = kind != K
                    tally["revert caught" if ok else "revert MISSED"] += 1
                elif label.startswith(K):
                    ok = kind == K
                    tally[("keep ok" if ok else "keep FALSE-POSITIVE") + ("?" if label == "KEEP?" else "")] += 1
                else:
                    ok = True
                    tally[f"either {kind}"] += 1
                if not ok:
                    misses.append(f"#{i} {label} got {kind}")
        print(f"run {run}: {dict(sorted(tally.items()))}")
        for line in misses:
            print(f"    {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
