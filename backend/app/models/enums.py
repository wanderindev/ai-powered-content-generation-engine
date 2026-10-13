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
    """Which content-pipeline stage a checked claim was extracted from."""

    SUGGESTION = "SUGGESTION"
    RESEARCH = "RESEARCH"
    ARTICLE = "ARTICLE"


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
    WEB_SEARCH = "WEB_SEARCH"  # Sonnet + web-search tool (#376)
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
