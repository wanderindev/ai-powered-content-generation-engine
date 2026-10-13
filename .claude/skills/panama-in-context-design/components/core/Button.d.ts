import * as React from 'react';

/**
 * Primary call-to-action button. Solid brand fill is the default;
 * `secondary` is a neutral outline, `soft` a brand tint, and `link`
 * an inline text action (pair with `arrow` for the "Learn more →" pattern).
 *
 * @startingPoint section="Core" subtitle="Brand button & link" viewport="700x140"
 */
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** Visual style. Default "primary". */
  variant?: 'primary' | 'secondary' | 'soft' | 'link';
  /** Padding/scale. Default "md". `lg` matches the hero/booking CTAs. */
  size?: 'sm' | 'md' | 'lg';
  /** Heroicon name rendered before the label. */
  iconLeading?: string;
  /** Heroicon name rendered after the label. */
  iconTrailing?: string;
  /** Append a trailing → arrow (the marketing link convention). */
  arrow?: boolean;
  /** Render as an <a> instead of <button>. */
  href?: string;
  disabled?: boolean;
}

export function Button(props: ButtonProps): JSX.Element;
export default Button;
