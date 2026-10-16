"""Golden-set schema, loader and the curated-drafts -> golden conversion (#627).

``backend/eval/rag/golden.json`` (curated in #653, never by this ticket)::

    {
      "version": 1,
      "description": "...",
      "curation": {"drafts_file": "...", "drafted": 60, "kept": 30, ...},
      "questions": [
        {
          "id": "d001",
          "language": "en" | "es",
          "question": "Who financed the Panama Railroad?",
          "type": "factual" | "multi-article" | "cross-lingual" | "reader" | "unanswerable",
          "expected_article_ids": [12],          # [] only for unanswerable
          "expected_section": "Financing",       # optional heading path
          "search_languages": ["en"],            # optional chunk-language filter
          "reference_answer": "...",             # optional, for the answer eval
          "subtype": "uncovered_panama",         # optional; see READER_SUBTYPES
          "key_facts": ["1510", "Enciso"],       # reader: strings the answer must contain
          "source": "operator" | "opus",         # reader-era items: who wrote the question
          "notes": "..."                         # optional
        }
      ]
    }

Rules (:func:`validate_questions`): ids are unique; ``language`` is ``en`` /
``es``; ``unanswerable`` has no expected articles and every other type has at
least one, ``multi-article`` at least two. ``cross-lingual`` means the answer
lives only in the *other* language's chunks: without an explicit
``search_languages`` it searches the other language (:func:`search_languages`).

``reader`` (#693) is a short, natural question a visitor would type, drafted
per article by Opus (or typed by the operator, ``source: operator``) and
checked by a second Opus pass rather than by hand (:mod:`.reader`). It needs
``key_facts``: short strings (a year, a name) a correct answer must contain,
matched case-, accent- and digit-separator-insensitively by the answer eval
(:func:`key_facts_found`).

The draft file (``draft-questions``) carries the same fields per draft plus
``source`` (chunk id, article, heading path, excerpt) and a ``status`` the
curator sets to ``keep`` / ``edit`` / ``drop``; :func:`drafts_to_golden`
refuses a file with any undecided draft.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

GOLDEN_VERSION = 1
LANGUAGES = ("en", "es")
TYPES = ("factual", "multi-article", "cross-lingual", "reader", "unanswerable")
# Reader question kinds (#693). Reader-era unanswerable items use ``uncovered_period``.
READER_SUBTYPES = ("who", "when", "where", "first", "how_long", "period_term", "distractor", "what")
STATUSES = ("keep", "edit", "drop")
# Thousands separators: comma, period, no-break / narrow no-break / thin space.
# Not a plain space (#708): "in 1513 500 men" is two numbers.
_DIGIT_GROUP = re.compile(r"(?<=\d)[.,\u00a0\u202f\u2009](?=\d{3}\b)")
DEFAULT_GOLDEN_PATH = Path(__file__).resolve().parents[2] / "eval" / "rag" / "golden.json"

_QUESTION_FIELDS = (
    "id",
    "language",
    "question",
    "type",
    "expected_article_ids",
    "expected_section",
    "search_languages",
    "reference_answer",
    "subtype",
    "key_facts",
    "source",
    "notes",
)


class GoldenError(ValueError):
    """A golden or draft file failed validation; ``errors`` lists every problem."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass
class GoldenQuestion:
    id: str
    language: str
    question: str
    type: str
    expected_article_ids: list[int] = field(default_factory=list)
    expected_section: str | None = None
    search_languages: list[str] | None = None
    reference_answer: str | None = None
    subtype: str | None = None
    key_facts: list[str] | None = None
    source: str | None = None
    notes: str | None = None

    @property
    def answerable(self) -> bool:
        return self.type != "unanswerable"

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class GoldenSet:
    questions: list[GoldenQuestion]
    version: int = GOLDEN_VERSION
    description: str | None = None
    curation: dict | None = None

    def to_dict(self) -> dict:
        out: dict = {"version": self.version}
        if self.description:
            out["description"] = self.description
        if self.curation:
            out["curation"] = self.curation
        out["questions"] = [q.to_dict() for q in self.questions]
        return out


