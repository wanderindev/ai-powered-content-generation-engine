// Shared by every public article card / header. An article without a feature
// image gets the on-brand placeholder in public/images (#657); the API returns
// relative "/static/..." paths for uploaded images and absolute URLs for
// Spaces-hosted ones.
export const ARTICLE_PLACEHOLDER_IMAGE = '/images/placeholder-article.svg';

export function articleImageUrl(path) {
  if (!path) return ARTICLE_PLACEHOLDER_IMAGE;
  if (path.startsWith('http')) return path;
  const apiBase = import.meta.env.VITE_API_URL || 'https://api.panamaincontext.com';
  return `${apiBase}${path}`;
}
