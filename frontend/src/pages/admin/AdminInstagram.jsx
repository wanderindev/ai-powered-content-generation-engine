import { useState, useEffect, useMemo, useCallback, useRef, Fragment } from 'react';
import { Dialog, Transition } from '@headlessui/react';
import { AllCommunityModule, themeAlpine } from 'ag-grid-community';
import { AgGridProvider, AgGridReact } from 'ag-grid-react';
import { useAdminAuth } from '../../contexts/AdminAuthContext';
import { adminService } from '../../services/admin';
import ActionsCellRenderer from '../../components/admin/ActionsCellRenderer';
import InlineStatusCell from '../../components/admin/InlineStatusCell';
import EditDrawer from '../../components/admin/EditDrawer';
import { ToastContainer } from '../../components/admin/Toast';
import { useToast } from '../../hooks/useToast';

const modules = [AllCommunityModule];

const TYPE_LABELS = { SABIAS_QUE: 'Sabías que', ARTICLE_PROMO: 'Blog promo' };

// Publication state badge: orange = queued for a future publish, green = live
// on Instagram (links to the post), red = the publish attempt failed.
function StateCell({ data }) {
  const state = data.derived_state;
  if (state === 'PUBLISHED') {
    return (
      <a
        href={data.ig_permalink || undefined}
        target="_blank"
        rel="noreferrer"
        className="inline-flex rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700 hover:bg-green-200"
        title={data.published_at ? `Published ${new Date(data.published_at).toLocaleString()}` : 'Published'}
      >
        Published
      </a>
    );
  }
  if (state === 'FAILED') {
    return (
      <span
        className="inline-flex rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700"
        title={data.publish_error || 'Publish failed'}
      >
        Failed
      </span>
    );
  }
  if (state === 'SCHEDULED') {
    return (
      <span
        className="inline-flex rounded-full bg-orange-100 px-2 py-0.5 text-xs font-medium text-orange-700"
        title={data.scheduled_at ? `Scheduled for ${new Date(data.scheduled_at).toLocaleString()}` : ''}
      >
        Scheduled
      </span>
    );
  }
  if (state === 'PUBLISHING') {
    return (
      <span className="inline-flex animate-pulse rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-700">
        Publishing…
      </span>
    );
  }
  return <span className="text-xs text-gray-400">—</span>;
}

function ImageCell({ data }) {
  if (!data.image_url) return <span className="text-xs text-gray-400">not rendered</span>;
  return (
    <a href={data.image_url} target="_blank" rel="noreferrer" title="Open rendered image">
      <img
        src={data.image_url}
        alt=""
        className="h-9 w-7 rounded object-cover ring-1 ring-gray-200"
      />
    </a>
  );
}

