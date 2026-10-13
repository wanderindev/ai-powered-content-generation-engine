/* Panama In Context — Website content sections.
   Schools, Tourists, Meet-the-Guide, Featured Articles, CTA. */
const DS = window.PanamaInContextDesignSystem_0dba89;

function Section({ children, dark, style }) {
  return (
    <section style={{ background: dark ? 'var(--surface-dark)' : 'var(--surface)', borderTop: dark ? 'none' : '1px solid var(--border)', padding: '88px var(--page-gutter)', ...style }}>
      <div style={{ maxWidth: 'var(--container-max)', margin: '0 auto' }}>{children}</div>
    </section>
  );
}

function Heading({ eyebrow, title, sub, center }) {
  return (
    <div style={{ maxWidth: 'var(--container-prose)', margin: center ? '0 auto' : 0, textAlign: center ? 'center' : 'left' }}>
      {eyebrow ? <p style={{ margin: 0, fontSize: 'var(--text-base)', fontWeight: 'var(--weight-semibold)', color: 'var(--color-brand)' }}>{eyebrow}</p> : null}
      <h2 style={{ margin: '0.5rem 0 0', fontSize: 'clamp(1.875rem, 4vw, 3rem)', fontWeight: 'var(--weight-semibold)', letterSpacing: 'var(--tracking-tight)', color: 'var(--text-heading)', textWrap: 'balance' }}>{title}</h2>
      {sub ? <p style={{ margin: '1.5rem 0 0', fontSize: 'var(--text-lg)', lineHeight: 'var(--leading-7)', color: 'var(--text-secondary)' }}>{sub}</p> : null}
    </div>
  );
}

function FeatureTrio({ items }) {
  return (
    <div style={{ marginTop: '4rem', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '3rem 2rem' }}>
      {items.map((f) => (
        <div key={f.title}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
            <DS.Icon name={f.icon} size={22} color="var(--color-brand)" />
            <h3 style={{ margin: 0, fontSize: 'var(--text-base)', fontWeight: 'var(--weight-semibold)', color: 'var(--text-heading)' }}>{f.title}</h3>
          </div>
          <p style={{ margin: '1rem 0 0', fontSize: 'var(--text-base)', lineHeight: 'var(--leading-7)', color: 'var(--text-secondary)' }}>{f.desc}</p>
        </div>
      ))}
    </div>
  );
}

function SchoolsSection({ lang, onNavigate }) {
  const t = {
    en: { eyebrow: 'For Educators', title: 'Transforming Field Trips Into Learning Experiences', sub: "Move beyond the standard field trip. Our expert-guided excursions bring Panama's history to life, creating memorable educational experiences students carry long after the classroom.", cta: 'Learn More', contact: 'Contact Us',
      f: [
        { icon: 'academic-cap', title: 'Expert-Led Experiences', desc: "A professional guide, expert in Panamanian history, leads students through Panama's most significant sites with engaging, age-appropriate narratives." },
        { icon: 'book-open', title: 'Curriculum-Aligned Content', desc: 'Each excursion includes educational materials that connect to your existing curriculum.' },
        { icon: 'user-group', title: 'Adaptable for All Grades', desc: 'Content is tailored to different grade levels, ensuring meaningful engagement for every student.' },
      ] },
    es: { eyebrow: 'Para Educadores', title: 'Transformamos las Excursiones en Experiencias de Aprendizaje', sub: 'Vaya más allá de la excursión tradicional. Nuestras experiencias guiadas por expertos dan vida a la historia de Panamá, creando aprendizajes memorables.', cta: 'Conocer Más', contact: 'Contáctenos',
      f: [
        { icon: 'academic-cap', title: 'Guiado por Expertos', desc: 'Un guía profesional, experto en historia panameña, conduce a los estudiantes por los sitios más importantes con narrativas adecuadas a su edad.' },
        { icon: 'book-open', title: 'Contenido Curricular', desc: 'Cada excursión incluye materiales educativos que se conectan con su currículo.' },
        { icon: 'user-group', title: 'Para Todos los Grados', desc: 'El contenido se adapta a cada nivel, garantizando una participación significativa.' },
      ] },
  }[lang];
  return (
    <Section>
      <Heading center eyebrow={t.eyebrow} title={t.title} sub={t.sub} />
      <FeatureTrio items={t.f} />
      <div style={{ marginTop: '4rem', display: 'flex', gap: 'var(--space-6)', justifyContent: 'center', alignItems: 'center', flexWrap: 'wrap' }}>
        <DS.Button variant="primary" onClick={() => onNavigate('excursions')}>{t.cta}</DS.Button>
        <DS.Button variant="link" arrow onClick={() => onNavigate('contact')}>{t.contact}</DS.Button>
      </div>
    </Section>
  );
}

