import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import AppShell from './components/layout/AppShell.jsx'
import Home from './pages/Home.jsx'

// Placeholder components for other pages to avoid build errors
import AcademicExcursions from './pages/AcademicExcursions.jsx'
import Tours from './pages/Tours.jsx'
import Blog from './pages/Blog.jsx'
import Article from './pages/Article.jsx'
import Contact from './pages/Contact.jsx'
import EduMaterialsLayout from './components/layout/EduMaterialsLayout.jsx'
import MediaLibrarySection from './pages/edu/MediaLibrarySection.jsx'
import ComingSoonSection from './pages/edu/ComingSoonSection.jsx'
import PresentationsSection from './pages/edu/PresentationsSection.jsx'
import ContactConfirmation from './pages/ContactConfirmation.jsx'
import CascoAntiguoPlazasPage from './pages/excursions/CascoAntiguoPlazasPage.jsx'
import CascoMuseumsPage from './pages/excursions/CascoMuseumsPage.jsx'
import PanamaViejoPage from './pages/excursions/PanamaViejoPage.jsx'
import BiomuseoPage from './pages/excursions/BiomuseoPage.jsx'
import MirafloresPage from './pages/excursions/MirafloresPage.jsx'
import ReligiousPage from './pages/excursions/ReligiousPage.jsx'
import CanalPage from './pages/tours/CanalPage.jsx'
import TourCascoPlazasPage from './pages/tours/CascoPlazasPage.jsx'
import TourCascoMuseumsPage from './pages/tours/CascoMuseumsPage.jsx'
import TourPanamaViejoPage from './pages/tours/PanamaViejoPage.jsx'
import TourBiomuseoPage from './pages/tours/BiomuseoPage.jsx'
import TourMiPueblitoPage from './pages/tours/MiPueblitoPage.jsx'
import SieteIglesiasPage from './pages/tours/SieteIglesiasPage.jsx'
import PanamaViejo507Page from './pages/tours/PanamaViejo507Page.jsx'
import ConfirmSubscription from './pages/ConfirmSubscription.jsx'
import Unsubscribe from './pages/Unsubscribe.jsx'
import TermsAndConditions from './pages/TermsAndConditions.jsx'
import PrivacyPolicy from './pages/PrivacyPolicy.jsx'
import NotFound from './pages/NotFound.jsx'

import { CartProvider } from './contexts/CartProvider';
import { BookingModalProvider } from './contexts/BookingModalProvider';
import { EducatorAuthProvider } from './contexts/EducatorAuthProvider';
import Checkout from './pages/Checkout';
import OrderConfirmation from './pages/OrderConfirmation';
import BookingManage from './pages/BookingManage';
import BookingCancel from './pages/BookingCancel';
import BookingRenewPayment from './pages/BookingRenewPayment';
// Admin routes are lazy-loaded so the heavy CMS chunks (AG Grid ~500 kB,
// TipTap ~300 kB) ship only when an admin route is visited — not in the
// initial bundle every public visitor downloads (#18).
const AdminLogin = lazy(() => import('./pages/admin/AdminLogin'));
const AdminLayout = lazy(() => import('./components/layout/AdminLayout'));
const AdminHome = lazy(() => import('./pages/admin/AdminHome'));
const AdminSuggestions = lazy(() => import('./pages/admin/AdminSuggestions'));
const AdminResearch = lazy(() => import('./pages/admin/AdminResearch'));
const AdminArticles = lazy(() => import('./pages/admin/AdminArticles'));
const AdminOutlines = lazy(() => import('./pages/admin/AdminOutlines'));
const AdminOrders = lazy(() => import('./pages/admin/AdminOrders'));
const AdminCalendar = lazy(() => import('./pages/admin/AdminCalendar'));
const AdminSettings = lazy(() => import('./pages/admin/AdminSettings'));
const AdminMediaLibrary = lazy(() => import('./pages/admin/AdminMediaLibrary'));
const AdminEduSuggestions = lazy(() => import('./pages/admin/AdminEduSuggestions'));
const AdminEduResearch = lazy(() => import('./pages/admin/AdminEduResearch'));
const AdminEduMaterials = lazy(() => import('./pages/admin/AdminEduMaterials'));
const AdminInstagram = lazy(() => import('./pages/admin/AdminInstagram'));
const AdminEditorialCalendar = lazy(() => import('./pages/admin/AdminEditorialCalendar'));
const AdminAgentTasks = lazy(() => import('./pages/admin/AdminAgentTasks'));
const AdminApprovals = lazy(() => import('./pages/admin/AdminApprovals'));

// Ask PIC (#636) is lazy too: a public page most visitors never open.
const AskPic = lazy(() => import('./pages/AskPic'));

// Centered spinner shown while a lazy admin chunk loads (matches the
// app's existing loading affordance, e.g. OrderConfirmation).
function AdminFallback() {
  return (
    <div className="flex min-h-screen items-center justify-center" aria-live="polite">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
    </div>
  );
}