def search_languages(question: GoldenQuestion) -> list[str] | None:
    """The chunk languages to search for ``question`` (``None`` = both)."""
    if question.search_languages:
        return list(question.search_languages)
    if question.type == "cross-lingual":
        return [lang for lang in LANGUAGES if lang != question.language]
    return None


def _validate_one(raw: object, where: str) -> tuple[GoldenQuestion | None, list[str]]:
    errors: list[str] = []
    if not isinstance(raw, dict):
        return None, [f"{where}: not an object"]
    qid = raw.get("id")
    label = f"{where} ({qid})" if qid else where
    if not isinstance(qid, str) or not qid.strip():
        errors.append(f"{label}: id must be a non-empty string")
    if raw.get("language") not in LANGUAGES:
        errors.append(f"{label}: language must be one of {LANGUAGES}")
    question = raw.get("question")
    if not isinstance(question, str) or not question.strip():
        errors.append(f"{label}: question must be a non-empty string")
    qtype = raw.get("type")
    if qtype not in TYPES:
        errors.append(f"{label}: type must be one of {TYPES}")
    expected = raw.get("expected_article_ids", [])
    if not isinstance(expected, list) or not all(
        isinstance(a, int) and not isinstance(a, bool) for a in expected
    ):
        errors.append(f"{label}: expected_article_ids must be a list of integers")
        expected = []
    if len(set(expected)) != len(expected):
        errors.append(f"{label}: expected_article_ids has duplicates")
    if qtype == "unanswerable" and expected:
        errors.append(f"{label}: an unanswerable question has no expected articles")
    if qtype in ("factual", "cross-lingual", "reader") and not expected:
        errors.append(f"{label}: {qtype} needs at least one expected article")
    if qtype == "multi-article" and len(set(expected)) < 2:
        errors.append(f"{label}: multi-article needs at least two expected articles")
    langs = raw.get("search_languages")
    if langs is not None and (
        not isinstance(langs, list) or not langs or any(lang not in LANGUAGES for lang in langs)
    ):
        errors.append(f"{label}: search_languages must be a non-empty list of {LANGUAGES}")
    facts = raw.get("key_facts")
    if facts is not None and (
        not isinstance(facts, list) or not all(isinstance(f, str) and f.strip() for f in facts)
    ):
        errors.append(f"{label}: key_facts must be a list of non-empty strings")
    if qtype == "reader" and not facts:
        errors.append(f"{label}: a reader question needs key_facts")
    for key in ("expected_section", "reference_answer", "subtype", "source", "notes"):
        if raw.get(key) is not None and not isinstance(raw.get(key), str):
            errors.append(f"{label}: {key} must be a string")
    if errors:
        return None, errors
    values = {k: raw.get(k) for k in _QUESTION_FIELDS if raw.get(k) is not None}
    values["question"] = values["question"].strip()
    values["expected_article_ids"] = list(expected)
    if facts:
        values["key_facts"] = [f.strip() for f in facts]
    return GoldenQuestion(**values), []


def normalize_fact_text(text: str) -> str:
    """Lowercase, accents stripped, digit-group separators removed, spaces collapsed.

    The separators are removed before NFKD, which folds a no-break space into
    a plain one: a plain space between numbers ("1513 500") is not a
    thousands separator (#708).
    """
    plain = _DIGIT_GROUP.sub("", text)
    decomposed = unicodedata.normalize("NFKD", plain)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()
    return " ".join(plain.split())


def _fact_pattern(fact: str) -> re.Pattern:
    """``fact`` (normalized) bounded on word / number edges (#708).

    A digit edge only forbids another digit there, so "25" is not found in
    "1525" or "25000" but is in "25th" (and "1500" in "1500s", "1" in "1º").
    A letter edge forbids any letter or digit, so "Peru" is not in "Peruvian".
    Any other edge (punctuation) is unconstrained.
    """

    def edge(ch: str, look: str) -> str:
        if ch.isdigit():
            return rf"(?{look}\d)"
        if ch.isalnum():
            return rf"(?{look}[^\W_])"
        return ""

    if not fact:
        return re.compile(r"(?!)")
    return re.compile(edge(fact[0], "<!") + re.escape(fact) + edge(fact[-1], "!"))


