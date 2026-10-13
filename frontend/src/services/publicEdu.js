const API_BASE = import.meta.env.VITE_API_URL || 'https://api.panamaincontext.com';

/**
 * Fetch the public pre-visit deliverables (narrated video + study-guide PDF),
 * grouped by excursion × grade band. Public / no login (#326).
 */
export async function getPublicDeliverables() {
  const response = await fetch(`${API_BASE}/api/v1/public-edu/deliverables`);
  if (!response.ok) throw new Error('Failed to fetch deliverables');
  return response.json();
}
