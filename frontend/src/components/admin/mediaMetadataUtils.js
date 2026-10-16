// Helpers shared by the media metadata forms (#589). Kept out of the component
// modules because those may export only components (react-refresh rule).

// Fields captured when importing an image by link (picker + library page).
export const IMPORT_FIELDS = ['title', 'caption', 'attribution', 'source_page_url', 'license', 'keywords'];

// Every editable column, for the edit modal.
export const EDIT_FIELDS = [
  'title',
  'caption',
  'attribution',
  'source_url',
  'license',
  'license_url',
  'alt_text',
  'search_terms',
];

// A readable default title from an image URL: last path segment, decoded,
// extension stripped, separators turned into spaces.
export function titleFromUrl(url) {
  let segment = '';
  try {
    segment = new URL(url.trim()).pathname.split('/').filter(Boolean).pop() || '';
  } catch {
    segment = (url.trim().split(/[?#]/)[0].split('/').filter(Boolean).pop()) || '';
  }
  try {
    segment = decodeURIComponent(segment);
  } catch {
    // keep the raw segment
  }
  return segment.replace(/\.[A-Za-z0-9]{1,5}$/, '').replace(/[_-]+/g, ' ').trim();
}

// The import form's values → the request's metadata (trimmed, blanks omitted).
export function importMetadata(values) {
  const out = {};
  for (const field of IMPORT_FIELDS) {
    const v = (values[field] || '').trim();
    if (v) out[field] = v;
  }
  return out;
}

// Only the fields whose value changed versus the loaded item; '' → null.
// Unchanged search_terms must not be sent: that suppresses regeneration.
export function changedFields(item, values) {
  const changes = {};
  for (const field of EDIT_FIELDS) {
    const next = (values[field] || '').trim();
    const prev = (item[field] || '').trim();
    if (next !== prev) changes[field] = next || null;
  }
  return changes;
}

// Form values from a media item (nulls → '').
export function valuesFromItem(item) {
  const values = {};
  for (const field of EDIT_FIELDS) values[field] = item?.[field] || '';
  return values;
}
