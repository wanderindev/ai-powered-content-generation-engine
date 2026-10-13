# Panama In Context — Design System

A design system for **Panama In Context**, a bilingual (Spanish / English)
educational-tourism business founded by guide **Diego Raúl Feliu**. The
brand offers three things: **academic field trips** for schools,
**private guided tours** for travellers, and a **history blog** about
Panama. Its promise — *"Where every site tells a story"* — frames every
visit as a guided journey through Panamá's history and culture, supported
by audio technology, curriculum-aligned materials, and deep local
storytelling.

This repository is a self-contained kit — colours, type, spacing, icons,
real brand imagery, reusable React components, and a full interactive
recreation of the marketing site — so a design agent can produce on-brand
screens, decks, and prototypes for Panama In Context.

## Sources this system was built from
- **Frontend codebase** (attached): `frontend/` — React 19 + Vite +
  Tailwind CSS 4, Headless UI, Heroicons, i18next. The brand colour,
  typography, layout rhythm, components, and bilingual copy were lifted
  directly from here.
- **GitHub repo:** <https://github.com/wanderindev/panama-in-context> —
  the full monorepo (FastAPI backend + React frontend). Explore it to go
  deeper on data models, booking logic, and content. *Reference for the
  reader; do not assume runtime access.*
- **Brand assets:** logos and heritage photography from
  `frontend/public/` (copied into `assets/`).

> No Figma file or slide template was provided. Sample slides are **not**
> included (per scope). If you have a deck template, share it and a Slides
> kit can be added.

---

## Content fundamentals

**Voice:** warm, expert, and quietly authoritative — a knowledgeable local
host, not a hype-driven marketer. Copy sells *understanding*, not just
sightseeing: *"helping you truly understand Panama, not just see it."*

- **Person:** addresses the reader as **you** ("Plan Your Visit",
  "Where Would You Like to Go?"); the guide speaks as **I** only on the
  personal Contact/bio ("Hi, I'm Diego…"). The company is **we / our**.
- **Bilingual, always.** Every user-facing string exists in English and
  Spanish (`frontend/public/locales/`). Spanish is not an afterthought —
  the company is Panamanian. Keep both voices natural, never machine-literal.
- **Headlines:** short, evocative, often a benefit or a promise —
  *"Learning Comes Alive"*, *"Experience Panama Beyond the Surface"*,
  *"Stories from Panama's Past"*. Title Case for headings.
- **Eyebrows:** a short brand-blue kicker labels the audience or section —
  *"For Educators"*, *"For Travelers"*, *"From the Blog"*, *"Your Guide"*.
