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
- **Museum-circuit deliverables are per museum, not per combination** (#523).
  Teachers pick which museums to visit, so ``museo-del-canal`` (Museo del Canal
  Interoceánico) is its own excursion owing its own video + study guide per
  band, grouped under the same public page ``casco_museums`` as the circuit
  key — the same many-keys-one-page shape as ``religious``. The circuit key
  ``casco-antiguo-museos`` stays (its research is approved and fact-checked);
  later museums follow the same pattern — ``museo-de-historia`` (Museo de
  Historia de Panamá) and ``museo-de-la-mola`` (MUMO) are the next two.
- Each (excursion, band) carries a **grade-band focus** (#339): the analytical lens
  for that band, fed into the study-guide condense step so each band emphasizes a
  different slice of the research instead of differing only by voice/length.

Total owed = 24 (excursion × grade-band) deliverable pairs (video + study guide).
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
        "4th-8th": (
            "Educational materials incorporate science, history, and global economics "
            "in age-appropriate formats."
        ),
    },
    # Per-museum lens (#523): written for the museum's actual route (ground
    # floor isthmus → 1879, upper floor the Zone, sovereignty and the ACP era),
    # not lifted from the circuit page's generic adaptations. The site copy is
    # aligned to these strings in #524 — keep the two identical.
    "museo-del-canal": {
        "3rd-6th": (
            "Follow the route across the isthmus through concrete objects and "
            "stories: the land bridge and its animals, mules and treasure on the "
            "Camino Real, the Gold Rush railroad, how a lock lifts a ship, and the "
            "story of Panama's flag."
        ),
        "7th-8th": (
            "Trace cause and effect across the eras of the route: why the French "
            "failed and the U.S. succeeded, what life in the Canal Zone (Gold and "
            "Silver Roll) meant for Panamanians, and how the road from the flag to "
            "9 January 1964 led to the 1977 treaties and the 1999 handover."
        ),
        "9th-12th": (
            "Read the museum critically: analyze the 1903 and 1977 treaties as "
            "primary sources, weigh contested figures and the museum's "
            "sovereignty-centered narrative, and evaluate the canal's present-day "
            "challenges of water and neutrality."
        ),
    },
    # The next two museums on the circuit, same per-museum lens. The public
    # page still lists them inside the circuit visit with the circuit's
    # generic adaptations; when they get their own visit block, copy these
    # strings into the site copy verbatim (as #524 did for the canal).
    "museo-de-historia": {
        "3rd-6th": (
            "Meet Panama's story through the building and its symbols: the "
            "council hall where independence was declared in 1821 and 1903, the "
            "colonial city and its treasure route, the first flag sewn by María "
            "Ossa de Amador, and the national symbols of the Republic."
        ),
        "7th-8th": (
            "Trace cause and effect from colony to republic: why Panama broke "
            "from Spain and joined Gran Colombia in 1821, why separation attempts "
            "recurred through the nineteenth century, and how the Thousand Days' "
            "War and the canal question led to 3 November 1903."
        ),
        "9th-12th": (
            "Read the museum critically: analyze the 1821 and 1903 acts of "
            "independence as primary sources, weigh the Panamanian, Colombian and "
            "U.S. interpretations of 1903, examine figures such as Victoriano "
            "Lorenzo, and ask what a national history museum chooses to celebrate."
        ),
    },
    "museo-de-la-mola": {
        "3rd-6th": (
            "Discover how a mola is made and what it shows: the layers and "
            "stitches of reverse appliqué, the animals, plants and stories in the "
            "designs, and who the Guna people are and where they live."
        ),
        "7th-8th": (
            "Connect the mola to Guna history and identity: how the art grew out "
            "of body painting, why wearing the mola became an act of resistance in "
            "the 1925 Guna Revolution, and how Guna Yala governs itself today."
        ),
        "9th-12th": (
            "Examine the mola as living heritage: analyze Guna cosmovision and "
            "symbolism, the comarca system as a model of Indigenous autonomy, and "
            "debates over commercialization, cultural appropriation and who has "
            "the authority to present a culture in a museum."
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
        "museo-del-canal",
        "casco_museums",
        ("3rd-6th", "7th-8th", "9th-12th"),
        _BAND_FOCUS["museo-del-canal"],
        display_name="Interoceanic Canal Museum",
        display_name_es="Museo del Canal Interoceánico",
    ),
    Excursion(
        "museo-de-historia",
        "casco_museums",
        ("3rd-6th", "7th-8th", "9th-12th"),
        _BAND_FOCUS["museo-de-historia"],
        display_name="Panama History Museum",
        display_name_es="Museo de Historia de Panamá",
    ),
    Excursion(
        "museo-de-la-mola",
        "casco_museums",
        ("3rd-6th", "7th-8th", "9th-12th"),
        _BAND_FOCUS["museo-de-la-mola"],
        display_name="Mola Museum",
        display_name_es="Museo de la Mola",
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
        ("4th-8th",),
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
    """Every (excursion_key, grade_band) deliverable pair we owe (24 total)."""
    return [(e.key, band) for e in EXCURSIONS for band in e.grade_bands]