function TouristsSection({ lang, onNavigate }) {
  const t = {
    en: { eyebrow: 'For Travelers', title: 'Experience Panama Beyond the Surface', sub: 'Discover the real Panama through the eyes of a local expert. Our private guided tours reveal the stories, hidden gems, and cultural richness that make this country unique.', cta: 'Explore Tours',
      f: [
        { icon: 'user', title: 'Private Guided Experiences', desc: 'Personalized attention with private tours led by an expert guide who brings every destination to life.' },
        { icon: 'globe-americas', title: 'Deep Cultural Context', desc: 'Go beyond surface-level tourism. Understand the stories, traditions, and significance behind what you see.' },
        { icon: 'calendar-days', title: 'Personalized Itineraries', desc: 'Tours tailored to your interests, pace, and schedule — whether a few hours or several days.' },
      ] },
    es: { eyebrow: 'Para Viajeros', title: 'Viva Panamá Más Allá de la Superficie', sub: 'Descubra el verdadero Panamá a través de los ojos de un experto local. Nuestros tours privados revelan las historias y la riqueza cultural que hacen único a este país.', cta: 'Explorar Tours',
      f: [
        { icon: 'user', title: 'Experiencias Privadas', desc: 'Atención personalizada con tours privados guiados por un experto que da vida a cada destino.' },
        { icon: 'globe-americas', title: 'Contexto Cultural Profundo', desc: 'Vaya más allá del turismo superficial. Comprenda las historias y tradiciones detrás de lo que ve.' },
        { icon: 'calendar-days', title: 'Itinerarios a su Medida', desc: 'Tours adaptados a sus intereses, ritmo y horario, ya sean unas horas o varios días.' },
      ] },
  }[lang];
  return (
    <Section>
      <Heading center eyebrow={t.eyebrow} title={t.title} sub={t.sub} />
      <FeatureTrio items={t.f} />
      <div style={{ marginTop: '4rem', textAlign: 'center' }}>
        <DS.Button variant="primary" onClick={() => onNavigate('tours')}>{t.cta}</DS.Button>
      </div>
    </Section>
  );
}

function GuideSection({ lang, onNavigate }) {
  const t = {
    en: { eyebrow: 'Your Guide', title: 'Meet Diego Raúl Feliu',
      b1: "Diego Raúl Feliu holds a degree in Historical Tourism and has over a decade of experience bringing Panama's rich heritage to life. Born and raised in Panama City, he is a member of the Asociación Panameña de Antropología e Historia.",
      b2: 'Whether leading school groups through the ruins of Panamá Viejo or guiding tourists through Casco Antiguo, Diego combines deep knowledge with a natural gift for storytelling.',
      cta: 'Get in Touch' },
    es: { eyebrow: 'Su Guía', title: 'Conozca a Diego Raúl Feliu',
      b1: 'Diego Raúl Feliu es licenciado en Turismo Histórico y cuenta con más de una década de experiencia dando vida al patrimonio de Panamá. Nacido y criado en la Ciudad de Panamá, es miembro de la Asociación Panameña de Antropología e Historia.',
      b2: 'Ya sea guiando grupos escolares por las ruinas de Panamá Viejo o turistas por el Casco Antiguo, Diego combina un profundo conocimiento con un don natural para narrar.',
      cta: 'Contáctelo' },
  }[lang];
  return (
    <Section>
      <Heading center title={t.title} eyebrow={t.eyebrow} />
      <div style={{ marginTop: '3rem', display: 'flex', gap: '3rem', alignItems: 'center', flexWrap: 'wrap', justifyContent: 'center', maxWidth: '56rem', marginLeft: 'auto', marginRight: 'auto' }}>
        <img src="../../assets/images/foto-diego-feliu.avif" alt="Diego Raúl Feliu" style={{ width: '220px', height: '220px', borderRadius: 'var(--radius-full)', objectFit: 'cover', boxShadow: 'var(--shadow-lg)', flex: 'none' }} />
        <div style={{ flex: 1, minWidth: '280px' }}>
          <p style={{ margin: 0, fontSize: 'var(--text-lg)', lineHeight: 'var(--leading-7)', color: 'var(--text-secondary)' }}>{t.b1}</p>
          <p style={{ margin: '1rem 0 0', fontSize: 'var(--text-lg)', lineHeight: 'var(--leading-7)', color: 'var(--text-secondary)' }}>{t.b2}</p>
          <div style={{ marginTop: '2rem' }}><DS.Button variant="link" arrow onClick={() => onNavigate('contact')}>{t.cta}</DS.Button></div>
        </div>
      </div>
    </Section>
  );
}

