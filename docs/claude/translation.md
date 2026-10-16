# Translation

Moved verbatim out of `CLAUDE.md` (size guard, 2026-10-12). Same
authority as CLAUDE.md; read this file before changing this area.

## Translation Has Three Entry Points, and Only Two Are Generic

`TranslationService` (`app/services/translation.py`, target `ES-419`, DeepL
`formality` as a per-call override) is the seam every DeepL call goes
through — articles (title / excerpt / content, `app/api/admin.py` and
`app/api/dashboard.py`), taxonomies and categories, and edu study guides
(`app/api/edu.py`). Which method you call is not a style choice:

- **`translate_text`** — plain strings only (titles, excerpts, names).
- **`translate_markdown`** — the Round-Trip Middleware: markdown → HTML →
  DeepL with `tag_handling="html"` → markdownify → whitespace cleanup. `<code>`
  and `<pre>` are given a `notranslate` class first (DeepL honours it) and
  stripped of it afterwards. Sending markdown through `translate_text` instead
  mangles the markup, which is what this path exists to prevent. It is also the
  **legacy** guide path, taken only when a guide has no `content_blocks`.
- **`translate_blocks`** — the structured-guide path (#340): one batched DeepL
  call for every translatable string across the blocks, written back into a deep
  copy (the input is untouched, and block order is preserved so
  `apply_spanish_block_overrides` can match ES to EN by position).

**All three normalise the Spanish thousands separator on the way out (#531).**
DeepL at ES-419 writes `1.500` in some articles and `1,500` in others (it has
no separator setting), while Panama and the English source use the comma, so
`TranslationService._normalize` runs every result, including each batched block
string, through `normalize_es_thousands`. That is a module-level function so
`scripts/normalize_es_thousands.py` can reuse it for already-stored content. A
dot counts as a separator only between a non-zero-led 1-3 digit group and
exactly three digits with no digit or `,d` / `.d` after, so decimals (`3.5`,
`0.125`), four-digit years, dotted dates and European `1.500,75` pass through.
Code spans, fenced code, `<code>` / `<pre>` and URLs are shielded. Rewriting a
narrated article's `content_es` this way re-flags those paragraphs for a
`"changed"` narration run, because the segment hash is taken from the spoken
text (the voice reads both forms identically).

**An optional EN→ES glossary rides on the same calls (#633).** With
`DEEPL_GLOSSARY_ENABLED` and `DEEPL_GLOSSARY_ID` set, `_translate` /
`_translate_many` add `glossary=` and `source_lang="EN"`, which the SDK requires
with a glossary. Glossaries are language-level, so an EN→ES one serves the
`ES-419` target. Off, or on with an empty id (one warning), the SDK kwargs are
byte-identical to before. Source TSV: `app/data/deepl_glossary_en_es.tsv`;
tooling is `scripts.glossary_candidates` / `sync_deepl_glossary` /
`eval_glossary` (see `docs/design/rag-knowledge-layer.md`).

**`translate_blocks` is a per-`type` `if/elif` dispatch with no default branch**,
and that is the asymmetry to design around: `app/utils/guide_blocks.py` walks the
same JSONB *generically* (string leaves minus a machine-key blocklist) precisely
so a new `GuideBlockType` needs no fact-check change — but the translator will
skip an unrecognised type in silence, shipping its text in English with no error
and no warning. All eleven current types have a branch. A new one needs a case
here, plus the usual machine-key discipline: `image_query` and `type` are never
translated, and an `mc_question` translates its `question` and `choices` but not
its `answer` (an index/letter, not prose — same exclusion the walker makes).

**Those three are the entry points inside `app/`; the UI copy has a fourth, in
the scripts tree, that bypasses the seam.** `backend/scripts/
translate_frontend.py` builds its own `deepl.Translator(settings.deepl_api)` —
as does `translate_taxonomy_category.py` — and reaches **across the container
boundary** into `frontend/public/locales/{en,es}/translation.json`, so it is
the one DeepL path that writes a file rather than a DB row. What it does is
narrower than "translate the locale file": `find_missing_keys` walks the EN
tree recursively and collects only keys that are **absent or blank in ES**,
translates each one individually at `ES-419` / `formality="default"`, and
writes them back by dot-path. It therefore never revises an existing Spanish
string, and a wrong-but-present translation is invisible to it. Three modes —
`--preview` (no DeepL call), `--dry-run` (translates, saves nothing) and
`--execute`, which prompts for an interactive `yes` before overwriting the ES
file, so it will not run unattended. This is the tooling behind "adding a
string means editing both JSON files"; the EN side is still hand-written.

**`translate_blocks` is also not the only per-`type` dispatch over a guide's
blocks — there are four, and every one of them fails silently in its own
way.** Adding a `GuideBlockType` means a branch in all four, plus a shape in
the generation prompt (`app/prompts/edu_material_generation.py`, which
enumerates the catalogue for the model). Without each branch: the translator
ships the block in English (above); `_block_to_frag`
(`app/services/guide_pdf.py`) **returns `None`**, so the block is simply absent
from the rendered PDF; `blocks_to_markdown`
(`app/services/edu_material_generation.py`, the `content` / `content_es`
projection) is an `if/elif` with no `else`, so the block contributes nothing to
the markdown the admin grid and search read; and `BlockFields` in
`frontend/src/components/admin/GuideBlocksEditor.jsx` falls through to
`default:` "Unknown block type.", leaving the block **uneditable in the very UI
the fact-check gate calls the correction path**. None of the four raises — the
same fail-soft shape as the drawer's `PlaceholderEditor`. The generic walkers
in `app/utils/guide_blocks.py` stay the deliberate exception: they need no
change at all.
