/* Panama In Context — Website demo app shell.
   Routes between home / tours / excursions / blog / contact / materials,
   toggles language, and drives the booking → cart flow. */
const Navbar = window.PicNavbar;
const Hero = window.PicHero;
const Footer = window.PicFooter;
const { SchoolsSection, TouristsSection, GuideSection, ArticlesSection, CTASection } = window.PicSections;
const { ToursPage, BookingModal, CartDrawer, ContactPage, BlogPage, SimplePage } = window.PicPages;

function App() {
  const [lang, setLang] = React.useState('en');
  const [view, setView] = React.useState('home');
  const [cart, setCart] = React.useState([]);
  const [cartOpen, setCartOpen] = React.useState(false);
  const [bookingTour, setBookingTour] = React.useState(null);

  const navigate = (key) => { setView(key); window.scrollTo({ top: 0 }); };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--surface)' }}>
      <Navbar lang={lang} setLang={setLang} active={view} onNavigate={navigate} cartCount={cart.length} onOpenCart={() => setCartOpen(true)} />
      <main style={{ flex: 1 }}>
        {view === 'home' ? (
          <>
            <Hero lang={lang} onNavigate={navigate} />
            <SchoolsSection lang={lang} onNavigate={navigate} />
            <TouristsSection lang={lang} onNavigate={navigate} />
            <GuideSection lang={lang} onNavigate={navigate} />
            <ArticlesSection lang={lang} />
            <CTASection lang={lang} onNavigate={navigate} />
          </>
        ) : null}
        {view === 'tours' || view === 'excursions' ? (
          <ToursPage lang={lang} onReserve={setBookingTour} />
        ) : null}
        {view === 'blog' ? <BlogPage lang={lang} /> : null}
        {view === 'contact' ? <ContactPage lang={lang} /> : null}
        {view === 'materials' ? (
          <SimplePage lang={lang}
            title={lang === 'en' ? 'Educational Materials' : 'Materiales Educativos'}
            sub={lang === 'en' ? 'Register for free to access historical infographics, study guides, and classroom resources. (Library available in the live product.)' : 'Regístrese gratis para acceder a infografías, guías de estudio y recursos. (Biblioteca disponible en el producto en vivo.)'} />
        ) : null}
      </main>
      <Footer lang={lang} onNavigate={navigate} />

      <CartDrawer lang={lang} open={cartOpen} items={cart} onClose={() => setCartOpen(false)} onRemove={(i) => setCart((c) => c.filter((_, j) => j !== i))} />
      {bookingTour ? (
        <BookingModal lang={lang} tour={bookingTour} onClose={() => setBookingTour(null)}
          onAdd={(item) => { setCart((c) => [...c, item]); setBookingTour(null); setCartOpen(true); }} />
      ) : null}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
