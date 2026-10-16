import { useState } from 'react';
import { NavLink, Outlet, Navigate, useLocation } from 'react-router-dom';
import { Dialog, DialogBackdrop, DialogPanel, TransitionChild } from '@headlessui/react';
import {
  HomeIcon,
  NewspaperIcon,
  AcademicCapIcon,
  PhotoIcon,
  CameraIcon,
  ShoppingCartIcon,
  CalendarIcon,
  CalendarDaysIcon,
  QueueListIcon,
  Cog6ToothIcon,
  XMarkIcon,
  ArrowRightStartOnRectangleIcon,
  ChevronRightIcon,
  ExclamationTriangleIcon,
  ShieldCheckIcon,
} from '@heroicons/react/24/outline';
import { Bars3Icon } from '@heroicons/react/20/solid';
import { useAdminAuth } from '../../contexts/AdminAuthContext';
import { AdminAuthProvider } from '../../contexts/AdminAuthProvider';
import { useBeforeUnload } from '../../hooks/useBeforeUnload';
import { useNewDeployAvailable } from '../../hooks/useNewDeployAvailable';

const navigation = [
  { name: 'Home', href: '/admin/dashboard', icon: HomeIcon },
  {
    name: 'Blog Posts',
    icon: NewspaperIcon,
    children: [
      { name: 'Suggestions', href: '/admin/suggestions' },
      { name: 'Research', href: '/admin/research' },
      { name: 'Outlines', href: '/admin/outlines' },
      { name: 'Articles', href: '/admin/articles' },
    ],
  },
  {
    name: 'Educational Content',
    icon: AcademicCapIcon,
    children: [
      { name: 'Suggestions', href: '/admin/edu-suggestions' },
      { name: 'Research', href: '/admin/edu-research' },
      { name: 'Materials', href: '/admin/edu-materials' },
    ],
  },
  { name: 'Instagram', href: '/admin/instagram', icon: CameraIcon },
  { name: 'Editorial Calendar', href: '/admin/editorial-calendar', icon: CalendarIcon },
  { name: 'Agent Tasks', href: '/admin/agent-tasks', icon: QueueListIcon },
  { name: 'Approvals', href: '/admin/approvals', icon: ShieldCheckIcon },
  { name: 'Media Library', href: '/admin/media-library', icon: PhotoIcon },
  { name: 'Orders', href: '/admin/orders', icon: ShoppingCartIcon },
  { name: 'Bookings', href: '/admin/calendar', icon: CalendarDaysIcon },
  { name: 'Settings', href: '/admin/settings', icon: Cog6ToothIcon },
];

function classNames(...classes) {
  return classes.filter(Boolean).join(' ');
}

function SidebarContent({ onLogout }) {
  const location = useLocation();

  // Accordion: determine which section should be initially expanded based on current route
  const initialExpanded = () => {
    for (const item of navigation) {
      if (item.children?.some((c) => location.pathname === c.href)) {
        return item.name;
      }
    }
    return null;
  };
  const [expandedSection, setExpandedSection] = useState(initialExpanded);
  // Track whether user has manually toggled — skip auto-expand if so
  const [userToggled, setUserToggled] = useState(false);
  // Track the last path we reacted to, so the auto-expand below runs once per
  // navigation by adjusting state during render instead of in an effect (which
  // would setState synchronously — set-state-in-effect).
  const [reactedPath, setReactedPath] = useState(location.pathname);

  // Auto-expand when navigating to a child route (e.g. via direct URL or sidebar click)
  if (location.pathname !== reactedPath) {
    setReactedPath(location.pathname);
    const activeSection = navigation.find((item) =>
      item.children?.some((c) => location.pathname === c.href),
    )?.name ?? null;
    if (activeSection && !userToggled) {
      setExpandedSection(activeSection);
    }
    // Reset the flag so future navigations auto-expand again
    setUserToggled(false);
  }

  const handleToggle = (name) => {
    setUserToggled(true);
    setExpandedSection((prev) => (prev === name ? null : name));
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
                      key={item.name}
                      item={item}
                      location={location}
                      expanded={expandedSection === item.name}
                      onToggle={() => handleToggle(item.name)}
                    />
                  );
                }
                return (
                  <li key={item.name}>
                    <NavLink
                      to={item.href}
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
                              isActive ? 'text-indigo-600' : 'text-gray-400 group-hover:text-indigo-600',
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
            <button
              onClick={onLogout}
              className="group flex w-full gap-x-3 rounded-md p-2 text-sm/6 font-semibold text-gray-700 hover:bg-gray-100 hover:text-indigo-600"
            >
              <ArrowRightStartOnRectangleIcon className="size-6 shrink-0 text-gray-400 group-hover:text-indigo-600" />
              Sign out
            </button>
          </li>
        </ul>
      </nav>
    </div>
  );
}

function CollapsibleNavItem({ item, location, expanded, onToggle }) {
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

function AdminLayoutInner() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { isAuthenticated, loading, logout, adminEmail } = useAdminAuth();

  // TODO(#119): foundation only. The `useBeforeUnload` hook is wired here so the
  // beforeunload guard has a single, well-known install site at the admin shell.
  // Pass `false` for now: no consumer signals dirty state yet. A follow-up PR
  // should lift this to a real "is any editor dirty?" signal (likely a small
  // React context populated by the editors in EditDrawer.jsx — see issues #118
  // and #120 for the per-editor dirty tracking that becomes the source of truth).
  useBeforeUnload(false);

  // #595: a tab opened before a deploy keeps running the old bundle against
  // the new backend (article 246 was re-narrated in full that way, #594).
  // The banner sits at z-[60], above the z-50 drawers, so it stays visible
  // while a drawer is open (headless UI makes it inert until the drawer closes).
  const newDeployAvailable = useNewDeployAvailable();

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="text-gray-500">Loading...</div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/admin/login" replace />;
  }

  const handleLogout = () => {
    logout();
  };

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
                <button type="button" onClick={() => setSidebarOpen(false)} className="-m-2.5 p-2.5">
                  <span className="sr-only">Close sidebar</span>
                  <XMarkIcon aria-hidden="true" className="size-6 text-white" />
                </button>
              </div>
            </TransitionChild>
            <SidebarContent onLogout={handleLogout} />
          </DialogPanel>
        </div>
      </Dialog>

      {/* Desktop sidebar */}
      <div className="hidden xl:fixed xl:inset-y-0 xl:z-50 xl:flex xl:w-72 xl:flex-col">
        <SidebarContent onLogout={handleLogout} />
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
            <span className="sr-only">Open sidebar</span>
            <Bars3Icon aria-hidden="true" className="size-5" />
          </button>
          <div className="flex-1" />
          <span className="text-sm text-gray-500">{adminEmail}</span>
        </div>

        {newDeployAvailable && (
          <div
            role="alert"
            className="sticky top-16 z-[60] flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 sm:px-6 lg:px-8"
          >
            <ExclamationTriangleIcon aria-hidden="true" className="size-5 shrink-0 text-amber-600" />
            <p className="flex-1 font-medium">
              A new version of the admin was deployed — reload before running any generation.
              Unsaved edits in an open drawer will be lost, so save them first.
            </p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="rounded-md bg-amber-600 px-3 py-1.5 font-semibold text-white shadow-xs hover:bg-amber-500"
            >
              Reload now
            </button>
          </div>
        )}

        <main className="py-10">
          <div className="px-4 sm:px-6 lg:px-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}

export default function AdminLayout() {
  return (
    <AdminAuthProvider>
      <AdminLayoutInner />
    </AdminAuthProvider>
  );
}
