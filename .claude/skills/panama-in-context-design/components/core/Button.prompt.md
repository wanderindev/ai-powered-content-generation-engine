Primary call-to-action — solid brand button, plus secondary/soft/link variants and the trailing-arrow link pattern used across the marketing site.

```jsx
<Button variant="primary" href="/excursiones-academicas">For Schools</Button>
<Button variant="link" arrow>Learn More</Button>
<Button variant="primary" size="lg" iconLeading="shopping-bag">Reserve Now</Button>
```

Variants: `primary` (brand fill, default), `secondary` (neutral outline), `soft` (brand-50 tint), `link` (inline text, often with `arrow`). Sizes `sm | md | lg` — `lg` matches the hero and booking CTAs. Pass `iconLeading` / `iconTrailing` a Heroicon name; `disabled` drops opacity to 50%.