function GenerateInstagramModal({ open, onClose, token, onSuccess }) {
  const [mode, setMode] = useState('sabias'); // 'sabias' | 'promo'
  const [researchList, setResearchList] = useState([]);
  const [articleList, setArticleList] = useState([]);
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
    Promise.all([
      adminService.getResearch(token),
      adminService.getPromotableArticles(token),
    ])
      .then(([research, articles]) => {
        setResearchList(research.filter((r) => r.status === 'APPROVED'));
        setArticleList(articles);
      })
      .catch((err) => setError(err.message))
      .finally(() => setFetching(false));
  }, [open, token]);

  const handleModeChange = (newMode) => {
    setMode(newMode);
    setSelectedId('');
    setResult(null);
    setError(null);
  };

  const handleGenerate = async () => {
    if (!selectedId) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      if (mode === 'sabias') {
        const posts = await adminService.generateSabiasQuePosts(token, Number(selectedId));
        setResult(`Generated ${posts.length} "Sabías que" draft${posts.length !== 1 ? 's' : ''}`);
      } else {
        const post = await adminService.generateArticlePromo(token, Number(selectedId));
        setResult(`Generated promo draft for "${post.title}"`);
      }
      onSuccess?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
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
            <Dialog.Panel className="w-full max-w-md max-h-[80vh] overflow-y-auto rounded-lg bg-white p-6 shadow-xl">
              <Dialog.Title className="text-lg font-semibold text-gray-900">
                Generate Instagram Posts
              </Dialog.Title>

              <div className="mt-3 flex gap-2">
                <button
                  onClick={() => handleModeChange('sabias')}
                  className={`rounded-full px-3 py-1 text-sm font-medium ${
                    mode === 'sabias'
                      ? 'bg-indigo-100 text-indigo-700'
                      : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
                >
                  Sabías que
                </button>
                <button
                  onClick={() => handleModeChange('promo')}
                  className={`rounded-full px-3 py-1 text-sm font-medium ${
                    mode === 'promo'
                      ? 'bg-indigo-100 text-indigo-700'
                      : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
                >
                  Blog promo
                </button>
              </div>

              <div className="mt-4">
                {fetching ? (
                  <p className="text-sm text-gray-500">Loading...</p>
                ) : mode === 'sabias' ? (
                  researchList.length === 0 ? (
                    <p className="text-sm text-gray-500">No approved research documents.</p>
                  ) : (
                    <>
                      <p className="mb-2 text-xs text-gray-500">
                        Generates 3 fact-card drafts from the research document.
                      </p>
                      <select
                        size={8}
                        className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                        value={selectedId}
                        onChange={(e) => { setSelectedId(e.target.value); setResult(null); }}
                      >
                        <option value="">Select research...</option>
                        {researchList.map((r) => (
                          <option key={r.id} value={r.id}>
                            {r.suggestion_title || `Research #${r.id}`}
                          </option>
                        ))}
                      </select>
                    </>
                  )
                ) : articleList.length === 0 ? (
                  <p className="text-sm text-gray-500">No approved articles to promote.</p>
                ) : (
                  <>
                    <p className="mb-2 text-xs text-gray-500">
                      Generates a "Nuevo en el blog" announcement for a series.
                    </p>
                    <select
                      size={8}
                      className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                      value={selectedId}
                      onChange={(e) => { setSelectedId(e.target.value); setResult(null); }}
                    >
                      <option value="">Select article / series...</option>
                      {articleList.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.title}{a.series_size > 1 ? ` (serie · ${a.series_size})` : ''}
                        </option>
                      ))}
                    </select>
                  </>
                )}
                {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
                {result && (
                  <div className="mt-2 rounded bg-green-50 p-3 text-sm text-green-800">{result}</div>
                )}
              </div>
              <div className="mt-6 flex justify-end gap-3">
                <button
                  onClick={onClose}
                  className="rounded px-4 py-2 text-sm text-gray-700 hover:bg-gray-100"
                >
                  {result ? 'Close' : 'Cancel'}
                </button>
                {!result && (
                  <button
                    onClick={handleGenerate}
                    disabled={!selectedId || loading}
                    className="rounded bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-700 disabled:opacity-50"
                  >
                    {loading ? 'Generating...' : 'Generate'}
                  </button>
                )}
              </div>
            </Dialog.Panel>
          </Transition.Child>
        </div>
      </Dialog>
    </Transition>
  );
}

const PUBLISHING_OFF_TITLE =
  'Publishing to Instagram is turned off (INSTAGRAM_PUBLISHING_ENABLED). '
  + 'Scheduled posts stay queued; use "Verify (no post)" to test the integration.';

