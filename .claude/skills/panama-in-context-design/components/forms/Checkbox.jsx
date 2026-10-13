import React from 'react';

/* Panama In Context — Checkbox
   Square brand-checked control + inline label, as used for transport
   opt-in and contact interests. */

export function Checkbox({ label, id, checked, defaultChecked, description, className, style, ...rest }) {
  const autoId = React.useId();
  const fieldId = id || autoId;
  return (
    <div className={className} style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-2)', fontFamily: 'var(--font-sans)', ...style }}>
      <input
        id={fieldId}
        type="checkbox"
        checked={checked}
        defaultChecked={defaultChecked}
        style={{
          width: '1rem',
          height: '1rem',
          marginTop: '0.15rem',
          flex: 'none',
          accentColor: 'var(--color-brand)',
          borderRadius: 'var(--radius-sm)',
          cursor: 'pointer',
        }}
        {...rest}
      />
      <label htmlFor={fieldId} style={{ cursor: 'pointer' }}>
        <span style={{ display: 'block', fontSize: 'var(--text-sm)', color: 'var(--text-heading)' }}>{label}</span>
        {description ? (
          <span style={{ display: 'block', fontSize: 'var(--text-xs)', color: 'var(--text-muted)', marginTop: '0.125rem' }}>{description}</span>
        ) : null}
      </label>
    </div>
  );
}

export default Checkbox;
