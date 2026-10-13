import * as React from 'react';

/**
 * Heroicons glyph, sized in px and inheriting `currentColor`.
 * Mirrors the product's @heroicons/react usage. Use outline names
 * for UI controls and `<name>-solid` for inline accents/checklists.
 *
 * @startingPoint section="Core" subtitle="Heroicons glyph" viewport="700x150"
 */
export interface IconProps extends React.SVGProps<SVGSVGElement> {
  /** Heroicon name, e.g. "academic-cap" or "check-circle-solid". */
  name: string;
  /** Pixel size (number) or any CSS length. Default 24. */
  size?: number | string;
  /** Convenience color override (maps to currentColor). */
  color?: string;
  /** Override the outline stroke width (default 1.5). */
  strokeWidth?: number;
}

export function Icon(props: IconProps): JSX.Element;
export const ICON_NAMES: string[];
export default Icon;