function ArticlesSection({ lang }) {
  const t = {
    en: { eyebrow: 'From the Blog', title: "Stories from Panama's Past", sub: "Deep dives into the events, people, and places that shaped Panama's rich history.", cta: 'View All Articles' },
    es: { eyebrow: 'Desde el Blog', title: 'Historias del Pasado de Panamá', sub: 'Análisis profundos de los eventos, personas y lugares que forjaron la rica historia de Panamá.', cta: 'Ver Todos los Artículos' },
  }[lang];
  const articles = [
    { img: '../../assets/images/panama-viejo-skyline.jpg', cat: lang === 'en' ? 'History' : 'Historia', title: lang === 'en' ? 'The Three Panamas: Understanding the Colonial History' : 'Los Tres Panamá: La Historia Colonial', desc: lang === 'en' ? 'From Panamá Viejo to Casco Antiguo to the modern city — how three urban centers tell 500 years of history.' : 'De Panamá Viejo al Casco Antiguo y la ciudad moderna: tres centros urbanos cuentan 500 años de historia.' },
    { img: '../../assets/images/museo-del-canal-exterior.jpg', cat: 'Canal', title: lang === 'en' ? 'The Panama Canal: Engineering Marvel and Global Impact' : 'El Canal de Panamá: Maravilla de Ingeniería', desc: lang === 'en' ? "How the Big Ditch transformed global trade and shaped Panama's national identity." : 'Cómo la gran zanja transformó el comercio mundial y forjó la identidad nacional.' },
  ];
  return (
    <Section>
      <Heading center eyebrow={t.eyebrow} title={t.title} sub={t.sub} />
      <div style={{ marginTop: '3.5rem', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '2.5rem' }}>
        {articles.map((a) => (
          <DS.Card key={a.title} image={a.img} aspect="3 / 2"
            badges={[{ label: a.cat, variant: 'brand' }, { label: lang === 'en' ? 'Diego R. Feliu' : 'Diego R. Feliu', variant: 'neutral' }]}
            title={a.title} description={a.desc} href="#blog"
            footer={<DS.Button variant="link" arrow>{lang === 'en' ? 'Read more' : 'Leer más'}</DS.Button>} />
        ))}
      </div>
      <div style={{ marginTop: '3rem', textAlign: 'center' }}>
        <DS.Button variant="link" arrow>{t.cta}</DS.Button>
      </div>
    </Section>
  );
}

function CTASection({ lang, onNavigate }) {
  const t = {
    en: { title: 'Ready to Discover Panama?', sub: "Whether you're planning a school field trip or looking for an authentic travel experience, we're here to help you explore Panama's rich history and culture.", contact: 'Contact Us', schools: 'For Schools' },
    es: { title: '¿Listo para Descubrir Panamá?', sub: 'Ya sea planeando una excursión escolar o buscando una experiencia de viaje auténtica, estamos aquí para ayudarle a explorar la historia y cultura de Panamá.', contact: 'Contáctenos', schools: 'Para Escuelas' },
  }[lang];
  return (
    <Section dark>
      <div style={{ textAlign: 'center', maxWidth: 'var(--container-prose)', margin: '0 auto' }}>
        <h2 style={{ margin: 0, color: '#fff', fontSize: 'clamp(1.875rem, 4vw, 3rem)', fontWeight: 'var(--weight-semibold)', letterSpacing: 'var(--tracking-tight)' }}>{t.title}</h2>
        <p style={{ margin: '1.5rem 0 0', color: 'var(--text-on-dark)', fontSize: 'var(--text-lg)', lineHeight: 'var(--leading-7)' }}>{t.sub}</p>
        <div style={{ marginTop: '2.5rem', display: 'flex', gap: 'var(--space-6)', justifyContent: 'center', flexWrap: 'wrap' }}>
          <DS.Button variant="primary" onClick={() => onNavigate('contact')}>{t.contact}</DS.Button>
          <DS.Button variant="secondary" onClick={() => onNavigate('excursions')}>{t.schools}</DS.Button>
        </div>
      </div>
    </Section>
  );
}

window.PicSections = { SchoolsSection, TouristsSection, GuideSection, ArticlesSection, CTASection };
