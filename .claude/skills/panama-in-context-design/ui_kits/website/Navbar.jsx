/* Panama In Context — Website Navbar
   White bar, hex logo, centered nav, language pill + cart.
   Uses DS Icon. Exports to window for sibling babel scripts. */
const { Icon } = window.PanamaInContextDesignSystem_0dba89;

function Navbar({ lang, setLang, active, onNavigate, cartCount, onOpenCart }) {
  const [menuOpen, setMenuOpen] = React.useState(false);
  const nav = [
    { key: 'home', en: 'Home', es: 'Inicio' },
    { key: 'excursions', en: 'Academic Field Trips', es: 'Excursiones Académicas' },
    { key: 'tours', en: 'Tours', es: 'Tours' },
    { key: 'blog', en: 'Blog', es: 'Blog' },
    { key: 'materials', en: 'Educational Materials', es: 'Materiales' },
    { key: 'contact', en: 'Contact', es: 'Contacto' },
  ];
  const link = (item, mobile) => (
    <a
      key={item.key}
      href={`#${item.key}`}
      onClick={(e) => { e.preventDefault(); onNavigate(item.key); setMenuOpen(false); }}
      style={{
        fontSize: mobile ? 'var(--text-base)' : 'var(--text-sm)',
        fontWeight: 'var(--weight-semibold)',
        color: active === item.key ? 'var(--color-brand)' : 'var(--text-heading)',
        textDecoration: 'none',
        padding: mobile ? '0.5rem 0' : 0,
        display: 'block',
      }}
    >
      {item[lang]}
    </a>
  );

  const LangToggle = () => (
    <div style={{ display: 'inline-flex', alignItems: 'center', borderRadius: 'var(--radius-full)', background: 'var(--surface-subtle)', padding: '0.25rem' }}>
      {['en', 'es'].map((l) => (
        <button
          key={l}
          onClick={() => setLang(l)}
          style={{
            border: 'none', cursor: 'pointer', borderRadius: 'var(--radius-full)',
            padding: '0.25rem 0.75rem', fontSize: 'var(--text-sm)', fontWeight: 'var(--weight-semibold)',
            transition: 'var(--transition-colors)',
            background: lang === l ? 'var(--surface)' : 'transparent',
            color: lang === l ? 'var(--color-brand)' : 'var(--text-muted)',
            boxShadow: lang === l ? 'var(--shadow-xs)' : 'none',
          }}
        >
          {l.toUpperCase()}
        </button>
      ))}
    </div>
  );

  return (
    <header style={{ background: 'var(--surface)', borderBottom: '1px solid var(--border)', position: 'sticky', top: 0, zIndex: 30 }}>
      <nav style={{ maxWidth: 'var(--container-max)', margin: '0 auto', padding: '0 var(--page-gutter)', height: '88px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 'var(--space-6)' }}>
        <a href="#home" onClick={(e) => { e.preventDefault(); onNavigate('home'); }} style={{ display: 'flex', flex: 'none' }}>
          <img src="../../assets/logo-h-116.png" alt="Panama In Context" style={{ height: '56px', width: 'auto' }} />
        </a>
        <div className="nav-links" style={{ display: 'flex', gap: 'var(--space-8)', alignItems: 'center' }}>
          {nav.map((i) => link(i, false))}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-5)', flex: 'none' }}>
          <button onClick={onOpenCart} aria-label="Cart" style={{ position: 'relative', background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-heading)', display: 'inline-flex' }}>
            <Icon name="shopping-bag" size={24} />
            {cartCount > 0 ? (
              <span style={{ position: 'absolute', top: -6, right: -8, background: 'var(--color-brand)', color: '#fff', fontSize: '10px', fontWeight: 700, borderRadius: 'var(--radius-full)', minWidth: '16px', height: '16px', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '0 4px' }}>{cartCount}</span>
            ) : null}
          </button>
          <div className="lang-desktop"><LangToggle /></div>
          <button className="menu-btn" onClick={() => setMenuOpen(true)} aria-label="Menu" style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-heading)', display: 'none' }}>
            <Icon name="bars-3" size={24} />
          </button>
        </div>
      </nav>

      {menuOpen ? (
        <div style={{ position: 'fixed', inset: 0, zIndex: 50 }}>
          <div onClick={() => setMenuOpen(false)} style={{ position: 'absolute', inset: 0, background: 'rgba(17,24,39,0.2)' }} />
          <div style={{ position: 'absolute', top: 0, right: 0, bottom: 0, width: '300px', maxWidth: '85%', background: 'var(--surface)', boxShadow: 'var(--shadow-xl)', padding: 'var(--space-6)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-6)' }}>
              <img src="../../assets/logo-h-116.png" alt="" style={{ height: '44px' }} />
              <button onClick={() => setMenuOpen(false)} aria-label="Close" style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-heading)', display: 'inline-flex' }}>
                <Icon name="x-mark" size={24} />
              </button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column' }}>{nav.map((i) => link(i, true))}</div>
            <div style={{ marginTop: 'var(--space-6)' }}><LangToggle /></div>
          </div>
        </div>
      ) : null}
    </header>
  );
}

window.PicNavbar = Navbar;
