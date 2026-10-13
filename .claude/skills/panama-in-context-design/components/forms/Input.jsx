import React from 'react';
import { Icon } from '../core/Icon';

/* Panama In Context — Input
   Label + control + hint/error. Control uses the inset-ring pattern
   (ring-gray-300 → focus ring-2 brand) from the product forms. */

export function Input({
  label,
  hint,
  error,
  id,
  icon,
  type = 'text',
  required = false,
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
      <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
        {icon ? (
          <span style={{ position: 'absolute', left: '0.75rem', color: 'var(--text-faint)', pointerEvents: 'none', display: 'inline-flex' }}>
            <Icon name={icon} size={18} />
          </span>
        ) : null}
        <input
          id={fieldId}
          type={type}
          required={required}
          onFocus={() => setFocus(true)}
          onBlur={() => setFocus(false)}
          style={{
            width: '100%',
            boxSizing: 'border-box',
            fontFamily: 'inherit',
            fontSize: 'var(--text-sm)',
            color: 'var(--text-heading)',
            background: 'var(--surface)',
            border: 'none',
            borderRadius: 'var(--radius-md)',
            padding: '0.625rem 0.75rem',
            paddingLeft: icon ? '2.25rem' : '0.75rem',
            boxShadow: `inset 0 0 0 ${ringWidth}px ${ringColor}`,
            outline: 'none',
          }}
          {...rest}
        />
      </div>
      {error ? (
        <p style={{ margin: '0.375rem 0 0', fontSize: 'var(--text-xs)', color: 'var(--error-600)' }}>{error}</p>
      ) : hint ? (
        <p style={{ margin: '0.375rem 0 0', fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>{hint}</p>
      ) : null}
    </div>
  );
}

export default Input;
