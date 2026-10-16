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
  DocumentCheckIcon,
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

// Grammar check (#410): one button per language. Green once that language has
// been checked (the tooltip carries the timestamp); clicking always re-runs —
// the backend allows it and re-stamps checked_at. Missing timestamps (backend
// not deployed yet) simply read as "unchecked".
function GrammarCheckButton({ data, language, checkedAt, running, onGrammarCheck }) {
  const label = language.toUpperCase();
  const title = running
    ? 'Grammar check running...'
    : checkedAt
      ? `Grammar checked ${new Date(checkedAt).toLocaleString()} — click to re-run`
      : `Grammar check (${label})`;

  return (
    <button
      onClick={() => !running && onGrammarCheck?.(data, language)}
      disabled={running}
      className={`flex items-center gap-0.5 rounded p-1 hover:bg-gray-100 disabled:animate-pulse disabled:cursor-wait disabled:text-purple-400 ${
        checkedAt ? 'text-green-500 hover:text-green-600' : 'text-gray-400 hover:text-purple-600'
      }`}
      title={title}
    >
      <DocumentCheckIcon className="h-4.5 w-4.5" />
      <span className="text-[9px] font-semibold">{label}</span>
    </button>
  );
}

function ArticleActionsCellRenderer({ data, colDef }) {
  const {
    onEdit, onGenerateTags, onTranslate, onSeriesSections,
    onFeatureImage, isGeneratingImage, onPublish, onDownload,
    onFactCheck, isFactChecking,
    onGrammarCheck, isGrammarChecking,
  } = colDef.cellRendererParams || {};

  const showTags = data.has_content && !data.has_tags;
  const showTranslate = data.status === 'APPROVED' && data.has_content && !data.has_spanish;
  const showSeries = data.is_series && data.has_spanish && !data.has_series_sections;
  // Generate / Regenerate feature image (#580) — needs written content.
  const showImage = data.has_content;
  const imageRunning = isGeneratingImage ? isGeneratingImage(data) : false;
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

      {data.has_content && (
        <GrammarCheckButton
          data={data}
          language="en"
          checkedAt={data.grammar_checked_en_at}
          running={isGrammarChecking ? isGrammarChecking(data, 'en') : false}
          onGrammarCheck={onGrammarCheck}
        />
      )}

      {data.has_spanish && (
        <GrammarCheckButton
          data={data}
          language="es"
          checkedAt={data.grammar_checked_es_at}
          running={isGrammarChecking ? isGrammarChecking(data, 'es') : false}
          onGrammarCheck={onGrammarCheck}
        />
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
          onClick={() => !imageRunning && onFeatureImage?.(data)}
          disabled={imageRunning}
          className={`rounded p-1 hover:bg-gray-100 disabled:animate-pulse disabled:cursor-wait disabled:text-teal-400 ${
            data.has_feature_image ? 'text-green-500 hover:text-green-600' : 'text-gray-400 hover:text-teal-600'
          }`}
          title={imageRunning
            ? 'Generating image...'
            : data.has_feature_image ? 'Regenerate image' : 'Generate image'}
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

// Read-only result view for a grammar-check run (#410): the FIND → REPLACE pairs
// the proofread returned, with skipped entries dimmed and their reason shown.
function GrammarResultModal({ open, onClose, result }) {
  const language = (result?.language || '').toUpperCase();
  const fixes = result?.fixes || [];

  return (
    <Transition appear show={open} as={Fragment}>
      <Dialog as="div" className="relative z-50" onClose={onClose}>
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
            <Dialog.Panel className="w-full max-w-2xl max-h-[80vh] overflow-y-auto rounded-lg bg-white p-6 shadow-xl">
              <Dialog.Title className="text-lg font-semibold text-gray-900">
                Grammar Check Result{language ? ` (${language})` : ''}
              </Dialog.Title>
              <p className="mt-1 text-sm text-gray-500">{result?.articleTitle}</p>
              <p className="mt-2 text-sm text-gray-600">
                <span className="font-medium text-green-700">{result?.applied_count ?? 0}</span> applied
                {' · '}
                <span className="font-medium text-gray-500">{result?.skipped_count ?? 0}</span> skipped
              </p>

              <ul className="mt-4 divide-y divide-gray-100 rounded border border-gray-200">
                {fixes.map((fix, idx) => (
                  <li
                    key={idx}
                    className={`px-3 py-2 text-xs ${fix.applied ? '' : 'bg-gray-50 opacity-60'}`}
                  >
                    <div className="font-mono break-words text-red-700 line-through">{fix.find}</div>
                    <div className="font-mono break-words text-green-700">{fix.replace}</div>
                    {!fix.applied && (
                      <div className="mt-1 text-[11px] text-gray-500">
                        Skipped: {fix.skipped_reason || 'no reason given'}
                      </div>
                    )}
                  </li>
                ))}
              </ul>

              <div className="mt-6 flex justify-end">
                <button
                  onClick={onClose}
                  className="rounded bg-gray-100 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-200"
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

// Paragraphs the Grammarly meaning check (#518) wrote but could not vouch for:
// before/after + Sonnet's reason, with a per-row Resolve once a human has
// looked (the text itself is fixed in the drawer, not here).
function GrammarReviewModal({ open, onClose, token, article, onResolved }) {
  const [diffs, setDiffs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(() => new Set());

  useEffect(() => {
    if (!open || !article) return;
    let active = true;
    setLoading(true);
    adminService
      .getGrammarReviewDiffs(token, article.id)
      .then((data) => { if (active) setDiffs(data.diffs || []); })
      .catch((err) => console.error('Failed to load grammar review diffs:', err))
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [open, article, token]);

  const resolve = async (diff) => {
    setBusy((prev) => new Set(prev).add(diff.id));
    try {
      await adminService.resolveGrammarReviewDiff(token, article.id, diff.id);
      setDiffs((prev) => prev.filter((d) => d.id !== diff.id));
      onResolved?.(article.id);
    } catch (err) {
      console.error('Failed to resolve grammar review diff:', err);
    } finally {
      setBusy((prev) => { const next = new Set(prev); next.delete(diff.id); return next; });
    }
  };

  return (
    <Transition appear show={open} as={Fragment}>
      <Dialog as="div" className="relative z-50" onClose={onClose}>
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
            <Dialog.Panel className="w-full max-w-3xl max-h-[80vh] overflow-y-auto rounded-lg bg-white p-6 shadow-xl">
              <Dialog.Title className="text-lg font-semibold text-gray-900">
                Grammar changes to review
              </Dialog.Title>
              <p className="mt-1 text-sm text-gray-500">{article?.title}</p>
              <p className="mt-2 text-sm text-gray-600">
                These paragraphs were written by the proofread but the meaning check could not
                vouch for them. Fix the text in the editor if needed, then mark each as seen.
              </p>

              {loading ? (
                <p className="mt-4 text-sm text-gray-500">Loading…</p>
              ) : diffs.length === 0 ? (
                <p className="mt-4 text-sm text-gray-500">Nothing left to review.</p>
              ) : (
                <ul className="mt-4 divide-y divide-gray-100 rounded border border-gray-200">
                  {diffs.map((diff) => (
                    <li key={diff.id} className="px-3 py-3 text-xs">
                      <div className="mb-1 flex items-center justify-between gap-2">
                        <span className="font-medium text-amber-800">
                          {diff.language.toUpperCase()} · paragraph {diff.block_index + 1} · {diff.reason}
                        </span>
                        <button
                          type="button"
                          disabled={busy.has(diff.id)}
                          onClick={() => resolve(diff)}
                          className="rounded bg-gray-100 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-200 disabled:opacity-50"
                        >
                          {busy.has(diff.id) ? 'Resolving…' : 'Mark as seen'}
                        </button>
                      </div>
                      <div className="whitespace-pre-wrap break-words text-red-700">{diff.before_text}</div>
                      <div className="mt-1 whitespace-pre-wrap break-words text-green-700">{diff.after_text}</div>
                    </li>
                  ))}
                </ul>
              )}

              <div className="mt-6 flex justify-end">
                <button
                  onClick={onClose}
                  className="rounded bg-gray-100 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-200"
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

// Grammar status pills for the grid (#410), driven by the two
// grammar_checked_*_at fields on the article list response.
function GrammarStatusCell({ data, colDef }) {
  const pill = 'inline-block rounded-full px-2 py-0.5 text-xs';
  const en = data.grammar_checked_en_at;
  const es = data.grammar_checked_es_at;
  const review = data.grammar_review_count || 0;
  const { onGrammarReview } = colDef?.cellRendererParams || {};

  return (
    <span className="inline-flex items-center gap-1">
      {review > 0 && (
        <button
          type="button"
          onClick={() => onGrammarReview?.(data)}
          className={`${pill} bg-amber-100 text-amber-800 hover:bg-amber-200`}
          title={`${review} paragraph(s) flagged by the meaning check — click to review`}
        >
          {review} to review
        </button>
      )}
      <span
        className={`${pill} ${en ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}
        title={en ? `Grammar checked ${new Date(en).toLocaleString()}` : 'Not grammar checked'}
      >
        {en ? 'EN ✓' : 'EN'}
      </span>
      {data.has_spanish && (
        <span
          className={`${pill} ${es ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}
          title={es ? `Grammar checked ${new Date(es).toLocaleString()}` : 'Not grammar checked'}
        >
          {es ? 'ES ✓' : 'ES'}
        </span>
      )}
    </span>
  );
}

// Cross-article consistency pill (#631, shadow mode): open findings where a
// claim contradicts another article or the verified-claims KB. Clicking opens
// the drawer, whose EN tab lists them with Dismiss / Mark resolved. Never a
// gate — publishing ignores it.
function ConsistencyCell({ data, colDef }) {
  const count = data.consistency_open_count || 0;
  if (count === 0) return null;
  const { onOpen } = colDef?.cellRendererParams || {};
  return (
    <button
      type="button"
      onClick={() => onOpen?.(data)}
      className="inline-block rounded-full bg-rose-100 px-2 py-0.5 text-xs text-rose-800 hover:bg-rose-200"
      title={`${count} claim(s) may contradict another article — click to review`}
    >
      {count} contradiction{count === 1 ? '' : 's'}
    </button>
  );
}

// Narration voice-check pill (#601): shown when the last narration run left
// high-pitch paragraphs to listen to. Clicking opens the drawer on its
// default (EN) tab; the list with timestamps is under the ES tab's narration
// audio section.
function NarrationQaCell({ data, colDef }) {
  const qa = data.audio_qa_es;
  const toReview = qa?.unresolved?.length || 0;
  if (!qa || (qa.status !== 'NEEDS_REVIEW' && toReview === 0)) return null;
  const { onOpen } = colDef?.cellRendererParams || {};
  return (
    <button
      type="button"
      onClick={() => onOpen?.(data)}
      className="inline-block rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-800 hover:bg-amber-200"
      title="High-pitch narration paragraphs to listen to — click to open the article, then switch to the ES tab"
    >
      {toReview} to listen
    </button>
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
  const [imageRunning, setImageRunning] = useState(() => new Set());
  const [regenerateImageArticle, setRegenerateImageArticle] = useState(null);
  const [categoryFilter, setCategoryFilter] = useState('');
  const [errorDialog, setErrorDialog] = useState(null);
  const { toasts, show: showToast, dismiss: dismissToast } = useToast();
  const [approveWarning, setApproveWarning] = useState(null);
  const [grammarRunning, setGrammarRunning] = useState(() => new Set());
  const [grammarResult, setGrammarResult] = useState(null);
  const [grammarReviewArticle, setGrammarReviewArticle] = useState(null);
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

  // Grammar check (#410). Synchronous call — no polling. EN and ES may run at
  // the same time, so the in-flight set is keyed by `${id}-${language}`.
  const isGrammarChecking = useCallback(
    (record, language) => grammarRunning.has(`${record.id}-${language}`),
    [grammarRunning],
  );

  const handleGrammarReview = useCallback((record) => setGrammarReviewArticle(record), []);
  const handleGrammarReviewResolved = useCallback((articleId) => {
    setRowData((prev) =>
      prev.map((r) =>
        r.id === articleId
          ? { ...r, grammar_review_count: Math.max(0, (r.grammar_review_count || 0) - 1) }
          : r,
      ),
    );
  }, []);

  const handleGrammarCheck = useCallback(async (record, language) => {
    const key = `${record.id}-${language}`;
    if (grammarRunning.has(key)) return;
    const label = language.toUpperCase();
    setGrammarRunning((prev) => new Set(prev).add(key));
    const toastId = showToast({
      type: 'info',
      title: `Grammar checking (${label})...`,
      message: record.title,
      duration: 0,
    });
    try {
      const res = await adminService.grammarCheckArticle(token, record.id, language);
      dismissToast(toastId);
      const field = language === 'es' ? 'grammar_checked_es_at' : 'grammar_checked_en_at';
      setRowData((prev) =>
        prev.map((r) => (r.id === record.id ? { ...r, [field]: res.checked_at } : r)),
      );
      const fixes = res.fixes || [];
      showToast({
        type: 'success',
        title: `Grammar check complete (${label})`,
        message: fixes.length === 0
          ? 'No fixes needed'
          : `${res.applied_count} fix(es) applied, ${res.skipped_count} skipped`,
      });
      if (fixes.length > 0) setGrammarResult({ ...res, articleTitle: record.title });
    } catch (err) {
      dismissToast(toastId);
      console.error('Grammar check failed:', err);
      showToast({ type: 'error', title: `Grammar check failed (${label})`, message: err.message });
    } finally {
      setGrammarRunning((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    }
  }, [token, showToast, dismissToast, grammarRunning]);

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

  // Feature image (#580): one synchronous call to generate-image (Sonnet
  // prompt + Gemini, typically well under a minute). Regenerating replaces the
  // current image, so it is confirmed first.
  const isGeneratingImage = useCallback(
    (record) => imageRunning.has(record.id),
    [imageRunning],
  );

  const runGenerateImage = useCallback(async (record) => {
    if (imageRunning.has(record.id)) return;
    setImageRunning((prev) => new Set(prev).add(record.id));
    const toastId = showToast({
      type: 'info',
      title: 'Generating feature image...',
      message: record.title,
      duration: 0,
    });
    try {
      await adminService.generateFeatureImage(token, record.id);
      dismissToast(toastId);
      setRowData((prev) =>
        prev.map((r) => (r.id === record.id ? { ...r, has_feature_image: true } : r)),
      );
      showToast({ type: 'success', title: 'Feature image generated', message: record.title });
    } catch (err) {
      dismissToast(toastId);
      console.error('Feature image generation failed:', err);
      setErrorDialog({ title: 'Feature Image Failed', message: err.message });
    } finally {
      setImageRunning((prev) => {
        const next = new Set(prev);
        next.delete(record.id);
        return next;
      });
    }
  }, [token, showToast, dismissToast, imageRunning]);

  const handleFeatureImage = useCallback((record) => {
    if (record.has_feature_image) {
      setRegenerateImageArticle(record);
      return;
    }
    runGenerateImage(record);
  }, [runGenerateImage]);

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
      headerName: 'Grammar',
      width: 110,
      sortable: false,
      filter: false,
      cellRenderer: GrammarStatusCell,
      cellRendererParams: { onGrammarReview: handleGrammarReview },
    },
    {
      headerName: 'Narration',
      width: 110,
      sortable: false,
      filter: false,
      cellRenderer: NarrationQaCell,
      cellRendererParams: { onOpen: handleEdit },
    },
    {
      headerName: 'Consistency',
      width: 130,
      sortable: false,
      filter: false,
      cellRenderer: ConsistencyCell,
      cellRendererParams: { onOpen: handleEdit },
    },
    {
      headerName: 'Actions',
      width: 280,
      sortable: false,
      filter: false,
      cellRenderer: ArticleActionsCellRenderer,
      cellRendererParams: {
        onEdit: handleEdit,
        onGenerateTags: handleGenerateTags,
        onTranslate: handleTranslate,
        onSeriesSections: handleSeriesSections,
        onFeatureImage: handleFeatureImage,
        isGeneratingImage,
        onPublish: (record) => setPublishArticle(record),
        onDownload: handleDownload,
        onFactCheck: factCheck.run,
        isFactChecking: factCheck.isRunning,
        onGrammarCheck: handleGrammarCheck,
        isGrammarChecking,
      },
    },
  ], [handleEdit, handleStatusChange, handleDownload, handleGenerateTags, handleTranslate, handleSeriesSections, handleFeatureImage, isGeneratingImage, nextArticleId, factCheck, handleGrammarCheck, isGrammarChecking, handleGrammarReview]);

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

      <ConfirmDialog
        open={!!regenerateImageArticle}
        onClose={() => setRegenerateImageArticle(null)}
        onConfirm={() => {
          const record = regenerateImageArticle;
          setRegenerateImageArticle(null);
          runGenerateImage(record);
        }}
        title="Regenerate feature image?"
        message="A new image will be generated and will replace the article's current feature image, on the admin and the public site."
        confirmLabel="Regenerate image"
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

      <GrammarReviewModal
        open={!!grammarReviewArticle}
        onClose={() => setGrammarReviewArticle(null)}
        token={token}
        article={grammarReviewArticle}
        onResolved={handleGrammarReviewResolved}
      />
      <GrammarResultModal
        open={!!grammarResult}
        onClose={() => setGrammarResult(null)}
        result={grammarResult}
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
