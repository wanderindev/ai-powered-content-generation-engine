import * as React from 'react';

/**
 * Square brand-checked checkbox with an inline label and optional description,
 * as used for the transport opt-in and contact-interest lists.
 *
 * @startingPoint section="Forms" subtitle="Checkbox + label" viewport="480x120"
 */
export interface CheckboxProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label: React.ReactNode;
  /** Secondary line under the label. */
  description?: React.ReactNode;
}

export function Checkbox(props: CheckboxProps): JSX.Element;
export default Checkbox;
