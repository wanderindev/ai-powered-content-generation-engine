Inline status banner for form success/error and notices.

```jsx
<Alert tone="success" title="Thanks!">Check your email to confirm your registration.</Alert>
<Alert tone="error">Something went wrong. Please try again later.</Alert>
<Alert tone="info" onClose={() => {}}>This tour is conducted in Spanish.</Alert>
```

Tones: `success | error | warning | info` (info is brand-tinted). Each carries a default Heroicon; override with `icon`. Pass `onClose` to show a dismiss button.
