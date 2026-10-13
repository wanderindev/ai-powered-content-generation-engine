import { useEffect, useRef, useState } from 'react';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Dialog, DialogBackdrop, DialogPanel, TransitionChild } from '@headlessui/react';
import {
  PhotoIcon,
  AcademicCapIcon,
  PuzzlePieceIcon,
  ArrowLeftIcon,
  XMarkIcon,
  ChevronRightIcon,
} from '@heroicons/react/24/outline';
import { Bars3Icon } from '@heroicons/react/20/solid';
import { trackPageView } from '../../analytics.js';

// Section routes live under /materiales-educativos. Labels are pulled from i18n
// at render time (bilingual), so the nav model is built inside the component.
function useNavigation() {
  const { t } = useTranslation();
  // `key` is a stable, language-independent id used for accordion state — never
  // the translated `name`, which changes on language toggle and would break the
  // expansion match.
  return [
    {
      key: 'media_library',
      name: t('edu_materials.nav.media_library'),
      href: '/materiales-educativos/biblioteca',
      icon: PhotoIcon,
    },
    {
      key: 'educational_content',
      name: t('edu_materials.nav.educational_content'),
      icon: AcademicCapIcon,
      children: [
        { name: t('edu_materials.nav.presentations'), href: '/materiales-educativos/presentaciones' },
        { name: t('edu_materials.nav.infographics'), href: '/materiales-educativos/infografias' },
        { name: t('edu_materials.nav.audio_guides'), href: '/materiales-educativos/guias-de-audio' },
      ],
    },
    {
      key: 'activities',
      name: t('edu_materials.nav.activities'),
      icon: PuzzlePieceIcon,
      children: [
        { name: t('edu_materials.nav.historical_trivia'), href: '/materiales-educativos/trivia-historica' },
      ],
    },
  ];
}

function classNames(...classes) {
  return classes.filter(Boolean).join(' ');
}

function SidebarContent({ onNavigate }) {
  const { t } = useTranslation();
  const location = useLocation();
  const navigation = useNavigation();

  // Accordion state is keyed by the stable `item.key` (not the translated
  // `name`), so expansion survives a language toggle.
  const initialExpanded = () => {
    for (const item of navigation) {
      if (item.children?.some((c) => location.pathname === c.href)) {
        return item.key;
      }
    }
    return null;
  };
  const [expandedSection, setExpandedSection] = useState(initialExpanded);
  const [userToggled, setUserToggled] = useState(false);
  // Adjust expansion during render (once per navigation) instead of in an
  // effect, mirroring AdminLayout — avoids the set-state-in-effect pattern.
  const [reactedPath, setReactedPath] = useState(location.pathname);
  if (location.pathname !== reactedPath) {
    setReactedPath(location.pathname);
    const activeSection =
      navigation.find((item) => item.children?.some((c) => location.pathname === c.href))?.key ??
      null;
    if (activeSection && !userToggled) {
      setExpandedSection(activeSection);
    }
    setUserToggled(false);
  }

  const handleToggle = (key) => {
    setUserToggled(true);
    setExpandedSection((prev) => (prev === key ? null : key));
  };

  return (
    <div className="flex grow flex-col gap-y-5 overflow-y-auto bg-gray-50 px-6">
      <div className="mt-4 flex shrink-0 items-center">
        <img src="/logo-h-116.png" alt="Panama In Context" className="h-[85px] w-auto" />
      </div>
      <nav className="flex flex-1 flex-col">
        <ul role="list" className="flex flex-1 flex-col gap-y-7">
          <li>
            <ul role="list" className="-mx-2 space-y-1">
              {navigation.map((item) => {
                if (item.children) {
                  return (
                    <CollapsibleNavItem
                      key={item.key}
                      item={item}
                      location={location}
                      expanded={expandedSection === item.key}
                      onToggle={() => handleToggle(item.key)}
                      onNavigate={onNavigate}
                    />
                  );
                }
                return (
                  <li key={item.key}>
                    <NavLink
                      to={item.href}
                      onClick={onNavigate}
                      className={({ isActive }) =>
                        classNames(
                          isActive
                            ? 'bg-gray-100 text-indigo-600'
                            : 'text-gray-700 hover:bg-gray-100 hover:text-indigo-600',
                          'group flex gap-x-3 rounded-md p-2 text-sm/6 font-semibold',
                        )
                      }
                    >
                      {({ isActive }) => (
                        <>
                          <item.icon
                            aria-hidden="true"
                            className={classNames(
                              isActive
                                ? 'text-indigo-600'
                                : 'text-gray-400 group-hover:text-indigo-600',
                              'size-6 shrink-0',
                            )}
                          />
                          {item.name}
                        </>
                      )}
                    </NavLink>
                  </li>
                );
              })}
            </ul>
          </li>
          <li className="mt-auto pb-4">
            <Link
              to="/"
              className="group flex w-full gap-x-3 rounded-md p-2 text-sm/6 font-semibold text-gray-700 hover:bg-gray-100 hover:text-indigo-600"
            >
              <ArrowLeftIcon className="size-6 shrink-0 text-gray-400 group-hover:text-indigo-600" />
              {t('edu_materials.nav.back_to_website')}
            </Link>
          </li>
        </ul>
      </nav>
    </div>
  );
}

