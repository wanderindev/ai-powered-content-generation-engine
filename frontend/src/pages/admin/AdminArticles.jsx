import { useState, useEffect, useMemo, useCallback, useRef, Fragment } from 'react';
import { Dialog, Transition } from '@headlessui/react';
import { Menu, MenuButton, MenuItems, MenuItem } from '@headlessui/react';
import { AllCommunityModule, themeAlpine } from 'ag-grid-community';
import { AgGridProvider, AgGridReact } from 'ag-grid-react';
import {
  PencilSquareIcon,
  SparklesIcon,
  ArrowDownTrayIcon,
  LanguageIcon,
  LinkIcon,
  PhotoIcon,
  CalendarIcon,
  ShieldCheckIcon,
} from '@heroicons/react/24/outline';
import { useAdminAuth } from '../../contexts/AdminAuthContext';
import { adminService } from '../../services/admin';
import { BooleanCell } from '../../components/admin/SharedCellRenderers';
import InlineStatusCell from '../../components/admin/InlineStatusCell';
import EditDrawer from '../../components/admin/EditDrawer';
import FactCheckStatusCell from '../../components/admin/FactCheckStatusCell';
import FactCheckModal from '../../components/admin/FactCheckModal';
import ConfirmDialog from '../../components/admin/ConfirmDialog';
import { ToastContainer } from '../../components/admin/Toast';
import { useToast } from '../../hooks/useToast';
import { useFactCheck, factCheckWarning } from '../../hooks/useFactCheck';

const modules = [AllCommunityModule];

function ArticleActionsCellRenderer({ data, colDef }) {
  const {
    onEdit, onGenerateTags, onTranslate, onSeriesSections,
    onFeatureImage, onPublish, onDownload,
    onFactCheck, isFactChecking,
  } = colDef.cellRendererParams || {};

  const showTags = data.has_content && !data.has_tags;
  const showTranslate = data.status === 'APPROVED' && data.has_content && !data.has_spanish;
  const showSeries = data.is_series && data.has_spanish && !data.has_series_sections;
  const showImage = data.has_content && !data.has_feature_image;
  const showPublish = data.status === 'APPROVED' && data.has_spanish && data.has_feature_image
    && (!data.is_series || data.has_series_sections) && !data.published_at;
  const isPublished = !!data.published_at;
  // Grounding check needs written content (#375/#378).
  const showFactCheck = data.has_content;
  const factChecking = isFactChecking ? isFactChecking(data) : false;

  return (
    <div className="flex items-center gap-1">
      <button
        onClick={() => onEdit?.(data)}
        className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-indigo-600"
        title="Edit"
      >
        <PencilSquareIcon className="h-4.5 w-4.5" />
      </button>

      {showFactCheck && (
        <button
          onClick={() => !factChecking && onFactCheck?.(data)}
          disabled={factChecking}
          className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-blue-600 disabled:animate-pulse disabled:cursor-wait disabled:text-blue-400"
          title={factChecking ? 'Fact-check running...' : 'Run fact-check (grounding)'}
        >
          <ShieldCheckIcon className="h-4.5 w-4.5" />
        </button>
      )}

      {showTags && (
        <button
          onClick={() => onGenerateTags?.(data)}
          className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-amber-600"
          title="Generate Tags"
        >
          <SparklesIcon className="h-4.5 w-4.5" />
        </button>
      )}

      {showTranslate && (
        <button
          onClick={() => onTranslate?.(data)}
          className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-indigo-600"
          title="Translate to Spanish"
        >
          <LanguageIcon className="h-4.5 w-4.5" />
        </button>
      )}

      {showSeries && (
        <button
          onClick={() => onSeriesSections?.(data)}
          className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-purple-600"
          title="Generate Series Sections"
        >
          <LinkIcon className="h-4.5 w-4.5" />
        </button>
      )}

      {showImage && (
        <button
          onClick={() => onFeatureImage?.(data)}
          className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-teal-600"
          title="Feature Image"
        >
          <PhotoIcon className="h-4.5 w-4.5" />
        </button>
      )}

      {showPublish && (
        <button
          onClick={() => onPublish?.(data)}
          className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-green-600"
          title="Publish"
        >
          <CalendarIcon className="h-4.5 w-4.5" />
        </button>
      )}

      {isPublished && (
        <span className="rounded p-1 text-green-500" title={`Published: ${new Date(data.published_at).toLocaleDateString()}`}>
          <CalendarIcon className="h-4.5 w-4.5" />
        </span>
      )}

      <Menu as="div" className="relative inline-block">
        <MenuButton className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-green-600" title="Download">
          <ArrowDownTrayIcon className="h-4.5 w-4.5" />
        </MenuButton>
        <MenuItems
          portal
          anchor="bottom end"
          className="z-50 w-36 rounded-md bg-white py-1 shadow-lg ring-1 ring-black/5 outline-none [--anchor-gap:4px]"
        >
          <MenuItem disabled={!data.has_content}>
            <button
              onClick={() => data.has_content && onDownload?.(data, 'en')}
              disabled={!data.has_content}
              className="block w-full px-3 py-1.5 text-left text-sm text-gray-700 data-focus:bg-gray-50 data-focus:text-gray-900 disabled:cursor-not-allowed disabled:text-gray-300"
            >
              English
            </button>
          </MenuItem>
          <MenuItem disabled={!data.has_spanish}>
            <button
              onClick={() => data.has_spanish && onDownload?.(data, 'es')}
              disabled={!data.has_spanish}
              className="block w-full px-3 py-1.5 text-left text-sm text-gray-700 data-focus:bg-gray-50 data-focus:text-gray-900 disabled:cursor-not-allowed disabled:text-gray-300"
            >
              Spanish
            </button>
          </MenuItem>
        </MenuItems>
      </Menu>
    </div>
  );
}

