import { useState, useEffect, useCallback } from 'react';
import {
  educatorSession,
  educatorUnsubscribe,
  getStoredAccess,
  setStoredAccess,
  clearStoredAccess,
} from '../services/educators';
import { EducatorAuthContext } from './EducatorAuthContext';

function notExpired(expiresAt) {
  return expiresAt && new Date(expiresAt) > new Date();
}

export function EducatorAuthProvider({ children }) {
  const stored = getStoredAccess();
  const [email, setEmail] = useState(() => stored?.email || null);
  const [token, setToken] = useState(() => stored?.token || null);
  const [expiresAt, setExpiresAt] = useState(() => stored?.expiresAt || null);
  // Optimistically trust a non-expired stored token until the server confirms.
  const [hasAccess, setHasAccess] = useState(() => !!(stored?.token && notExpired(stored?.expiresAt)));
  // Start `loading` true when there is a stored token to re-validate, so the
  // mount effect never has to setState synchronously (set-state-in-effect).
  const [loading, setLoading] = useState(() => !!stored?.token);
  const [verifyError, setVerifyError] = useState(false);

  // On mount, re-validate the stored token against the backend (server is the
  // source of truth on expiry/revocation — never trust localStorage alone).
  useEffect(() => {
    const s = getStoredAccess();
    if (!s?.token) return;

    educatorSession(s.token)
      .then((result) => {
        setVerifyError(false);
        if (result.valid) {
          setEmail(s.email);
          setToken(s.token);
          setHasAccess(true);
          setExpiresAt(result.expires_at);
          setStoredAccess({ email: s.email, token: s.token, expiresAt: result.expires_at });
        } else {
          // Token expired/revoked. Keep the email for a one-field re-entry, but
          // drop the dead token so no gated request carries it.
          setHasAccess(false);
          setToken(null);
          setExpiresAt(null);
          clearStoredAccess();
          setEmail(s.email);
        }
      })
      .catch(() => {
        // Network error — do NOT grant access from stale local state. Surface a
        // "can't verify" state so the user can retry.
        setHasAccess(false);
        setVerifyError(true);
      })
      .finally(() => setLoading(false));
  }, []);

  const grantAccess = useCallback((newEmail, newToken, newExpiresAt) => {
    setEmail(newEmail);
    setToken(newToken);
    setHasAccess(true);
    setExpiresAt(newExpiresAt);
    setStoredAccess({ email: newEmail, token: newToken, expiresAt: newExpiresAt });
  }, []);

  const logout = useCallback(() => {
    setEmail(null);
    setToken(null);
    setHasAccess(false);
    setExpiresAt(null);
    clearStoredAccess();
  }, []);

  const unsubscribe = useCallback(async () => {
    if (token) {
      try {
        await educatorUnsubscribe(token);
      } catch {
        /* best-effort; clear local state regardless */
      }
    }
    logout();
  }, [token, logout]);

  return (
    <EducatorAuthContext.Provider
      value={{ email, token, hasAccess, expiresAt, loading, verifyError, grantAccess, logout, unsubscribe }}
    >
      {children}
    </EducatorAuthContext.Provider>
  );
}