function CollapsibleNavItem({ item, location, expanded, onToggle, onNavigate }) {
  const childActive = item.children.some((c) => location.pathname === c.href);

  return (
    <li>
      <button
        onClick={onToggle}
        className={classNames(
          childActive
            ? 'bg-gray-100 text-indigo-600'
            : 'text-gray-700 hover:bg-gray-100 hover:text-indigo-600',
          'group flex w-full gap-x-3 rounded-md p-2 text-sm/6 font-semibold',
        )}
      >
        <item.icon
          aria-hidden="true"
          className={classNames(
            childActive ? 'text-indigo-600' : 'text-gray-400 group-hover:text-indigo-600',
            'size-6 shrink-0',
          )}
        />
        {item.name}
        <ChevronRightIcon
          aria-hidden="true"
          className={classNames(
            'ml-auto size-5 shrink-0 text-gray-400 transition-transform duration-150',
            expanded && 'rotate-90',
          )}
        />
      </button>
      {expanded && (
        <ul className="mt-1 space-y-1">
          {item.children.map((child) => (
            <li key={child.name}>
              <NavLink
                to={child.href}
                onClick={onNavigate}
                className={({ isActive }) =>
                  classNames(
                    isActive
                      ? 'bg-gray-100 text-indigo-600'
                      : 'text-gray-700 hover:bg-gray-100 hover:text-indigo-600',
                    'block rounded-md py-2 pl-9 pr-2 text-sm/6 font-semibold',
                  )
                }
              >
                {child.name}
              </NavLink>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function LanguageToggle() {
  const { i18n } = useTranslation();
  return (
    <div className="flex items-center rounded-full bg-gray-100 p-1">
      <button
        onClick={() => i18n.changeLanguage('en')}
        className={`rounded-full px-3 py-1 text-sm font-semibold transition-all ${
          i18n.language === 'en'
            ? 'bg-white text-indigo-600 shadow-sm'
            : 'text-gray-500 hover:text-gray-900'
        }`}
      >
        EN
      </button>
      <button
        onClick={() => i18n.changeLanguage('es')}
        className={`rounded-full px-3 py-1 text-sm font-semibold transition-all ${
          i18n.language === 'es'
            ? 'bg-white text-indigo-600 shadow-sm'
            : 'text-gray-500 hover:text-gray-900'
        }`}
      >
        ES
      </button>
    </div>
  );
}

export default function EduMaterialsLayout() {
  const { t } = useTranslation();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { pathname } = useLocation();

  // Scroll to top on section change (AppShell does this for the rest of the
  // site; this shell is outside AppShell so it wires its own).
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  // Fire PageView on SPA navigation between sections (parity with AppShell's
  // PageTracking; skip the first render, already tracked on initial load).
  const isFirstRender = useRef(true);
  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    trackPageView(pathname);
  }, [pathname]);

  const closeSidebar = () => setSidebarOpen(false);

  return (
    <div>
      {/* Mobile sidebar */}
      <Dialog open={sidebarOpen} onClose={setSidebarOpen} className="relative z-50 xl:hidden">
        <DialogBackdrop
          transition
          className="fixed inset-0 bg-gray-900/80 transition-opacity duration-300 ease-linear data-closed:opacity-0"
        />
        <div className="fixed inset-0 flex">
          <DialogPanel
            transition
            className="relative mr-16 flex w-full max-w-xs flex-1 transform transition duration-300 ease-in-out data-closed:-translate-x-full"
          >
            <TransitionChild>
              <div className="absolute top-0 left-full flex w-16 justify-center pt-5 duration-300 ease-in-out data-closed:opacity-0">
                <button type="button" onClick={closeSidebar} className="-m-2.5 p-2.5">
                  <span className="sr-only">{t('nav.close_menu')}</span>
                  <XMarkIcon aria-hidden="true" className="size-6 text-white" />
                </button>
              </div>
            </TransitionChild>
            <SidebarContent onNavigate={closeSidebar} />
          </DialogPanel>
        </div>
      </Dialog>

      {/* Desktop sidebar */}
      <div className="hidden xl:fixed xl:inset-y-0 xl:z-50 xl:flex xl:w-72 xl:flex-col">
        <SidebarContent />
      </div>

      {/* Main content */}
      <div className="xl:pl-72">
        {/* Top bar */}
        <div className="sticky top-0 z-40 flex h-16 shrink-0 items-center gap-x-6 border-b border-gray-200 bg-white px-4 shadow-xs sm:px-6 lg:px-8">
          <button
            type="button"
            onClick={() => setSidebarOpen(true)}
            className="-m-2.5 p-2.5 text-gray-700 xl:hidden"
          >
            <span className="sr-only">{t('nav.open_menu')}</span>
            <Bars3Icon aria-hidden="true" className="size-5" />
          </button>
          <div className="flex-1" />
          <LanguageToggle />
        </div>

        <main className="py-10">
          <div className="px-4 sm:px-6 lg:px-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
