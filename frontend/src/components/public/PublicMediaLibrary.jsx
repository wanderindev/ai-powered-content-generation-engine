import { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { MagnifyingGlassIcon } from '@heroicons/react/24/outline';
import { getPublicMedia, getPublicMediaStats } from '../../services/publicMedia';
import PublicMediaCard from './PublicMediaCard';
import PublicMediaDetailModal from './PublicMediaDetailModal';

const PAGE_SIZE = 30;

export default function PublicMediaLibrary() {
  const { t } = useTranslation();

  // Filters
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [page, setPage] = useState(1);

  // Data
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [totalAvailable, setTotalAvailable] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Modal
  const [selectedItem, setSelectedItem] = useState(null);
  const [modalOpen, setModalOpen] = useState(false);

  // Debounce search
  const searchTimer = useRef(null);
  useEffect(() => {
    clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
    }, 400);
    return () => clearTimeout(searchTimer.current);
  }, [search]);

  // Fetch stats and tags once
  useEffect(() => {
    getPublicMediaStats().then((s) => setTotalAvailable(s.total)).catch(() => {});
  }, []);

  // Fetch items when filters change. Wrapped in an async IIFE so the spinner
  // setState calls aren't in the synchronous effect body (set-state-in-effect).
  useEffect(() => {
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await getPublicMedia({
          search: debouncedSearch || undefined,
          page,
          pageSize: PAGE_SIZE,
        });
        setItems(data.items);
        setTotal(data.total);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    })();
  }, [debouncedSearch, page]);

  const totalPages = Math.ceil(total / PAGE_SIZE);

  const openDetail = useCallback((item) => {
    setSelectedItem(item);
    setModalOpen(true);
  }, []);

  const handleNavigate = useCallback(
    (direction) => {
      const idx = items.findIndex((i) => i.id === selectedItem?.id);
      const nextIdx = idx + direction;
      if (nextIdx >= 0 && nextIdx < items.length) {
        setSelectedItem(items[nextIdx]);
      }
    },
    [items, selectedItem],
  );

  const currentIdx = items.findIndex((i) => i.id === selectedItem?.id);

  return (
    <div>
      {/* Intro text */}
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-gray-900">
          {t('edu_materials.media_library.intro_heading')}
        </h1>
        <p className="mt-2 text-sm text-gray-600 leading-relaxed max-w-3xl">
          {t('edu_materials.media_library.intro_text')}
        </p>
      </div>

      {/* Search + stats */}
      <div className="flex flex-wrap items-center gap-3 mb-5">
        <div className="relative flex-1 min-w-[200px] max-w-sm">
          <MagnifyingGlassIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('edu_materials.media_library.search_placeholder')}
            className="w-full rounded-md border border-gray-300 pl-9 pr-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
          />
        </div>

        {/* Stats */}
        <div className="flex items-center gap-3 ml-auto text-sm text-gray-500">
          {totalAvailable != null && (
            <span>{t('edu_materials.media_library.total_images', { count: totalAvailable })}</span>
          )}
          {debouncedSearch && (
            <>
              <span className="text-gray-300">|</span>
              <span>{t('edu_materials.media_library.results_count', { count: total })}</span>
            </>
          )}
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-md bg-red-50 p-4 mb-5">
          <p className="text-sm text-red-700">{error}</p>
        </div>
      )}

      {/* Grid */}
      {loading ? (
        <div className="flex items-center justify-center py-20">
          <div className="text-gray-400">{t('edu_materials.media_library.loading')}</div>
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-gray-400">
          <p className="text-lg font-medium">{t('edu_materials.media_library.no_results')}</p>
          <p className="text-sm mt-1">{t('edu_materials.media_library.no_results_hint')}</p>
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 2xl:grid-cols-6 gap-3">
          {items.map((item) => (
            <PublicMediaCard key={item.id} item={item} onClick={openDetail} />
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-3 mt-6 pt-4 border-t border-gray-200">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {t('edu_materials.media_library.previous')}
          </button>
          <span className="text-sm text-gray-600">
            {page} / {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page === totalPages}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {t('edu_materials.media_library.next')}
          </button>
        </div>
      )}

      {/* Detail modal */}
      <PublicMediaDetailModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        item={selectedItem}
        onNavigate={handleNavigate}
        hasPrev={currentIdx > 0}
        hasNext={currentIdx < items.length - 1}
      />
    </div>
  );
}