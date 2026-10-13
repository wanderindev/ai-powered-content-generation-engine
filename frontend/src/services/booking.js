const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

/**
 * fetch() wrapper that sends a magic-link token in the Authorization header
 * instead of the URL query string. Used by both the admin booking methods (#57)
 * and the customer order methods getOrder/cancelOrder/renewPayment (#43), which
 * receive the customer VIEW_ORDER token from the page's URL fragment.
 */
async function authFetch(token, url, options = {}) {
  const { headers, ...rest } = options;
  return fetch(url, {
    ...rest,
    headers: { ...(headers || {}), Authorization: `Bearer ${token}` },
  });
}

export const bookingService = {
    // Tours
    async getTours(lang = 'en') {
        const res = await fetch(`${API_BASE}/api/v1/tours?lang=${lang}`);
        if (!res.ok) throw new Error('Failed to fetch tours');
        return res.json();
    },

    async getTour(slug, lang = 'en') {
        const res = await fetch(`${API_BASE}/api/v1/tours/${slug}?lang=${lang}`);
        if (!res.ok) throw new Error('Failed to fetch tour');
        return res.json();
    },

    async getAvailability(slug, startDate, endDate) {
        const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
        const res = await fetch(`${API_BASE}/api/v1/tours/${slug}/availability?${params}`);
        if (!res.ok) throw new Error('Failed to fetch availability');
        return res.json();
    },

    // Zones & Hotels
    async getZones(lang = 'en') {
        const res = await fetch(`${API_BASE}/api/v1/zones?lang=${lang}`);
        if (!res.ok) throw new Error('Failed to fetch zones');
        return res.json();
    },

    async searchHotels(query, lang = 'en') {
        const params = new URLSearchParams({ q: query, lang });
        const res = await fetch(`${API_BASE}/api/v1/zones/hotels/search?${params}`);
        if (!res.ok) throw new Error('Failed to search hotels');
        return res.json();
    },

    // Pricing
    async calculatePrice(data) {
        const res = await fetch(`${API_BASE}/api/v1/bookings/calculate-price`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) throw new Error('Failed to calculate price');
        return res.json();
    },

    // Orders
    async createOrder(data) {
        const res = await fetch(`${API_BASE}/api/v1/orders`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        // Don't throw here immediately, as we might want to handle 400s with error messages from API
        if (!res.ok) {
            const errorData = await res.json();
            // Handle Pydantic validation errors (array of objects)
            if (Array.isArray(errorData.detail)) {
                const messages = errorData.detail.map(err =>
                    `${err.loc?.join('.')}: ${err.msg}`
                ).join('; ');
                console.error('Validation errors:', errorData.detail);
                throw new Error(messages || 'Validation failed');
            }
            throw new Error(errorData.detail || 'Failed to create order');
        }
        return res.json();
    },

    async getOrder(reference, token) {
        const res = await authFetch(token, `${API_BASE}/api/v1/orders/${reference}`);
        if (!res.ok) throw new Error('Failed to fetch order');
        return res.json();
    },

    async cancelOrder(reference, token, reason = null) {
        const res = await authFetch(token, `${API_BASE}/api/v1/orders/${reference}/cancel`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason }),
        });
        if (!res.ok) throw new Error('Failed to cancel order');
        return res.json();
    },

    async renewPayment(reference, token) {
        const res = await authFetch(token, `${API_BASE}/api/v1/orders/${reference}/renew-payment`, {
            method: 'POST',
        });
        if (!res.ok) throw new Error('Failed to renew payment');
        return res.json();
    },

    // Magic Links
    async verifyToken(token) {
        const res = await fetch(`${API_BASE}/api/v1/auth/verify-token`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token }),
        });
        if (!res.ok) throw new Error('Invalid or expired token');
        return res.json();
    },

    // Admin
    async requestAdminAccess(email) {
        const res = await fetch(`${API_BASE}/api/v1/auth/admin/request-access`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email }),
        });
        if (!res.ok) throw new Error('Failed to request admin access');
        return res.json();
    },

    async getAdminOrders(token, status = null) {
        const params = new URLSearchParams();
        if (status) params.append('status', status);
        const res = await authFetch(token, `${API_BASE}/api/v1/admin/bookings/orders?${params}`);
        if (!res.ok) throw new Error('Failed to fetch admin orders');
        return res.json();
    },

    async markPaymentSent(reference, token, notes = null) {
        const res = await authFetch(token, `${API_BASE}/api/v1/admin/bookings/orders/${reference}/mark-payment-sent`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ notes }),
        });
        if (!res.ok) throw new Error('Failed to mark payment sent');
        return res.json();
    },

    async markPaid(reference, token, notes = null) {
        const res = await authFetch(token, `${API_BASE}/api/v1/admin/bookings/orders/${reference}/mark-paid`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ notes }),
        });
        if (!res.ok) throw new Error('Failed to mark paid');
        return res.json();
    },

    async getAdminOrderDetail(reference, token) {
        const res = await authFetch(token, `${API_BASE}/api/v1/admin/bookings/orders/${reference}`);
        if (!res.ok) throw new Error('Failed to fetch order detail');
        return res.json();
    },

    async createInvoice(reference, token) {
        const res = await authFetch(token, `${API_BASE}/api/v1/admin/bookings/orders/${reference}/create-invoice`, {
            method: 'POST',
        });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Failed to create invoice');
        }
        return res.json();
    },

    async sendInvoice(reference, token) {
        const res = await authFetch(token, `${API_BASE}/api/v1/admin/bookings/orders/${reference}/send-invoice`, {
            method: 'POST',
        });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Failed to send invoice');
        }
        return res.json();
    },

    async cancelInvoice(reference, token) {
        const res = await authFetch(token, `${API_BASE}/api/v1/admin/bookings/orders/${reference}/cancel-invoice`, {
            method: 'POST',
        });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Failed to cancel invoice');
        }
        return res.json();
    },

    async updateOrderNotes(reference, token, notes) {
        const res = await authFetch(token, `${API_BASE}/api/v1/admin/bookings/orders/${reference}/notes`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ notes }),
        });
        if (!res.ok) throw new Error('Failed to update notes');
        return res.json();
    },

    // Calendar
    async getCalendarBookings(token, startDate, endDate) {
        const params = new URLSearchParams();
        if (startDate) params.append('start_date', startDate);
        if (endDate) params.append('end_date', endDate);
        const res = await authFetch(token, `${API_BASE}/api/v1/admin/bookings/calendar?${params}`);
        if (!res.ok) throw new Error('Failed to fetch calendar bookings');
        return res.json();
    },

    async adminCancelOrder(reference, token, reason = null) {
        const res = await authFetch(token, `${API_BASE}/api/v1/admin/bookings/orders/${reference}/admin-cancel`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason }),
        });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Failed to cancel order');
        }
        return res.json();
    },

    async rescheduleBooking(bookingReference, token, data) {
        const res = await authFetch(token, `${API_BASE}/api/v1/admin/bookings/${bookingReference}/reschedule`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Failed to reschedule booking');
        }
        return res.json();
    },

    async getInvoiceStatus(reference, token) {
        const res = await authFetch(token, `${API_BASE}/api/v1/admin/bookings/orders/${reference}/invoice-status`);
        if (!res.ok) throw new Error('Failed to fetch invoice status');
        return res.json();
    },

    // Revoke the admin magic link server-side on logout (#57) so a 24h token
    // can't be replayed from history/logs after the admin signs out.
    async adminLogout(token) {
        const res = await authFetch(token, `${API_BASE}/api/v1/auth/admin/logout`, {
            method: 'POST',
        });
        if (!res.ok) throw new Error('Failed to log out');
    },
};