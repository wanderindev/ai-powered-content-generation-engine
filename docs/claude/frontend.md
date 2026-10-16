# Frontend, Build-Time Config and the Admin CMS

Moved verbatim out of `CLAUDE.md` (size guard, 2026-10-12). Same
authority as CLAUDE.md; read this file before changing this area.

### Copy and Build-Time Configuration
- **No UI string is bundled.** All copy lives in
  `frontend/public/locales/{en,es}/translation.json` and is fetched at runtime by
  `i18next-http-backend` from `/locales/{{lng}}/{{ns}}.json`, cache-busted with a
  `VITE_BUILD_HASH` stamped at image-build time (`docker/frontend/Dockerfile.prod`
  sets it from `date +%s`). `fallbackLng` is **`es`**, and `i18n.js` keeps
  `<html lang>` in sync on every language switch (`index.html` hardcodes `en`).
  Adding a string means editing both JSON files, not a component.
- **The same `VITE_BUILD_HASH` is also published as `/version.json`** (#595):
  the Dockerfile computes it once, builds with it, then writes
  `{"buildHash": …}` into `dist/`. `hooks/useNewDeployAvailable.js` polls it
  from `AdminLayout` (60s + on focus) and shows a "reload before running any
  generation" banner when it differs from the bundle's own hash — the guard
  against a pre-deploy admin tab calling a changed API (article 246, #594).
  Admin-only; inert when the bundle has no hash (dev, CI). nginx is
  deliberately untouched: a root `.json` falls to `location /` with no
  `Cache-Control`, so the poll itself uses `cache: 'no-store'` + a `?t=`
  buster. Computing the hash twice in the Dockerfile would make every tab
  permanently "stale".
- **On the tour and excursion pages that is true of the *content*, not just the
  strings.** Each of the 14 pages under `pages/tours/` and `pages/excursions/`
  declares its own local `getList(key)` — `t(key, { returnObjects: true })`
  narrowed with `Array.isArray(items) ? items : []` — and loops over arrays of
  objects that live in the locale JSON: `details.highlights` /
  `details.adaptations` (excursions), `details.includes` / `details.facts`
  (tours), plus one-off shapes like `siete_iglesias.details.churches` and the
  `casco_museums.details.tours` array that #524 introduced when the museum page
  was split into its two visits. So restructuring a page's material is a
  translation.json edit plus a loop, not JSX — and the failure is silent: a key
  that is missing or not an array yields `[]`, so the heading renders with
  nothing under it. `backend/scripts/translate_frontend.py` cannot help there —
  `find_missing_keys` recurses into `dict` only, so an array is one opaque leaf
  it skips whenever ES has the key at all, and the per-item Spanish strings are
  hand-written.
- **The page photos are a static data module, not the media library.**
  `frontend/src/data/tourImages.js` and `excursionImages.js` map a page slug to
  a list of `{id, src, alt}` pointing at files in `frontend/public/images/`,
  read through `getTourImages(key)` / `getExcursionImages(key)` (both `|| []`
  on an unknown slug) and fed to `components/ui/ImageGallery.jsx`. Nothing in
  them is i18n'd: all 68 `alt` strings are hardcoded English on a bilingual
  site. Together with the hand-written route table and the copy above, that is
  the third leg — neither a public tour page's prose nor its pictures comes
  from the database.
- **Everything environment-specific is a build-time `VITE_*` arg**, baked into the
  bundle by `docker/frontend/Dockerfile.prod` (`VITE_API_URL`, `VITE_SENTRY_DSN`,
  `VITE_ANALYTICS_ENABLED` + `VITE_GA_ID` + `VITE_META_PIXEL_ID`) — changing one
  needs a rebuild, not a restart. `src/analytics.js` gates *every* GA / Meta Pixel
  call on `VITE_ANALYTICS_ENABLED === 'true'`, which only the prod Dockerfile sets,
  so dev, CI, forks and preview builds never report into the production property
  (#112). Same pattern as the Sentry DSN, which no-ops when unset.
- **`VITE_API_URL`'s *fallback* is not uniform, and six modules fall back to
  production.** Every caller writes `import.meta.env.VITE_API_URL || <literal>`,
  but the literal differs: `services/articles.js`, `ask.js`, `educators.js`,
  `publicEdu.js`, `publicMedia.js`, `subscribe.js` (and the inline copies in
  `ArticleContent.jsx` / `FeaturedArticles.jsx` / `BlogList.jsx` /
  `RelatedArticles.jsx`) default to
  `https://api.panamaincontext.com`, while `services/admin.js`, `booking.js`,
  `pages/Contact.jsx` and `components/admin/MediaPickerModal.jsx` /
  `MediaEditModal.jsx` / `ImportedMediaPanel.jsx` default to `http://localhost:8000`. So
  a build with the var **unset** points the public surface — including the
  `subscribe` / educator-signup writes and the Ask PIC `POST /ask` (a write
  that spends real Anthropic / Voyage budget and the live per-IP and daily
  rate-limit quota) — at the live API while admin and booking
  talk to localhost. `docker-compose.yml` and `frontend/.env.example` both set it
  to `http://localhost:8000`, so this only bites a bare `npm run dev`/`preview`
  or a harness with no `.env`; check it before concluding a dev session wrote
  nothing to prod. `vite.config.js`'s dev-server `proxy` for `/api` →
  `http://localhost:8000` does **not** rescue that case: nothing in
  `frontend/src` issues a relative `fetch('/api/...')` (every module goes
  through `API_BASE`), so the proxy is dead config and a dev session inherits
  whichever literal above applies.
- **The bundle's chunk layout is deliberate, and AG Grid / TipTap are
  deliberately absent from it.** `vite.config.js` names only three stable
  vendor groups in `build.rollupOptions.output.manualChunks` — `react-vendor`,
  `i18n`, `sentry` — so they stay cached across deploys instead of churning the
  app chunk (#18); the two heavy CMS libraries split *naturally* at the lazy
  `/admin` route boundary in `App.jsx`, and naming them here would force them
  back into a chunk the public site references. `chunkSizeWarningLimit` is
  raised to `1300` for the same reason: the only chunks still over Vite's
  500 kB default are those two admin-only ones. So "silence the chunk-size
  warning by adding it to manualChunks" undoes the split that keeps the CMS
  chunks out of the public download.

### The Public Site Has No Meta / SEO Layer
The SPA renders every route client-side and **nothing manages the document head**:
`frontend/index.html` ships one static `<title>Panama In Context</title>` and a
`viewport` tag, there is no `react-helmet`(-async) or equivalent in
`package.json`, no `document.title` / `<meta name="description">` / Open Graph /
`twitter:` / canonical write anywhere in `frontend/src`, and no `robots.txt` or
`sitemap.xml` under `frontend/public/`. `vite.config.js` has no SSR or prerender
plugin, and the edge serves `try_files $uri $uri/ /index.html`, so a crawler or
a link-preview bot gets the same empty shell for an article as for the homepage.
Consequences: "add a meta description to articles" or "fix the social preview"
is **new infrastructure** (a head manager plus either prerendering or a bot
path), not a template edit; and per-article SEO cannot be tested by reading the
API response, because nothing consumes it into markup. The one head value that
*is* dynamic is `<html lang>`, which `i18n.js` syncs on language switch.

### ESLint Is a Real Gate, and Two of Its Rules Shaped the Tree
With no frontend test suite, `npm run lint` (`eslint src`) is one of only two
things standing between a frontend change and prod — and
`frontend/eslint.config.js` raises three rules above the recommended baseline to
**`error`**, so they fail CI rather than warn. There is no `--max-warnings`
flag, which is what "errors-only" means here: the pre-existing
`react-hooks/exhaustive-deps` warnings are deliberately tolerated (#273/#289).

- **`react-refresh/only-export-components`** is why every context is **two
  modules**: `*Context.jsx` holds the `createContext` object plus the `use*`
  hook, and a separate `*Provider.jsx` holds the provider *component*, because a
  component module may export only components. That is the split behind
  `AdminAuthContext`/`AdminAuthProvider`, `EducatorAuthContext`/`Provider`,
  `CartContext`/`CartProvider` and `BookingModalContext`/`BookingModalProvider`,
  and behind `useToast` living in `hooks/useToast.js` while `Toast.jsx` keeps
  only `ToastContainer`. A new context added as one file fails the build.
- **`react-hooks/set-state-in-effect`** forbids a `setState` in an effect's
  *synchronous* body, which is why ~10 sites are written the way they are and
  carry a comment naming the rule: data-fetch effects wrap in an async IIFE so
  the spinner `setState` falls outside the sync body (`PublicMediaLibrary`,
  `AdminMediaLibrary`, `AdminSuggestions`, `AdminCalendar`), both auth providers
  derive synchronous defaults in `useState` initializers with verification in
  callbacks, `BookingModal` is remounted by key instead of reset by effect (the
  provider fact recorded below), and `AdminLayout` / `PublicMediaDetailModal`
  adjust state during render. Reaching for a reset effect re-breaks the gate.
- **`no-unused-vars` is `error` with `varsIgnorePattern: '^[A-Z_]'`** — an
  unused `CONST`-cased binding passes, an unused lowercase one fails.

### The Admin CMS Is Nine Grids Over One Polymorphic Drawer
Every content-editing admin page has the same two halves: an `AgGridReact`
listing and `components/admin/EditDrawer.jsx`, the single ~2550-line drawer that
holds **every** entity's editor. Nine pages under `frontend/src/pages/admin/`
mount a grid (`AdminArticles`, `AdminSuggestions`, `AdminOutlines`,
`AdminResearch`, `AdminEduSuggestions`, `AdminEduResearch`, `AdminEduMaterials`,
`AdminInstagram`, `AdminOrders`) and eight of them hand the drawer a
`recordType` string — `Research` / `Article` / `Suggestion` / `Outline` /
`EduSuggestion` / `EduResearch` / `EduMaterial` / `InstagramPost` — which its
`renderEditor()` if-chain maps to the matching `*Editor` function in the same
file. So adding an admin entity is a case in that chain plus a grid page, not a
new drawer component, and the shared cell renderers live in
`SharedCellRenderers.jsx` / `InlineStatusCell.jsx` / `ActionsCellRenderer.jsx`.
Three properties to know before touching it:

- **An unrecognised `recordType` fails soft.** The fall-through is
  `PlaceholderEditor`, which renders the record's id / title / status and the
  text "Full editing form coming soon." — no error, no console warning. A drawer
  that opens showing three read-only fields means the type never got a case.
- **Dirty-state tracking is per editor, exposed upward through a ref.** Each
  editor compares its state against a `savedSnapshot` ref and publishes its own
  `requestClose` through the `requestCloseRef` prop; the drawer shell's close
  button and `Dialog onClose` route through `handleClose`, which calls that ref
  so the *editor's* confirm dialog fires. An editor added without wiring the ref
  simply closes and discards edits. The snapshot compares raw stored HTML, so
  `RichTextEditor`'s prop→editor sync must call `setContent(…, { emitUpdate:
  false })` (TipTap v3 options object; the v2 positional `false` is ignored):
  echoing TipTap's re-serialized HTML through `onChange` made the article
  drawer dirty on merely opening the ES tab, which turned "Discard & Switch"
  into a routine click that silently reverts real edits (#596).
- **The browser-level guard is deliberately inert.** `useBeforeUnload` is
  installed once, at the admin shell (`components/layout/AdminLayout.jsx`), with
  a hardcoded `false` and a TODO (#119/#118/#120) explaining that no consumer
  signals dirty state yet. Closing the tab or hitting Back mid-edit therefore
  prompts nothing — only in-app drawer closes are guarded.

`AdminOrders` is the one grid page outside that scheme: the booking side keeps
its own inline `Dialog` drawer (the one the Payments section describes), and the
non-grid pages — `AdminCalendar`, `AdminEditorialCalendar`, `AdminMediaLibrary`,
`AdminAgentTasks`, `AdminSettings`, `AdminHome` — are bespoke. `AdminEditorialCalendar`
is the only caller that picks `recordType` at runtime, per clicked pill
(`Article` or `InstagramPost`).

### The Admin Editor Round-Trips Through HTML, and Its PUT Is a Full Replace
The DB stores **markdown**, but the admin editor never sees markdown: the detail
endpoints return `content_html=markdown_to_html(...)`, TipTap edits that HTML,
and the save path runs the *inverse* conversion — `html_to_markdown`
(`app/utils/markdown.py`, markdownify with `heading_style="ATX"`, `bullets="-"`)
— over the whole body. That applies to articles (`PUT
/admin/dashboard/articles/{id}`, both languages) and research (`PUT
.../research/{id}`). Two consequences:

- **Every admin save rewrites the entire body**, so anything the
  render → TipTap → markdownify chain cannot represent is silently dropped.
  Concretely, `markdown_to_html` enables the `tables` extension and
  `_ALLOWED_TAGS` keeps `<table>`, while the editor loads only StarterKit +
  Link + a custom Image (`RichTextEditor.jsx`; there is no
  `@tiptap/extension-table` in `package.json`) — so a markdown table renders,
  is dropped on parse, and is gone from the stored markdown after one save. The
  same pass also launders: raw HTML in the stored markdown is stripped by the
  read-time sanitizer and never comes back.
- **`ArticleUpdateRequest` is a replace, not a patch.** Only `title` and
  `content_html` are required; `excerpt`, `title_es`, `excerpt_es`,
  `content_html_es` default to `None` and `tag_ids` to `[]`, and
  `update_article` writes every one of them unconditionally (`article.tags = []`
  on an empty list). A partial body therefore blanks the Spanish article — which
  then fails the publish gate's `content_es` precondition and strips the
  narration source — and removes every tag. `EditDrawer.jsx` always sends the
  full set, and its comment records that the outline editor's dead `handleSave`
  would have posted `content_html: ''` + `tag_ids: []`; the endpoint is not on
  `DRIVER_ALLOWED_CALLS`, so the admin UI is its only caller today.
