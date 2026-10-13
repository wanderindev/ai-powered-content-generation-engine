import * as React from 'react';

/**
 * Labelled text field using the product's inset-ring treatment
 * (ring-gray-300, focus → 2px brand ring). Supports a leading Heroicon,
 * helper hint, and an error state that recolors the ring + message.
 *
 * @startingPoint section="Forms" subtitle="Labelled text field" viewport="480x150"
 */
export interface InputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'children'> {
  label?: React.ReactNode;
  /** Helper text below the field (hidden when `error` is set). */
  hint?: React.ReactNode;
  /** Error message — recolors ring + text. */
  error?: React.ReactNode;
  /** Leading Heroicon name. */
  icon?: string;
  required?: boolean;
}

export function Input(props: InputProps): JSX.Element;
export default Input;