// Schedule / publish-now modal for an APPROVED + rendered post. A rescheduled
// FAILED post has its error cleared server-side (the operator retry path).
// publishingEnabled === false (the default, #576) disables publish-now; verify
// still works — it creates a media container but never publishes it.
function ScheduleModal({ open, onClose, post, token, onSuccess, publishingEnabled }) {
  const [dateValue, setDateValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  // Keyed by post id so a result never shows on another post's modal.
  const [verify, setVerify] = useState({ postId: null, result: null });
  const verifyResult = verify.postId === post?.id ? verify.result : null;

  const runVerify = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await adminService.verifyInstagram(token, post.id);
      setVerify({ postId: post.id, result });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) {
      const seed = post?.scheduled_at ? new Date(post.scheduled_at) : new Date();
      // datetime-local wants local wall-clock, not the UTC ISO slice.
      const local = new Date(seed.getTime() - seed.getTimezoneOffset() * 60000);
      setDateValue(local.toISOString().slice(0, 16));
      setError(null);
    }
  }, [open, post]);

  const run = async (fn, ...args) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fn(token, post.id, ...args);
      onSuccess?.(res);
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

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
            <Dialog.Panel className="w-full max-w-lg max-h-[80vh] overflow-y-auto rounded-lg bg-white p-6 shadow-xl">
              <Dialog.Title className="text-lg font-semibold text-gray-900">
                Schedule Instagram Post
              </Dialog.Title>
              <p className="mt-1 text-sm text-gray-500">{post?.title}</p>
              {post?.publish_error && (
                <p className="mt-2 rounded bg-red-50 p-2 text-xs text-red-700">
                  Last attempt failed: {post.publish_error}. Scheduling again retries it.
                </p>
              )}
              <div className="mt-4">
                <label className="block text-sm font-medium text-gray-700">Publish at</label>
                <input
                  type="datetime-local"
                  value={dateValue}
                  onChange={(e) => setDateValue(e.target.value)}
                  disabled={loading}
                  className="mt-1 w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none disabled:opacity-50"
                />
                {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
              </div>
              {verifyResult && (
                <div className="mt-4 rounded border border-gray-200 p-2 text-xs">
                  <p className={verifyResult.ok ? 'font-medium text-green-700' : 'font-medium text-red-700'}>
                    {verifyResult.ok ? 'Verify passed — nothing was posted' : 'Verify found problems — nothing was posted'}
                  </p>
                  <ul className="mt-1 space-y-0.5">
                    {verifyResult.steps.map((step) => (
                      <li key={step.name} className={step.ok ? 'text-gray-700' : 'text-red-700'}>
                        {step.ok ? '✓' : '✗'} {step.name}: {step.detail}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <div className="mt-6 flex items-center justify-between gap-3">
                <div className="flex gap-2">
                  {/* The span carries the tooltip: a disabled button fires no hover events. */}
                  <span title={publishingEnabled ? '' : PUBLISHING_OFF_TITLE}>
                    <button
                      onClick={() => {
                        if (window.confirm('Publish this post to Instagram right now?')) {
                          run(adminService.publishInstagramPostNow);
                        }
                      }}
                      disabled={loading || !publishingEnabled}
                      className="rounded border border-green-600 px-3 py-2 text-sm font-medium text-green-700 hover:bg-green-50 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      Publish now
                    </button>
                  </span>
                  <button
                    onClick={runVerify}
                    disabled={loading}
                    title="Check the account, the token and this post's image container. Never posts."
                    className="rounded border border-gray-300 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                  >
                    Verify (no post)
                  </button>
                </div>
                <div className="flex gap-2">
                  {post?.scheduled_at && (
                    <button
                      onClick={() => run(adminService.unscheduleInstagramPost)}
                      disabled={loading}
                      className="rounded px-3 py-2 text-sm text-gray-700 hover:bg-gray-100 disabled:opacity-50"
                    >
                      Unschedule
                    </button>
                  )}
                  <button
                    onClick={onClose}
                    disabled={loading}
                    className="rounded px-3 py-2 text-sm text-gray-700 hover:bg-gray-100 disabled:opacity-50"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => run(
                      adminService.scheduleInstagramPost,
                      new Date(dateValue).toISOString(),
                    )}
                    disabled={!dateValue || loading}
                    className="rounded bg-indigo-600 px-3 py-2 text-sm text-white hover:bg-indigo-700 disabled:opacity-50"
                  >
                    {loading ? 'Working...' : 'Schedule'}
                  </button>
                </div>
              </div>
            </Dialog.Panel>
          </Transition.Child>
        </div>
      </Dialog>
    </Transition>
  );
}

export default function AdminInstagram() {
  const { token } = useAdminAuth();
  const gridRef = useRef(null);
  const [rowData, setRowData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedRecord, setSelectedRecord] = useState(null);
  const [generateModalOpen, setGenerateModalOpen] = useState(false);
  const [schedulePost, setSchedulePost] = useState(null);
  // { configured, publishing_enabled }; null until loaded (publish-now stays
  // disabled meanwhile — off is the safe default).
  const [publishingStatus, setPublishingStatus] = useState(null);
  const publishingEnabled = !!publishingStatus?.publishing_enabled;
  const { toasts, show: showToast, dismiss: dismissToast } = useToast();

  const refreshGrid = useCallback(() => {
    adminService.getInstagramPosts(token)
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

  const handleStatusChange = useCallback(async (record, newStatus) => {
    try {
      await adminService.updateStatus(token, 'instagram/posts', record.id, newStatus);
      refreshGrid();
    } catch (err) {
      console.error('Status update failed:', err);
      showToast({ type: 'error', title: 'Failed to update status', message: err.message || 'Unexpected error' });
    }
  }, [token, refreshGrid, showToast]);

  const handleDelete = useCallback(async (record) => {
    if (!window.confirm(`Delete "${record.title}"? This cannot be undone.`)) return;
    try {
      await adminService.deleteInstagramPost(token, record.id);
      setRowData((prev) => prev.filter((r) => r.id !== record.id));
      showToast({ type: 'success', title: 'Draft deleted' });
    } catch (err) {
      console.error('Delete failed:', err);
      showToast({ type: 'error', title: 'Failed to delete', message: err.message || 'Unexpected error' });
    }
  }, [token, showToast]);

  const colDefs = useMemo(() => [
    {
      field: 'post_type',
      headerName: 'Type',
      width: 120,
      filter: true,
      valueFormatter: (p) => TYPE_LABELS[p.value] || p.value,
    },
    { field: 'title', headerName: 'Title', flex: 2, minWidth: 220, filter: true },
    {
      headerName: 'Image',
      width: 90,
      sortable: false,
      filter: false,
      cellRenderer: ImageCell,
    },
    {
      field: 'status',
      headerName: 'Status',
      width: 160,
      filter: true,
      cellRenderer: InlineStatusCell,
      cellRendererParams: { onStatusChange: handleStatusChange },
    },
    {
      field: 'derived_state',
      headerName: 'Publication',
      width: 130,
      filter: true,
      cellRenderer: StateCell,
    },
    {
      field: 'scheduled_at',
      headerName: 'Scheduled / Published',
      width: 190,
      valueGetter: (p) => p.data.published_at || p.data.scheduled_at,
      valueFormatter: (p) => (p.value ? new Date(p.value).toLocaleString() : ''),
    },
    {
      headerName: 'Actions',
      width: 150,
      sortable: false,
      filter: false,
      cellRenderer: ActionsCellRenderer,
      cellRendererParams: {
        onEdit: handleEdit,
        // Calendar icon opens the schedule/publish-now modal once the post is
        // APPROVED and rendered; hidden after publication.
        showPublish: (d) => d.status === 'APPROVED' && !!d.image_url && !d.published_at,
        publishTitle: 'Schedule / publish',
        onPublish: (d) => setSchedulePost(d),
        showDelete: (d) => !d.published_at,
        onDelete: handleDelete,
      },
    },
  ], [handleEdit, handleStatusChange, handleDelete]);

  useEffect(() => {
    adminService.getInstagramPosts(token)
      .then(setRowData)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    adminService.getInstagramPublishingStatus(token)
      .then(setPublishingStatus)
      .catch((err) => console.error('Publishing status failed:', err));
  }, [token]);

  if (error) return <div className="text-red-600">Error: {error}</div>;

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Instagram</h1>
          <p className="mt-2 flex items-center gap-2 text-sm text-gray-700">
            {rowData.length} posts
            {publishingStatus && !publishingEnabled && (
              <span
                className="inline-flex rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800"
                title={PUBLISHING_OFF_TITLE}
              >
                Publishing off
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => gridRef.current?.api?.setFilterModel(null)}
            className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
          >
            Clear Filters
          </button>
          <button
            onClick={() => setGenerateModalOpen(true)}
            className="rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
          >
            Generate Posts
          </button>
        </div>
      </div>

      <AgGridProvider modules={modules}>
        <div className="mt-6" style={{ height: 'calc(100vh - 240px)', minHeight: 400 }}>
          <AgGridReact
            ref={gridRef}
            theme={themeAlpine}
            rowData={rowData}
            columnDefs={colDefs}
            loading={loading}
            rowHeight={44}
            pagination={true}
            paginationPageSize={25}
            paginationPageSizeSelector={[25, 50, 100]}
            defaultColDef={{ sortable: true, resizable: true }}
          />
        </div>
      </AgGridProvider>

      <EditDrawer
        open={drawerOpen}
        onClose={() => {
          setDrawerOpen(false);
          refreshGrid();
        }}
        record={selectedRecord}
        recordType="InstagramPost"
        token={token}
      />

      <GenerateInstagramModal
        open={generateModalOpen}
        onClose={() => setGenerateModalOpen(false)}
        token={token}
        onSuccess={refreshGrid}
      />

      <ScheduleModal
        open={schedulePost !== null}
        onClose={() => setSchedulePost(null)}
        post={schedulePost}
        token={token}
        onSuccess={refreshGrid}
        publishingEnabled={publishingEnabled}
      />

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
