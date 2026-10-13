# Study Guide Templates — Panama In Context

A **portrait, print-ready** template set for building student study guides
and educational handouts on the brand. Authored on **US Letter (816×1056,
8.5×11 @96dpi)** and styled entirely from the design-system tokens (brand
blue `#1095d3`, system-sans type, Heroicons, heritage photography).

Per the brand's document convention, the **hexagon emblem appears only on
the first and last page** — interior pages use a quiet running footer
(chapter · page number) instead.

## Two ways to use it
- **`index.html`** — all eleven page types assembled into one continuous,
  **printable document**. Open it and **Print → Save as PDF**: each page
  maps to one Letter sheet (print CSS handles the page breaks, no browser
  chrome). This is the file to duplicate for a new guide.
- **`page-NN-*.html`** — each page type as a standalone, self-scaling card
  (these are the tiles shown in the Design System → **Study Guides** group).
  Copy whichever ones you need.

## The eleven page types
| # | File | Use |
|---|------|-----|
| 1 | `page-01-title` | Cover — hero band, brand line, title, subtitle, author *(emblem)* |
| 2 | `page-02-section` | Chapter divider — numeral, title, chapter contents |
| 3 | `page-03-bullets` | Heading + brand-marked key points |
| 4 | `page-04-photo-top` | Banner photo, then title + short-form text |
| 5 | `page-05-timeline` | **Vertical** dated timeline |
| 6 | `page-06-numbered` | Ordered list with brand number badges |
| 7 | `page-07-reading-a` | Subtitle · paragraph · bullets · two paragraphs |
| 8 | `page-08-reading-b` | Subtitle · three paragraphs · photo · caption |
| 9 | `page-09-reading-c` | Subtitle · paragraph · **Key Terms** box · pull quote |
| 10 | `page-10-reading-d` | Subtitle · **two-column** paragraphs with a side note |
| 11 | `page-11-closing` | Review questions + thank-you + contact *(emblem)* |

Pages 7–10 are the long-form reading layouts — mix and reorder them freely
to fit your lesson. Reusable building blocks available in `guide.css`:
`.g-callout` (Key Terms / "Did you know?" box), `.g-quote` (pull quote),
`.g-bullets`, `.g-numbers`, `.g-figure` + `.g-caption`, and `.g-cols`
(two-column) / `.g-stack` (vertical rhythm).

## Editing
- All page markup is **static HTML** — click a heading or paragraph in
  edit mode and retype it. Layout lives in `guide.css`.
- The sample content (the "Three Panamas" module) is placeholder — swap in
  your own copy and images. Photos come from `../../assets/images/`.
- To reorder or drop pages in the printable doc, move/delete the
  corresponding `<div class="page …">` block in `index.html`.

## Export
- **PDF:** open `index.html` → Print → Save as PDF (Letter portrait).
- **PowerPoint:** the slide deck in `../slides/` is the landscape companion;
  use this kit for printed handouts, not projection.
