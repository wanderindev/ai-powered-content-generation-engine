import { useState, useEffect, useCallback, useRef, Fragment } from 'react';
import { Dialog, Transition } from '@headlessui/react';
import { adminService } from '../../services/admin';
import MediaMetadataFields from './MediaMetadataFields';
import { IMPORT_FIELDS, importMetadata, titleFromUrl } from './mediaMetadataUtils';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

function mediaUrl(path) {
  if (!path) return '';
  if (path.startsWith('http')) return path;
  return `${API_BASE}${path}`;
}

// excludeFeatureImages defaults on for the article-body insert (don't reoffer
// images already serving as some article's hero); guide/slide editors pass
// false — for them an article's feature image is as good as any other photo.
export default function MediaPickerModal({
  open,
  onClose,
  token,
  onSelect,
  initialSearch = '',
  excludeFeatureImages = true,
}) {
  const [media, setMedia] = useState([]);
  const [mediaTotal, setMediaTotal] = useState(0);
  const [mediaPage, setMediaPage] = useState(1);
  const [mediaSearch, setMediaSearch] = useState('');
  // The search term the fetch actually uses — trails the input by a debounce
  // so typing doesn't fire a request per keystroke.
  const [querySearch, setQuerySearch] = useState('');
  const [mediaLoading, setMediaLoading] = useState(false);
  const [selectedMedia, setSelectedMedia] = useState(null);
  const [importUrl, setImportUrl] = useState('');
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState(null);
  // The metadata form revealed by "Import" (#589); null while hidden.
  const [importForm, setImportForm] = useState(null);
  const fetchSeq = useRef(0);

  const fetchMedia = useCallback(async () => {
    if (!token) return;
    const seq = ++fetchSeq.current;
    setMediaLoading(true);
    try {
      const data = await adminService.getMediaList(token, {
        search: querySearch || undefined,
        // LOCAL = images an editor imported by pasting a web URL.
        source: 'WIKIMEDIA,LOC,LOCAL',
        excludeFeatureImages,
        page: mediaPage,
        pageSize: 50,
      });
      if (seq !== fetchSeq.current) return; // superseded by a newer request
      setMedia(data.items || []);
      setMediaTotal(data.total || 0);
    } catch {
      if (seq === fetchSeq.current) setMedia([]);
    } finally {
      if (seq === fetchSeq.current) setMediaLoading(false);
    }
  }, [token, querySearch, mediaPage, excludeFeatureImages]);

  useEffect(() => {
    if (open) fetchMedia();
  }, [open, fetchMedia]);

  useEffect(() => {
    if (open) {
      setSelectedMedia(null);
      // Seed the search with the caller's context (e.g. a guide block's
      // image_query) so relevant candidates appear without retyping. Both
      // values are set so the first fetch runs immediately, undebounced.
      setMediaSearch(initialSearch || '');
      setQuerySearch(initialSearch || '');
      setMediaPage(1);
      setImportUrl('');
      setImportError(null);
      setImportForm(null);
    }
  }, [open, initialSearch]);

  // First "Import" (or Enter) reveals the metadata form, prefilled with a
  // title from the URL; "Import image" then submits it.
  const handleImportUrl = () => {
    const url = importUrl.trim();
    if (!url || importing) return;
    if (!importForm) {
      setImportForm({ title: titleFromUrl(url) });
      return;
    }
    submitImport();
  };

  const submitImport = async () => {
    const url = importUrl.trim();
    if (!url || importing || !importForm?.title?.trim()) return;
    setImporting(true);
    setImportError(null);
    try {
      const item = await adminService.importMediaFromUrl(token, url, importMetadata(importForm));
      // Surface the import immediately: selected (ready to Insert) and at the
      // front of the grid, regardless of the current search.
      setSelectedMedia(item);
      setMedia((prev) => [item, ...prev.filter((m) => m.id !== item.id)]);
      setImportUrl('');
      setImportForm(null);
    } catch (err) {
      setImportError(err.message);
    } finally {
      setImporting(false);
    }
  };

  useEffect(() => {
    if (mediaSearch === querySearch) return;
    const t = setTimeout(() => {
      setQuerySearch(mediaSearch);
      setMediaPage(1);
    }, 350);
    return () => clearTimeout(t);
  }, [mediaSearch, querySearch]);

  const totalPages = Math.ceil(mediaTotal / 50);

  return (
    <Transition appear show={open} as={Fragment}>
      <Dialog as="div" className="relative z-[60]" onClose={onClose}>
        <Transition.Child
          as={Fragment}
          enter="ease-out duration-200" enterFrom="opacity-0" enterTo="opacity-100"
          leave="ease-in duration-150" leaveFrom="opacity-100" leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-black/40" />
        </Transition.Child>

        <div className="fixed inset-0 overflow-y-auto">
          <div className="flex min-h-full items-center justify-center p-4">
            <Transition.Child
              as={Fragment}
              enter="ease-out duration-200" enterFrom="opacity-0 scale-95" enterTo="opacity-100 scale-100"
              leave="ease-in duration-150" leaveFrom="opacity-100 scale-100" leaveTo="opacity-0 scale-95"
            >
              <Dialog.Panel className="w-full max-w-3xl rounded-xl bg-white shadow-2xl flex flex-col" style={{ maxHeight: '85vh' }}>
                <div className="px-5 py-4 border-b border-gray-200">
                  <Dialog.Title className="text-lg font-semibold text-gray-900">Insert Image</Dialog.Title>
                  <p className="text-sm text-gray-500 mt-0.5">Select an image from the media library</p>
                </div>

                <div className="flex-1 flex flex-col min-h-0 px-5 py-4">
                  {/* Search */}
                  <div className="flex items-center gap-3 mb-3">
                    <input
                      type="text"
                      value={mediaSearch}
                      onChange={(e) => setMediaSearch(e.target.value)}
                      placeholder="Search images..."
                      className="flex-1 rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                    />
                    <span className="text-xs text-gray-400">{mediaTotal} images</span>
                  </div>

                  {/* Import from URL — escape hatch when the library has no good photo */}
                  <div className="flex items-center gap-2 mb-3">
                    <input
                      type="url"
                      value={importUrl}
                      onChange={(e) => { setImportUrl(e.target.value); setImportError(null); }}
                      onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleImportUrl(); } }}
                      placeholder="Or paste an image URL from the web to import it..."
                      className="flex-1 rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                    />
                    {!importForm && (
                      <button
                        onClick={handleImportUrl}
                        disabled={!importUrl.trim() || importing}
                        className="rounded bg-white px-3 py-1.5 text-sm font-medium text-indigo-600 ring-1 ring-inset ring-indigo-300 hover:bg-indigo-50 disabled:opacity-40 shrink-0"
                      >
                        Import
                      </button>
                    )}
                  </div>
                  {importForm && (
                    <div className="mb-3 rounded-md border border-indigo-100 bg-indigo-50/40 p-3 overflow-y-auto" style={{ maxHeight: '40vh' }}>
                      <MediaMetadataFields
                        values={importForm}
                        onChange={(field, value) => setImportForm((prev) => ({ ...prev, [field]: value }))}
                        fields={IMPORT_FIELDS}
                      />
                      <div className="mt-3 flex justify-end gap-2">
                        <button
                          onClick={() => { setImportForm(null); setImportError(null); }}
                          disabled={importing}
                          className="rounded px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 disabled:opacity-40"
                        >
                          Cancel
                        </button>
                        <button
                          onClick={submitImport}
                          disabled={!importUrl.trim() || !importForm.title?.trim() || importing}
                          className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-40"
                        >
                          {importing ? 'Importing...' : 'Import image'}
                        </button>
                      </div>
                    </div>
                  )}
                  {importError && (
                    <p className="mb-3 text-xs text-red-600">{importError}</p>
                  )}

                  {/* Selected preview */}
                  {selectedMedia && (
                    <div className="mb-3 flex items-center gap-3 rounded-md bg-indigo-50 p-3">
                      <img
                        src={mediaUrl(selectedMedia.thumbnail_path || selectedMedia.file_path)}
                        alt={selectedMedia.title}
                        className="h-16 w-16 rounded object-cover"
                      />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-gray-900 truncate">{selectedMedia.title}</p>
                        <p className="text-xs text-gray-500">{selectedMedia.source} &middot; {selectedMedia.license}</p>
                      </div>
                      <button
                        onClick={() => {
                          onSelect({
                            src: mediaUrl(selectedMedia.file_path),
                            alt: selectedMedia.alt_text || selectedMedia.title || '',
                            attribution: selectedMedia.attribution,
                            license: selectedMedia.license,
                            id: selectedMedia.id,
                            filePath: selectedMedia.file_path,
                          });
                          onClose();
                        }}
                        className="rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 shrink-0"
                      >
                        Insert
                      </button>
                      <button
                        onClick={() => setSelectedMedia(null)}
                        className="rounded px-2 py-1 text-xs text-gray-500 hover:bg-gray-200 shrink-0"
                      >
                        Cancel
                      </button>
                    </div>
                  )}

                  {/* Grid */}
                  <div className="overflow-y-auto flex-1 min-h-0">
                    {mediaLoading ? (
                      <div className="flex items-center justify-center py-12 text-gray-400 text-sm">Loading...</div>
                    ) : media.length === 0 ? (
                      <div className="flex items-center justify-center py-12 text-gray-400 text-sm">No images found.</div>
                    ) : (
                      <div className="grid grid-cols-4 sm:grid-cols-5 gap-2">
                        {media.map((m) => (
                          <button
                            key={m.id}
                            onClick={() => setSelectedMedia(m)}
                            className={`group relative aspect-square rounded-md overflow-hidden border-2 transition-all ${
                              selectedMedia?.id === m.id
                                ? 'border-indigo-500 ring-2 ring-indigo-200'
                                : 'border-transparent hover:border-gray-300'
                            }`}
                          >
                            <img
                              src={mediaUrl(m.thumbnail_path || m.file_path)}
                              alt={m.title}
                              loading="lazy"
                              className="h-full w-full object-cover"
                            />
                            <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/60 to-transparent p-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                              <p className="text-[10px] text-white truncate">{m.title}</p>
                            </div>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Pagination */}
                  {totalPages > 1 && (
                    <div className="flex items-center justify-between mt-3 pt-3 border-t border-gray-100">
                      <button
                        onClick={() => setMediaPage((p) => Math.max(1, p - 1))}
                        disabled={mediaPage <= 1}
                        className="text-xs text-gray-500 hover:text-gray-700 disabled:opacity-30"
                      >
                        Previous
                      </button>
                      <span className="text-xs text-gray-400">Page {mediaPage} of {totalPages}</span>
                      <button
                        onClick={() => setMediaPage((p) => Math.min(totalPages, p + 1))}
                        disabled={mediaPage >= totalPages}
                        className="text-xs text-gray-500 hover:text-gray-700 disabled:opacity-30"
                      >
                        Next
                      </button>
                    </div>
                  )}
                </div>

                {/* Footer */}
                <div className="px-5 py-3 border-t border-gray-200 flex justify-end">
                  <button
                    onClick={onClose}
                    className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
                  >
                    Cancel
                  </button>
                </div>
              </Dialog.Panel>
            </Transition.Child>
          </div>
        </div>
      </Dialog>
    </Transition>
  );
}