function PublishModal({ open, onClose, article, token, onSuccess }) {
  const [dateValue, setDateValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (open) {
      setDateValue(new Date().toISOString().slice(0, 16));
      setError(null);
    }
  }, [open]);

  const handlePublish = async () => {
    if (!dateValue || !article) return;
    setLoading(true);
    setError(null);
    try {
      await adminService.publishArticle(token, article.id, new Date(dateValue).toISOString());
      onSuccess?.(article.id, dateValue);
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  // Gate dismissal while the publish call is in flight (#122).
  const handleClose = loading ? () => {} : onClose;

  return (
    <Transition appear show={open} as={Fragment}>
      <Dialog as="div" className="relative z-50" onClose={handleClose}>
        <Transition.Child
          as={Fragment}
          enter="ease-out duration-200" enterFrom="opacity-0" enterTo="opacity-100"
          leave="ease-in duration-150" leaveFrom="opacity-100" leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-black/30" />
        </Transition.Child>
        <div className="fixed inset-0 flex items-center justify-center p-4">
          <Transition.Child
            as={Fragment}
            enter="ease-out duration-200" enterFrom="opacity-0 scale-95" enterTo="opacity-100 scale-100"
            leave="ease-in duration-150" leaveFrom="opacity-100 scale-100" leaveTo="opacity-0 scale-95"
          >
            <Dialog.Panel className="w-full max-w-sm max-h-[80vh] overflow-y-auto rounded-lg bg-white p-6 shadow-xl">
              <Dialog.Title className="text-lg font-semibold text-gray-900">
                Publish Article
              </Dialog.Title>
              <p className="mt-1 text-sm text-gray-500">{article?.title}</p>
              <div className="mt-4">
                <label className="block text-sm font-medium text-gray-700">Publish date</label>
                <input
                  type="datetime-local"
                  value={dateValue}
                  onChange={(e) => setDateValue(e.target.value)}
                  disabled={loading}
                  className="mt-1 w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none disabled:opacity-50"
                />
                {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
              </div>
              <div className="mt-6 flex justify-end gap-3">
                <button
                  onClick={onClose}
                  disabled={loading}
                  className="rounded px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Cancel
                </button>
                <button
                  onClick={handlePublish}
                  disabled={!dateValue || loading}
                  className="rounded bg-green-600 px-4 py-2 text-sm text-white hover:bg-green-700 disabled:opacity-50"
                >
                  {loading ? 'Publishing...' : 'Publish'}
                </button>
              </div>
            </Dialog.Panel>
          </Transition.Child>
        </div>
      </Dialog>
    </Transition>
  );
}

function GenerateArticleModal({ open, onClose, token, onSuccess }) {
  const [articles, setArticles] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [loading, setLoading] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  useEffect(() => {
    if (!open) return;
    setSelectedId('');
    setError(null);
    setResult(null);
    setFetching(true);
    adminService.getArticlesForWriting(token)
      .then((data) => setArticles(data))
      .catch((err) => setError(err.message))
      .finally(() => setFetching(false));
  }, [open, token]);

  const handleGenerate = async () => {
    if (!selectedId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await adminService.writeArticle(token, Number(selectedId));
      setResult(res);
      onSuccess?.();
      setTimeout(() => onClose(), 2000);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  // Gate dismissal while writeArticle is in flight (#122) — this is a multi-minute
  // LLM call; backdrop click or Escape would lose the success-confirmation panel.
  const handleClose = loading ? () => {} : onClose;

  return (
    <Transition appear show={open} as={Fragment}>
      <Dialog as="div" className="relative z-50" onClose={handleClose}>
        <Transition.Child
          as={Fragment}
          enter="ease-out duration-200" enterFrom="opacity-0" enterTo="opacity-100"
          leave="ease-in duration-150" leaveFrom="opacity-100" leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-black/30" />
        </Transition.Child>
        <div className="fixed inset-0 flex items-center justify-center p-4">
          <Transition.Child
            as={Fragment}
            enter="ease-out duration-200" enterFrom="opacity-0 scale-95" enterTo="opacity-100 scale-100"
            leave="ease-in duration-150" leaveFrom="opacity-100 scale-100" leaveTo="opacity-0 scale-95"
          >
            <Dialog.Panel className="w-full max-w-md max-h-[80vh] overflow-y-auto rounded-lg bg-white p-6 shadow-xl">
              <Dialog.Title className="text-lg font-semibold text-gray-900">
                Generate Article
              </Dialog.Title>
              <div className="mt-4">
                {fetching ? (
                  <p className="text-sm text-gray-500">Loading articles ready for writing...</p>
                ) : articles.length === 0 ? (
                  <p className="text-sm text-gray-500">No articles with approved outlines are ready for writing.</p>
                ) : (
                  <select
                    className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                    value={selectedId}
                    onChange={(e) => setSelectedId(e.target.value)}
                    disabled={loading}
                  >
                    <option value="">Select an article...</option>
                    {articles.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.title} ({a.category_name})
                      </option>
                    ))}
                  </select>
                )}
                {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
                {result && (
                  <div className="mt-2 rounded bg-green-50 p-3 text-sm text-green-800">
                    Generated article: <strong>{result.title}</strong> ({result.word_count.toLocaleString()} words)
                  </div>
                )}
                {loading && (
                  <p className="mt-2 text-xs text-gray-500">Generation in progress — this may take a few minutes. Please wait.</p>
                )}
              </div>
              <div className="mt-6 flex justify-end gap-3">
                <button
                  onClick={onClose}
                  disabled={loading}
                  className="rounded px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Cancel
                </button>
                <button
                  onClick={handleGenerate}
                  disabled={!selectedId || loading}
                  className="rounded bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-700 disabled:opacity-50"
                >
                  {loading ? 'Generating...' : 'Generate'}
                </button>
              </div>
            </Dialog.Panel>
          </Transition.Child>
        </div>
      </Dialog>
    </Transition>
  );
}

function FeatureImageModal({ open, onClose, article, token, onSuccess }) {
  const [tab, setTab] = useState('prompt');
  const [promptText, setPromptText] = useState('');
  const [generating, setGenerating] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [uploadResult, setUploadResult] = useState(null);

  useEffect(() => {
    if (open) {
      setPromptText('');
      setError(null);
      setCopied(false);
      setSelectedFile(null);
      setPreview(null);
      setUploadResult(null);
      setTab('prompt');
    }
  }, [open]);

  useEffect(() => {
    if (!selectedFile) { setPreview(null); return; }
    const url = URL.createObjectURL(selectedFile);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [selectedFile]);

  const handleGenerate = async () => {
    if (!article) return;
    setGenerating(true);
    setError(null);
    try {
      const res = await adminService.generateImagePrompt(token, article.id);
      setPromptText(res.prompt_text);
    } catch (err) {
      setError(err.message);
    } finally {
      setGenerating(false);
    }
  };

  const handleCopy = async () => {
    await navigator.clipboard.writeText(promptText);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleUpload = async () => {
    if (!selectedFile || !article) return;
    setUploading(true);
    setError(null);
    try {
      await adminService.uploadFeatureImage(token, article.id, selectedFile);
      setUploadResult(true);
      onSuccess?.(article.id);
      setTimeout(() => onClose(), 1500);
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
    }
  };

  // Gate dismissal while a prompt-generation or image-upload is in flight (#122).
  const busy = generating || uploading;
  const handleClose = busy ? () => {} : onClose;

  return (
    <Transition appear show={open} as={Fragment}>
      <Dialog as="div" className="relative z-50" onClose={handleClose}>
        <Transition.Child
          as={Fragment}
          enter="ease-out duration-200" enterFrom="opacity-0" enterTo="opacity-100"
          leave="ease-in duration-150" leaveFrom="opacity-100" leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-black/30" />
        </Transition.Child>
        <div className="fixed inset-0 flex items-center justify-center p-4">
          <Transition.Child
            as={Fragment}
            enter="ease-out duration-200" enterFrom="opacity-0 scale-95" enterTo="opacity-100 scale-100"
            leave="ease-in duration-150" leaveFrom="opacity-100 scale-100" leaveTo="opacity-0 scale-95"
          >
            <Dialog.Panel className="w-full max-w-lg max-h-[80vh] overflow-y-auto rounded-lg bg-white p-6 shadow-xl">
              <Dialog.Title className="text-lg font-semibold text-gray-900">
                Feature Image
              </Dialog.Title>
              <p className="mt-1 text-sm text-gray-500 truncate">{article?.title}</p>

              {/* Tab switcher */}
              <div className="mt-4 flex gap-1 rounded-lg bg-gray-100 p-1">
                <button
                  onClick={() => setTab('prompt')}
                  disabled={busy}
                  className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${tab === 'prompt' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                >
                  Generate Prompt
                </button>
                <button
                  onClick={() => setTab('upload')}
                  disabled={busy}
                  className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${tab === 'upload' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                >
                  Upload Image
                </button>
              </div>

              {/* Prompt tab */}
              {tab === 'prompt' && (
                <div className="mt-4">
                  {!promptText ? (
                    <button
                      onClick={handleGenerate}
                      disabled={generating}
                      className="rounded bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-700 disabled:opacity-50"
                    >
                      {generating ? 'Generating...' : 'Generate Prompt'}
                    </button>
                  ) : (
                    <>
                      <textarea
                        readOnly
                        value={promptText}
                        rows={8}
                        className="w-full rounded border border-gray-300 bg-gray-50 px-3 py-2 text-sm font-mono focus:outline-none"
                      />
                      <button
                        onClick={handleCopy}
                        className="mt-2 rounded bg-gray-100 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-200"
                      >
                        {copied ? 'Copied!' : 'Copy to Clipboard'}
                      </button>
                    </>
                  )}
                  {generating && (
                    <p className="mt-2 text-xs text-gray-500">Generation in progress — please wait.</p>
                  )}
                </div>
              )}

              {/* Upload tab */}
              {tab === 'upload' && (
                <div className="mt-4">
                  <input
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    onChange={(e) => setSelectedFile(e.target.files?.[0] || null)}
                    disabled={uploading}
                    className="block w-full text-sm text-gray-500 file:mr-3 file:rounded file:border-0 file:bg-gray-100 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-gray-700 hover:file:bg-gray-200 disabled:opacity-50"
                  />
                  {preview && (
                    <img
                      src={preview}
                      alt="Preview"
                      className="mt-3 max-h-48 w-full rounded border border-gray-200 object-contain"
                    />
                  )}
                  {selectedFile && !uploadResult && (
                    <button
                      onClick={handleUpload}
                      disabled={uploading}
                      className="mt-3 rounded bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-700 disabled:opacity-50"
                    >
                      {uploading ? 'Uploading...' : 'Upload'}
                    </button>
                  )}
                  {uploadResult && (
                    <div className="mt-3 rounded bg-green-50 p-3 text-sm text-green-800">
                      Feature image uploaded successfully!
                    </div>
                  )}
                  {uploading && (
                    <p className="mt-2 text-xs text-gray-500">Upload in progress — please wait.</p>
                  )}
                </div>
              )}

              {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

              <div className="mt-6 flex justify-end">
                <button
                  onClick={onClose}
                  disabled={busy}
                  className="rounded px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Close
                </button>
              </div>
            </Dialog.Panel>
          </Transition.Child>
        </div>
      </Dialog>
    </Transition>
  );
}

export default function AdminArticles() {
  const { token } = useAdminAuth();
  const gridRef = useRef(null);
  const [rowData, setRowData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedRecord, setSelectedRecord] = useState(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [publishArticle, setPublishArticle] = useState(null);
  const [featureImageArticle, setFeatureImageArticle] = useState(null);
  const [categoryFilter, setCategoryFilter] = useState('');
  const [errorDialog, setErrorDialog] = useState(null);
  const { toasts, show: showToast, dismiss: dismissToast } = useToast();
  const [approveWarning, setApproveWarning] = useState(null);
  const factCheck = useFactCheck({
    token,
    subjectType: 'ARTICLE',
    entityType: 'articles',
    showToast,
    dismissToast,
    titleOf: (r) => r.title,
  });

  const refreshGrid = useCallback(() => {
    adminService.getArticles(token)
      .then(setRowData)
      .catch((err) => {
        console.error('Refresh failed:', err);
        showToast({ type: 'error', title: 'Failed to refresh', message: 'Data may be out of date' });
      });
  }, [token, showToast]);

  const handleEdit = useCallback((record) => {
    setSelectedRecord(record);
    setDrawerOpen(true);
  }, []);

  const handleDownload = useCallback(async (record, lang = 'en') => {
    try {
      const { blob, filename } = await adminService.downloadArticle(token, record.id, lang);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Download failed:', err);
      showToast({ type: 'error', title: 'Failed to download', message: err.message || 'Unexpected error' });
    }
  }, [token, showToast]);

  const applyStatusChange = useCallback(async (record, newStatus) => {
    try {
      await adminService.updateStatus(token, 'articles', record.id, newStatus);
      setRowData((prev) =>
        prev.map((r) => (r.id === record.id ? { ...r, status: newStatus } : r)),
      );
    } catch (err) {
      console.error('Status update failed:', err);
      showToast({ type: 'error', title: 'Failed to update status', message: err.message || 'Unexpected error' });
    }
  }, [token, showToast]);

  const handleStatusChange = useCallback(async (record, newStatus) => {
    // Warn — never block — when approving without a clean fact-check (#378).
    if (newStatus === 'APPROVED') {
      const warning = factCheckWarning(factCheck.getCounts(record), 'article');
      if (warning) {
        setApproveWarning({ record, newStatus, warning });
        return;
      }
    }
    await applyStatusChange(record, newStatus);
  }, [applyStatusChange, factCheck]);

  const handleGenerateTags = useCallback(async (record) => {
    try {
      await adminService.generateTags(token, record.id);
      setRowData((prev) =>
        prev.map((r) => (r.id === record.id ? { ...r, has_tags: true } : r)),
      );
    } catch (err) {
      console.error('Tag generation failed:', err);
      showToast({ type: 'error', title: 'Failed to generate tags', message: err.message || 'Unexpected error' });
    }
  }, [token, showToast]);

  const handleTranslate = useCallback(async (record) => {
    const toastId = showToast({ type: 'info', title: 'Translating...', message: record.title, duration: 0 });
    try {
      await adminService.translateArticle(token, record.id);
      dismissToast(toastId);
      setRowData((prev) =>
        prev.map((r) => (r.id === record.id ? { ...r, has_spanish: true } : r)),
      );
      showToast({ type: 'success', title: 'Translation complete', message: record.title });
    } catch (err) {
      dismissToast(toastId);
      console.error('Translation failed:', err);
      showToast({ type: 'error', title: 'Translation failed', message: err.message });
    }
  }, [token, showToast, dismissToast]);

  const handleSeriesSections = useCallback(async (record) => {
    const toastId = showToast({ type: 'info', title: 'Generating series sections...', message: record.title, duration: 0 });
    try {
      const res = await adminService.generateSeriesSections(token, record.id);
      dismissToast(toastId);
      if (res.success) {
        // Update all articles in the same series
        setRowData((prev) =>
          prev.map((r) => {
            const sameParent = r.series_parent_id === record.series_parent_id
              || r.id === record.series_parent_id
              || r.series_parent_id === record.id;
            const isSelf = r.id === record.id;
            return (isSelf || sameParent) ? { ...r, has_series_sections: true } : r;
          }),
        );
        showToast({ type: 'success', title: 'Series sections generated', message: record.title });
      } else {
        setErrorDialog({ title: 'Series Sections Failed', message: res.message });
      }
    } catch (err) {
      dismissToast(toastId);
      setErrorDialog({ title: 'Series Sections Failed', message: err.message });
    }
  }, [token, showToast, dismissToast]);

  const handleFeatureImage = useCallback((record) => {
    setFeatureImageArticle(record);
  }, []);

  const handleFeatureImageSuccess = useCallback((articleId) => {
    setRowData((prev) =>
      prev.map((r) => (r.id === articleId ? { ...r, has_feature_image: true } : r)),
    );
  }, []);

  const handlePublishSuccess = useCallback((articleId, publishedAt) => {
    setRowData((prev) =>
      prev.map((r) => (r.id === articleId ? { ...r, published_at: publishedAt } : r)),
    );
  }, []);

  const categories = useMemo(() => {
    const seen = new Map();
    rowData.forEach(r => {
      if (r.category_name && !seen.has(r.category_name))
        seen.set(r.category_name, r.category_id ?? Infinity);
    });
    return [...seen.entries()].sort((a, b) => a[1] - b[1]).map(([name]) => name);
  }, [rowData]);
  const filteredData = useMemo(() => {
    if (!categoryFilter) return rowData;
    return rowData.filter(r => r.category_name === categoryFilter);
  }, [rowData, categoryFilter]);

  // The next article to review (lowest suggestion_id among PENDING articles)
  const nextArticleId = useMemo(() => {
    const pending = rowData.filter(r => r.status === 'PENDING');
    if (pending.length === 0) return null;
    return pending.reduce((min, r) =>
      (r.suggestion_id ?? Infinity) < (min.suggestion_id ?? Infinity) ? r : min
    ).id;
  }, [rowData]);

  const colDefs = useMemo(() => [
    {
      field: 'title',
      headerName: 'Title',
      flex: 2,
      minWidth: 200,
      filter: true,
      cellRenderer: ({ data, value }) => (
        <span>
          {value}
          {data.id === nextArticleId && (
            <span className="ml-2 inline-flex items-center rounded bg-indigo-100 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-700">
              Next
            </span>
          )}
        </span>
      ),
    },
    {
      field: 'status',
      headerName: 'Status',
      width: 160,
      filter: true,
      cellRenderer: InlineStatusCell,
      cellRendererParams: { onStatusChange: handleStatusChange },
    },
    { field: 'has_spanish', headerName: 'Spanish', width: 100, cellRenderer: BooleanCell },
    {
      field: 'published_at',
      headerName: 'Published',
      width: 120,
      valueFormatter: (params) => {
        if (!params.value) return '';
        return new Date(params.value).toLocaleDateString();
      },
    },
    {
      headerName: 'Fact Check',
      width: 120,
      sortable: false,
      filter: false,
      cellRenderer: FactCheckStatusCell,
      cellRendererParams: {
        getCounts: factCheck.getCounts,
        onOpen: factCheck.setModalRecord,
      },
    },
    {
      headerName: 'Actions',
      width: 240,
      sortable: false,
      filter: false,
      cellRenderer: ArticleActionsCellRenderer,
      cellRendererParams: {
        onEdit: handleEdit,
        onGenerateTags: handleGenerateTags,
        onTranslate: handleTranslate,
        onSeriesSections: handleSeriesSections,
        onFeatureImage: handleFeatureImage,
        onPublish: (record) => setPublishArticle(record),
        onDownload: handleDownload,
        onFactCheck: factCheck.run,
        isFactChecking: factCheck.isRunning,
      },
    },
  ], [handleEdit, handleStatusChange, handleDownload, handleGenerateTags, handleTranslate, handleSeriesSections, handleFeatureImage, nextArticleId, factCheck]);

  useEffect(() => {
    adminService.getArticles(token)
      .then(setRowData)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [token]);

  if (error) return <div className="text-red-600">Error: {error}</div>;

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Article Content</h1>
          <p className="mt-2 text-sm text-gray-700">{filteredData.length} of {rowData.length} articles</p>
        </div>
        <div className="flex items-center gap-3">
          {categories.length > 1 && (
            <select
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value)}
              className="rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-indigo-500 focus:outline-none"
            >
              <option value="">All Categories</option>
              {categories.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          )}
          <button
            onClick={() => gridRef.current?.api?.setFilterModel(null)}
            className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
          >
            Clear Filters
          </button>
          <button
            onClick={() => setModalOpen(true)}
            className="rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
          >
            Generate Article
          </button>
        </div>
      </div>

      <AgGridProvider modules={modules}>
        <div className="mt-6" style={{ height: 'calc(100vh - 240px)', minHeight: 400 }}>
          <AgGridReact
            ref={gridRef}
            theme={themeAlpine}
            rowData={filteredData}
            columnDefs={colDefs}
            loading={loading}
            pagination={true}
            paginationPageSize={25}
            paginationPageSizeSelector={[25, 50, 100]}
            defaultColDef={{ sortable: true, resizable: true }}
          />
        </div>
      </AgGridProvider>

      <EditDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        record={selectedRecord}
        recordType="Article"
        token={token}
      />

      <GenerateArticleModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        token={token}
        onSuccess={refreshGrid}
      />

      <PublishModal
        open={!!publishArticle}
        onClose={() => setPublishArticle(null)}
        article={publishArticle}
        token={token}
        onSuccess={handlePublishSuccess}
      />

      <FeatureImageModal
        open={!!featureImageArticle}
        onClose={() => setFeatureImageArticle(null)}
        article={featureImageArticle}
        token={token}
        onSuccess={handleFeatureImageSuccess}
      />

      <Transition appear show={!!errorDialog} as={Fragment}>
        <Dialog as="div" className="relative z-50" onClose={() => setErrorDialog(null)}>
          <Transition.Child
            as={Fragment}
            enter="ease-out duration-200" enterFrom="opacity-0" enterTo="opacity-100"
            leave="ease-in duration-150" leaveFrom="opacity-100" leaveTo="opacity-0"
          >
            <div className="fixed inset-0 bg-black/30" />
          </Transition.Child>
          <div className="fixed inset-0 flex items-center justify-center p-4">
            <Transition.Child
              as={Fragment}
              enter="ease-out duration-200" enterFrom="opacity-0 scale-95" enterTo="opacity-100 scale-100"
              leave="ease-in duration-150" leaveFrom="opacity-100 scale-100" leaveTo="opacity-0 scale-95"
            >
              <Dialog.Panel className="w-full max-w-sm max-h-[80vh] overflow-y-auto rounded-lg bg-white p-6 shadow-xl">
                <Dialog.Title className="text-lg font-semibold text-gray-900">
                  {errorDialog?.title || 'Error'}
                </Dialog.Title>
                <p className="mt-2 text-sm text-gray-600">{errorDialog?.message}</p>
                <div className="mt-5 flex justify-end">
                  <button
                    onClick={() => setErrorDialog(null)}
                    className="rounded bg-gray-100 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-200"
                  >
                    OK
                  </button>
                </div>
              </Dialog.Panel>
            </Transition.Child>
          </div>
        </Dialog>
      </Transition>

      <FactCheckModal
        open={!!factCheck.modalRecord}
        onClose={() => factCheck.setModalRecord(null)}
        token={token}
        subjectType="ARTICLE"
        subjectId={factCheck.modalRecord?.id}
        title={factCheck.modalRecord?.title}
        onCountsChange={factCheck.updateCounts}
      />

      <ConfirmDialog
        open={!!approveWarning}
        onClose={() => setApproveWarning(null)}
        onConfirm={async () => {
          const { record, newStatus } = approveWarning;
          setApproveWarning(null);
          await applyStatusChange(record, newStatus);
        }}
        title="Approve without a clean fact-check?"
        message={approveWarning?.warning}
        confirmLabel="Approve anyway"
      />

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
