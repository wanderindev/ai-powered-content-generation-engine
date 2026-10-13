import React from 'react';
import { Icon } from '../core/Icon';

/* Panama In Context — Select
   Native select with the inset-ring field treatment and a Heroicons
   chevron, matching the booking/contact forms. */

export function Select({
  label,
  hint,
  error,
  id,
  required = false,
  children,
  className,
  style,
  ...rest
}) {
  const [focus, setFocus] = React.useState(false);
  const autoId = React.useId();
  const fieldId = id || autoId;
  const ringColor = error ? 'var(--error-600)' : focus ? 'var(--focus-ring)' : 'var(--border-strong)';
  const ringWidth = focus || error ? 2 : 1;
  return (
    <div className={className} style={{ fontFamily: 'var(--font-sans)', ...style }}>
      {label ? (
        <label htmlFor={fieldId} style={{ display: 'block', fontSize: 'var(--text-sm)', fontWeight: 'var(--weight-medium)', color: 'var(--text-body)', marginBottom: 'var(--space-2)' }}>
          {label}{required ? <span style={{ color: 'var(--error-600)' }}> *</span> : null}
        </label>
      ) : null}
      <div style={{ position: 'relative' }}>
        <select
          id={fieldId}
          required={required}
          onFocus={() => setFocus(true)}
          onBlur={() => setFocus(false)}
          style={{
            width: '100%',
            boxSizing: 'border-box',
            appearance: 'none',
            WebkitAppearance: 'none',
            fontFamily: 'inherit',
            fontSize: 'var(--text-sm)',
            color: 'var(--text-heading)',
            background: 'var(--surface)',
            border: 'none',
            borderRadius: 'var(--radius-md)',
            padding: '0.625rem 2.25rem 0.625rem 0.75rem',
            boxShadow: `inset 0 0 0 ${ringWidth}px ${ringColor}`,
            outline: 'none',
            cursor: 'pointer',
          }}
          {...rest}
        >
          {children}
        </select>
        <span style={{ position: 'absolute', right: '0.625rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-faint)', pointerEvents: 'none', display: 'inline-flex' }}>
          <Icon name="chevron-up-down-solid" size={18} />
        </span>
      </div>
      {error ? (
        <p style={{ margin: '0.375rem 0 0', fontSize: 'var(--text-xs)', color: 'var(--error-600)' }}>{error}</p>
      ) : hint ? (
        <p style={{ margin: '0.375rem 0 0', fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>{hint}</p>
      ) : null}
    </div>
  );
}

export default Select;
