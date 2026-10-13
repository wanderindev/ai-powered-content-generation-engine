/**
 * Mailing list subscription API service
 */

const API_BASE = import.meta.env.VITE_API_URL || 'https://api.panamaincontext.com';

/**
 * Subscribe to the mailing list
 * @param {string} email - Subscriber email
 * @param {string} source - Source of subscription ('educators' or 'blog')
 * @param {string} language - Preferred language ('en' or 'es')
 * @returns {Promise<{success: boolean, message: string}>}
 */
export async function subscribe(email, source, language) {
  const response = await fetch(`${API_BASE}/api/v1/subscribe`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, source, language })
  });
  if (!response.ok) throw new Error('Failed to subscribe');
  return response.json();
}

/**
 * Confirm a subscription using the token from the confirmation email
 * @param {string} token - Confirmation token
 * @param {string} language - Preferred language ('en' or 'es')
 * @returns {Promise<{success: boolean, confirmed: boolean, email?: string, message?: string}>}
 */
export async function confirmSubscription(token, language) {
  const response = await fetch(`${API_BASE}/api/v1/confirm-subscription`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, language })
  });
  if (!response.ok) throw new Error('Failed to confirm subscription');
  return response.json();
}

/**
 * Unsubscribe from the mailing list
 * @param {string} email - Email to unsubscribe
 * @returns {Promise<{success: boolean, unsubscribed: boolean, message?: string}>}
 */
export async function unsubscribe(email) {
  const response = await fetch(`${API_BASE}/api/v1/unsubscribe`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email })
  });
  if (!response.ok) throw new Error('Failed to unsubscribe');
  return response.json();
}
