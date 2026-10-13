/* Panama In Context — Website inner pages & flows:
   Tours grid · Booking modal · Cart drawer · Contact form · Blog list. */
const P = window.PanamaInContextDesignSystem_0dba89;

function PageHead({ eyebrow, title, sub }) {
  return (
    <div style={{ maxWidth: 'var(--container-prose)', margin: '0 auto', textAlign: 'center' }}>
      {eyebrow ? <p style={{ margin: 0, fontSize: 'var(--text-base)', fontWeight: 'var(--weight-semibold)', color: 'var(--color-brand)' }}>{eyebrow}</p> : null}
      <h1 style={{ margin: '0.5rem 0 0', fontSize: 'clamp(2rem, 4vw, 3rem)', fontWeight: 'var(--weight-semibold)', letterSpacing: 'var(--tracking-tight)', color: 'var(--text-heading)' }}>{title}</h1>
      {sub ? <p style={{ margin: '1.25rem 0 0', fontSize: 'var(--text-lg)', lineHeight: 'var(--leading-7)', color: 'var(--text-secondary)' }}>{sub}</p> : null}
    </div>
  );
}

const TOURS = [
  { slug: 'canal', img: '../../assets/images/miraflores.jpg', price: 49, title: { en: 'Panama Canal: Miraflores Locks', es: 'Canal de Panamá: Esclusas de Miraflores' }, dur: '3h', cat: 'Canal', desc: { en: 'Watch massive ships navigate the historic locks — an engineering feat that changed global trade.', es: 'Vea grandes buques navegar las esclusas históricas, una hazaña de ingeniería que cambió el comercio mundial.' } },
  { slug: 'casco-plazas', img: '../../assets/images/catedral-interior.jpg', price: 45, title: { en: 'Casco Antiguo: Plazas & Churches', es: 'Casco Antiguo: Plazas e Iglesias' }, dur: '3h', cat: 'UNESCO', desc: { en: 'Colonial plazas and historic churches reveal Panama\u2019s journey from Spanish colony to republic.', es: 'Plazas coloniales e iglesias históricas revelan el camino de Panamá de colonia a república.' } },
  { slug: 'casco-museums', img: '../../assets/images/museo-del-canal-exterior.jpg', price: 55, title: { en: 'Casco Antiguo: Museum Circuit', es: 'Casco Antiguo: Circuito de Museos' }, dur: '4h', cat: { en: 'Museums', es: 'Museos' }, desc: { en: 'Trace Panama\u2019s evolution through curated museum visits, from indigenous artistry to the Canal.', es: 'Recorra la evolución de Panamá a través de museos, del arte indígena al Canal.' } },
  { slug: 'panama-viejo', img: '../../assets/images/panama-viejo-torre-2x3.jpg', price: 45, title: { en: 'Panama Viejo: Archaeological Site', es: 'Panamá Viejo: Sitio Arqueológico' }, dur: '2.5h', cat: { en: 'History', es: 'Historia' }, desc: { en: 'Walk the excavated streets of the first European city on the Pacific, founded 1519.', es: 'Camine por las calles excavadas de la primera ciudad europea del Pacífico, fundada en 1519.' } },
  { slug: 'mi-pueblito', img: '../../assets/images/mi-pueblito-mural.jpg', price: 40, title: { en: 'Mi Pueblito: Cultural Heritage', es: 'Mi Pueblito: Patrimonio Cultural' }, dur: '2.5h', cat: { en: 'Culture', es: 'Cultura' }, desc: { en: 'Experience the three foundational roots of Panama at this living open-air museum.', es: 'Viva las tres raíces fundacionales de Panamá en este museo vivo al aire libre.' } },
  { slug: 'biomuseo', img: '../../assets/images/biomuseo.jpg', price: 50, title: { en: 'Biomuseo: Museum of Biodiversity', es: 'Biomuseo: Museo de la Biodiversidad' }, dur: '2h', cat: { en: 'Nature', es: 'Naturaleza' }, desc: { en: 'Inside Frank Gehry\u2019s masterpiece, discover how Panama changed Earth\u2019s biology.', es: 'Dentro de la obra de Frank Gehry, descubra cómo Panamá cambió la biología de la Tierra.' } },
];

