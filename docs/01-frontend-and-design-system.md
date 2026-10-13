# Frontend & Design System

[← Back to overview](README.md)

The frontend is a single React 19 + Vite 6 SPA that does double duty: it's both the public marketing/booking site and the admin dashboard that drives every content pipeline. I keep them in one codebase but strictly separated — the admin half is lazy‑loaded, so a tourist browsing tours never downloads the ag‑grid and TipTap bundles that only the CMS needs.

![Panama In Context home page](images/home.png)

## The stack, and why

- **React 19.2 + Vite 6.4** — fast dev server, instant HMR, and a small public bundle.
- **Tailwind CSS v4** — note the *v4*: the directive syntax and config model differ from v3, which trips people up. I drive the whole design system through CSS custom properties that map onto Tailwind's token names.
- **i18next + react‑i18next** — bilingual ES/EN, Spanish‑first.
- **react‑router‑dom 7** — with a deliberately bilingual route table (more below).
- **TipTap 3** (rich‑text article editing) and **ag‑grid 35** (admin data grids) — both admin‑only and code‑split out of the public bundle.
- **@sentry/react 10** — error reporting with aggressive PII scrubbing.

## Routing: bilingual by construction

Routing is where the Spanish‑first decision shows up most clearly. The public routes use Spanish slugs (`/excursiones-academicas`, `/tours-guiados`, `/materiales-educativos`, `/contacto`), and the blog is reachable under both language prefixes:

```
/blog            /en/blog            /es/blog
/articles/:slug  /en/articles/:slug  /es/articulos/:slug
```

The app is structured as **three sibling route trees**, each with its own layout shell:

- A **public tree** wrapped in `<AppShell>` (navbar, footer, analytics page‑view tracking) — home, the academic‑excursions and tours hubs, six excursion detail pages, seven tour detail pages, contact, the booking flow (`/checkout`, `/order-confirmation/:reference`, `/booking/{manage,cancel,renew-payment}`), mailing‑list confirmation, and the legal pages.
- A **materials tree** wrapped in `<EduMaterialsLayout>` at `/materiales-educativos` — a standalone, app‑like **dashboard shell** (more below) that deliberately does *not* use the public navbar/footer.
- An **admin tree** wrapped in a lazy `<AdminLayout>` behind `<Suspense>` — `/admin/login` plus a dozen dashboard routes.

Providers nest in a deliberate order around all three (`CartProvider > BookingModalProvider > EducatorAuthProvider` wrapping the whole `<Routes>`). The cart has to outlive the booking modal (you add to cart from inside the modal), and the educator‑auth context sits innermost yet still spans every tree — it gates the materials shell's Media Library section independently of the cart.

### The materials dashboard shell

The public educational‑materials area used to be a flat three‑tab page inside `<AppShell>`. It's now its own **standalone shell** — a sibling of `AppShell` and a near‑twin of the admin `AdminLayout`, so browsing classroom resources feels like an app, not a marketing page. A persistent left sidebar (logo up top; **Media Library**; **Educational Content** → Presentations / Infographics / Audio Guides; **Activities** → Historical Trivia; a **"Back to website"** link pinned at the bottom, mirroring admin's "Sign out") drives a right‑hand content pane, with the EN/ES toggle moved into a top bar since the public navbar is gone. Sections are deep‑linkable nested routes (`/biblioteca`, `/presentaciones`, …); empty ones render a shared `ComingSoonSection`, and the [educator gate](09-forms-mailing-and-educator-access.md) wraps only the Media Library section so the shell itself stays open. One bug worth remembering: the sidebar accordion originally keyed its expanded state on the *translated* label, so it collapsed on every language toggle — it now keys on a stable, language‑independent id.

```
src/
├── pages/            top-level pages + excursions/, tours/, admin/, edu/
├── components/
│   ├── admin/        CMS grids, editors, crawl panels
│   ├── booking/      booking modal, price breakdown
│   ├── cart/         cart drawer
│   ├── layout/       AppShell, Navbar, Footer, AdminLayout, EduMaterialsLayout
│   ├── public/       educator gate, public media library
│   ├── sections/     home/, blog/, tours/, academic/ marketing sections
│   └── ui/           shared primitives
├── contexts/         Cart, BookingModal, AdminAuth, EducatorAuth
├── hooks/            useToast, useBeforeUnload
└── services/         thin API clients (admin, articles, booking, …)
```

