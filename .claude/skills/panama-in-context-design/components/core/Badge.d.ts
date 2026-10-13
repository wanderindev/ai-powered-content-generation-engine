import * as React from 'react';

/**
 * Rounded-full pill for article taxonomies, categories, tags and small
 * status markers. `brand` and `neutral` cover the marketing chips;
 * `outline` is the faint tag style; success/error/warning use status colors.
 *
 * @startingPoint section="Core" subtitle="Pill / chip / tag" viewport="700x140"
 */
export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Default "brand". */
  variant?: 'brand' | 'neutral' | 'outline' | 'success' | 'error' | 'warning';
  /** Default "md". */
  size?: 'sm' | 'md';
  /** Show a leading status dot. */
  dot?: boolean;
}

export function Badge(props: BadgeProps): JSX.Element;
export default Badge;