function ToursPage({ lang, onReserve }) {
  const tx = (v) => (typeof v === 'object' ? v[lang] : v);
  return (
    <div style={{ padding: '72px var(--page-gutter)', maxWidth: 'var(--container-max)', margin: '0 auto' }}>
      <PageHead eyebrow={lang === 'en' ? 'Our Tours' : 'Nuestros Tours'} title={lang === 'en' ? 'Where Would You Like to Go?' : '¿A Dónde le Gustaría Ir?'} sub={lang === 'en' ? 'Private guided tours led by an expert in Panamanian history.' : 'Tours privados guiados por un experto en historia panameña.'} />
      <div style={{ marginTop: '3.5rem', display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '2.5rem' }}>
        {TOURS.map((t) => (
          <P.Card key={t.slug} image={t.img} aspect="3 / 2"
            badges={[{ label: tx(t.cat), variant: 'brand' }, { label: t.dur, variant: 'neutral' }]}
            title={tx(t.title)} description={tx(t.desc)}
            meta={<span style={{ fontWeight: 'var(--weight-semibold)', color: 'var(--text-heading)' }}>{lang === 'en' ? 'From' : 'Desde'} ${t.price} <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>/ {lang === 'en' ? 'person' : 'persona'}</span></span>}
            footer={<P.Button variant="soft" size="sm" iconLeading="calendar-days" onClick={() => onReserve(t)}>{lang === 'en' ? 'Reserve' : 'Reservar'}</P.Button>} />
        ))}
      </div>
    </div>
  );
}

