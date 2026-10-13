import * as React from 'react';

/**
 * Native <select> with the inset-ring field treatment and a Heroicons
 * chevron, matching the booking and contact forms. Pass <option>s as children.
 *
 * @startingPoint section="Forms" subtitle="Dropdown select" viewport="480x150"
 */
export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label?: React.ReactNode;
  hint?: React.ReactNode;
  error?: React.ReactNode;
  required?: boolean;
}

export function Select(props: SelectProps): JSX.Element;
export default Select;
