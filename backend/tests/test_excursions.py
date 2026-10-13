"""Unit tests for the canonical excursion taxonomy (app/core/excursions.py, #319)."""

from app.core.excursions import (
    EXCURSIONS,
    edu_keys_for_public,
    focus_for,
    get_excursion,
    owed_combos,
    public_key_for,
)


def test_seven_excursions_with_unique_keys():
    keys = [e.key for e in EXCURSIONS]
    assert len(keys) == 7
    assert len(keys) == len(set(keys))


def test_owed_combos_total_is_15():
    combos = owed_combos()
    assert len(combos) == 15
    assert len(combos) == len(set(combos))  # no duplicate (excursion, band) pairs


def test_every_excursion_has_grade_bands():
    for e in EXCURSIONS:
        assert e.grade_bands, f"{e.key} has no grade bands"


def test_public_key_resolution_is_explicit_not_mechanical():
    # hyphen->underscore is NOT a simple transform — verify the real mapping
    assert public_key_for("panama-viejo") == "panama_viejo"
    assert public_key_for("casco-antiguo-plazas") == "casco_plazas"
    assert public_key_for("casco-antiguo-museos") == "casco_museums"
    assert public_key_for("unknown-key") is None


def test_religious_is_two_excursions_under_one_public_page():
    keys = edu_keys_for_public("religious")
    assert set(keys) == {"inter-religiosa-abrahamica", "inter-religiosa-oriental"}
    # both 9th-12th only
    for k in keys:
        assert get_excursion(k).grade_bands == ("9th-12th",)


def test_grade_band_ids_use_canonical_format_not_frontend_labels():
    bands = {b for e in EXCURSIONS for b in e.grade_bands}
    assert "3rd-6th" in bands
    assert "4th-6th" in bands
    # frontend display label ("3rd - 6th Grade") must NOT leak into canonical ids
    assert all(" Grade" not in b for b in bands)


# --- focus_for / band_focus (#339) ---


def test_focus_for_returns_band_specific_focus():
    # Different bands of the same excursion get different lenses.
    f_7_8 = focus_for("panama-viejo", "7th-8th")
    f_9_12 = focus_for("panama-viejo", "9th-12th")
    assert f_7_8 and "trade networks and piracy" in f_7_8
    assert f_9_12 and "Pizarro" in f_9_12
    assert f_7_8 != f_9_12


def test_focus_for_returns_none_for_unknown_or_missing():
    assert focus_for("panama-viejo", "no-such-band") is None
    assert focus_for("unknown-excursion", "7th-8th") is None
    # No excursion (standalone material) → no focus to inject.
    assert focus_for(None, "7th-8th") is None


def test_every_owed_combo_has_a_focus():
    # Guards against adding an excursion/band without a matching focus string.
    for key, band in owed_combos():
        focus = focus_for(key, band)
        assert isinstance(focus, str) and focus.strip(), f"{key}/{band} has no focus"


def test_every_excursion_has_bilingual_display_names():
    # The display name is the study-guide title for excursion-linked materials —
    # a missing name silently falls back to LLM invention (#2 of the redesign).
    for e in EXCURSIONS:
        assert e.display_name, f"{e.key} missing display_name"
        assert e.display_name_es, f"{e.key} missing display_name_es"


def test_religious_display_names_are_disambiguated():
    # Both religious tracks share one public page title; their guides must still
    # be distinguishable from each other.
    abrahamic = get_excursion("inter-religiosa-abrahamica")
    oriental = get_excursion("inter-religiosa-oriental")
    assert abrahamic.display_name != oriental.display_name
    assert abrahamic.display_name_es != oriental.display_name_es
