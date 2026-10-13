Labelled text field with the inset-ring focus treatment, optional leading icon, hint, and error state.

```jsx
<Input label="Email address" type="email" icon="envelope" placeholder="you@example.com" required />
<Input label="School name" hint="As it should appear on the invoice" />
<Input label="Email" error="Please enter a valid email" defaultValue="not-an-email" />
```

`error` overrides `hint` and recolors the ring red. Pass `icon` a Heroicon name for a leading glyph.
