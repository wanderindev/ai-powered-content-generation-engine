import { Fragment, useState, useEffect } from 'react';
import { Dialog, DialogPanel, DialogTitle, Transition, TransitionChild } from '@headlessui/react';
import {
  XMarkIcon,
  ArrowTopRightOnSquareIcon,
  CheckIcon,
  XCircleIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
} from '@heroicons/react/24/outline';
import { adminService } from '../../services/admin';

function scoreBadge(score) {
  if (score == null) return { color: 'bg-gray-100 text-gray-600', label: 'Unscored' };
  if (score >= 0.8) return { color: 'bg-green-100 text-green-700', label: score.toFixed(2) };
  if (score >= 0.5) return { color: 'bg-yellow-100 text-yellow-700', label: score.toFixed(2) };
  return { color: 'bg-red-100 text-red-700', label: score.toFixed(2) };
}

function formatFileSize(bytes) {
  if (!bytes) return null;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function MediaDetailModal({
  open,
  onClose,
  candidate,
  token,
  onStatusChange,
  onNavigate,
  hasPrev,
  hasNext,
}) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState(null);
  const [notes, setNotes] = useState('');
  const [imgLoaded, setImgLoaded] = useState(false);

  useEffect(() => {
    if (!candidate) return;
    setDetail(null);
    setLoading(true);
    setError(null);
    setNotes('');
    setImgLoaded(false);
    adminService
      .getMediaCandidate(token, candidate.id)
      .then(setDetail)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [candidate, token]);

  // Keyboard navigation
  useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      if (e.key === 'ArrowLeft' && hasPrev) onNavigate(-1);
      if (e.key === 'ArrowRight' && hasNext) onNavigate(1);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, hasPrev, hasNext, onNavigate]);

  const handleApprove = async () => {
    setActionLoading(true);
    try {
      const updated = await adminService.approveCandidate(token, candidate.id, notes || undefined);
      onStatusChange(updated);
    } catch (err) {
      setError(err.message);
    } finally {
      setActionLoading(false);
    }
  };

  const handleReject = async () => {
    setActionLoading(true);
    try {
      const updated = await adminService.rejectCandidate(token, candidate.id, notes || undefined);
      onStatusChange(updated);
    } catch (err) {
      setError(err.message);
    } finally {
      setActionLoading(false);
    }
  };

  const d = detail;
  const badge = d ? scoreBadge(d.relevance_score) : null;

  return (
    <Transition appear show={open} as={Fragment}>
      <Dialog as="div" className="relative z-50" onClose={onClose}>
        <TransitionChild
          as={Fragment}
          enter="ease-out duration-200"
          enterFrom="opacity-0"
          enterTo="opacity-100"
          leave="ease-in duration-150"
          leaveFrom="opacity-100"
          leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-gray-900/60" />
        </TransitionChild>

        <div className="fixed inset-0 overflow-y-auto">
          <div className="flex min-h-full items-center justify-center p-4">
            <TransitionChild
              as={Fragment}
              enter="ease-out duration-200"
              enterFrom="opacity-0 scale-95"
              enterTo="opacity-100 scale-100"
              leave="ease-in duration-150"
              leaveFrom="opacity-100 scale-100"
              leaveTo="opacity-0 scale-95"
            >
              <DialogPanel className="relative w-full max-w-4xl transform overflow-hidden rounded-xl bg-white shadow-2xl transition-all">
                {/* Close button */}
                <button
                  onClick={onClose}
                  className="absolute top-3 right-3 z-10 rounded-full bg-white/80 p-1 text-gray-500 hover:text-gray-700 hover:bg-white"
                >
                  <XMarkIcon className="h-5 w-5" />
                </button>

                {/* Nav arrows */}
                {hasPrev && (
                  <button
                    onClick={() => onNavigate(-1)}
                    className="absolute left-3 top-1/2 -translate-y-1/2 z-10 rounded-full bg-white/80 p-1.5 text-gray-500 hover:text-gray-700 hover:bg-white shadow"
                  >
                    <ChevronLeftIcon className="h-5 w-5" />
                  </button>
                )}
                {hasNext && (
                  <button
                    onClick={() => onNavigate(1)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 z-10 rounded-full bg-white/80 p-1.5 text-gray-500 hover:text-gray-700 hover:bg-white shadow"
                  >
                    <ChevronRightIcon className="h-5 w-5" />
                  </button>
                )}

                {loading ? (
                  <div className="flex items-center justify-center h-96">
                    <div className="text-gray-400">Loading...</div>
                  </div>
                ) : error && !d ? (
                  <div className="flex items-center justify-center h-96 text-red-500">{error}</div>
                ) : d ? (
                  <div className="flex flex-col md:flex-row">
                    {/* Image */}
                    <div className="md:w-1/2 bg-gray-50 flex items-center justify-center min-h-[300px] max-h-[500px]">
                      {!imgLoaded && (
                        <div className="absolute text-gray-300 text-sm">Loading image...</div>
                      )}
                      <img
                        src={d.media_id ? (d.thumbnail_cdn_url || d.external_url) : d.external_url}
                        alt={d.title}
                        onLoad={() => setImgLoaded(true)}
                        className={`max-h-[500px] w-full object-contain transition-opacity ${imgLoaded ? 'opacity-100' : 'opacity-0'}`}
                      />
                    </div>

                    {/* Details */}
                    <div className="md:w-1/2 p-5 flex flex-col gap-4 overflow-y-auto max-h-[500px]">
                      <DialogTitle className="text-lg font-semibold text-gray-900 pr-8">
                        {d.title}
                      </DialogTitle>

                      {/* Score + Status */}
                      <div className="flex items-center gap-2">
                        {badge && (
                          <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${badge.color}`}>
                            Score: {badge.label}
                          </span>
                        )}
                        <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                          d.status === 'APPROVED' ? 'bg-green-100 text-green-700'
                            : d.status === 'REJECTED' ? 'bg-red-100 text-red-700'
                            : 'bg-gray-100 text-gray-600'
                        }`}>
                          {d.status}
                        </span>
                      </div>

                      {/* Description */}
                      {d.description && (
                        <p className="text-sm text-gray-600 leading-relaxed">{d.description}</p>
                      )}

                      {/* Relevance notes */}
                      {d.relevance_notes && (
                        <p className="text-xs text-gray-500 italic">AI: {d.relevance_notes}</p>
                      )}

                      {/* Metadata grid */}
                      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                        {d.author && (
                          <>
                            <dt className="text-gray-500">Author</dt>
                            <dd className="text-gray-900 truncate" title={d.author}>{d.author}</dd>
                          </>
                        )}
                        {d.license && (
                          <>
                            <dt className="text-gray-500">License</dt>
                            <dd className="text-gray-900">
                              {d.license_url ? (
                                <a href={d.license_url} target="_blank" rel="noopener noreferrer" className="text-indigo-600 hover:underline">
                                  {d.license}
                                </a>
                              ) : d.license}
                            </dd>
                          </>
                        )}
                        {d.width && d.height && (
                          <>
                            <dt className="text-gray-500">Dimensions</dt>
                            <dd className="text-gray-900">{d.width} x {d.height}</dd>
                          </>
                        )}
                        {d.file_size && (
                          <>
                            <dt className="text-gray-500">Size</dt>
                            <dd className="text-gray-900">{formatFileSize(d.file_size)}</dd>
                          </>
                        )}
                        {d.mime_type && (
                          <>
                            <dt className="text-gray-500">Type</dt>
                            <dd className="text-gray-900">{d.mime_type}</dd>
                          </>
                        )}
                      </dl>

                      {/* Categories */}
                      {d.source_categories?.length > 0 && (
                        <div>
                          <p className="text-xs font-medium text-gray-500 mb-1">Categories</p>
                          <div className="flex flex-wrap gap-1">
                            {d.source_categories.slice(0, 10).map((cat) => (
                              <span key={cat} className="inline-flex rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-600">
                                {cat}
                              </span>
                            ))}
                            {d.source_categories.length > 10 && (
                              <span className="text-[10px] text-gray-400">+{d.source_categories.length - 10} more</span>
                            )}
                          </div>
                        </div>
                      )}

                      {/* Wikimedia link */}
                      {d.page_url && (
                        <a
                          href={d.page_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-sm text-indigo-600 hover:underline"
                        >
                          View on Wikimedia Commons
                          <ArrowTopRightOnSquareIcon className="h-3.5 w-3.5" />
                        </a>
                      )}

                      {/* Review actions */}
                      {d.status === 'PENDING' && (
                        <div className="border-t pt-3 mt-auto">
                          <textarea
                            value={notes}
                            onChange={(e) => setNotes(e.target.value)}
                            placeholder="Review notes (optional)"
                            rows={2}
                            className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                          />
                          {error && <p className="text-xs text-red-500 mt-1">{error}</p>}
                          <div className="flex gap-2 mt-2">
                            <button
                              onClick={handleApprove}
                              disabled={actionLoading}
                              className="flex-1 inline-flex justify-center items-center gap-1.5 rounded-md bg-green-600 px-3 py-2 text-sm font-semibold text-white hover:bg-green-500 disabled:opacity-50"
                            >
                              <CheckIcon className="h-4 w-4" />
                              {actionLoading ? 'Saving...' : 'Approve'}
                            </button>
                            <button
                              onClick={handleReject}
                              disabled={actionLoading}
                              className="flex-1 inline-flex justify-center items-center gap-1.5 rounded-md bg-red-600 px-3 py-2 text-sm font-semibold text-white hover:bg-red-500 disabled:opacity-50"
                            >
                              <XCircleIcon className="h-4 w-4" />
                              {actionLoading ? 'Saving...' : 'Reject'}
                            </button>
                          </div>
                        </div>
                      )}

                      {/* Already reviewed */}
                      {d.status !== 'PENDING' && d.review_notes && (
                        <div className="border-t pt-3">
                          <p className="text-xs text-gray-500">
                            <span className="font-medium">Review notes:</span> {d.review_notes}
                          </p>
                        </div>
                      )}
                    </div>
                  </div>
                ) : null}
              </DialogPanel>
            </TransitionChild>
          </div>
        </div>
      </Dialog>
    </Transition>
  );
}
