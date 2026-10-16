// Generated artifacts (guide PDFs, deck MP4s) are overwritten IN PLACE at a
// stable Spaces key, so a regenerated file keeps the URL the browser already
// cached — an admin who re-renders and clicks "Open current PDF" sees the old
// version. Appending a per-load timestamp forces a fresh fetch without touching
// the stored URL.
export function cacheBustUrl(url, stamp = Date.now()) {
  if (!url) return url;
  return `${url}${url.includes('?') ? '&' : '?'}v=${stamp}`;
}
