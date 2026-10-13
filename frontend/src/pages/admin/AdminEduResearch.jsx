import { useState, useEffect, useMemo, useCallback, useRef, Fragment } from 'react';
import { Dialog, Transition } from '@headlessui/react';
import { AllCommunityModule, themeAlpine } from 'ag-grid-community';
import { AgGridProvider, AgGridReact } from 'ag-grid-react';
import { useAdminAuth } from '../../contexts/AdminAuthContext';
import { adminService } from '../../services/admin';
import { BooleanCell } from '../../components/admin/SharedCellRenderers';
import ActionsCellRenderer from '../../components/admin/ActionsCellRenderer';
import InlineStatusCell from '../../components/admin/InlineStatusCell';
import EditDrawer from '../../components/admin/EditDrawer';
import { ToastContainer } from '../../components/admin/Toast';
import { useToast } from '../../hooks/useToast';

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
    adminService.getEduSuggestionsForResearch(token)
      .then((data) => setSuggestions(data))
      .catch((err) => setError(err.message))
      .finally(() => setFetching(false));
  }, [open, token]);

  const handleGenerate = async () => {
    if (!selectedId) return;
    setLoading(true);
    setError(null);
    try {
      const result = await adminService.generateEduResearchPrompt(token, Number(selectedId));
      const blob = new Blob([result.prompt_text], { type: 'text/markdown' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `edu_suggestion_${result.suggestion_id}_prompt.md`;
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
                Generate Edu Research Prompt
              </Dialog.Title>
              <div className="mt-4">
                {fetching ? (
                  <p className="text-sm text-gray-500">Loading suggestions...</p>
                ) : suggestions.length === 0 ? (
                  <p className="text-sm text-gray-500">No approved edu suggestions without research.</p>
                ) : (
                  <select
                    className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                    value={selectedId}
                    onChange={(e) => setSelectedId(e.target.value)}
                  >
                    <option value="">Select a suggestion...</option>
                    {suggestions.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.title} ({(s.grade_bands || []).join(', ')})
                      </option>
                    ))}
                  </select>
                )}
                {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
              </div>
              <div className="mt-6 flex justify-end gap-3">
                <button
                  onClick={onClose}
                  className="rounded px-4 py-2 text-sm text-gray-700 hover:bg-gray-100"
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
    adminService.getEduSuggestionsForResearch(token)
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
      const res = await adminService.uploadEduResearch(token, file, Number(selectedId));
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
                Upload Edu Research Document
              </Dialog.Title>
              <div className="mt-4">
                <label className="mb-1 block text-sm font-medium text-gray-700">Suggestion</label>
                {fetching ? (
                  <p className="text-sm text-gray-500">Loading suggestions...</p>
                ) : suggestions.length === 0 ? (
                  <p className="text-sm text-gray-500">No approved edu suggestions without research.</p>
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
                        {s.title} ({(s.grade_bands || []).join(', ')})
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

export default function AdminEduResearch() {
  const { token } = useAdminAuth();
  const gridRef = useRef(null);
  const [rowData, setRowData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedRecord, setSelectedRecord] = useState(null);
  const [promptModalOpen, setPromptModalOpen] = useState(false);
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const { toasts, show: showToast, dismiss: dismissToast } = useToast();

  const refreshGrid = useCallback(() => {
    adminService.getEduResearch(token)
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

  const handleDownload = useCallback(async (record) => {
    try {
      const { blob } = await adminService.downloadEduResearch(token, record.id);
      const titleSlug = record.suggestion_title.replace(/\s+/g, '_');
      const filename = `edu_${record.suggestion_id}_${titleSlug}_research.md`;
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

  const handleStatusChange = useCallback(async (record, newStatus) => {
    try {
      await adminService.updateStatus(token, 'edu/research', record.id, newStatus);
      setRowData((prev) =>
        prev.map((r) => (r.id === record.id ? { ...r, status: newStatus } : r)),
      );
    } catch (err) {
      console.error('Status update failed:', err);
      showToast({ type: 'error', title: 'Failed to update status', message: err.message || 'Unexpected error' });
    }
  }, [token, showToast]);

  const colDefs = useMemo(() => [
    { field: 'suggestion_title', headerName: 'Suggestion Title', flex: 2, minWidth: 250, filter: true },
    {
      field: 'status',
      headerName: 'Status',
      width: 160,
      filter: true,
      cellRenderer: InlineStatusCell,
      cellRendererParams: { onStatusChange: handleStatusChange },
    },
    { field: 'has_content', headerName: 'Has Content', width: 130, cellRenderer: BooleanCell },
    {
      headerName: 'Actions',
      width: 130,
      sortable: false,
      filter: false,
      cellRenderer: ActionsCellRenderer,
      cellRendererParams: {
        onEdit: handleEdit,
        onDownload: handleDownload,
        isDownloadDisabled: (d) => !d.has_content,
      },
    },
  ], [handleEdit, handleStatusChange, handleDownload]);

  useEffect(() => {
    adminService.getEduResearch(token)
      .then(setRowData)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [token]);

  if (error) return <div className="text-red-600">Error: {error}</div>;

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Educational Research</h1>
          <p className="mt-2 text-sm text-gray-700">{rowData.length} research documents</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => gridRef.current?.api?.setFilterModel(null)}
            className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
          >
            Clear Filters
          </button>
          <button
            onClick={() => setPromptModalOpen(true)}
            className="rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
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
            rowData={rowData}
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
        recordType="EduResearch"
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

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
