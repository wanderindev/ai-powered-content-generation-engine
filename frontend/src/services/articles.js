/**
 * Articles API service
 */

const API_BASE = import.meta.env.VITE_API_URL || 'https://api.panamaincontext.com';

/**
 * Get the latest article series for homepage
 * @param {string} language - Language code (en or es)
 * @returns {Promise<{series_title: string, articles: Array}>}
 */
export async function getLatestSeries(language = 'en') {
  const response = await fetch(`${API_BASE}/api/v1/articles/latest-series?lang=${language}`);
  if (!response.ok) {
    throw new Error('Failed to fetch latest series');
  }
  return response.json();
}

/**
 * Get all articles for blog page
 * @param {string} language - Language code (en or es)
 * @param {number} limit - Maximum number of articles
 * @returns {Promise<Array>}
 */
export async function getArticles(language = 'en', limit = 100, search = '') {
  let url = `${API_BASE}/api/v1/articles?lang=${language}&limit=${limit}`;
  if (search) {
    url += `&search=${encodeURIComponent(search)}`;
  }
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error('Failed to fetch articles');
  }
  return response.json();
}

/**
 * Get article by slug
 * @param {string} slug - Article slug
 * @param {string} language - Language code (en or es)
 * @returns {Promise<Object>} Article detail object
 */
export async function getArticleBySlug(slug, language = 'en') {
  const response = await fetch(`${API_BASE}/api/v1/articles/by-slug/${slug}?lang=${language}`);
  if (!response.ok) {
    throw new Error('Article not found');
  }
  return response.json();
}
