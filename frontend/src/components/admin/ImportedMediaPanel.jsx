import { useEffect, useRef, useState } from 'react';
import { MagnifyingGlassIcon } from '@heroicons/react/24/outline';
import { adminService } from '../../services/admin';
import MediaEditModal from './MediaEditModal';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const PAGE_SIZE = 50;

function mediaUrl(path) {
  if (!path) return '';
  if (path.startsWith('http')) return path;
  return `${API_BASE}${path}`;
}

// The library page's "Imported" view (#589): images added by link
// (source=LOCAL), searchable, each editable through MediaEditModal.
export default function ImportedMediaPanel({ token, refreshKey }) {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(null);

  const searchTimer = useRef(null);
  useEffect(() => {
    clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
    }, 400);
    return () => clearTimeout(searchTimer.current);
  }, [search]);

  // Async IIFE so the spinner setState calls aren't in the synchronous effect
  // body (set-state-in-effect).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await adminService.getMediaList(token, {
          source: 'LOCAL',
          search: debouncedSearch || undefined,
          page,
          pageSize: PAGE_SIZE,
        });
        if (cancelled) return;
        setItems(data.items || []);
        setTotal(data.total || 0);
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, debouncedSearch, page, refreshKey]);

  const totalPages = Math.ceil(total / PAGE_SIZE);

  const handleSaved = (updated) => {
    setItems((prev) => prev.map((m) => (m.id === updated.id ? updated : m)));
    setEditing(null);
  };

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 mb-5">
        <div className="relative flex-1 min-w-[200px] max-w-sm">
          <MagnifyingGlassIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search title, description or search terms..."
            className="w-full rounded-md border border-gray-300 pl-9 pr-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
          />
        </div>
        <span className="text-sm text-gray-500 ml-auto">
          {total.toLocaleString()} imported image{total !== 1 ? 's' : ''}
        </span>
      </div>

      {error && (
        <div className="rounded-md bg-red-50 p-4 mb-5">
          <p className="text-sm text-red-700">{error}</p>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-20 text-gray-400">Loading...</div>
      ) : items.length === 0 ? (
        <div className="flex items-center justify-center py-20 text-sm text-gray-400">
          No imported images. Use “Add from link” to add one.
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
          {items.map((m) => (
            <button
              key={m.id}
              type="button"
              onClick={() => setEditing(m)}
              className="group text-left rounded-lg border border-gray-200 bg-white overflow-hidden hover:border-indigo-300 hover:shadow-sm transition"
            >
              <div className="aspect-square bg-gray-100">
                <img
                  src={mediaUrl(m.thumbnail_path || m.file_path)}
                  alt={m.alt_text || m.title || ''}
                  loading="lazy"
                  className="h-full w-full object-cover"
                />
              </div>
              <div className="p-2">
                <p className="text-sm font-medium text-gray-900 truncate">{m.title}</p>
                <p className="text-xs text-gray-500 truncate">{m.attribution || 'No credit'}</p>
              </div>
            </button>
          ))}
        </div>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-end gap-3 mt-6 pt-4 border-t border-gray-200">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Previous
          </button>
          <span className="text-sm text-gray-600">
            Page {page} of {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page === totalPages}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Next
          </button>
        </div>
      )}

      {editing && (
        <MediaEditModal
          key={editing.id}
          item={editing}
          token={token}
          onClose={() => setEditing(null)}
          onSaved={handleSaved}
        />
      )}
    </div>
  );
}
