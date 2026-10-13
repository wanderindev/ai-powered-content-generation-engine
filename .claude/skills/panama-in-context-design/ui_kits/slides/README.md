# Slide Templates — Panama In Context

A PowerPoint-style template set for building **educational presentations**
on the brand. Authored on a **16:9 / 1280×720** canvas and styled entirely
from the design-system tokens (brand blue `#1095d3`, system-sans type,
Heroicons, heritage photography).

## Two ways to use it
- **`index.html`** — all nine templates assembled into one navigable
  **deck** (built on `deck-stage.js`). Arrow keys / Space to advance, a
  thumbnail rail to jump & reorder, and **Print → Save as PDF** /
  **PPTX export** produce one page per slide. This is the file to present
  from and to duplicate for a new lesson.
- **`slide-0N-*.html`** — each template as a standalone, self-scaling page
  (these are the cards shown in the Design System → **Slides** group).
  Copy whichever ones you need.

## The nine templates
| # | File | Use |
|---|------|-----|
| 1 | `slide-01-title` | Opening title — emblem, eyebrow, title, byline over a hero photo |
| 2 | `slide-02-section` | Chapter / section divider — big numeral on a dark photo |
| 3 | `slide-03-bullets` | Title + brand-marked bullet list |
| 4 | `slide-04-two-col-image-left` | Photo left, text right |
| 5 | `slide-05-two-col-image-right` | Text left, photo right |
| 6 | `slide-06-full-image` | Full-bleed photo with caption chip |
| 7 | `slide-07-timeline` | Horizontal timeline with alternating milestones |
| 8 | `slide-08-map` | Location list + map panel with pins and a callout |
| 9 | `slide-09-closing` | Thank-you / contact slide |

## Editing
- All slide markup is **static HTML** — click a heading or paragraph in
  edit mode and retype it directly. Layout/spacing live in `slides.css`.
- The sample content (the "Three Panamas" lesson) is placeholder — swap in
  your own copy and images. Photos come from `../../assets/images/`; drop
  new ones there and update the `src`.
- **Map slide:** the right panel is a styled placeholder. Replace it with
  a real map of your area (export to PowerPoint and drop an image on top,
  or set a background image on `.mapfield`), then position the
  `.pin` markers with `left` / `top` percentages.

## Export to PowerPoint
Open `index.html`, then use the **Export as PPTX** flow. The deck is sized
1280×720 so it maps cleanly to a 16:9 PowerPoint. Editable mode emits real
text boxes; screenshot mode is pixel-perfect.
