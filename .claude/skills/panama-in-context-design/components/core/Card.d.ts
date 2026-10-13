import * as React from 'react';

/**
 * Image-topped content card for articles, tours and excursions. The photo
 * sits in a rounded-2xl frame with a hairline inset ring; pass `href` to make
 * the title a link (with hover state). Compose `badges` and a `footer` action.
 *
 * @startingPoint section="Core" subtitle="Article / tour card" viewport="420x440"
 */
export interface CardBadge {
  label: string;
  variant?: 'brand' | 'neutral' | 'outline' | 'success' | 'error' | 'warning';
}

export interface CardProps extends React.HTMLAttributes<HTMLElement> {
  /** Image URL shown in the rounded-2xl frame. */
  image?: string;
  imageAlt?: string;
  /** Pills above the title (taxonomy, category…). */
  badges?: CardBadge[];
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Link target — makes the title an anchor with hover state. */
  href?: string;
  /** Small meta line under the description (tags, duration…). */
  meta?: React.ReactNode;
  /** Footer slot — typically a <Button variant="link" arrow>. */
  footer?: React.ReactNode;
  /** CSS aspect-ratio for the image. Default "3 / 2". */
  aspect?: string;
}

export function Card(props: CardProps): JSX.Element;
export default Card;
