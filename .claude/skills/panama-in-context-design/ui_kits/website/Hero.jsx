/* Panama In Context — Website Hero
   Dark stage with slowly crossfading heritage photography, scrim
   overlay, centered headline + dual CTA. */
const { Button: HeroButton } = window.PanamaInContextDesignSystem_0dba89;

function Hero({ lang, onNavigate }) {
  const images = [
    '../../assets/images/ship-in-locks.jpg',
    '../../assets/images/cupula-san-francisco.jpg',
    '../../assets/images/biomuseo.jpg',
    '../../assets/images/plaza-herrera.jpg',
  ];
  const [idx, setIdx] = React.useState(0);
  React.useEffect(() => {
    const t = setInterval(() => setIdx((i) => (i + 1) % images.length), 5000);
    return () => clearInterval(t);
  }, []);
  const copy = {
    en: {
      title: 'Where every site tells a story.',
      sub: "Expert-guided experiences that transform visits into meaningful journeys through Panama's rich history and vibrant culture.",
      schools: 'For Schools', tourists: 'For Tourists',
    },
    es: {
      title: 'Donde cada lugar cuenta una historia.',
      sub: 'Experiencias guiadas por expertos que transforman las visitas en viajes significativos por la rica historia y vibrante cultura de Panamá.',
      schools: 'Para Escuelas', tourists: 'Para Turistas',
    },
  }[lang];

  return (
    <section style={{ position: 'relative', background: 'var(--surface-dark)', overflow: 'hidden' }}>
      {images.map((src, i) => (
        <img key={src} src={src} alt="" style={{
          position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover',
          opacity: i === idx ? 0.55 : 0, transition: 'opacity 1s var(--ease-standard)',
        }} />
      ))}
      <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(to bottom, rgba(17,24,39,0.55), rgba(17,24,39,0.3) 45%, rgba(17,24,39,0.6))' }} />
      <div style={{ position: 'relative', maxWidth: 'var(--container-prose)', margin: '0 auto', padding: '140px var(--page-gutter)', textAlign: 'center' }}>
        <h1 style={{ margin: 0, color: '#fff', fontSize: 'clamp(2.5rem, 6vw, 4rem)', fontWeight: 'var(--weight-semibold)', letterSpacing: 'var(--tracking-tight)', lineHeight: 1.1, textWrap: 'balance' }}>
          {copy.title}
        </h1>
        <p style={{ margin: '2rem auto 0', maxWidth: '36rem', color: 'var(--text-on-dark)', fontSize: 'var(--text-xl)', lineHeight: 'var(--leading-normal)', textWrap: 'pretty' }}>
          {copy.sub}
        </p>
        <div style={{ marginTop: '2.5rem', display: 'flex', gap: 'var(--space-6)', justifyContent: 'center', alignItems: 'center', flexWrap: 'wrap' }}>
          <HeroButton variant="primary" size="lg" onClick={() => onNavigate('excursions')}>{copy.schools}</HeroButton>
          <a href="#tours" onClick={(e) => { e.preventDefault(); onNavigate('tours'); }} style={{ color: '#fff', fontWeight: 'var(--weight-semibold)', fontSize: 'var(--text-sm)', textDecoration: 'none' }}>
            {copy.tourists} <span aria-hidden="true">&rarr;</span>
          </a>
        </div>
      </div>
    </section>
  );
}

window.PicHero = Hero;
