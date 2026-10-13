# Website UI Kit — Panama In Context

An interactive, click-through recreation of the public marketing site
(`frontend/` — React 19 + Tailwind 4). It composes the design-system
primitives (`Button`, `Badge`, `Card`, `Input`, `Select`, `Checkbox`,
`Alert`, `Icon`) rather than re-implementing them.

## Run it
Open `index.html`. The page loads React + Babel + the compiled
`_ds_bundle.js`, then mounts the section files.

## What's interactive
- **Language toggle (EN / ES)** in the navbar swaps all visible copy.
- **Top navigation** routes between Home, Field Trips, Tours, Blog,
  Educational Materials, and Contact (mobile hamburger drawer included).
- **Hero** crossfades through heritage photography every 5s.
- **Tours grid → Reserve** opens a **booking modal** (date, time slot,
  participant stepper, live total) → **Add to Cart** → **cart drawer**
  slides in with subtotal and checkout.
- **Contact** is a working form (Select / Input / Checkbox) that shows a
  success `Alert` on submit.

## Files
| File | Role |
|------|------|
| `index.html` | Shell: loads deps + bundle, mounts the babel scripts |
| `App.jsx` | Routing, language + cart/booking state |
| `Navbar.jsx` | Sticky header, language pill, cart badge, mobile menu |
| `Hero.jsx` | Dark crossfading hero with dual CTA |
| `Sections.jsx` | Schools, Tourists, Meet-the-Guide, Articles, CTA |
| `Pages.jsx` | Tours grid, Booking modal, Cart drawer, Contact, Blog |
| `Footer.jsx` | Link row, social glyphs, legal + copyright |

## Fidelity notes
- Layout, type, color, and component patterns are lifted from the real
  components (`Navbar`, `HeroSection`, `SchoolsSection`,
  `FeaturedArticles`, `BookingModal`, etc.).
- The decorative pink/purple blur blob from the stock Tailwind hero
  template is intentionally omitted — it is off-brand template residue.
- Pricing and availability are illustrative; the live product computes
  these from the FastAPI backend.
