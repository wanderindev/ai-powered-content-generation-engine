import { useState, useEffect, useMemo, useCallback, useRef, Fragment } from 'react';
import { Dialog, Transition } from '@headlessui/react';
import { AllCommunityModule, themeAlpine } from 'ag-grid-community';
import { AgGridProvider, AgGridReact } from 'ag-grid-react';
import { useAdminAuth } from '../../contexts/AdminAuthContext';
import { adminService } from '../../services/admin';
import { BooleanCell, ArticlesStatusCell } from '../../components/admin/SharedCellRenderers';
import ActionsCellRenderer from '../../components/admin/ActionsCellRenderer';
import InlineStatusCell from '../../components/admin/InlineStatusCell';
import EditDrawer from '../../components/admin/EditDrawer';
import FactCheckStatusCell from '../../components/admin/FactCheckStatusCell';
import FactCheckModal from '../../components/admin/FactCheckModal';
import ConfirmDialog from '../../components/admin/ConfirmDialog';
import { ToastContainer } from '../../components/admin/Toast';
import { useToast } from '../../hooks/useToast';
import { useFactCheck, factCheckWarning } from '../../hooks/useFactCheck';

const modules = [AllCommunityModule];

function GeneratePromptModal({ open, onClose, token, onSuccess }) {
  const [suggestions, setSuggestions] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [loading, setLoading] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open) return;
    setSelectedId('');
    setError(null);
    setFetching(true);
    adminService.getSuggestionsForResearch(token)
      .then((data) => setSuggestions(data))
      .catch((err) => setError(err.message))
      .finally(() => setFetching(false));
  }, [open, token]);

  const handleGenerate = async () => {
    if (!selectedId) return;
    setLoading(true);
    setError(null);
    try {
      const result = await adminService.generateResearchPrompt(token, Number(selectedId));
      const blob = new Blob([result.prompt_text], { type: 'text/markdown' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `suggestion_${result.suggestion_id}_prompt.md`;
      a.click();
      URL.revokeObjectURL(url);
      onSuccess?.();
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  // Gate dismissal while a generation is in flight (#122).
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
                Generate Research Prompt
              </Dialog.Title>
              <div className="mt-4">
                {fetching ? (
                  <p className="text-sm text-gray-500">Loading suggestions...</p>
                ) : suggestions.length === 0 ? (
                  <p className="text-sm text-gray-500">No approved suggestions have pending research.</p>
                ) : (
                  <select
                    className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                    value={selectedId}
                    onChange={(e) => setSelectedId(e.target.value)}
                  >
                    <option value="">Select a suggestion...</option>
                    {suggestions.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.title} ({s.category_name})
                      </option>
                    ))}
                  </select>
                )}
                {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
                {loading && (
                  <p className="mt-2 text-xs text-gray-500">Generation in progress — please wait.</p>
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

function UploadResearchModal({ open, onClose, token, onSuccess }) {
  const [file, setFile] = useState(null);
  const [suggestions, setSuggestions] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [fetching, setFetching] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  useEffect(() => {
    if (!open) {
      setFile(null);
      setSelectedId('');
      setError(null);
      setResult(null);
      return;
    }
    setFetching(true);
    adminService.getSuggestionsForResearch(token)
      .then((data) => setSuggestions(data))
      .catch((err) => setError(err.message))
      .finally(() => setFetching(false));
  }, [open, token]);

  const handleUpload = async () => {
    if (!file || !selectedId) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await adminService.uploadResearch(token, file, Number(selectedId));
      setResult(res);
      onSuccess?.();
      setTimeout(() => onClose(), 2000);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  // Gate dismissal while an upload is in flight (#122).
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
                Upload Research Document
              </Dialog.Title>
              <div className="mt-4">
                <label className="mb-1 block text-sm font-medium text-gray-700">Suggestion</label>
                {fetching ? (
                  <p className="text-sm text-gray-500">Loading suggestions...</p>
                ) : suggestions.length === 0 ? (
                  <p className="text-sm text-gray-500">No approved suggestions have pending research.</p>
                ) : (
                  <select
                    className="mb-3 w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none disabled:opacity-50"
                    value={selectedId}
                    onChange={(e) => setSelectedId(e.target.value)}
                    disabled={loading}
                  >
                    <option value="">Select a suggestion...</option>
                    {suggestions.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.title} ({s.category_name})
                      </option>
                    ))}
                  </select>
                )}
                <input
                  type="file"
                  accept=".md"
                  onChange={(e) => setFile(e.target.files[0] || null)}
                  disabled={loading}
                  className="w-full text-sm text-gray-500 file:mr-4 file:rounded file:border-0 file:bg-indigo-50 file:px-4 file:py-2 file:text-sm file:font-semibold file:text-indigo-700 hover:file:bg-indigo-100 disabled:opacity-50"
                />
                {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
                {result && (
                  <div className="mt-2 rounded bg-green-50 p-3 text-sm text-green-800">
                    <p>Uploaded successfully: <strong>{result.suggestion_title}</strong> ({result.word_count.toLocaleString()} words)</p>
                    {result.references_generated && (
                      <p className="mt-1 text-green-700">A References section was auto-generated from inline citations.</p>
                    )}
                  </div>
                )}
                {loading && (
                  <p className="mt-2 text-xs text-gray-500">Upload in progress — please wait.</p>
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
                  onClick={handleUpload}
                  disabled={!file || !selectedId || loading}
                  className="rounded bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-700 disabled:opacity-50"
                >
                  {loading ? 'Uploading...' : 'Upload'}
                </button>
              </div>
            </Dialog.Panel>
          </Transition.Child>
        </div>
      </Dialog>
    </Transition>
  );
}

export default function AdminResearch() {
  const { token } = useAdminAuth();
  const gridRef = useRef(null);
  const [rowData, setRowData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedRecord, setSelectedRecord] = useState(null);
  const [promptModalOpen, setPromptModalOpen] = useState(false);
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const [categoryFilter, setCategoryFilter] = useState('');
  const { toasts, show: showToast, dismiss: dismissToast } = useToast();
  const [approveWarning, setApproveWarning] = useState(null);
  const factCheck = useFactCheck({
    token,
    subjectType: 'RESEARCH',
    entityType: 'research',
    showToast,
    dismissToast,
    titleOf: (r) => r.suggestion_title,
  });

  const refreshGrid = useCallback(() => {
    adminService.getResearch(token)
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

  const handleDownload = useCallback(async (record, type) => {
    try {
      const fetcher = type === 'summary'
        ? adminService.downloadResearchSummary(token, record.id)
        : adminService.downloadResearch(token, record.id);
      const { blob, filename } = await fetcher;
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

  const handleGenerateSummary = useCallback(async (record) => {
    try {
      await adminService.generateResearchSummary(token, record.id);
      setRowData((prev) =>
        prev.map((r) => (r.id === record.id ? { ...r, has_summary: true } : r)),
      );
    } catch (err) {
      console.error('Summary generation failed:', err);
      showToast({ type: 'error', title: 'Failed to generate summary', message: err.message || 'Unexpected error' });
    }
  }, [token, showToast]);

  const applyStatusChange = useCallback(async (record, newStatus) => {
    try {
      await adminService.updateStatus(token, 'research', record.id, newStatus);
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
      const warning = factCheckWarning(factCheck.getCounts(record), 'research document');
      if (warning) {
        setApproveWarning({ record, newStatus, warning });
        return;
      }
    }
    await applyStatusChange(record, newStatus);
  }, [applyStatusChange, factCheck]);

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

  // Disable Generate Prompt until all non-rejected research is approved + all articles published
  const canGenerate = useMemo(() => {
    const active = rowData.filter(r => r.status !== 'REJECTED');
    if (active.length === 0) return true;
    return active.every(r => r.status === 'APPROVED' && r.articles_status === 'ALL');
  }, [rowData]);

  // The next pending research to review (lowest suggestion_id among PENDING)
  const nextPendingId = useMemo(() => {
    const pending = rowData.filter(r => r.status === 'PENDING');
    if (pending.length === 0) return null;
    return pending.reduce((min, r) => r.suggestion_id < min.suggestion_id ? r : min).id;
  }, [rowData]);

  // The next approved research needing a summary (lowest suggestion_id among APPROVED without summary)
  const nextSummarizeId = useMemo(() => {
    const needsSummary = rowData.filter(r => r.status === 'APPROVED' && !r.has_summary);
    if (needsSummary.length === 0) return null;
    return needsSummary.reduce((min, r) => r.suggestion_id < min.suggestion_id ? r : min).id;
  }, [rowData]);

  const colDefs = useMemo(() => [
    {
      field: 'suggestion_title',
      headerName: 'Suggestion Title',
      flex: 2,
      minWidth: 250,
      filter: true,
      cellRenderer: ({ data, value }) => (
        <span>
          {value}
          {data.id === nextPendingId && (
            <span className="ml-2 inline-flex items-center rounded bg-indigo-100 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-700">
              Next
            </span>
          )}
          {data.id === nextSummarizeId && (
            <span className="ml-2 inline-flex items-center rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700">
              Summarize
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
    { field: 'articles_status', headerName: 'Articles', width: 120, cellRenderer: ArticlesStatusCell },
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
      width: 180,
      sortable: false,
      filter: false,
      cellRenderer: ActionsCellRenderer,
      cellRendererParams: {
        onEdit: handleEdit,
        onGenerate: handleGenerateSummary,
        showGenerate: (d) => d.status === 'APPROVED' && !d.has_summary,
        onDownload: handleDownload,
        downloadOptions: [
          { value: 'content', label: 'Full Content', isDisabled: (d) => !d.has_content },
          { value: 'summary', label: 'Summary', isDisabled: (d) => !d.has_summary },
        ],
        showFactCheck: (d) => d.has_content,
        onFactCheck: factCheck.run,
        isFactChecking: factCheck.isRunning,
        factCheckTitle: 'Fact-check: extract claims & queue deep research',
      },
    },
  ], [handleEdit, handleStatusChange, handleDownload, handleGenerateSummary, nextPendingId, nextSummarizeId, factCheck]);

  useEffect(() => {
    adminService.getResearch(token)
      .then(setRowData)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [token]);

  if (error) return <div className="text-red-600">Error: {error}</div>;

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Research</h1>
          <p className="mt-2 text-sm text-gray-700">{filteredData.length} of {rowData.length} research documents</p>
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
            onClick={() => setPromptModalOpen(true)}
            disabled={!canGenerate}
            className="rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
            title={!canGenerate ? 'Complete all pending research before generating new prompts' : ''}
          >
            Generate Prompt
          </button>
          <button
            onClick={() => setUploadModalOpen(true)}
            className="rounded bg-green-600 px-4 py-2 text-sm font-medium text-white hover:bg-green-700"
          >
            Upload Research
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
        recordType="Research"
        token={token}
      />

      <GeneratePromptModal
        open={promptModalOpen}
        onClose={() => setPromptModalOpen(false)}
        token={token}
        onSuccess={refreshGrid}
      />

      <UploadResearchModal
        open={uploadModalOpen}
        onClose={() => setUploadModalOpen(false)}
        token={token}
        onSuccess={refreshGrid}
      />

      <FactCheckModal
        open={!!factCheck.modalRecord}
        onClose={() => factCheck.setModalRecord(null)}
        token={token}
        subjectType="RESEARCH"
        subjectId={factCheck.modalRecord?.id}
        title={factCheck.modalRecord?.suggestion_title}
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
