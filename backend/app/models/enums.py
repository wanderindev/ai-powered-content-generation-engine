"""Centralized enums for status / type / controlled-vocabulary columns.

Single source of truth for the discrete value sets used by model columns. See
CLAUDE.md -> "Status / type column convention" for how these map to the database.

Two storage patterns reference these enums:

* **PG ENUM types** (``content_status_type``, ``media_type_type``,
  ``media_source_type``) — stable vocabularies created in the baseline schema.
  The column stays ``SAEnum(*Enum.values(), name=..., create_type=False)``.
* **``String(N)`` + named ``CHECK`` constraint** — every other status/type
  column. Build the constraint with :func:`status_check` so the allowed values
  always track the enum.

Adding a new status/type column? Define its enum here and use one of the two
patterns above — never a bare ``String`` and never a new PG ENUM type.
"""

from __future__ import annotations

import enum

from sqlalchemy import CheckConstraint


class StrEnum(enum.StrEnum):
    """Base ``str`` enum exposing the allowed values as a tuple."""

    @classmethod
    def values(cls) -> tuple[str, ...]:
        """Return every member's value, e.g. ``("PENDING", "APPROVED", ...)``."""
        return tuple(member.value for member in cls)


# --- Content pipeline (PG enum: content_status_type) ------------------------
class ContentStatus(StrEnum):
    """Approval state shared by articles, tags, research, suggestions, edu_*."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


# --- Media (PG enums: media_type_type / media_source_type) ------------------
class MediaType(StrEnum):
    """Mirrors the ``media_type_type`` PG enum (DB is the source of truth)."""

    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    DOCUMENT = "DOCUMENT"
    PDF = "PDF"
    SPREADSHEET = "SPREADSHEET"
    OTHER = "OTHER"


class MediaSource(StrEnum):
    """Mirrors the ``media_source_type`` PG enum."""

    LOCAL = "LOCAL"
    YOUTUBE = "YOUTUBE"
    S3 = "S3"
    WIKIMEDIA = "WIKIMEDIA"
    GENERATED = "GENERATED"
    LOC = "LOC"


# --- Media candidates (String + CHECK) --------------------------------------
class CandidateStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class CandidateSource(StrEnum):
    WIKIMEDIA = "WIKIMEDIA"
    LOC = "LOC"
    SMITHSONIAN = "SMITHSONIAN"


# --- Media-acquisition crawl jobs (String + CHECK) --------------------------
class CrawlJobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class CrawlJobType(StrEnum):
    WIKIMEDIA = "WIKIMEDIA"
    LOC = "LOC"
    SCORE = "SCORE"
    # Fact-check runs (#384): research-sized runs exceed the proxy timeout, so
    # they execute off the request path via the same background-job table.
    FACT_CHECK = "FACT_CHECK"
    # Long edu pipeline steps (#346): a full deck's video assembly runs ~15-20
    # min and guide generation several minutes, past the api. vhost's 300s
    # proxy ceiling, so they run here too instead of as blocking requests.
    EDU_GENERATE_GUIDES = "EDU_GENERATE_GUIDES"
    EDU_RENDER_SLIDES = "EDU_RENDER_SLIDES"
    EDU_GENERATE_AUDIO = "EDU_GENERATE_AUDIO"
    EDU_ASSEMBLE_VIDEO = "EDU_ASSEMBLE_VIDEO"
    # Spanish article narration (#547): a large article's per-segment TTS plus
    # post-processing runs well past the 300s proxy ceiling.
    ARTICLE_NARRATION = "ARTICLE_NARRATION"


# --- Bookings / orders (String + CHECK) -------------------------------------
class BookingStatus(StrEnum):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    PAYMENT_LINK_SENT = "PAYMENT_LINK_SENT"
    PAID = "PAID"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class TimeSlot(StrEnum):
    AM = "AM"
    PM = "PM"


class TransportDirection(StrEnum):
    TO = "TO"
    FROM = "FROM"
    BOTH = "BOTH"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    PAYMENT_LINK_SENT = "PAYMENT_LINK_SENT"
    PAID = "PAID"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class PaymentMethod(StrEnum):
    PAYPAL = "PAYPAL"
    CUANTO = "CUANTO"  # Credit card via Cuanto App
    YAPPY = "YAPPY"  # Yappy by Banco General


# --- Auth (String + CHECK) --------------------------------------------------
class MagicLinkAction(StrEnum):
    VIEW_ORDER = "VIEW_ORDER"
    CANCEL_BOOKING = "CANCEL_BOOKING"
    RENEW_PAYMENT = "RENEW_PAYMENT"
    ADMIN_ACCESS = "ADMIN_ACCESS"


# --- Educators (String + CHECK) ---------------------------------------------
class EducatorStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    UNSUBSCRIBED = "UNSUBSCRIBED"


# NOTE: ``educators.source`` is intentionally NOT an enum. It records which UI
# CTA triggered signup (e.g. "home_cta", "media_library", "MailingListBlog") —
# an open provenance vocabulary that grows with new front-end components, so a
# closed CHECK constraint would be the wrong tool. Leave it a plain String.


# --- Educational materials (String + CHECK) ---------------------------------
class MaterialType(StrEnum):
    GUIDE = "guide"
    SLIDES = "slides"
    INFOGRAPHIC = "infographic"
    PODCAST = "podcast"
    VIDEO = "video"  # narrated slide MP4 (#323)


# --- Slide deck templates (String + CHECK; one per edu_slides row, #320) -----
# Mirrors the brand slide kit's templates
# (.claude/skills/panama-in-context-design/ui_kits/slides/). The renderer (#321)
# maps each value to the matching HTML template.
class SlideTemplate(StrEnum):
    TITLE = "title"
    SECTION = "section"
    BULLETS = "bullets"
    TWO_COL_IMAGE_LEFT = "two-col-image-left"
    TWO_COL_IMAGE_RIGHT = "two-col-image-right"
    FULL_IMAGE = "full-image"
    TIMELINE = "timeline"
    MAP = "map"
    CLOSING = "closing"


class GuideBlockType(StrEnum):
    """Block types for a structured study guide (#340).

    Stored as JSON on ``EduMaterial.content_blocks`` (not a column), so validated
    in code — not via a DB CHECK. Rendered to the branded PDF and projected to
    markdown (``content``/``content_es``) for the admin UI + search.
    """

    COVER = "cover"
    SECTION = "section"
    PARAGRAPH = "paragraph"
    BULLETS = "bullets"
    ORDERED_LIST = "ordered_list"
    QUOTE = "quote"
    MAP = "map"
    TIMELINE = "timeline"
    IMAGE = "image"
    CALLOUT = "callout"  # aside box (.g-callout): Key Terms, Did-you-know, reflection prompts
    MC_QUESTION = "mc_question"  # one multiple-choice review question (post-visit ladder)


# --- Fact-check pipeline (String + CHECK; epic #373) ------------------------
class FactCheckSubject(StrEnum):
    """Which content-pipeline stage a checked claim was extracted from.

    The blog pipeline owns the first three; ``EDU_RESEARCH`` (#460) and
    ``EDU_MATERIAL`` (#461) extend the same machinery to the
    educational-materials pipeline. Adding a value here needs a matching
    drop-and-recreate of ``ck_fact_check_claims_subject_type`` in a migration —
    the column is ``String`` + named CHECK, not a PG enum.

    ``EDU_MATERIAL`` is one value for every ``material_type``: the subject is
    the ``EduMaterial`` row, and the text checked depends on its type (guides
    read ``content_blocks``; the deck reads its ``edu_slides`` rows, #464).
    """

    SUGGESTION = "SUGGESTION"
    RESEARCH = "RESEARCH"
    ARTICLE = "ARTICLE"
    EDU_RESEARCH = "EDU_RESEARCH"
    EDU_MATERIAL = "EDU_MATERIAL"


class FactClaimType(StrEnum):
    """Kind of atomic factual claim extracted by Haiku (#374)."""

    EVENT = "EVENT"  # a historical event occurred
    PERSON = "PERSON"  # a person's role / association with an event
    DATE = "DATE"  # a specific date or year
    PLACE = "PLACE"  # a location fact
    NUMBER = "NUMBER"  # a quantity / measurement / count
    OTHER = "OTHER"  # parser fallback for anything off-vocabulary


class FactCheckVerdict(StrEnum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    INACCURATE = "INACCURATE"
    UNVERIFIABLE = "UNVERIFIABLE"
    DISPUTED = "DISPUTED"
    # The automated tiers could not resolve the claim; it belongs in the next
    # /fact-check-prompt deep-research batch (#377).
    NEEDS_DEEP_RESEARCH = "NEEDS_DEEP_RESEARCH"
    # An INACCURATE claim whose correction was applied to the content (#387).
    # Terminal green state: the claim is still wrong in the world (the KB row
    # stays INACCURATE) but the content no longer asserts it.
    CORRECTED = "CORRECTED"


class FactCheckMethod(StrEnum):
    """How a verdict was reached (``checked_via``)."""

    GROUNDING = "GROUNDING"  # judged against the approved research doc (#375)
    # Historical: the Sonnet + web-search tier (#376) was removed in #751 after
    # one run. Its ~193 claim rows and ~192 KB rows stay valid (CHECK + KB_MATCH),
    # so the value stays; nothing writes it any more.
    WEB_SEARCH = "WEB_SEARCH"
    DEEP_RESEARCH = "DEEP_RESEARCH"  # manual claude.ai deep-research loop (#377)
    KB_MATCH = "KB_MATCH"  # matched an already-verified claim (#378)


# --- Instagram posts (String + CHECK; social pipeline) ----------------------
class InstagramPostType(StrEnum):
    """Kind of Instagram post produced by the social pipeline."""

    SABIAS_QUE = "SABIAS_QUE"  # "¿Sabías que…?" historical fact card
    ARTICLE_PROMO = "ARTICLE_PROMO"  # "Nuevo en el blog" series/article announcement


class InstagramTemplateVariant(StrEnum):
    """Visual treatment of a sabías-que card (ui_kits/social); NULL for promos."""

    AZUL = "AZUL"  # deep-navy textured card (post-sabias-que-azul)
    CLARO = "CLARO"  # warm-gray card, sepia polaroid (post-sabias-que-claro)


# --- Contact form (String + CHECK) ------------------------------------------
class ContactInterest(StrEnum):
    FIELD_TRIPS = "field-trips"
    TOURS = "tours"


# --- Language (String + CHECK; shared by orders + educators) ----------------
class Language(StrEnum):
    EN = "en"
    ES = "es"


# --- RAG knowledge layer (String + CHECK; knowledge_chunks.source_type) ------
class KnowledgeSourceType(StrEnum):
    """What a ``knowledge_chunks`` row was cut from (#623, epic #617).

    ``ARTICLE`` rows are sections of an article body (``source_id`` = the
    article id); ``ARTICLE_CLAIM`` rows are single fact-check claims of an
    article (``source_id`` = the ``fact_check_claims`` id); ``VERIFIED_CLAIM``
    rows are verified-claims KB entries (``source_id`` = the
    ``verified_claims`` id, no ``article_id``).
    """

    ARTICLE = "ARTICLE"
    ARTICLE_CLAIM = "ARTICLE_CLAIM"
    VERIFIED_CLAIM = "VERIFIED_CLAIM"


# --- Ask Panama in Context (String + CHECK; ask_pic_queries.status) ---------
class AskPicQueryStatus(StrEnum):
    """Outcome of one public Ask PIC request (#634, epic #619).

    ``NULL`` in the column means the query was admitted and is still in flight
    (its ``cost_usd`` then holds the worst-case reservation of its LLM call).
    ``RATE_LIMITED`` / ``BUDGET_EXCEEDED`` rows are refusals by the controls
    themselves and never count toward the per-IP / global limits.
    """

    ANSWERED = "ANSWERED"
    NOT_COVERED = "NOT_COVERED"
    REFUSED = "REFUSED"
    RATE_LIMITED = "RATE_LIMITED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    INVALID = "INVALID"
    ERROR = "ERROR"


# --- Cross-article consistency (String + CHECK; consistency_findings) -------
class ConsistencyVerdict(StrEnum):
    """The judge's label for one (claim, other-article fact) pair (#631).

    Only ``CONTRADICTS`` is stored today (shadow mode); the per-label counts
    are logged per article for #632. The column CHECK allows all three so
    recording ``CONSISTENT`` later is not a migration.
    """

    CONSISTENT = "CONSISTENT"
    CONTRADICTS = "CONTRADICTS"
    UNRELATED = "UNRELATED"


class ConsistencySource(StrEnum):
    """Where the other side of a consistency pair came from (#631).

    ``ARTICLE_CLAIM`` = another article's fact-check claim (``other_source_id``
    is its ``fact_check_claims`` id at check time); ``VERIFIED_CLAIM`` = a
    verified-claims KB row (``other_source_id`` = its ``verified_claims`` id).
    """

    ARTICLE_CLAIM = "ARTICLE_CLAIM"
    VERIFIED_CLAIM = "VERIFIED_CLAIM"


class ConsistencyResolution(StrEnum):
    """How an editor closed a consistency finding (#631)."""

    DISMISSED = "DISMISSED"
    RESOLVED = "RESOLVED"


# --- Grammar-check provenance (String + CHECK; grammar_check_fixes.source) ---
class GrammarFixSource(StrEnum):
    """Which proofreader produced a ``grammar_check_fixes`` row.

    ``LLM`` is the Sonnet corrections pass (#409); ``GRAMMARLY`` is the
    operator-driven Grammarly web-editor pass applied through
    ``POST /admin/dashboard/articles/{id}/grammar-apply`` by the PIC Companion
    extension. ``ES_ACCURACY`` is the Spanish-vs-English meaning check run after
    translation (#557, ``POST /admin/articles/{id}/es-accuracy-check``); its rows
    are always ``language = 'es'``.
    """

    LLM = "LLM"
    GRAMMARLY = "GRAMMARLY"
    ES_ACCURACY = "ES_ACCURACY"


# --- Weekly-series agent tasks (String + CHECK; epic #422) -------------------
class AgentTaskKind(StrEnum):
    """Which pipeline step a task row represents (the #417 weekly graph)."""

    RESEARCH_FACT_CHECK = "RESEARCH_FACT_CHECK"
    APPLY_RESEARCH_CORRECTIONS = "APPLY_RESEARCH_CORRECTIONS"
    GENERATE_OUTLINE = "GENERATE_OUTLINE"
    # Fan-out: article rows don't exist until the outline runs; #417 may emit
    # this task to expand the per-article graph after GENERATE_OUTLINE.
    PLAN_ARTICLES = "PLAN_ARTICLES"
    WRITE_ARTICLE = "WRITE_ARTICLE"
    ARTICLE_FACT_CHECK = "ARTICLE_FACT_CHECK"
    APPLY_ARTICLE_CORRECTIONS = "APPLY_ARTICLE_CORRECTIONS"
    GRAMMAR_CHECK_EN = "GRAMMAR_CHECK_EN"
    GENERATE_TAGS = "GENERATE_TAGS"
    TRANSLATE = "TRANSLATE"
    GRAMMAR_CHECK_ES = "GRAMMAR_CHECK_ES"
    GENERATE_FEATURE_IMAGE = "GENERATE_FEATURE_IMAGE"
    GENERATE_SERIES_SECTIONS = "GENERATE_SERIES_SECTIONS"
    APPROVE_ARTICLE = "APPROVE_ARTICLE"
    SET_PUBLISH_DATE = "SET_PUBLISH_DATE"
    GENERATE_SABIAS_QUE = "GENERATE_SABIAS_QUE"
    GENERATE_ARTICLE_PROMO = "GENERATE_ARTICLE_PROMO"
    APPROVE_IG_POST = "APPROVE_IG_POST"
    RENDER_IG_POST = "RENDER_IG_POST"
    SCHEDULE_IG_POST = "SCHEDULE_IG_POST"
    # Wait kind (#554): research is ready for the outline once the operator has
    # resolved its fact-check (no PENDING / NEEDS_DEEP_RESEARCH claims). Replaces
    # RESEARCH_FACT_CHECK + APPLY_RESEARCH_CORRECTIONS in new plans; those two
    # values stay for goals planned before it.
    WAIT_RESEARCH_READY = "WAIT_RESEARCH_READY"
    # Wait kind (#555): between ARTICLE_FACT_CHECK and APPLY_ARTICLE_CORRECTIONS,
    # held until the article has no PENDING / NEEDS_DEEP_RESEARCH claims (ABSENT
    # grounding verdicts now queue for deep research).
    WAIT_ARTICLE_CLAIMS_RESOLVED = "WAIT_ARTICLE_CLAIMS_RESOLVED"
    # Wait kinds (#556): the operator's Grammarly pass in the pic-extension,
    # held until ``articles.grammar_checked_en_at`` / ``_es_at`` is stamped.
    # Replace GRAMMAR_CHECK_EN / GRAMMAR_CHECK_ES (the LLM pass) in new plans;
    # those two values stay for goals planned before it.
    WAIT_GRAMMAR_CHECKED_EN = "WAIT_GRAMMAR_CHECKED_EN"
    WAIT_GRAMMAR_CHECKED_ES = "WAIT_GRAMMAR_CHECKED_ES"
    # Normal kind (#557): after TRANSLATE and before WAIT_GRAMMAR_CHECKED_ES,
    # a Sonnet pass that fixes Spanish that does not say what the (already
    # fact-checked) English says — DeepL mistakes. Not a wait.
    ES_ACCURACY_CHECK = "ES_ACCURACY_CHECK"


class AgentTaskStatus(StrEnum):
    """Lifecycle of one task row; the driver moves it through these states."""

    PENDING = "PENDING"  # dependencies not yet all DONE
    READY = "READY"  # claimable by a driver
    LEASED = "LEASED"  # claimed; returns to the pool when the lease expires
    # A wait kind's condition did not hold yet (operator work on the laptop);
    # claimable again once next_check_at passes (#553).
    WAITING = "WAITING"
    DONE = "DONE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class AgentTaskAction(StrEnum):
    """A human (or MCP agent) intervention on one task (#420, #641).

    Values are lowercase because they are the ``action`` verbs of
    ``POST .../agent-tasks/{id}/action``; ``agent_task_actions.action`` stores
    them verbatim.
    """

    RETRY = "retry"
    SKIP = "skip"


class AgentTaskSubject(StrEnum):
    """What entity a task acts on (polymorphic, mirrors FactCheckSubject)."""

    RESEARCH = "RESEARCH"
    ARTICLE = "ARTICLE"
    INSTAGRAM_POST = "INSTAGRAM_POST"


class McpScope(StrEnum):
    """Scopes an ``mcp_tokens`` row can grant (#638, epic #620).

    Enforcement is the per-tool ``require_scope`` check in ``app.mcp.auth``;
    tool annotations are only hints. No scope implies another.
    """

    READ = "read"  # knowledge search, articles, claims, pipeline status
    PIPELINE = "pipeline"  # research intake, deep-research loop, task retry/skip
    PUBLISH_REQUEST = "publish_request"  # *request* publish/approve; never executes


class McpCallOutcome(StrEnum):
    """Outcome of one MCP tool call in the ``mcp_tool_calls`` audit log (#639)."""

    OK = "OK"
    ERROR = "ERROR"  # the tool raised (a ToolError, a crash, or CancelledError: client gone)
    DENIED = "DENIED"  # the token lacks the tool's scope; the tool body never ran


class McpApprovalAction(StrEnum):
    """What an MCP ``publish_request`` tool asks an admin to do (#642).

    Stored in ``mcp_approval_requests.action``; executed only when an admin
    approves the request, through ``app.services.article_publishing``.
    """

    PUBLISH = "PUBLISH"  # article_publishing.publish_article (all preconditions)
    UNPUBLISH = "UNPUBLISH"  # article_publishing.unpublish_article
    APPROVE_ARTICLE = "APPROVE_ARTICLE"  # article_publishing.approve_article


class McpApprovalStatus(StrEnum):
    """Lifecycle of an ``mcp_approval_requests`` row (#642).

    An admin's approve executes the action in the same transaction, so an
    approved request is stored as ``EXECUTED`` or ``FAILED`` (with the
    reason in ``result``), never as a separate "approved, not yet run" state.
    """

    PENDING = "PENDING"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


def status_check(table: str, column: str, *enum_classes: type[StrEnum]) -> CheckConstraint:
    """Build a named ``CHECK`` constraint limiting *column* to the enums' values.

    The constraint name is stable (``ck_<table>_<column>``) so adding a value is a
    clean drop-and-recreate migration. Declared in model ``__table_args__``; the
    Alembic migration mirrors the same name + value list onto production.

    ``CHECK`` passes ``NULL`` automatically, so nullable columns stay nullable.

    Pass more than one enum for a polymorphic column whose value may come from
    several vocabularies (e.g. the booking status log records both booking and
    order transitions); the allowed set is their de-duplicated union.
    """
    allowed_values: list[str] = []
    for enum_cls in enum_classes:
        for value in enum_cls.values():
            if value not in allowed_values:
                allowed_values.append(value)
    allowed = ", ".join(f"'{value}'" for value in allowed_values)
    return CheckConstraint(f"{column} IN ({allowed})", name=f"ck_{table}_{column}")


def array_subset_check(table: str, column: str, *enum_classes: type[StrEnum]) -> CheckConstraint:
    """Named ``CHECK`` limiting every element of an ARRAY *column* to the enums' values.

    The array counterpart of :func:`status_check` (same ``ck_<table>_<column>``
    name, so a new value is the same drop-and-recreate migration). ``<@`` is
    "is contained by": an empty array passes, and so does ``NULL``.
    """
    allowed_values: list[str] = []
    for enum_cls in enum_classes:
        for value in enum_cls.values():
            if value not in allowed_values:
                allowed_values.append(value)
    allowed = ", ".join(f"'{value}'" for value in allowed_values)
    return CheckConstraint(f"{column} <@ ARRAY[{allowed}]::varchar[]", name=f"ck_{table}_{column}")
