import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { AllCommunityModule, themeAlpine } from 'ag-grid-community';
import { AgGridProvider, AgGridReact } from 'ag-grid-react';
import { useAdminAuth } from '../../contexts/AdminAuthContext';
import { adminService } from '../../services/admin';
import { BooleanCell, ArticlesStatusCell, DuplicateCell } from '../../components/admin/SharedCellRenderers';
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

function GenerateModal({ open, onClose, onGenerate, generating, generateError }) {
  const [categories, setCategories] = useState([]);
  const [loadingCategories, setLoadingCategories] = useState(false);
  const [selectedCategoryId, setSelectedCategoryId] = useState('');
  const { token } = useAdminAuth();

  useEffect(() => {
    if (!open) return;
    // Async IIFE so these setState calls aren't in the synchronous effect body
    // (set-state-in-effect).
    (async () => {
      setLoadingCategories(true);
      setSelectedCategoryId('');
      try {
        setCategories(await adminService.getCategoriesForGeneration(token));
      } catch {
        setCategories([]);
      } finally {
        setLoadingCategories(false);
      }
    })();
  }, [open, token]);

  if (!open) return null;

  // NOTE (#122): this modal is a plain <div> (not a Headless UI <Dialog>), so it has
  // no backdrop-click or Escape-key dismissal to gate. The Cancel button already
  // carries `disabled={generating}`, which is the only dismissal surface here. The
  // hint below mirrors the in-progress affordance used by the Dialog-based generation
  // modals in AdminArticles/AdminOutlines/AdminResearch.

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
        <h2 className="text-lg font-semibold text-gray-900">Generate Suggestions</h2>
        <p className="mt-1 text-sm text-gray-600">
          Select a Historical Panama category to generate article suggestions.
        </p>

        {generateError && (
          <div className="mt-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {generateError}
          </div>
        )}

        <div className="mt-4">
          {loadingCategories ? (
            <p className="text-sm text-gray-500">Loading categories...</p>
          ) : categories.length === 0 ? (
            <p className="text-sm text-gray-500">All Historical Panama categories already have suggestions.</p>
          ) : (
            <select
              value={selectedCategoryId}
              onChange={(e) => setSelectedCategoryId(e.target.value)}
              disabled={generating}
              className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none disabled:opacity-50"
            >
              <option value="">Select a category...</option>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          )}
          {generating && (
            <p className="mt-2 text-xs text-gray-500">Generation in progress — this may take a few minutes. Please wait.</p>
          )}
        </div>

        <div className="mt-6 flex justify-end gap-3">
          <button
            onClick={onClose}
            disabled={generating}
            className="rounded border border-gray-300 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Cancel
          </button>
          <button
            onClick={() => onGenerate(Number(selectedCategoryId))}
            disabled={!selectedCategoryId || generating}
            className="rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {generating ? 'Generating...' : 'Generate'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function AdminSuggestions() {
  const { token } = useAdminAuth();
  const gridRef = useRef(null);
  const [rowData, setRowData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedRecord, setSelectedRecord] = useState(null);
  const [drawerRecordType, setDrawerRecordType] = useState('Suggestion');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState(null);
  const [approveWarning, setApproveWarning] = useState(null);
  const { toasts, show: showToast, dismiss: dismissToast } = useToast();
  const factCheck = useFactCheck({
    token,
    subjectType: 'SUGGESTION',
    entityType: 'suggestions',
    showToast,
    dismissToast,
    titleOf: (r) => r.title,
  });

  const handleEdit = useCallback((record) => {
    setSelectedRecord(record);
    setDrawerRecordType('Suggestion');
    setDrawerOpen(true);
  }, []);

  // A "possible duplicate" pill pointing at another suggestion opens its drawer.
  const handleOpenSuggestion = useCallback((id) => {
    const match = rowData.find((r) => r.id === id);
    if (match) handleEdit(match);
  }, [rowData, handleEdit]);

  // A pill pointing at an article opens the admin Article drawer (it fetches
  // its own detail by id); never the public page, which 404s an unpublished one.
  const handleOpenArticle = useCallback((id) => {
    setSelectedRecord({ id });
    setDrawerRecordType('Article');
    setDrawerOpen(true);
  }, []);

  const applyStatusChange = useCallback(async (record, newStatus) => {
    try {
      await adminService.updateStatus(token, 'suggestions', record.id, newStatus);
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
      const warning = factCheckWarning(factCheck.getCounts(record), 'suggestion');
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

  // Disable Generate until all non-rejected suggestions have research + all articles published
  const canGenerate = useMemo(() => {
    const active = rowData.filter(r => r.status !== 'REJECTED');
    if (active.length === 0) return true;
    return active.every(r => r.has_research && r.articles_status === 'ALL');
  }, [rowData]);

  const handleGenerate = useCallback(async (categoryId) => {
    setGenerating(true);
    setGenerateError(null);
    try {
      await adminService.generateSuggestions(token, categoryId);
      const refreshed = await adminService.getSuggestions(token);
      setRowData(refreshed);
      setModalOpen(false);
    } catch (err) {
      setGenerateError(err.message);
    } finally {
      setGenerating(false);
    }
  }, [token]);

  const colDefs = useMemo(() => [
    { field: 'title', headerName: 'Title', flex: 2, minWidth: 200, filter: true },
    { field: 'category_name', headerName: 'Category', flex: 1, minWidth: 120, filter: true },
    {
      field: 'duplicate_similarity',
      headerName: 'Duplicate?',
      flex: 1,
      minWidth: 160,
      filter: false,
      cellRenderer: DuplicateCell,
      cellRendererParams: { onOpenSuggestion: handleOpenSuggestion, onOpenArticle: handleOpenArticle },
    },
    {
      field: 'status',
      headerName: 'Status',
      width: 160,
      filter: true,
      cellRenderer: InlineStatusCell,
      cellRendererParams: { onStatusChange: handleStatusChange },
    },
    { field: 'has_research', headerName: 'Research', width: 110, cellRenderer: BooleanCell },
    { field: 'articles_status', headerName: 'Articles', width: 110, cellRenderer: ArticlesStatusCell },
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
      width: 90,
      sortable: false,
      filter: false,
      cellRenderer: ActionsCellRenderer,
      cellRendererParams: {
        onEdit: handleEdit,
        showFactCheck: () => true,
        onFactCheck: factCheck.run,
        isFactChecking: factCheck.isRunning,
        factCheckTitle: 'Fact-check: extract claims & queue deep research',
      },
    },
  ], [handleEdit, handleStatusChange, factCheck, handleOpenSuggestion, handleOpenArticle]);

  useEffect(() => {
    adminService.getSuggestions(token)
      .then(setRowData)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [token]);

  if (error) return <div className="text-red-600">Error: {error}</div>;

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Article Suggestions</h1>
          <p className="mt-2 text-sm text-gray-700">{filteredData.length} of {rowData.length} suggestions</p>
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
            onClick={() => { setGenerateError(null); setModalOpen(true); }}
            disabled={!canGenerate}
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
            title={!canGenerate ? 'Complete all pending suggestions before generating new ones' : ''}
          >
            Generate Suggestions
          </button>
          <button
            onClick={() => gridRef.current?.api?.setFilterModel(null)}
            className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
          >
            Clear Filters
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
        recordType={drawerRecordType}
        token={token}
      />

      <GenerateModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onGenerate={handleGenerate}
        generating={generating}
        generateError={generateError}
      />

      <FactCheckModal
        open={!!factCheck.modalRecord}
        onClose={() => factCheck.setModalRecord(null)}
        token={token}
        subjectType="SUGGESTION"
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
