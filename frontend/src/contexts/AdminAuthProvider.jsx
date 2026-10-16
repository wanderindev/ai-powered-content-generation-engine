import { useState, useEffect } from 'react';
import { bookingService } from '../services/booking';
import { AdminAuthContext } from './AdminAuthContext';

export function AdminAuthProvider({ children }) {
  // Derive the synchronous defaults at init so the mount effect never has to
  // setState synchronously (react-hooks/set-state-in-effect). In dev we bypass
  // auth with a static token; in prod we optimistically adopt any stored token
  // and stay `loading` until the server re-validates it below.
  const [token, setToken] = useState(() =>
    import.meta.env.DEV ? 'dev-token' : sessionStorage.getItem('adminToken'),
  );
  const [adminEmail, setAdminEmail] = useState(() =>
    import.meta.env.DEV ? 'dev@localhost' : sessionStorage.getItem('adminEmail'),
  );
  const [isAuthenticated, setIsAuthenticated] = useState(() => import.meta.env.DEV);
  const [loading, setLoading] = useState(() => !import.meta.env.DEV);

  async function logout() {
    // Best-effort server-side revocation so the 7-day token can't be replayed
    // from history/logs after logout (#57). Clear local state regardless.
    const current = sessionStorage.getItem('adminToken');
    if (current && current !== 'dev-token') {
      try {
        await bookingService.adminLogout(current);
      } catch {
        // Network/expired-token errors are non-fatal — local logout still proceeds.
      }
    }
    setToken(null);
    setAdminEmail(null);
    setIsAuthenticated(false);
    sessionStorage.removeItem('adminToken');
    sessionStorage.removeItem('adminEmail');
  }

  useEffect(() => {
    // In development, persist the dev token (state defaults already reflect it).
    if (import.meta.env.DEV) {
      sessionStorage.setItem('adminToken', 'dev-token');
      sessionStorage.setItem('adminEmail', 'dev@localhost');
      return;
    }

    // Run the prod bootstrap inside an async IIFE so its setState calls fall
    // outside the synchronous effect body (react-hooks/set-state-in-effect).
    (async () => {
      // Magic-link landing (#57). The admin link points at /admin, which mounts
      // this provider — so the token must be extracted here. It arrives in the URL
      // *fragment* (#token=...), which browsers never send to the server (no
      // access-log / Referer leak); we stash it in sessionStorage and strip it
      // from the URL. Query (?token=) is read only as a fallback so magic links
      // already in flight before this change keep working for their 7-day window.
      const hashMatch = window.location.hash.match(/(?:^#|&)token=([^&]+)/);
      const queryToken = new URLSearchParams(window.location.search).get('token');
      const landingToken = hashMatch ? decodeURIComponent(hashMatch[1]) : queryToken;
      if (landingToken) {
        sessionStorage.setItem('adminToken', landingToken);
        window.history.replaceState(null, '', window.location.pathname);
      }

      const stored = sessionStorage.getItem('adminToken');
      if (!stored) {
        setLoading(false);
        setIsAuthenticated(false);
        return;
      }

      try {
        const data = await bookingService.verifyToken(stored);
        if (data.valid && data.action === 'ADMIN_ACCESS') {
          setToken(stored);
          setAdminEmail(data.admin_email);
          setIsAuthenticated(true);
          sessionStorage.setItem('adminEmail', data.admin_email);
        } else {
          await logout();
        }
      } catch {
        await logout();
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  return (
    <AdminAuthContext.Provider value={{ token, adminEmail, isAuthenticated, loading, logout }}>
      {children}
    </AdminAuthContext.Provider>
  );
}
