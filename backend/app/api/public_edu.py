"""Public educational-deliverables API (#326/#327).

Teacher-facing, read-only, **no login** (self-serve per the epic's acceptance).

The operator publishes the two things they curate per excursion × grade band:
the **slides deck** (the "presentation") and the **study guide**. This surfaces
only APPROVED + published (published_at set and in the past) ``slides`` and
``guide`` materials, grouped by the canonical excursion × grade-band taxonomy
(``app.core.excursions``):

- a published **slides** deck plays its assembled sibling **video** material
  (the rendered MP4), with the first rendered slide frame as the poster;
- a published **guide** offers its PDF, with the guide's cover image as a
  thumbnail.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.database import get_db
from app.core.excursions import EXCURSIONS, get_excursion
from app.models.edu_material import EduMaterial
from app.models.edu_research import EduResearch
from app.schemas.public_edu import (
    PublicDeliverableBand,
    PublicDeliverableExcursion,
    PublicDeliverablesResponse,
    PublicDeliverableVideo,
)

router = APIRouter(
    prefix="/public-edu",
    tags=["public-edu"],
)


def _first_slide_frame(slides_material: EduMaterial) -> str | None:
    """The rendered frame of the first slide — the presentation's cover poster."""
    frames = [s for s in slides_material.slides if s.image_url]
    if not frames:
        return None
    return min(frames, key=lambda s: s.position).image_url


def _guide_cover(guide_material: EduMaterial) -> str | None:
    """The study guide's human-picked cover image (its cover block's media_path)."""
    for block in guide_material.content_blocks or []:
        if isinstance(block, dict) and block.get("type") == "cover" and block.get("media_path"):
            return block["media_path"]
    return None


@router.get("/deliverables", response_model=PublicDeliverablesResponse)
def list_public_deliverables(db: Session = Depends(get_db)):
    """List published pre-visit deliverables (video + study-guide PDF) by excursion × band.

    Public / no login. A deliverable shows only when its material is APPROVED,
    has ``published_at`` set and in the past, and its excursion is in the
    canonical taxonomy. Slides decks additionally require an assembled sibling
    video (there is nothing to play otherwise).
    """
    published = (
        db.query(EduMaterial)
        .join(EduResearch, EduMaterial.research_id == EduResearch.id)
        .options(
            joinedload(EduMaterial.research).joinedload(EduResearch.suggestion),
            selectinload(EduMaterial.slides),
        )
        .filter(
            EduMaterial.material_type.in_(["slides", "guide"]),
            EduMaterial.status == "APPROVED",
            EduMaterial.published_at.isnot(None),
            EduMaterial.published_at <= func.now(),
        )
        .all()
    )

    # The assembled video lives on a sibling video material (keyed by
    # research_id + grade_band). The operator publishes the *slides* deck; the
    # video is the rendered asset it plays.
    research_ids = {
        m.research_id
        for m in published
        if m.material_type == "slides" and m.research_id is not None
    }
    videos_by_key: dict[tuple[int, str | None], EduMaterial] = {}
    if research_ids:
        for v in (
            db.query(EduMaterial)
            .filter(
                EduMaterial.material_type == "video",
                EduMaterial.research_id.in_(research_ids),
                EduMaterial.file_url.isnot(None),
            )
            .all()
        ):
            videos_by_key[(v.research_id, v.grade_band)] = v

    # Group by excursion_key -> grade_band -> {material_type: EduMaterial}. Only
    # excursions in the canonical taxonomy are surfaced (they carry the public
    # page key + display names).
    by_excursion: dict[str, dict[str | None, dict[str, EduMaterial]]] = {}
    for m in published:
        suggestion = m.research.suggestion if m.research else None
        exc_key = suggestion.excursion_key if suggestion else None
        if not exc_key or get_excursion(exc_key) is None:
            continue
        band_map = by_excursion.setdefault(exc_key, {})
        slot = band_map.setdefault(m.grade_band, {})
        slot[m.material_type] = m

    # Emit in canonical excursion order, each excursion's bands in canonical order
    # (any unexpected extra band trails alphabetically).
    excursions_out: list[PublicDeliverableExcursion] = []
    for exc in EXCURSIONS:
        band_map = by_excursion.get(exc.key)
        if not band_map:
            continue
        extras = sorted(b for b in band_map if b not in exc.grade_bands)
        ordered_bands = [b for b in exc.grade_bands if b in band_map] + extras

        bands_out: list[PublicDeliverableBand] = []
        for band in ordered_bands:
            slot = band_map[band]
            slides_m = slot.get("slides")
            guide_m = slot.get("guide")

            video = None
            if slides_m is not None:
                video_m = videos_by_key.get((slides_m.research_id, band))
                if video_m is not None:
                    video = PublicDeliverableVideo(
                        url=video_m.file_url,
                        thumbnail_url=_first_slide_frame(slides_m),
                        duration_ms=video_m.duration_ms,
                    )

            guide_pdf_url = guide_m.file_url if guide_m else None
            guide_thumbnail_url = _guide_cover(guide_m) if guide_m else None

            if video is None and guide_pdf_url is None:
                continue
            bands_out.append(
                PublicDeliverableBand(
                    grade_band=band,
                    video=video,
                    guide_pdf_url=guide_pdf_url,
                    guide_thumbnail_url=guide_thumbnail_url,
                )
            )

        if not bands_out:
            continue
        excursions_out.append(
            PublicDeliverableExcursion(
                excursion_key=exc.key,
                public_key=exc.public_key,
                display_name=exc.display_name,
                display_name_es=exc.display_name_es,
                bands=bands_out,
            )
        )

    return PublicDeliverablesResponse(excursions=excursions_out)