The `services/` layer is intentionally thin — each file is a small wrapper around `fetch` for one API domain, so the components stay declarative and there's exactly one place that knows about auth headers per domain.

## Bilingual content & i18n

i18next is configured with an HTTP backend and browser language detection, falling back to Spanish. Translations live in `frontend/public/locales/{es,en}/translation.json` under a single `translation` namespace with ~23 top‑level domain keys (`home`, `nav`, `hero`, `excursions`, `tours`, `booking`, `cart`, `checkout`, `order`, `edu_materials`, …).

Two small details I care about:

- **Cache‑busting on deploy.** The locale files are fetched with a `?v=<build-hash>` query so a new deploy guarantees fresh copy instead of a stale CDN cache.
- **`<html lang>` stays in sync.** Switching language updates the document's `lang` attribute, which matters for accessibility and for search engines that treat the ES/EN pages as distinct.

Database‑backed content (articles, categories, materials) is bilingual at the column level — every model carries paired `field` / `field_es` columns — and the API picks the language from a `lang` query param, falling back to English when a Spanish field is empty. The blog and content pipeline page covers how the Spanish columns get populated (DeepL).

## The design system

The brand lives as a self‑contained design skill (`.claude/skills/panama-in-context-design/`) that I invoke whenever I build UI — for the website, the admin, the slide decks, or throwaway mockups. It's the single source of truth that keeps the marketing site, the generated slides, and the generated PDFs looking like the same brand.

What's in it:

- **Tokens** — CSS custom properties for color, type, spacing, radii, and shadows. The brand blue `#1095d3` is mapped onto Tailwind's `indigo` name, so `indigo-600` *is* the brand color throughout; neutrals are stock Tailwind gray. Semantic aliases (`--color-brand`, `--text-heading`, `--surface`, `--focus-ring`) sit on top.
- **React component primitives** — Icon, Button, Badge, Card, Alert, and form inputs, each shipped with a `.d.ts` and a `.prompt.md` so they're easy to reuse or regenerate on‑brand.
- **Assets** — logos, heritage photography, and inline Heroicons for plain‑HTML contexts (the slide and PDF renderers can't import React).
- **Two UI kits** — a recreation of the marketing website to copy patterns from, and a 1280×720 slide kit with nine templates (title, section, bullets, two‑column, full‑image, timeline, map, closing). That slide kit isn't decorative: its nine templates mirror the backend `SlideTemplate` enum one‑to‑one, so the decks the pipeline renders match the kit exactly.

The house rules are deliberately spare: one brand‑blue accent on white, semibold tight‑tracked system‑sans headings, generous whitespace, heritage photos in `rounded-2xl` with a hairline ring, Heroicons, bilingual copy, **no emoji**, no gradient blobs or heavy shadows. You can see the language carry from the marketing page through to a generated study guide:

![Academic field trips page](images/academic-excursions.png)

## Analytics & error reporting

- **Analytics** (`analytics.js`) — Google Analytics + Meta Pixel, both gated behind a single `VITE_ANALYTICS_ENABLED` flag that's only on in the production build. `AppShell` fires a page‑view on each SPA navigation and a content‑view event for content routes (excursions, tours, articles). The checkout flow fires a real `Purchase` event with the order total.
- **Sentry** — initialized only when a DSN is present (it's baked into the production build, absent in dev). The interesting part is the privacy posture: `sendDefaultPii: false`, plus `beforeSend` / `beforeBreadcrumb` hooks that **redact magic‑link tokens** out of URLs and strip request bodies, cookies, query strings, and user data before anything leaves the browser. Magic‑link security is only as good as the weakest place a token can leak, and error reporting is an easy place to forget.

---

[← Back to overview](README.md) · Next: [Blog & Content Pipeline →](02-blog-and-content-pipeline.md)
