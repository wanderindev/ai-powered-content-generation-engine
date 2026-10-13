import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
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

export default function AdminEduSuggestions() {
  const { token } = useAdminAuth();
  const gridRef = useRef(null);
  const [rowData, setRowData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedRecord, setSelectedRecord] = useState(null);
  const { toasts, show: showToast, dismiss: dismissToast } = useToast();

  const handleEdit = useCallback((record) => {
    setSelectedRecord(record);
    setDrawerOpen(true);
  }, []);

  const handleStatusChange = useCallback(async (record, newStatus) => {
    try {
      await adminService.updateEduSuggestionStatus(token, record.id, newStatus);
      setRowData((prev) =>
        prev.map((r) => (r.id === record.id ? { ...r, status: newStatus } : r)),
      );
    } catch (err) {
      console.error('Status update failed:', err);
      showToast({ type: 'error', title: 'Failed to update status', message: err.message || 'Unexpected error' });
    }
  }, [token, showToast]);

  const colDefs = useMemo(() => [
    { field: 'title', headerName: 'Title', flex: 2, minWidth: 200, filter: true },
    { field: 'excursion_key', headerName: 'Excursion', flex: 1, minWidth: 140, filter: true },
    {
      field: 'grade_bands',
      headerName: 'Grade Bands',
      flex: 1,
      minWidth: 140,
      valueFormatter: (params) => (params.value || []).join(', '),
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
    {
      headerName: 'Actions',
      width: 100,
      sortable: false,
      filter: false,
      cellRenderer: ActionsCellRenderer,
      cellRendererParams: { onEdit: handleEdit },
    },
  ], [handleStatusChange, handleEdit]);

  useEffect(() => {
    adminService.getEduSuggestions(token)
      .then(setRowData)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [token]);

  if (error) return <div className="text-red-600">Error: {error}</div>;

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Educational Suggestions</h1>
          <p className="mt-2 text-sm text-gray-700">{rowData.length} suggestions</p>
        </div>
        <button
          onClick={() => gridRef.current?.api?.setFilterModel(null)}
          className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
        >
          Clear Filters
        </button>
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
        recordType="EduSuggestion"
        token={token}
      />

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
