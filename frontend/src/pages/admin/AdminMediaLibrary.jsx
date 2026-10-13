import { useState, useEffect, useCallback, useRef } from 'react';
import { useAdminAuth } from '../../contexts/AdminAuthContext';
import { adminService } from '../../services/admin';
import MediaGrid from '../../components/admin/MediaGrid';
import MediaDetailModal from '../../components/admin/MediaDetailModal';
import CrawlPanel from '../../components/admin/CrawlPanel';
import { MagnifyingGlassIcon } from '@heroicons/react/24/outline';

const SCORE_OPTIONS = [
  { label: 'All scores', value: null },
  { label: '0.9+ (Highly relevant)', value: 0.9 },
  { label: '0.8+ (Relevant)', value: 0.8 },
  { label: '0.7+ (Somewhat relevant)', value: 0.7 },
  { label: '0.5+ (Tangential)', value: 0.5 },
  { label: 'Below 0.5', value: -0.5 },
];

const STATUS_OPTIONS = [
  { label: 'All statuses', value: null },
  { label: 'Pending', value: 'PENDING' },
  { label: 'Approved', value: 'APPROVED' },
  { label: 'Rejected', value: 'REJECTED' },
];

const PAGE_SIZE_OPTIONS = [25, 50, 100];

export default function AdminMediaLibrary() {
  const { token } = useAdminAuth();

  // Filters
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [status, setStatus] = useState(null);
  const [minScore, setMinScore] = useState(0.8);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  // Bumped when a crawl/score job finishes, to re-fetch the grid.
  const [refreshKey, setRefreshKey] = useState(0);

  // Data
  const [candidates, setCandidates] = useState([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Modal
  const [selectedCandidate, setSelectedCandidate] = useState(null);
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

  // Fetch stats once
  useEffect(() => {
    adminService.getMediaStats(token).then(setStats).catch(() => {});
  }, [token]);

  // Fetch candidates when filters change. Wrapped in an async IIFE so the
  // spinner setState calls aren't in the synchronous effect body
  // (set-state-in-effect).
  useEffect(() => {
    (async () => {
      setLoading(true);
      setError(null);

      // Handle the special "below 0.5" option
      const scoreParam = minScore === -0.5 ? 0 : minScore;

      try {
        const data = await adminService.getMediaCandidates(token, {
          status,
          minScore: scoreParam,
          search: debouncedSearch || undefined,
          page,
          pageSize,
        });
        setCandidates(data.items);
        setTotal(data.total);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    })();
  }, [token, status, minScore, debouncedSearch, page, pageSize, refreshKey]);

  // When a crawl/score job completes, refresh the grid and the catalog stats.
  const handleJobComplete = useCallback(() => {
    setRefreshKey((k) => k + 1);
    adminService.getMediaStats(token).then(setStats).catch(() => {});
  }, [token]);

  const totalPages = Math.ceil(total / pageSize);

  const openDetail = useCallback((candidate) => {
    setSelectedCandidate(candidate);
    setModalOpen(true);
  }, []);

  const handleNavigate = useCallback(
    (direction) => {
      const idx = candidates.findIndex((c) => c.id === selectedCandidate?.id);
      const nextIdx = idx + direction;
      if (nextIdx >= 0 && nextIdx < candidates.length) {
        setSelectedCandidate(candidates[nextIdx]);
      }
    },
    [candidates, selectedCandidate],
  );

  const handleStatusChange = useCallback(
    (updated) => {
      // Update the candidate in the list
      setCandidates((prev) => prev.map((c) => (c.id === updated.id ? { ...c, status: updated.status } : c)));
      setSelectedCandidate((prev) => (prev?.id === updated.id ? { ...prev, status: updated.status } : prev));
      // Refresh stats
      adminService.getMediaStats(token).then(setStats).catch(() => {});
    },
    [token],
  );

  const currentIdx = candidates.findIndex((c) => c.id === selectedCandidate?.id);

  return (
    <div>
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Media Library</h1>
          {stats && (
            <p className="text-sm text-gray-500 mt-1">
              {stats.total.toLocaleString()} images
              {stats.scored > 0 && <> &middot; avg relevance {stats.avg_relevance?.toFixed(2)}</>}
              {stats.by_status?.APPROVED > 0 && (
                <> &middot; <span className="text-green-600">{stats.by_status.APPROVED} approved</span></>
              )}
            </p>
          )}
        </div>
      </div>

      {/* Crawl & scoring */}
      <CrawlPanel token={token} onComplete={handleJobComplete} />

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3 mb-5">
        {/* Search */}
        <div className="relative flex-1 min-w-[200px] max-w-sm">
          <MagnifyingGlassIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search title or description..."
            className="w-full rounded-md border border-gray-300 pl-9 pr-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
          />
        </div>

        {/* Status filter */}
        <select
          value={status || ''}
          onChange={(e) => { setStatus(e.target.value || null); setPage(1); }}
          className="rounded-md border border-gray-300 px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
        >
          {STATUS_OPTIONS.map((opt) => (
            <option key={opt.label} value={opt.value || ''}>{opt.label}</option>
          ))}
        </select>

        {/* Score filter */}
        <select
          value={minScore ?? ''}
          onChange={(e) => { setMinScore(e.target.value ? Number(e.target.value) : null); setPage(1); }}
          className="rounded-md border border-gray-300 px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
        >
          {SCORE_OPTIONS.map((opt) => (
            <option key={opt.label} value={opt.value ?? ''}>{opt.label}</option>
          ))}
        </select>

        {/* Results count */}
        <span className="text-sm text-gray-500 ml-auto">
          {total.toLocaleString()} result{total !== 1 ? 's' : ''}
        </span>
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
          <div className="text-gray-400">Loading...</div>
        </div>
      ) : (
        <MediaGrid candidates={candidates} onCardClick={openDetail} />
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-6 pt-4 border-t border-gray-200">
          <div className="flex items-center gap-2">
            <span className="text-sm text-gray-500">Per page:</span>
            {PAGE_SIZE_OPTIONS.map((size) => (
              <button
                key={size}
                onClick={() => { setPageSize(size); setPage(1); }}
                className={`rounded px-2 py-1 text-sm ${
                  pageSize === size
                    ? 'bg-indigo-100 text-indigo-700 font-semibold'
                    : 'text-gray-500 hover:bg-gray-100'
                }`}
              >
                {size}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-3">
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
        </div>
      )}

      {/* Detail modal */}
      <MediaDetailModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        candidate={selectedCandidate}
        token={token}
        onStatusChange={handleStatusChange}
        onNavigate={handleNavigate}
        hasPrev={currentIdx > 0}
        hasNext={currentIdx < candidates.length - 1}
      />
    </div>
  );
}
