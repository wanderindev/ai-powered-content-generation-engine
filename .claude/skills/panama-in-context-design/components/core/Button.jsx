import React from 'react';
import { Icon } from './Icon';

/* Panama In Context — Button
   Primary = solid brand; secondary = neutral outline; soft = brand-tint;
   link = inline text action (often with a trailing arrow). */

const base = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 'var(--space-2)',
  fontFamily: 'var(--font-sans)',
  fontWeight: 'var(--weight-semibold)',
  lineHeight: 1,
  borderRadius: 'var(--radius-md)',
  border: '1px solid transparent',
  cursor: 'pointer',
  textDecoration: 'none',
  whiteSpace: 'nowrap',
  transition: 'var(--transition-colors)',
  WebkitTapHighlightColor: 'transparent',
};

const sizes = {
  sm: { fontSize: 'var(--text-sm)', padding: '0.5rem 0.875rem' },
  md: { fontSize: 'var(--text-sm)', padding: '0.625rem 0.875rem' },
  lg: { fontSize: 'var(--text-base)', padding: '0.75rem 1.25rem', fontWeight: 'var(--weight-medium)' },
};

const variants = {
  primary: {
    background: 'var(--color-brand)',
    color: 'var(--text-on-brand)',
    boxShadow: 'var(--elevation-button)',
  },
  secondary: {
    background: 'var(--surface)',
    color: 'var(--text-heading)',
    borderColor: 'var(--border-strong)',
    boxShadow: 'var(--shadow-xs)',
  },
  soft: {
    background: 'var(--color-brand-soft)',
    color: 'var(--color-brand)',
  },
  link: {
    background: 'transparent',
    color: 'var(--color-brand)',
    padding: 0,
    boxShadow: 'none',
  },
};

const hoverBg = {
  primary: 'var(--color-brand-hover)',
  secondary: 'var(--surface-muted)',
  soft: 'var(--brand-100)',
  link: 'transparent',
};

export function Button({
  children,
  variant = 'primary',
  size = 'md',
  iconLeading,
  iconTrailing,
  arrow = false,
  disabled = false,
  href,
  type = 'button',
  className,
  style,
  ...rest
}) {
  const [hover, setHover] = React.useState(false);
  const isLink = variant === 'link';
  const composed = {
    ...base,
    ...(isLink ? {} : sizes[size]),
    ...variants[variant],
    ...(hover && !disabled ? { background: hoverBg[variant], ...(isLink ? { color: 'var(--text-link-hover)' } : {}) } : {}),
    ...(disabled ? { opacity: 0.5, cursor: 'not-allowed', boxShadow: 'none' } : {}),
    ...style,
  };
  const iconPx = size === 'lg' ? 20 : 18;
  const content = (
    <>
      {iconLeading ? <Icon name={iconLeading} size={iconPx} /> : null}
      {children}
      {iconTrailing ? <Icon name={iconTrailing} size={iconPx} /> : null}
      {arrow ? <span aria-hidden="true" style={{ marginLeft: '0.125rem' }}>&rarr;</span> : null}
    </>
  );
  const handlers = {
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    className,
    style: composed,
  };
  if (href && !disabled) {
    return <a href={href} {...handlers} {...rest}>{content}</a>;
  }
  return (
    <button type={type} disabled={disabled} {...handlers} {...rest}>{content}</button>
  );
}

export default Button;
