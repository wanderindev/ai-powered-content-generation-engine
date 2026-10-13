import React from 'react';
import { Badge } from './Badge';

/* Panama In Context — Card
   Image-topped content card used for articles, tours and excursions.
   Photo sits in a rounded-2xl frame with a hairline inset ring; the
   whole card is clickable when `href` is set. */

export function Card({
  image,
  imageAlt = '',
  badges = [],
  title,
  description,
  href,
  meta,
  footer,
  aspect = '3 / 2',
  className,
  style,
  ...rest
}) {
  const [hover, setHover] = React.useState(false);
  const TitleTag = href ? 'a' : 'div';
  return (
    <article
      className={className}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex',
        flexDirection: 'column',
        fontFamily: 'var(--font-sans)',
        ...style,
      }}
      {...rest}
    >
      {image ? (
        <div style={{ position: 'relative', width: '100%', marginBottom: 'var(--space-6)' }}>
          <img
            src={image}
            alt={imageAlt}
            style={{
              display: 'block',
              width: '100%',
              aspectRatio: aspect,
              objectFit: 'cover',
              borderRadius: 'var(--radius-2xl)',
              background: 'var(--surface-subtle)',
            }}
          />
          <span style={{ position: 'absolute', inset: 0, borderRadius: 'var(--radius-2xl)', boxShadow: 'var(--ring-inset-imagery)' }} />
        </div>
      ) : null}

      {badges.length ? (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)', marginBottom: 'var(--space-3)' }}>
          {badges.map((b, i) => (
            <Badge key={i} variant={b.variant || 'brand'} size="sm">{b.label}</Badge>
          ))}
        </div>
      ) : null}

      <TitleTag
        href={href}
        style={{
          fontSize: 'var(--text-lg)',
          fontWeight: 'var(--weight-semibold)',
          color: hover && href ? 'var(--text-secondary)' : 'var(--text-heading)',
          textDecoration: 'none',
          lineHeight: 'var(--leading-snug)',
          transition: 'var(--transition-colors)',
        }}
      >
        {title}
      </TitleTag>

      {description ? (
        <p style={{ margin: 'var(--space-3) 0 0', fontSize: 'var(--text-sm)', lineHeight: 'var(--leading-relaxed)', color: 'var(--text-secondary)' }}>
          {description}
        </p>
      ) : null}

      {meta ? (
        <div style={{ marginTop: 'var(--space-3)', fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>{meta}</div>
      ) : null}

      {footer ? <div style={{ marginTop: 'var(--space-4)' }}>{footer}</div> : null}
    </article>
  );
}

export default Card;
