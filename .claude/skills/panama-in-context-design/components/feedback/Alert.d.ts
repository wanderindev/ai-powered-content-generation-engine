import * as React from 'react';

/**
 * Inline status message for form success/error and notices — the
 * confirmation and error states across mailing-list, contact and booking flows.
 *
 * @startingPoint section="Feedback" subtitle="Status / notice banner" viewport="560x120"
 */
export interface AlertProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Default "info" (brand-tinted). */
  tone?: 'success' | 'error' | 'warning' | 'info';
  title?: React.ReactNode;
  /** Override the default tone icon (Heroicon name). */
  icon?: string;
  /** Show a dismiss button wired to this handler. */
  onClose?: () => void;
}

export function Alert(props: AlertProps): JSX.Element;
export default Alert;
