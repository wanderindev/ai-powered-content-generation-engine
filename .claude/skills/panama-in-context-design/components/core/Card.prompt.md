Image-topped content card for articles, tours, and excursions — photo in a rounded-2xl frame with a hairline inset ring.

```jsx
<Card
  image="/assets/images/ship-in-locks.jpg"
  badges={[{ label: 'Canal', variant: 'brand' }, { label: '3 hrs', variant: 'neutral' }]}
  title="The Panama Canal: Engineering Marvel and Global Impact"
  description="How the Big Ditch transformed global trade and shaped Panama's identity."
  href="#"
  footer={<Button variant="link" arrow>Read more</Button>}
/>
```

Set `href` to make the title a hover-linked anchor. `badges` renders pills above the title; `meta` is a small line under the description; `footer` holds an action (usually a link Button). `aspect` controls the image ratio (default `3 / 2`).