function App() {
  return (
    <BrowserRouter>
      <CartProvider>
        <BookingModalProvider>
        <EducatorAuthProvider>
        <Routes>
          <Route path="/" element={<AppShell />}>
            <Route index element={<Home />} />
            <Route path="excursiones-academicas" element={<AcademicExcursions />} />
            <Route path="tours-guiados" element={<Tours />} />
            {/* Blog listing - with language prefix */}
            <Route path="en/blog" element={<Blog />} />
            <Route path="es/blog" element={<Blog />} />
            <Route path="blog" element={<Blog />} />
            {/* Article detail - localized paths */}
            <Route path="en/articles/:slug" element={<Article />} />
            <Route path="es/articulos/:slug" element={<Article />} />
            <Route path="articles/:slug" element={<Article />} />
            {/* Ask PIC (#636) — EN /ask, ES /preguntar; the path sets the language */}
            <Route path="ask" element={<Suspense fallback={<AdminFallback />}><AskPic /></Suspense>} />
            <Route path="preguntar" element={<Suspense fallback={<AdminFallback />}><AskPic /></Suspense>} />
            <Route path="contacto" element={<Contact />} />
            <Route path="contacto/confirmacion" element={<ContactConfirmation />} />
            <Route path="excursiones-academicas/casco-antiguo-plazas" element={<CascoAntiguoPlazasPage />} />
            <Route path="excursiones-academicas/casco-antiguo-museos" element={<CascoMuseumsPage />} />
            <Route path="excursiones-academicas/panama-viejo" element={<PanamaViejoPage />} />
            <Route path="excursiones-academicas/biomuseo" element={<BiomuseoPage />} />
            <Route path="excursiones-academicas/miraflores" element={<MirafloresPage />} />
            <Route path="excursiones-academicas/inter-religiosa" element={<ReligiousPage />} />
            <Route path="tours-guiados/canal-de-panama" element={<CanalPage />} />
            <Route path="tours-guiados/casco-antiguo-plazas" element={<TourCascoPlazasPage />} />
            <Route path="tours-guiados/casco-antiguo-museos" element={<TourCascoMuseumsPage />} />
            <Route path="tours-guiados/panama-viejo" element={<TourPanamaViejoPage />} />
            <Route path="tours-guiados/biomuseo" element={<TourBiomuseoPage />} />
            <Route path="tours-guiados/mi-pueblito" element={<TourMiPueblitoPage />} />
            <Route path="tours-guiados/siete-iglesias" element={<SieteIglesiasPage />} />
            <Route path="tours-guiados/panama-viejo-507" element={<PanamaViejo507Page />} />
            {/* Mailing list confirmation and unsubscribe */}
            <Route path="confirm-subscription" element={<ConfirmSubscription />} />
            <Route path="unsubscribe" element={<Unsubscribe />} />
            <Route path="terminos-y-condiciones" element={<TermsAndConditions />} />
            <Route path="politica-de-privacidad" element={<PrivacyPolicy />} />
            {/* Add other routes here */}
            {/* New booking routes */}
            <Route path="/checkout" element={<Checkout />} />
            <Route path="/order-confirmation/:reference" element={<OrderConfirmation />} />
            <Route path="/booking/manage" element={<BookingManage />} />
            <Route path="/booking/cancel" element={<BookingCancel />} />
            <Route path="/booking/renew-payment" element={<BookingRenewPayment />} />

            {/* Catch-all 404 — must be the last child route of AppShell */}
            <Route path="*" element={<NotFound />} />
          </Route>

          {/* Educational Materials — standalone app-like shell (#309), a sibling
              of AppShell so it replaces the public Navbar/Footer with its own
              dashboard chrome (mirrors the admin layout). Sections render in the
              right pane via <Outlet>. */}
          <Route path="/materiales-educativos" element={<EduMaterialsLayout />}>
            <Route index element={<Navigate to="/materiales-educativos/biblioteca" replace />} />
            <Route path="biblioteca" element={<MediaLibrarySection />} />
            <Route path="presentaciones" element={<PresentationsSection />} />
            <Route
              path="infografias"
              element={<ComingSoonSection titleKey="edu_materials.nav.infographics" />}
            />
            <Route
              path="guias-de-audio"
              element={<ComingSoonSection titleKey="edu_materials.nav.audio_guides" />}
            />
            <Route
              path="trivia-historica"
              element={<ComingSoonSection titleKey="edu_materials.nav.historical_trivia" />}
            />
            <Route path="*" element={<Navigate to="/materiales-educativos/biblioteca" replace />} />
          </Route>

          {/* Admin routes — lazy-loaded behind a Suspense boundary (#18).
              Suspense can't be a direct child of <Routes>, so the boundary
              wraps each lazy element; the layout route's <Outlet> children
              resolve under the same fallback. */}
          <Route path="/admin/login" element={<Suspense fallback={<AdminFallback />}><AdminLogin /></Suspense>} />
          <Route path="/admin" element={<Suspense fallback={<AdminFallback />}><AdminLayout /></Suspense>}>
            <Route index element={<Navigate to="/admin/dashboard" replace />} />
            <Route path="dashboard" element={<AdminHome />} />
            <Route path="suggestions" element={<AdminSuggestions />} />
            <Route path="research" element={<AdminResearch />} />
            <Route path="articles" element={<AdminArticles />} />
            <Route path="outlines" element={<AdminOutlines />} />
            <Route path="edu-suggestions" element={<AdminEduSuggestions />} />
            <Route path="edu-research" element={<AdminEduResearch />} />
            <Route path="edu-materials" element={<AdminEduMaterials />} />
            <Route path="instagram" element={<AdminInstagram />} />
            <Route path="editorial-calendar" element={<AdminEditorialCalendar />} />
            <Route path="agent-tasks" element={<AdminAgentTasks />} />
            <Route path="approvals" element={<AdminApprovals />} />
            <Route path="media-library" element={<AdminMediaLibrary />} />
            <Route path="orders" element={<AdminOrders />} />
            <Route path="calendar" element={<AdminCalendar />} />
            <Route path="settings" element={<AdminSettings />} />
          </Route>
        </Routes>
        </EducatorAuthProvider>
        </BookingModalProvider>
      </CartProvider>
    </BrowserRouter>
  )
}

export default App
