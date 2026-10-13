"""Canonical academic-excursion taxonomy (issue #319).

Single source of truth reconciling the public site's excursion pages (frontend
i18n keys, underscored) with the admin pipeline's ``edu_suggestions.excursion_key``
(hyphenated) and the canonical grade-band ids the generation prompts already use
(e.g. ``"3rd-6th"`` — NOT the frontend display label ``"3rd - 6th Grade"``). The
public delivery surface (#326) and the coverage report build on this.

Key facts this module pins down:

- The frontend↔DB key map is **not** a mechanical transform
  (``casco-antiguo-plazas`` → ``casco_plazas``, not ``casco_antiguo_plazas``), so
  it is spelled out explicitly here.
- **Religious is two excursions** — ``inter-religiosa-abrahamica`` (Abrahamic) and
  ``inter-religiosa-oriental`` (Eastern) — that share the single public page
  ``religious``. Each owes its own video + study guide (operator decision,
  2026-06-20). So the public ``religious`` page surfaces two deliverable sets.
- Each (excursion, band) carries a **grade-band focus** (#339): the analytical lens
  for that band, fed into the study-guide condense step so each band emphasizes a
  different slice of the research instead of differing only by voice/length.

Total owed = 15 (excursion × grade-band) deliverable pairs (video + study guide).
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Excursion:
    """One canonical excursion (the unit that owes deliverables per grade band)."""

    key: str  # edu_suggestions.excursion_key — canonical pipeline id (hyphenated)
    public_key: str  # frontend route / i18n key (underscored); many keys → one page
    grade_bands: tuple[str, ...]  # canonical band ids, e.g. "3rd-6th"
    # Per-band analytical lens (band id → focus text), fed into the study-guide
    # condense so each band emphasizes a different slice of the research (#339).
    # Excluded from eq/hash (keeps Excursion hashable despite the dict field).
    band_focus: dict[str, str] = field(default_factory=dict, compare=False)
    # Human-facing excursion name, lifted from the public pages' i18n titles
    # (``excursions.<public_key>.title``). Used verbatim as the study-guide title
    # for excursion-linked materials — never LLM-invented. The two religious
    # tracks share one public page title, so their names carry an Abrahamic/
    # Eastern suffix to keep the two guides distinguishable.
    display_name: str = ""
    display_name_es: str = ""


# Per-band grade-level focus, lifted verbatim from the public excursion pages'
# "adaptations" (frontend i18n: ``excursions.<public_key>.details.adaptations``).
# Drives per-band condensation (#339).
#
# KNOWN-WEAK entries (flagged for possible sharpening, kept verbatim to match the
# public site): the two religious tracks share one generic 9-12 string, and
# miraflores's reads as a description rather than a sharp lens.
_RELIGIOUS_9_12 = (
    "Analyze similarities and differences in belief systems, examine how immigrant "
    "religious communities adapted, and evaluate how different religious perspectives "
    "address modern challenges."
)

_BAND_FOCUS: dict[str, dict[str, str]] = {
    "casco-antiguo-plazas": {
        "3rd-6th": (
            "Focus on architectural features, basic historical sequence, and Panama's "
            "strategic importance."
        ),
        "7th-8th": (
            "Examine the independence movements, Bolivar's vision, and the Amphictyonic Congress."
        ),
        "9th-12th": (
            "Analyze the complex geopolitical forces behind Panama's 1903 independence "
            "and evaluate competing historical narratives from Panamanian, Colombian, "
            "and American sources."
        ),
    },
    "casco-antiguo-museos": {
        "3rd-6th": (
            "Engage with interactive displays on Panama's diverse cultural heritage and "
            "learn about Panama's independence."
        ),
        "7th-8th": (
            "Critically evaluate how Panama's strategic location shaped its historical development."
        ),
        "9th-12th": (
            "Explore how Panama's unique position created complex political and economic "
            "relationships with global powers, particularly Spain, Colombia, and the "
            "United States."
        ),
    },
    "panama-viejo": {
        "3rd-6th": "Focus on basic archaeological concepts and colonial life.",
        "7th-8th": ("Examine trade networks and piracy's impact on settlement patterns."),
        "9th-12th": (
            "Explore how Panama Viejo functioned as a staging area for Francisco "
            "Pizarro's conquest of the Inca Empire, analyzing the expedition's logistics "
            "and ethical questions surrounding colonization."
        ),
    },
    "biomuseo": {
        "3rd-6th": "Focus on observable biodiversity and basic ecological concepts.",
        "7th-8th": ("Explore species adaptation, geology, and complex biological systems."),
        "9th-12th": (
            "Examine current research on climate change impacts on Panama's "
            "biodiversity. Analyze data showing how the isthmus creation altered global "
            "ocean currents and climate patterns."
        ),
    },
    "miraflores": {
        "4th-6th": (
            "Educational materials incorporate science, history, and global economics "
            "in age-appropriate formats."
        ),
    },
    "inter-religiosa-abrahamica": {"9th-12th": _RELIGIOUS_9_12},
    "inter-religiosa-oriental": {"9th-12th": _RELIGIOUS_9_12},
}


EXCURSIONS: tuple[Excursion, ...] = (
    Excursion(
        "casco-antiguo-plazas",
        "casco_plazas",
        ("3rd-6th", "7th-8th", "9th-12th"),
        _BAND_FOCUS["casco-antiguo-plazas"],
        display_name="Casco Antiguo: Plazas and Churches",
        display_name_es="Casco Antiguo: Plazas e iglesias",
    ),
    Excursion(
        "casco-antiguo-museos",
        "casco_museums",
        ("3rd-6th", "7th-8th", "9th-12th"),
        _BAND_FOCUS["casco-antiguo-museos"],
        display_name="Casco Antiguo Museum Circuit",
        display_name_es="Casco Antiguo: Circuito de Museos",
    ),
    Excursion(
        "panama-viejo",
        "panama_viejo",
        ("3rd-6th", "7th-8th", "9th-12th"),
        _BAND_FOCUS["panama-viejo"],
        display_name="Panama Viejo Archeological Complex",
        display_name_es="Complejo Arqueológico de Panamá Viejo",
    ),
    Excursion(
        "biomuseo",
        "biomuseo",
        ("3rd-6th", "7th-8th", "9th-12th"),
        _BAND_FOCUS["biomuseo"],
        display_name="Museum of Biodiversity",
        display_name_es="Museo de la Biodiversidad",
    ),
    Excursion(
        "miraflores",
        "miraflores",
        ("4th-6th",),
        _BAND_FOCUS["miraflores"],
        display_name="Miraflores Visitor Center and IMAX",
        display_name_es="Centro de Visitantes de Miraflores e IMAX",
    ),
    Excursion(
        "inter-religiosa-abrahamica",
        "religious",
        ("9th-12th",),
        _BAND_FOCUS["inter-religiosa-abrahamica"],
        display_name="Panama's Inter-Religious Journey: Abrahamic Traditions",
        display_name_es="La jornada interreligiosa de Panamá: tradiciones abrahámicas",
    ),
    Excursion(
        "inter-religiosa-oriental",
        "religious",
        ("9th-12th",),
        _BAND_FOCUS["inter-religiosa-oriental"],
        display_name="Panama's Inter-Religious Journey: Eastern Traditions",
        display_name_es="La jornada interreligiosa de Panamá: tradiciones orientales",
    ),
)

_BY_KEY: dict[str, Excursion] = {e.key: e for e in EXCURSIONS}


def get_excursion(excursion_key: str) -> Excursion | None:
    """The canonical excursion for an ``edu_suggestions.excursion_key``, or None."""
    return _BY_KEY.get(excursion_key)


def focus_for(excursion_key: str | None, band: str) -> str | None:
    """The per-band analytical lens for an excursion + band, or ``None``.

    ``None`` when there is no excursion (standalone material), the excursion key is
    unknown, or the band has no defined focus — callers inject nothing in that case,
    so the condense/outline/content prompts simply omit the focus block (#339).
    """
    exc = _BY_KEY.get(excursion_key) if excursion_key else None
    return exc.band_focus.get(band) if exc else None


def public_key_for(excursion_key: str) -> str | None:
    """The public (frontend) page key a generated material belongs to.

    A material's excursion key is reached via
    ``material.research.suggestion.excursion_key`` — pass that here to resolve the
    public excursion page it should be delivered on.
    """
    exc = _BY_KEY.get(excursion_key)
    return exc.public_key if exc else None


def edu_keys_for_public(public_key: str) -> list[str]:
    """The excursion key(s) grouped under a public page (``religious`` → 2)."""
    return [e.key for e in EXCURSIONS if e.public_key == public_key]


def owed_combos() -> list[tuple[str, str]]:
    """Every (excursion_key, grade_band) deliverable pair we owe (15 total)."""
    return [(e.key, band) for e in EXCURSIONS for band in e.grade_bands]
