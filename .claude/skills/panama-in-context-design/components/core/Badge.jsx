import React from 'react';

/* Panama In Context — Badge / chip / tag
   Rounded-full pill used for article taxonomies, categories, tags,
   and small status markers. */

const variants = {
  brand:   { background: 'var(--color-brand-soft)', color: 'var(--color-brand)' },
  neutral: { background: 'var(--surface-subtle)',   color: 'var(--text-secondary)' },
  outline: { background: 'var(--surface-muted)',    color: 'var(--text-muted)', boxShadow: 'inset 0 0 0 1px var(--border)' },
  success: { background: 'var(--success-50)',       color: 'var(--success-700)' },
  error:   { background: 'var(--error-50)',         color: 'var(--error-700)' },
  warning: { background: 'var(--warning-50)',       color: 'var(--warning-600)' },
};

const sizes = {
  sm: { fontSize: '0.6875rem', padding: '0.125rem 0.5rem' },
  md: { fontSize: 'var(--text-xs)', padding: '0.375rem 0.75rem' },
};

export function Badge({ children, variant = 'brand', size = 'md', dot = false, className, style, ...rest }) {
  return (
    <span
      className={className}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.375rem',
        fontFamily: 'var(--font-sans)',
        fontWeight: 'var(--weight-medium)',
        lineHeight: 1.2,
        borderRadius: 'var(--radius-full)',
        whiteSpace: 'nowrap',
        ...sizes[size],
        ...variants[variant],
        ...style,
      }}
      {...rest}
    >
      {dot ? (
        <span style={{ width: '0.375rem', height: '0.375rem', borderRadius: 'var(--radius-full)', background: 'currentColor', flex: 'none' }} />
      ) : null}
      {children}
    </span>
  );
}

export default Badge;
