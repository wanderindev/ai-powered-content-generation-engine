import { useState, useEffect, useMemo, useCallback, useRef, Fragment } from 'react';
import { Dialog, Transition } from '@headlessui/react';
import { AllCommunityModule, themeAlpine } from 'ag-grid-community';
import { AgGridProvider, AgGridReact } from 'ag-grid-react';
import { useAdminAuth } from '../../contexts/AdminAuthContext';
import { adminService } from '../../services/admin';
import { StatusCell, BooleanCell } from '../../components/admin/SharedCellRenderers';
import ActionsCellRenderer from '../../components/admin/ActionsCellRenderer';
import InlineStatusCell from '../../components/admin/InlineStatusCell';
import EditDrawer from '../../components/admin/EditDrawer';
import StatusDropdown from '../../components/admin/StatusDropdown';
import { ToastContainer } from '../../components/admin/Toast';
import { useToast } from '../../hooks/useToast';

const modules = [AllCommunityModule];

function OutlineStatusCell({ data, colDef }) {
  const value = data.outline_status;
  const onStatusChange = colDef.cellRendererParams?.onOutlineStatusChange;

  return (
    <div className="flex items-center gap-1">
      <StatusCell value={value} />
      {onStatusChange && (
        <StatusDropdown
          currentStatus={value || 'PENDING'}
          onStatusChange={(newStatus) => onStatusChange(data, newStatus)}
        />
      )}
    </div>
  );
}

function GenerateOutlineModal({ open, onClose, token, onSuccess }) {
  const [research, setResearch] = useState([]);
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
    adminService.getResearchForOutlines(token)
      .then((data) => setResearch(data))
      .catch((err) => setError(err.message))
      .finally(() => setFetching(false));
  }, [open, token]);

  const handleGenerate = async () => {
    if (!selectedId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await adminService.generateOutlines(token, Number(selectedId));
      setResult(res);
      onSuccess?.();
      setTimeout(() => onClose(), 2000);
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
                Generate Outlines
              </Dialog.Title>
              <div className="mt-4">
                {fetching ? (
                  <p className="text-sm text-gray-500">Loading available research...</p>
                ) : research.length === 0 ? (
                  <p className="text-sm text-gray-500">No approved research available for outline generation.</p>
                ) : (
                  <select
                    className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                    value={selectedId}
                    onChange={(e) => setSelectedId(e.target.value)}
                  >
                    <option value="">Select research...</option>
                    {research.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.suggestion_title}
                      </option>
                    ))}
                  </select>
                )}
                {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
                {result && (
                  <div className="mt-2 rounded bg-green-50 p-3 text-sm text-green-800">
                    Created {result.articles_created} article(s){result.is_series ? ' (series)' : ''}.
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

export default function AdminOutlines() {
  const { token } = useAdminAuth();
  const gridRef = useRef(null);
  const [rowData, setRowData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedRecord, setSelectedRecord] = useState(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [categoryFilter, setCategoryFilter] = useState('');
  const { toasts, show: showToast, dismiss: dismissToast } = useToast();

  const refreshGrid = useCallback(() => {
    adminService.getArticles(token)
      .then((data) => setRowData(data.filter((a) => a.has_outline)))
      .catch((err) => {
        console.error('Refresh failed:', err);
        showToast({ type: 'error', title: 'Failed to refresh', message: 'Data may be out of date' });
      });
  }, [token, showToast]);

  const handleEdit = useCallback((record) => {
    setSelectedRecord(record);
    setDrawerOpen(true);
  }, []);

  const handleOutlineStatusChange = useCallback(async (record, newStatus) => {
    try {
      await adminService.updateOutlineStatus(token, record.id, newStatus);
      setRowData((prev) =>
        prev.map((r) => (r.id === record.id ? { ...r, outline_status: newStatus } : r)),
      );
    } catch (err) {
      console.error('Outline status update failed:', err);
      showToast({ type: 'error', title: 'Failed to update outline status', message: err.message || 'Unexpected error' });
    }
  }, [token, showToast]);

  const handleStatusChange = useCallback(async (record, newStatus) => {
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

  // The next outline to review (lowest suggestion_id among PENDING outlines)
  const nextOutlineId = useMemo(() => {
    const pending = rowData.filter(r => r.outline_status === 'PENDING');
    if (pending.length === 0) return null;
    return pending.reduce((min, r) =>
      (r.suggestion_id ?? Infinity) < (min.suggestion_id ?? Infinity) ? r : min
    ).id;
  }, [rowData]);

  // Disable Generate Outlines if any articles exist but are not published
  const canGenerate = useMemo(() => {
    if (rowData.length === 0) return true;
    return rowData.every(r => r.published_at != null);
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
          {data.id === nextOutlineId && (
            <span className="ml-2 inline-flex items-center rounded bg-indigo-100 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-700">
              Next
            </span>
          )}
        </span>
      ),
    },
    {
      field: 'outline_status',
      headerName: 'Outline Status',
      width: 170,
      filter: true,
      cellRenderer: OutlineStatusCell,
      cellRendererParams: { onOutlineStatusChange: handleOutlineStatusChange },
    },
    {
      field: 'status',
      headerName: 'Article Status',
      width: 160,
      filter: true,
      cellRenderer: InlineStatusCell,
      cellRendererParams: { onStatusChange: handleStatusChange },
    },
    { field: 'has_content', headerName: 'Has Content', width: 110, cellRenderer: BooleanCell },
    {
      headerName: 'Actions',
      width: 60,
      sortable: false,
      filter: false,
      cellRenderer: ActionsCellRenderer,
      cellRendererParams: {
        onEdit: handleEdit,
      },
    },
  ], [handleEdit, handleStatusChange, handleOutlineStatusChange, nextOutlineId]);

  useEffect(() => {
    adminService.getArticles(token)
      .then((data) => setRowData(data.filter((a) => a.has_outline)))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [token]);

  if (error) return <div className="text-red-600">Error: {error}</div>;

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Article Outlines</h1>
          <p className="mt-2 text-sm text-gray-700">{filteredData.length} of {rowData.length} articles with outlines</p>
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
            disabled={!canGenerate}
            className="rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
            title={!canGenerate ? 'Publish all generated articles before generating new outlines' : ''}
          >
            Generate Outlines
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
        recordType="Outline"
        token={token}
      />

      <GenerateOutlineModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        token={token}
        onSuccess={refreshGrid}
      />

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