function BookingModal({ lang, tour, onClose, onAdd }) {
  const [adults, setAdults] = React.useState(2);
  const [date, setDate] = React.useState('');
  const [time, setTime] = React.useState('morning');
  if (!tour) return null;
  const tx = (v) => (typeof v === 'object' ? v[lang] : v);
  const total = adults * tour.price;
  const valid = date && adults > 0;
  const Stepper = () => (
    <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
      <button onClick={() => setAdults((a) => Math.max(1, a - 1))} style={stepBtn}><P.Icon name="minus-solid" size={18} /></button>
      <span style={{ fontSize: 'var(--text-lg)', fontWeight: 'var(--weight-semibold)', minWidth: '2ch', textAlign: 'center' }}>{adults}</span>
      <button onClick={() => setAdults((a) => Math.min(15, a + 1))} style={stepBtn}><P.Icon name="plus-solid" size={18} /></button>
    </div>
  );
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 60, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '1rem' }}>
      <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(17,24,39,0.6)' }} />
      <div style={{ position: 'relative', width: '100%', maxWidth: '640px', background: 'var(--surface)', borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow-xl)', overflow: 'hidden', display: 'grid', gridTemplateColumns: '0.8fr 1fr' }}>
        <div style={{ background: 'var(--surface-muted)', padding: 'var(--space-6)' }}>
          <img src={tour.img} alt="" style={{ width: '100%', aspectRatio: '3/2', objectFit: 'cover', borderRadius: 'var(--radius-lg)' }} />
          <h3 style={{ margin: '1rem 0 0', fontSize: 'var(--text-lg)', fontWeight: 'var(--weight-bold)', color: 'var(--text-heading)' }}>{tx(tour.title)}</h3>
          <p style={{ margin: '0.5rem 0 0', fontSize: 'var(--text-sm)', color: 'var(--text-muted)' }}>{tx(tour.desc)}</p>
        </div>
        <div style={{ padding: 'var(--space-6)', position: 'relative' }}>
          <button onClick={onClose} aria-label="Close" style={{ position: 'absolute', top: '1rem', right: '1rem', background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-faint)' }}><P.Icon name="x-mark" size={22} /></button>
          <h3 style={{ margin: '0 0 1.5rem', fontSize: 'var(--text-base)', fontWeight: 'var(--weight-semibold)', color: 'var(--text-heading)' }}>{lang === 'en' ? 'Reserve Your Spot' : 'Reserve su Cupo'}</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
            <P.Input label={lang === 'en' ? 'Select date' : 'Seleccione fecha'} type="date" value={date} onChange={(e) => setDate(e.target.value)} icon="calendar-days" required />
            <P.Select label={lang === 'en' ? 'Time slot' : 'Horario'} value={time} onChange={(e) => setTime(e.target.value)}>
              <option value="morning">{lang === 'en' ? 'Morning · 9:00 AM' : 'Mañana · 9:00 AM'}</option>
              <option value="afternoon">{lang === 'en' ? 'Afternoon · 2:00 PM' : 'Tarde · 2:00 PM'}</option>
            </P.Select>
            <div>
              <label style={{ display: 'block', fontSize: 'var(--text-sm)', fontWeight: 'var(--weight-medium)', color: 'var(--text-body)', marginBottom: 'var(--space-2)' }}>{lang === 'en' ? 'Participants' : 'Participantes'}</label>
              <Stepper />
            </div>
          </div>
          <div style={{ marginTop: '1.5rem', paddingTop: '1.5rem', borderTop: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div><span style={{ fontSize: 'var(--text-sm)', color: 'var(--text-muted)' }}>{lang === 'en' ? 'Total' : 'Total'}</span><div style={{ fontSize: 'var(--text-2xl)', fontWeight: 'var(--weight-bold)', color: 'var(--text-heading)' }}>${total}</div></div>
            <P.Button variant="primary" size="lg" disabled={!valid} iconLeading="shopping-bag" onClick={() => onAdd({ tour, adults, date, time, total })}>{lang === 'en' ? 'Add to Cart' : 'Agregar'}</P.Button>
          </div>
        </div>
      </div>
    </div>
  );
}
const stepBtn = { width: '36px', height: '36px', borderRadius: 'var(--radius-full)', border: '1px solid var(--border-strong)', background: 'var(--surface)', color: 'var(--text-heading)', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' };

function CartDrawer({ lang, open, items, onClose, onRemove }) {
  const tx = (v) => (typeof v === 'object' ? v[lang] : v);
  const subtotal = items.reduce((s, i) => s + i.total, 0);
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 70, pointerEvents: open ? 'auto' : 'none' }}>
      <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(17,24,39,0.5)', opacity: open ? 1 : 0, transition: 'opacity .3s' }} />
      <div style={{ position: 'absolute', top: 0, right: 0, bottom: 0, width: '420px', maxWidth: '92%', background: 'var(--surface)', boxShadow: 'var(--shadow-xl)', transform: open ? 'translateX(0)' : 'translateX(100%)', transition: 'transform .3s var(--ease-standard)', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: 'var(--space-6)', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ margin: 0, fontSize: 'var(--text-lg)', fontWeight: 'var(--weight-semibold)' }}>{lang === 'en' ? 'Your Cart' : 'Su Carrito'}</h3>
          <button onClick={onClose} aria-label="Close" style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-faint)' }}><P.Icon name="x-mark" size={22} /></button>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--space-6)' }}>
          {items.length === 0 ? (
            <p style={{ color: 'var(--text-muted)', fontSize: 'var(--text-sm)', textAlign: 'center', marginTop: '3rem' }}>{lang === 'en' ? 'Your cart is empty.' : 'Su carrito está vacío.'}</p>
          ) : items.map((it, i) => (
            <div key={i} style={{ display: 'flex', gap: '1rem', paddingBottom: '1rem', marginBottom: '1rem', borderBottom: '1px solid var(--border)' }}>
              <img src={it.tour.img} alt="" style={{ width: '64px', height: '64px', objectFit: 'cover', borderRadius: 'var(--radius-md)', flex: 'none' }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <p style={{ margin: 0, fontSize: 'var(--text-sm)', fontWeight: 'var(--weight-semibold)', color: 'var(--text-heading)' }}>{tx(it.tour.title)}</p>
                <p style={{ margin: '0.25rem 0 0', fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>{it.date} · {it.adults} {lang === 'en' ? 'pax' : 'pax'}</p>
                <button onClick={() => onRemove(i)} style={{ marginTop: '0.25rem', background: 'transparent', border: 'none', color: 'var(--error-600)', fontSize: 'var(--text-xs)', cursor: 'pointer', padding: 0 }}>{lang === 'en' ? 'Remove' : 'Quitar'}</button>
              </div>
              <span style={{ fontWeight: 'var(--weight-semibold)', color: 'var(--text-heading)' }}>${it.total}</span>
            </div>
          ))}
        </div>
        <div style={{ padding: 'var(--space-6)', borderTop: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '1rem' }}>
            <span style={{ color: 'var(--text-secondary)' }}>{lang === 'en' ? 'Subtotal' : 'Subtotal'}</span>
            <span style={{ fontWeight: 'var(--weight-bold)', fontSize: 'var(--text-lg)' }}>${subtotal}</span>
          </div>
          <P.Button variant="primary" size="lg" disabled={items.length === 0} style={{ width: '100%' }}>{lang === 'en' ? 'Go to Checkout' : 'Ir a Pagar'}</P.Button>
        </div>
      </div>
    </div>
  );
}

function ContactPage({ lang }) {
  const [sent, setSent] = React.useState(false);
  return (
    <div style={{ padding: '72px var(--page-gutter)', maxWidth: '640px', margin: '0 auto' }}>
      <PageHead title={lang === 'en' ? 'Get in Touch' : 'Contáctenos'} sub={lang === 'en' ? "Planning a school field trip or a private tour? We're here to help." : '¿Planea una excursión escolar o un tour privado? Estamos para ayudarle.'} />
      <form onSubmit={(e) => { e.preventDefault(); setSent(true); }} style={{ marginTop: '2.5rem', display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
        {sent ? <P.Alert tone="success" title={lang === 'en' ? 'Thank you!' : '¡Gracias!'}>{lang === 'en' ? "Your message has been sent. We'll be in touch soon." : 'Su mensaje fue enviado. Le contactaremos pronto.'}</P.Alert> : null}
        <P.Select label={lang === 'en' ? 'I am interested in' : 'Estoy interesado en'} defaultValue="">
          <option value="" disabled>{lang === 'en' ? 'Select an option' : 'Seleccione una opción'}</option>
          <option value="field-trips">{lang === 'en' ? 'Academic Field Trips' : 'Excursiones Académicas'}</option>
          <option value="tours">{lang === 'en' ? 'Private Tours' : 'Tours Privados'}</option>
        </P.Select>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.25rem' }}>
          <P.Input label={lang === 'en' ? 'First name' : 'Nombre'} required />
          <P.Input label={lang === 'en' ? 'Last name' : 'Apellido'} required />
        </div>
        <P.Input label="Email" type="email" icon="envelope" required />
        <P.Checkbox label={lang === 'en' ? 'Send me educational materials and updates' : 'Envíenme materiales educativos y novedades'} defaultChecked />
        <div><P.Button variant="primary" size="lg" type="submit">{lang === 'en' ? 'Send message' : 'Enviar mensaje'}</P.Button></div>
      </form>
    </div>
  );
}

function BlogPage({ lang }) {
  const posts = [
    { img: '../../assets/images/panama-viejo-skyline.jpg', cat: lang === 'en' ? 'History' : 'Historia', title: lang === 'en' ? 'The Three Panamas: Understanding Colonial History' : 'Los Tres Panamá', desc: lang === 'en' ? 'How three distinct urban centers tell the story of 500 years of Panamanian history.' : 'Cómo tres centros urbanos cuentan 500 años de historia panameña.' },
    { img: '../../assets/images/museo-del-canal-exterior.jpg', cat: 'Canal', title: lang === 'en' ? 'The Panama Canal: Engineering Marvel' : 'El Canal de Panamá', desc: lang === 'en' ? "How the Big Ditch transformed global trade and national identity." : 'Cómo la gran zanja transformó el comercio mundial.' },
    { img: '../../assets/images/arco-chato-tour.jpg', cat: lang === 'en' ? 'Education' : 'Educación', title: lang === 'en' ? 'Teaching History Through Field Trips' : 'Enseñar Historia con Excursiones', desc: lang === 'en' ? 'Practical strategies for making historical sites come alive for students.' : 'Estrategias prácticas para dar vida a los sitios históricos.' },
  ];
  return (
    <div style={{ padding: '72px var(--page-gutter)', maxWidth: 'var(--container-max)', margin: '0 auto' }}>
      <PageHead eyebrow={lang === 'en' ? 'From the Blog' : 'Desde el Blog'} title={lang === 'en' ? "Stories from Panama's Past" : 'Historias del Pasado' } />
      <div style={{ marginTop: '3.5rem', display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '2.5rem' }}>
        {posts.map((a) => (
          <P.Card key={a.title} image={a.img} aspect="3 / 2" badges={[{ label: a.cat, variant: 'brand' }]} title={a.title} description={a.desc} href="#" footer={<P.Button variant="link" arrow>{lang === 'en' ? 'Read more' : 'Leer más'}</P.Button>} />
        ))}
      </div>
    </div>
  );
}

function SimplePage({ lang, title, sub }) {
  return (
    <div style={{ padding: '96px var(--page-gutter)', maxWidth: 'var(--container-max)', margin: '0 auto', minHeight: '40vh' }}>
      <PageHead title={title} sub={sub} />
    </div>
  );
}

window.PicPages = { ToursPage, BookingModal, CartDrawer, ContactPage, BlogPage, SimplePage, TOURS };
