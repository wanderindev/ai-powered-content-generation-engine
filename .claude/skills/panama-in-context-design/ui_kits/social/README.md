# Social Post Templates — Panama In Context

Instagram post designs matching the **@panamaincontextbydiego** feed.
Portrait **1080×1350 (4:5)**, styled from the design-system tokens plus the
**accent orange** (`--accent-*`) the account uses for social art.

## The templates
| File | Type | Notes |
|------|------|-------|
| `post-sabias-que-azul.html` | "Sabías que" fact card — **deep blue** | Textured navy card, hand-drawn connector, tilted polaroid + handwritten caption |
| `post-sabias-que-claro.html` | "Sabías que" fact card — **warm gray** | Light paper card, sepia polaroid — the alternate treatment seen in the feed |
| `post-nuevo-en-el-blog.html` | **Blog announcement** | Full-bleed photo, "Nuevo en el blog" chip, post **title**, author + read-time, "Léelo ahora" CTA |

## Recurring motifs (lifted from the account)
- **"Sabías que" heading** in a handwriting script (Google **Caveat** — see below), orange.
- **Hand-drawn connector**: a thin wavy line running down the left margin from the heading to a dot near the polaroid (white on blue, orange on gray). Decorative brand SVG — edit the `<path d="…">` to reshape.
- **Polaroid**: white frame, slight rotation, `Caveat` caption. Swap `<img src>` and rotate via the inline `transform`.
- **Brand lockup**: hexagon emblem + typeset `PANAMA / IN CONTEXT / by Diego`. Set `--wm` to `#fff` over dark, or a dark blue over light.

## Editing
- Copy is **Spanish** (matching the feed). Click any text to retype.
- Swap the fact/quote text and the polaroid image; captions and the connector are the only fixed motifs.
- For the blog post, replace the headline, category kicker, author line, and background photo (`assets/images/`).

## Fonts — please confirm
The account's "Sabías que" / caption lettering is a hand-drawn script. The
exact font isn't in the codebase, so these use Google Fonts **Caveat** as the
closest match. If you have the real brand script (or want a different one),
send it and I'll swap it in — it's set via `--font-hand` in each file's head.

## Export
Open a post and use **Save as PDF** or screenshot at 1080×1350. (For a true
1080×1350 PNG, capture the `.post` element.)
