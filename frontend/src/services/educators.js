/**
 * Educator access API service (low-friction, token-based gate).
 *
 * The opaque session token issued by /access or /signup is the credential for
 * every gated public-media request. It lives in localStorage under STORAGE_KEY;
 * the helpers here are the single source of truth for reading/writing it, shared
 * by EducatorAuthContext and the publicMedia service.
 */

const API_BASE = import.meta.env.VITE_API_URL || 'https://api.panamaincontext.com';

export const STORAGE_KEY = 'educator_access';

export function getStoredAccess() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function setStoredAccess({ email, token, expiresAt }) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ email, token, expiresAt }));
}

export function clearStoredAccess() {
  localStorage.removeItem(STORAGE_KEY);
}

/** Bearer token for gated requests, or null if none stored. */
export function getAccessToken() {
  return getStoredAccess()?.token || null;
}

/** Returning educator: { granted, status, token, expires_at }. */
export async function educatorAccess(email, language) {
  const response = await fetch(`${API_BASE}/api/v1/educators/access`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, language }),
  });
  if (!response.ok) throw new Error('Failed to request educator access');
  return response.json();
}

/** New educator: { success, granted, token, expires_at, mailing_list_pending }. */
export async function educatorSignup({ email, name, institution, source, language, mailingList }) {
  const response = await fetch(`${API_BASE}/api/v1/educators/signup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      email,
      name: name || null,
      institution: institution || null,
      source: source || 'media_library',
      language: language || 'es',
      mailing_list: mailingList || false,
    }),
  });
  if (!response.ok) throw new Error('Failed to sign up educator');
  return response.json();
}

/** Validate a stored token: { valid, expires_at }. */
export async function educatorSession(token) {
  const response = await fetch(`${API_BASE}/api/v1/educators/session`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  });
  if (!response.ok) throw new Error('Failed to validate educator session');
  return response.json();
}

/** Unsubscribe the caller's own row, authenticated by their session token. */
export async function educatorUnsubscribe(token) {
  const response = await fetch(`${API_BASE}/api/v1/educators/unsubscribe`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  });
  if (!response.ok) throw new Error('Failed to unsubscribe educator');
  return response.json();
}
