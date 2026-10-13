import React from 'react';
import { Icon } from '../core/Icon';

/* Panama In Context — Alert
   Inline status message for form success/error and notices. Mirrors the
   confirmation/error states (mailing list, contact, booking). */

const tones = {
  success: { bg: 'var(--success-50)', fg: 'var(--success-700)', icon: 'check-circle-solid' },
  error:   { bg: 'var(--error-50)',   fg: 'var(--error-700)',   icon: 'x-circle' },
  warning: { bg: 'var(--warning-50)', fg: 'var(--warning-600)', icon: 'exclamation-triangle' },
  info:    { bg: 'var(--color-brand-soft)', fg: 'var(--brand-800)', icon: 'information-circle-solid' },
};

export function Alert({ tone = 'info', title, children, icon, onClose, className, style, ...rest }) {
  const t = tones[tone];
  return (
    <div
      role="status"
      className={className}
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 'var(--space-3)',
        fontFamily: 'var(--font-sans)',
        background: t.bg,
        color: t.fg,
        borderRadius: 'var(--radius-lg)',
        padding: 'var(--space-4)',
        ...style,
      }}
      {...rest}
    >
      <span style={{ display: 'inline-flex', flex: 'none', marginTop: '1px' }}>
        <Icon name={icon || t.icon} size={20} />
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        {title ? <p style={{ margin: 0, fontSize: 'var(--text-sm)', fontWeight: 'var(--weight-semibold)' }}>{title}</p> : null}
        {children ? (
          <div style={{ margin: title ? '0.25rem 0 0' : 0, fontSize: 'var(--text-sm)', lineHeight: 'var(--leading-normal)', color: 'currentColor', opacity: 0.9 }}>
            {children}
          </div>
        ) : null}
      </div>
      {onClose ? (
        <button onClick={onClose} aria-label="Dismiss" style={{ flex: 'none', background: 'transparent', border: 'none', color: 'currentColor', cursor: 'pointer', padding: 2, opacity: 0.7, display: 'inline-flex' }}>
          <Icon name="x-mark" size={18} />
        </button>
      ) : null}
    </div>
  );
}

export default Alert;