def key_facts_found(text: str, key_facts: list[str]) -> list[str]:
    """The ``key_facts`` that occur in ``text`` as whole words / numbers
    (case-, accent- and thousands-separator-insensitive)."""
    haystack = normalize_fact_text(text)
    return [f for f in key_facts if _fact_pattern(normalize_fact_text(f)).search(haystack)]


def validate_questions(raw_questions: object) -> list[GoldenQuestion]:
    """Validate a list of question objects; raise :class:`GoldenError` listing all problems."""
    if not isinstance(raw_questions, list) or not raw_questions:
        raise GoldenError(["questions must be a non-empty list"])
    questions: list[GoldenQuestion] = []
    errors: list[str] = []
    for i, raw in enumerate(raw_questions):
        question, problems = _validate_one(raw, f"questions[{i}]")
        errors += problems
        if question:
            questions.append(question)
    dupes = [qid for qid, n in Counter(q.id for q in questions).items() if n > 1]
    if dupes:
        errors.append(f"duplicate ids: {sorted(dupes)}")
    if errors:
        raise GoldenError(errors)
    return questions


def parse_golden(data: object) -> GoldenSet:
    """Validate a parsed golden document."""
    if not isinstance(data, dict):
        raise GoldenError(["golden file must be a JSON object"])
    if data.get("version") != GOLDEN_VERSION:
        raise GoldenError([f"unsupported golden version {data.get('version')!r}"])
    return GoldenSet(
        questions=validate_questions(data.get("questions")),
        description=data.get("description"),
        curation=data.get("curation"),
    )


def load_golden(path: str | Path = DEFAULT_GOLDEN_PATH) -> GoldenSet:
    """Read and validate a golden file."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GoldenError([f"{path}: invalid JSON ({exc})"]) from None
    return parse_golden(data)


@dataclass
class CurationReport:
    drafted: int
    kept: int
    edited: int
    dropped: int
    by_type: dict[str, int]
    by_language: dict[str, int]

    def as_dict(self) -> dict:
        return asdict(self)


def drafts_to_golden(
    drafts_doc: dict, *, drafts_file: str | None = None, description: str | None = None
) -> tuple[GoldenSet, CurationReport]:
    """Turn a curated draft document into a golden set plus the curation counts.

    Every draft must have ``status`` ``keep`` / ``edit`` / ``drop``. ``keep``
    and ``edit`` drafts become golden questions (with the curator's edits, as
    they now stand in the file); ``drop`` ones are left out. Raises
    :class:`GoldenError` on an undecided draft or an invalid kept question.
    """
    drafts = drafts_doc.get("drafts") if isinstance(drafts_doc, dict) else None
    if not isinstance(drafts, list) or not drafts:
        raise GoldenError(["drafts file has no drafts"])
    undecided = [
        str(d.get("id", f"#{i}") if isinstance(d, dict) else f"#{i}")
        for i, d in enumerate(drafts)
        if not isinstance(d, dict) or d.get("status") not in STATUSES
    ]
    if undecided:
        raise GoldenError(
            [f"{len(undecided)} draft(s) without status keep/edit/drop: {', '.join(undecided)}"]
        )
    counts = Counter(d["status"] for d in drafts)
    # A #627 draft's ``source`` is its sampled chunk (a dict); only a string
    # label (``operator`` / ``opus``) is a golden field.
    chosen = [
        {k: d.get(k) for k in _QUESTION_FIELDS if k != "source" or isinstance(d.get(k), str)}
        for d in drafts
        if d["status"] != "drop"
    ]
    questions = validate_questions(chosen)
    report = CurationReport(
        drafted=len(drafts),
        kept=counts["keep"],
        edited=counts["edit"],
        dropped=counts["drop"],
        by_type=dict(Counter(q.type for q in questions)),
        by_language=dict(Counter(q.language for q in questions)),
    )
    curation = {"drafts_file": drafts_file, **report.as_dict()}
    if drafts_doc.get("generated_at"):
        curation["drafts_generated_at"] = drafts_doc["generated_at"]
    golden = GoldenSet(questions=questions, description=description, curation=curation)
    return golden, report
