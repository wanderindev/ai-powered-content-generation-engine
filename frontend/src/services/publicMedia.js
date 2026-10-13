import { getAccessToken } from './educators';

const API_BASE = import.meta.env.VITE_API_URL || 'https://api.panamaincontext.com';

/** Authorization header carrying the educator session token (gate — #50). */
function authHeaders() {
  const token = getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function getPublicMedia({ search, tag, page = 1, pageSize = 30 } = {}) {
  const params = new URLSearchParams({ page, page_size: pageSize });
  if (search) params.set('search', search);
  if (tag) params.set('tag', tag);

  const response = await fetch(`${API_BASE}/api/v1/public-media?${params}`, {
    headers: authHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch media');
  return response.json();
}

export async function getPublicMediaStats() {
  const response = await fetch(`${API_BASE}/api/v1/public-media/stats`, {
    headers: authHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch stats');
  return response.json();
}

export async function getPublicMediaTags() {
  const response = await fetch(`${API_BASE}/api/v1/public-media/tags`, {
    headers: authHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch tags');
  return response.json();
}

export async function getPublicMediaDetail(id) {
  const response = await fetch(`${API_BASE}/api/v1/public-media/${id}`, {
    headers: authHeaders(),
  });
  if (!response.ok) throw new Error('Media not found');
  return response.json();
}

/**
 * Download a gated media file. The endpoint requires the Bearer token, so a
 * plain <a href> navigation won't carry it — fetch the bytes with the auth
 * header, then trigger a blob download.
 */
export async function downloadPublicMedia(mediaId, filenameHint) {
  const response = await fetch(`${API_BASE}/api/v1/public-media/${mediaId}/download`, {
    headers: authHeaders(),
  });
  if (!response.ok) throw new Error('Failed to download media');

  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = objectUrl;
  // Prefer the server's Content-Disposition filename; fall back to the hint.
  const disposition = response.headers.get('Content-Disposition') || '';
  const match = disposition.match(/filename="?([^"]+)"?/);
  a.download = match ? match[1] : filenameHint || '';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
}