- **Body:** complete, confident sentences with concrete history (dates,
  names, places: *"founded in 1519"*, *"destroyed by the pirate Henry
  Morgan in 1671"*). Specific over generic. Sentence case.
- **CTAs:** plain and action-led — *"For Schools"*, *"Reserve Now"*,
  *"Learn More"*, *"Add to Cart"*, *"Go to Checkout"*. The marketing
  "link" action carries a trailing arrow: *"Contact Us →"*.
- **Tone with audiences:** educators get rigor (curriculum, retention,
  grade-level adaptations); travellers get wonder and access (hidden gems,
  private, personalized). Never childish, never salesy.
- **Emoji:** none. The brand never uses emoji in product copy. Icons do
  that job.

---

## Visual foundations

A clean, editorial, **Tailwind-UI–descended** aesthetic: white canvas,
one confident brand blue, generous whitespace, and large warm photography
of Panamá's heritage sites. Restrained and trustworthy — the design gets
out of the way of the stories and the imagery.

**Colour**
- A single accent: **brand sky-blue `#1095d3`** (mapped onto the Tailwind
  `indigo` name in the product). Used for links, primary buttons, eyebrows,
  focus rings, and active nav. `brand-700 #0d78aa` is the hover/pressed
  darken; `brand-50 #e8f6fc` is the soft chip/highlight fill.
- Neutrals are the **stock Tailwind gray scale** — `gray-900` headings,
  `gray-700` body, `gray-600/500` secondary & meta, `gray-200` hairline
  borders, `gray-50/100` muted surfaces, `gray-900` dark stages.
- Status colours (forms/toasts) are conventional green / red / amber.
- No multi-colour gradients in UI. The only "gradient" is a dark scrim
  over hero photography for text legibility.

**Type**
- **No webfont** — the product uses the native **system sans stack**
  (`ui-sans-serif, system-ui, …`), Tailwind 4's default. Fast, neutral,
  legible. Headings are **semibold (600)** with **tight tracking
  (-0.025em)**; body is generously leaded (1.625–1.75).
- Hierarchy: hero `~3–4.5rem`, section titles `2.25–3rem`, body `1.125rem`,
  UI labels `0.875rem` semibold. Eyebrows are `1rem` semibold in brand blue.

**Spacing & layout**
- Tailwind `0.25rem` step scale. Content lives in a centred
  **`max-w-7xl` (80rem)** column with **`px-6` → `lg:px-8`** gutters.
- Sections breathe: **`py-24` → `sm:py-32`**, separated by a single
  `1px gray-200` top border. Centred eyebrow → title → subtitle is the
  default section opener; feature trios sit in an even 3-column grid.

**Imagery**
- Real, warm, sun-lit documentary photography of Panamá — colonial domes,
  the Canal locks, Casco Antiguo plazas, the Biomuseo, Afro-Caribbean
  murals. Saturated, daylight, no heavy filters or B&W.
- Feature/article images use **`rounded-2xl`** corners with an **inset
  `ring-gray-900/10`** hairline. The hero is a full-bleed crossfade
  (5s interval, 1s fade) at ~55–60% opacity over a `gray-900` stage.

**Shape, elevation & borders**
- Radii: `md (6px)` buttons & inputs · `lg (8px)` modals/images/wells ·
  `2xl (16px)` feature imagery · `full` pills, avatars, toggles.
- Shadows are soft and sparing: `xs` on resting buttons, `sm` cards,
  `lg` on the guide avatar & popovers, `xl` on modals. Cards more often
  rely on a hairline ring/border than a heavy shadow.
- Inputs use the **inset-ring** pattern: `ring-gray-300` at rest →
  **2px brand ring** on focus (no hard outline).

**Motion & states**
- Subtle and quick. Colour/opacity transitions ~200ms; hero crossfade 1s;
  modal enter ~300ms. No bounces, no decorative infinite loops.
- **Hover:** primary buttons darken to `brand-700`; links shift toward
  brand blue; nav items go brand on active. **Focus:** 2px brand ring.
  **Disabled:** 50% opacity, no shadow.

**Things to avoid** (off-brand): purple/pink gradient blobs (stock
template residue in the original hero — omitted here), emoji, hand-drawn
SVG icons, heavy drop shadows, multiple accent colours.

---

## Iconography

The product uses **[Heroicons](https://heroicons.com)** (via
`@heroicons/react`) — the natural companion to its Tailwind/Headless-UI
stack. This system bundles the exact Heroicons glyphs as inline SVG path
data (no runtime CDN) so they render identically in cards, components,
slides, and screenshot capture.

- **Outline (24px, 1.5px stroke)** for interface controls and feature
  icons — `academic-cap`, `book-open`, `user-group`, `map`,
  `globe-americas`, `calendar-days`, `shopping-bag`, `bars-3`, `x-mark`,
  `magnifying-glass`, `chevron-*`, `sun`/`moon`, etc.
- **Solid (filled)** for inline accents and checklists — `check-circle`,
  `star`, `chevron-up-down`, `plus`/`minus`, `sparkles`, `heart`…
  (append `-solid` to the name).
- Feature icons are typically **brand-blue**; UI controls inherit
  `gray-700`/`currentColor`.
- **Two ways to use them:**
  - **React:** `<Icon name="academic-cap" size={24} />` (see
    `components/core/Icon`). `ICON_NAMES` lists everything bundled.
  - **Plain HTML:** load `assets/heroicons.js`, then
    `<i data-hicon="check-circle-solid"></i>` (auto-swapped for inline
    SVG sized by `font-size`, coloured by `currentColor`).
- **Social glyphs** (Instagram, YouTube) use their official brand marks —
  see `ui_kits/website/Footer.jsx`.
- No emoji or Unicode pictographs are used as icons.

---

## Repository index

**Foundations**
- `styles.css` — global entry point (imports only). **Consumers link this.**
- `tokens/colors.css` · `typography.css` · `spacing.css` · `effects.css` —
  CSS custom properties (base scales + semantic aliases).
- `guidelines/*.html` — foundation specimen cards (Design System tab):
  colour scales, text/surface roles, status, type scale & weights,
  spacing, radii, shadows, logo, photography, iconography.

**Assets** (`assets/`)
- `logo-h-116.png` (horizontal lockup), `logo_512x512.png` (hex emblem),
  `apple-touch-icon.png`, `favicon-32x32.png`.
- `images/` — heritage photography (Canal, Casco Antiguo, Biomuseo,
  Panamá Viejo, Mi Pueblito, the guide portrait).
- `heroicons.js` — inline Heroicons registry for plain-HTML use.

**Components** (`components/`) — React primitives, namespace
`window.PanamaInContextDesignSystem_0dba89`
- `core/` — `Icon`, `Button`, `Badge`, `Card`
- `forms/` — `Input`, `Select`, `Checkbox`
- `feedback/` — `Alert`
- Each has `<Name>.jsx`, `<Name>.d.ts`, `<Name>.prompt.md`, and a
  `@dsCard` demo HTML.

**UI kits** (`ui_kits/`)
- `website/` — interactive recreation of the marketing site (home, tours,
  booking → cart, contact, blog; EN/ES). See its `README.md`.
- `slides/` — PowerPoint-style presentation templates (title, section,
  bullets, two-column, full image, timeline, map, closing) as a
  navigable, PPTX-exportable deck. See its `README.md`.
- `study_guides/` — portrait, print-ready study-guide templates (title,
  section, bullets, photo-top, vertical timeline, numbered, four long-form
  reading layouts, closing) as one printable PDF document. See its
  `README.md`.
- `social/` — Instagram post templates (1080×1350) matching the
  @panamaincontextbydiego feed: two "Sabías que" fact cards (blue + warm
  gray) and a blog-announcement post. Uses the accent-orange tokens and
  Google **Caveat** for handwriting. See its `README.md`.

**Other**
- `SKILL.md` — Agent-Skill manifest for use in Claude Code.
- `readme.md` — this file.

---

*Want it sharper? The single biggest open question is **fonts** — the live
site ships the system sans stack, so this kit does too. If you'd prefer a
branded typeface (or have one in mind), say so and it can be wired in.*
