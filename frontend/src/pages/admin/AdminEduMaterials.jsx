import { useState, useEffect, useMemo, useCallback, useRef, Fragment } from 'react';
import { Dialog, Transition } from '@headlessui/react';
import { AllCommunityModule, themeAlpine } from 'ag-grid-community';
import { AgGridProvider, AgGridReact } from 'ag-grid-react';
import { useAdminAuth } from '../../contexts/AdminAuthContext';
import { adminService } from '../../services/admin';
import ActionsCellRenderer from '../../components/admin/ActionsCellRenderer';
import InlineStatusCell from '../../components/admin/InlineStatusCell';
import { cacheBustUrl } from '../../utils/cacheBust';

// Only the two public-delivery types are publishable, and only once APPROVED
// (#326/#327). Publishing the slides deck makes its assembled video go live.
const PUBLISHABLE_TYPES = ['slides', 'guide'];

// The assembled MP4 is stored as a sibling `video` EduMaterial (assemble-video
// upserts on research_id × "video" × grade_band), but it is an implementation
// artifact, not something an admin acts on: it is reached, regenerated and
// published through its slides deck (the deck drawer's "Open video" link,
// #361), and the public surface ignores the video row's own status. Showing
// it only offers a misleading inline-status control, so keep it off the grid.
const HIDDEN_TYPES = ['video'];
const visibleMaterials = (rows) => rows.filter((r) => !HIDDEN_TYPES.includes(r.material_type));
import EditDrawer from '../../components/admin/EditDrawer';
import FactCheckStatusCell from '../../components/admin/FactCheckStatusCell';
import FactCheckModal from '../../components/admin/FactCheckModal';
import { ToastContainer } from '../../components/admin/Toast';
import { useToast } from '../../hooks/useToast';
import { useFactCheck, factCheckState } from '../../hooks/useFactCheck';
import ConfirmDialog from '../../components/admin/ConfirmDialog';

const modules = [AllCommunityModule];

function GenerateMaterialsModal({ open, onClose, token, onSuccess }) {
  const [mode, setMode] = useState('guide'); // 'guide' | 'slides'
  const [researchList, setResearchList] = useState([]);
  const [guideList, setGuideList] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [loading, setLoading] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [progress, setProgress] = useState(null); // guide job's current band (#346)

  const selectedResearch = researchList.find((r) => String(r.id) === selectedId);
  const selectedGuide = guideList.find((g) => String(g.id) === selectedId);

  useEffect(() => {
    if (!open) return;
    setSelectedId('');
    setError(null);
    setResult(null);
    setFetching(true);
    Promise.all([
      adminService.getEduResearchForMaterials(token),
      adminService.getEduGuidesForSlides(token),
    ])
      .then(([research, guides]) => {
        setResearchList(research);
        setGuideList(guides);
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
    setProgress(null);
    try {
      if (mode === 'guide') {
        // Background job (#346): bands commit as they finish; the button label
        // tracks which band is being written.
        const res = await adminService.generateEduMaterials(token, Number(selectedId), {
          onProgress: (job) =>
            setProgress(
              job.current_unit && job.units_total
                ? `${job.current_unit} (${job.units_done + 1}/${job.units_total})`
                : null,
            ),
        });
        setResult(res);
      } else {
        const res = await adminService.generateEduSlides(token, Number(selectedId));
        setResult(res);
      }
      onSuccess?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
      setProgress(null);
    }
  };

  const isGenerateDisabled = mode === 'guide'
    ? !selectedId || loading || !selectedResearch?.grade_bands?.length
    : !selectedId || loading;

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
                {mode === 'guide' ? 'Generate Study Guides' : 'Generate Slide Deck'}
              </Dialog.Title>

              {/* Mode toggle */}
              <div className="mt-3 flex gap-2">
                <button
                  onClick={() => handleModeChange('guide')}
                  className={`rounded-full px-3 py-1 text-sm font-medium ${
                    mode === 'guide'
                      ? 'bg-indigo-100 text-indigo-700'
                      : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
                >
                  Study Guide
                </button>
                <button
                  onClick={() => handleModeChange('slides')}
                  className={`rounded-full px-3 py-1 text-sm font-medium ${
                    mode === 'slides'
                      ? 'bg-indigo-100 text-indigo-700'
                      : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
                >
                  Slide Deck
                </button>
              </div>

              <div className="mt-4">
                {fetching ? (
                  <p className="text-sm text-gray-500">Loading...</p>
                ) : mode === 'guide' ? (
                  /* Study Guide mode */
                  researchList.length === 0 ? (
                    <p className="text-sm text-gray-500">No approved research with study guides left to generate.</p>
                  ) : (
                    <>
                      <select
                        size={8}
                        className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                        value={selectedId}
                        onChange={(e) => { setSelectedId(e.target.value); setResult(null); }}
                      >
                        <option value="">Select research...</option>
                        {researchList.map((r) => (
                          <option key={r.id} value={r.id}>
                            {r.suggestion_title}
                          </option>
                        ))}
                      </select>
                      {selectedResearch && (
                        <p className="mt-2 text-xs text-gray-500">
                          Grade bands: {selectedResearch.grade_bands.join(', ') || 'None configured'}
                          {selectedResearch.bands_to_generate?.length > 0 &&
                            selectedResearch.bands_to_generate.length < selectedResearch.grade_bands.length && (
                              <>
                                <br />
                                Will generate: {selectedResearch.bands_to_generate.join(', ')}{' '}
                                (approved guides are kept; pending ones are rewritten)
                              </>
                            )}
                        </p>
                      )}
                    </>
                  )
                ) : (
                  /* Slide Deck mode */
                  guideList.length === 0 ? (
                    <p className="text-sm text-gray-500">No approved guides without slides.</p>
                  ) : (
                    <>
                      <select
                        size={8}
                        className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                        value={selectedId}
                        onChange={(e) => { setSelectedId(e.target.value); setResult(null); }}
                      >
                        <option value="">Select study guide...</option>
                        {guideList.map((g) => (
                          <option key={g.id} value={g.id}>
                            {g.suggestion_title} ({g.grade_band})
                          </option>
                        ))}
                      </select>
                      {selectedGuide && (
                        <p className="mt-2 text-xs text-gray-500">
                          {selectedGuide.title} — {selectedGuide.grade_band}
                        </p>
                      )}
                    </>
                  )
                )}
                {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
                {result && mode === 'guide' && (
                  <div className="mt-2 rounded bg-green-50 p-3 text-sm text-green-800">
                    <p>
                      Generated <strong>{result.materials_created}</strong> study guide{result.materials_created !== 1 ? 's' : ''} for{' '}
                      <strong>{result.suggestion_title}</strong>
                    </p>
                    <p className="mt-1 text-green-700">
                      Grade bands: {result.grade_bands.join(', ')}
                    </p>
                  </div>
                )}
                {result && mode === 'slides' && (
                  <div className="mt-2 rounded bg-green-50 p-3 text-sm text-green-800">
                    <p>
                      Generated slide deck for <strong>{result.suggestion_title}</strong> ({result.grade_band})
                    </p>
                  </div>
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
                    disabled={isGenerateDisabled}
                    className="rounded bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-700 disabled:opacity-50"
                  >
                    {loading
                      ? (mode === 'guide'
                        ? `Generating study guides...${progress ? ` ${progress}` : ''}`
                        : 'Generating slides...')
                      : 'Generate'}
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

const MATERIAL_TYPE_OPTIONS = [
  { value: 'slides', label: 'Slides (PDF)' },
  { value: 'infographic', label: 'Infographic (PNG)' },
  { value: 'podcast', label: 'Podcast (M4A)' },
];

const ACCEPT_MAP = {
  slides: '.pdf',
  infographic: '.png',
  podcast: '.m4a',
};

function UploadMaterialModal({ open, onClose, token, onSuccess }) {
  const [researchList, setResearchList] = useState([]);
  const [materialType, setMaterialType] = useState('slides');
  const [selectedResearchId, setSelectedResearchId] = useState('');
  const [file, setFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const fileInputRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    setSelectedResearchId('');
    setFile(null);
    setError(null);
    setResult(null);
    setFetching(true);
    adminService.getBlogResearchForUpload(token)
      .then(setResearchList)
      .catch((err) => setError(err.message))
      .finally(() => setFetching(false));
  }, [open, token]);

  const handleTypeChange = (type) => {
    setMaterialType(type);
    setFile(null);
    setResult(null);
    setError(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleUpload = async () => {
    if (!selectedResearchId || !file) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await adminService.uploadMaterial(token, {
        materialType,
        blogResearchId: Number(selectedResearchId),
        file,
      });
      setResult(res);
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
                Upload Material
              </Dialog.Title>

              {/* Material type selector */}
              <div className="mt-3 flex gap-2">
                {MATERIAL_TYPE_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => handleTypeChange(opt.value)}
                    className={`rounded-full px-3 py-1 text-sm font-medium ${
                      materialType === opt.value
                        ? 'bg-indigo-100 text-indigo-700'
                        : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>

              <div className="mt-4 space-y-3">
                {fetching ? (
                  <p className="text-sm text-gray-500">Loading...</p>
                ) : researchList.length === 0 ? (
                  <p className="text-sm text-gray-500">No approved blog research available.</p>
                ) : (
                  <>
                    <select
                      size={8}
                      className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                      value={selectedResearchId}
                      onChange={(e) => { setSelectedResearchId(e.target.value); setResult(null); }}
                    >
                      <option value="">Select research...</option>
                      {researchList.map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.suggestion_title}
                        </option>
                      ))}
                    </select>

                    <input
                      ref={fileInputRef}
                      type="file"
                      accept={ACCEPT_MAP[materialType]}
                      onChange={(e) => setFile(e.target.files[0] || null)}
                      className="block w-full text-sm text-gray-500 file:mr-4 file:rounded file:border-0 file:bg-indigo-50 file:px-4 file:py-2 file:text-sm file:font-medium file:text-indigo-700 hover:file:bg-indigo-100"
                    />
                  </>
                )}

                {error && <p className="text-sm text-red-600">{error}</p>}
                {result && (
                  <div className="rounded bg-green-50 p-3 text-sm text-green-800">
                    <p>
                      Uploaded <strong>{result.material_type}</strong> for{' '}
                      <strong>{result.suggestion_title}</strong>
                    </p>
                  </div>
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
                    onClick={handleUpload}
                    disabled={!selectedResearchId || !file || loading}
                    className="rounded bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-700 disabled:opacity-50"
                  >
                    {loading ? 'Uploading...' : 'Upload'}
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

export default function AdminEduMaterials() {
  const { token } = useAdminAuth();
  const gridRef = useRef(null);
  const [rowData, setRowData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedRecord, setSelectedRecord] = useState(null);
  const [generateModalOpen, setGenerateModalOpen] = useState(false);
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const { toasts, show: showToast, dismiss: dismissToast } = useToast();
  const [confirmAction, setConfirmAction] = useState(null);
  // Guides are grounded against their research (#461), so there is no mode to
  // pick. entityType is the edu router's path segment: the run POSTs to
  // /admin/dashboard/edu/materials/{id}/fact-check.
  const factCheck = useFactCheck({
    token,
    subjectType: 'EDU_MATERIAL',
    entityType: 'edu/materials',
    showToast,
    dismissToast,
    titleOf: (r) => r.title,
  });

  const refreshGrid = useCallback(() => {
    adminService.getEduMaterials(token)
      .then((rows) => setRowData(visibleMaterials(rows)))
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
      if (record.file_url) {
        window.open(cacheBustUrl(record.file_url), '_blank');
        return;
      }
      const { blob, filename } = await adminService.downloadEduMaterial(token, record.id);
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
      await adminService.updateStatus(token, 'edu/materials', record.id, newStatus);
      setRowData((prev) =>
        prev.map((r) => (r.id === record.id ? { ...r, status: newStatus } : r)),
      );
    } catch (err) {
      console.error('Status update failed:', err);
      showToast({ type: 'error', title: 'Failed to update status', message: err.message || 'Unexpected error' });
    }
  }, [token, showToast]);

  // Translate an APPROVED study guide to Spanish (DeepL). Slides/narration are
  // authored natively in Spanish elsewhere, so this only applies to guides.
  const runTranslate = useCallback(async (record) => {
    try {
      const res = await adminService.translateEduGuide(token, record.id);
      showToast({ type: 'success', title: 'Translated to Spanish', message: `${res.word_count} words` });
      // Reflect the new translation so the Translate action hides (issue: it lingered).
      setRowData((prev) => prev.map((r) => (r.id === record.id ? { ...r, has_spanish: true } : r)));
    } catch (err) {
      console.error('Translate failed:', err);
      showToast({ type: 'error', title: 'Failed to translate', message: err.message || 'Unexpected error' });
    }
  }, [token, showToast]);

  // Soft gate (#462). The backend hard-blocks INACCURATE; unresolved claims are
  // allowed through on an internal step, but the operator should know first —
  // a correction later means paying DeepL for the same guide twice.
  const handleTranslate = useCallback((record) => {
    const state = factCheckState(factCheck.getCounts(record));
    if (state.kind === 'open' || state.kind === 'disputed') {
      setConfirmAction({
        record,
        run: runTranslate,
        title: 'Translate with unresolved claims?',
        message:
          'This guide still has claims awaiting deep research or editorial review. '
          + 'If any turn out inaccurate you will need to correct and re-translate.',
        confirmLabel: 'Translate anyway',
      });
      return;
    }
    runTranslate(record);
  }, [factCheck, runTranslate]);

  // Publish / unpublish a video or guide material to the public delivery
  // surface (#326/#327). Reflect published_at locally so the cell updates.
  const runPublish = useCallback(async (record) => {
    try {
      const res = await adminService.publishEduMaterial(token, record.id);
      setRowData((prev) =>
        prev.map((r) => (r.id === record.id ? { ...r, published_at: res.published_at } : r)),
      );
      showToast({ type: 'success', title: 'Published', message: 'Now live for teachers' });
    } catch (err) {
      console.error('Publish failed:', err);
      showToast({ type: 'error', title: 'Failed to publish', message: err.message || 'Unexpected error' });
    }
  }, [token, showToast]);

  // Soft gate (#462). Unresolved claims are hard-blocked by the backend on this
  // step; the one case left to the operator is material that was never checked
  // at all — warn, matching how the blog pipeline treats an unchecked article.
  const handlePublish = useCallback((record) => {
    if (factCheckState(factCheck.getCounts(record)).kind === 'none') {
      setConfirmAction({
        record,
        run: runPublish,
        title: 'Publish without a fact-check?',
        message:
          'This material has never been fact-checked. Publishing puts it in front of '
          + 'teachers — and, for a deck, its narrated video in Diego\'s voice.',
        confirmLabel: 'Publish anyway',
      });
      return;
    }
    runPublish(record);
  }, [factCheck, runPublish]);

  const handleUnpublish = useCallback(async (record) => {
    try {
      await adminService.unpublishEduMaterial(token, record.id);
      setRowData((prev) =>
        prev.map((r) => (r.id === record.id ? { ...r, published_at: null } : r)),
      );
      showToast({ type: 'success', title: 'Unpublished' });
    } catch (err) {
      console.error('Unpublish failed:', err);
      showToast({ type: 'error', title: 'Failed to unpublish', message: err.message || 'Unexpected error' });
    }
  }, [token, showToast]);

  const colDefs = useMemo(() => [
    { field: 'suggestion_title', headerName: 'Suggestion', flex: 2, minWidth: 200, filter: true },
    { field: 'material_type', headerName: 'Type', width: 120, filter: true },
    { field: 'title', headerName: 'Title', flex: 2, minWidth: 200, filter: true },
    { field: 'grade_band', headerName: 'Grade Band', width: 130, filter: true },
    {
      field: 'status',
      headerName: 'Status',
      width: 160,
      filter: true,
      cellRenderer: InlineStatusCell,
      cellRendererParams: { onStatusChange: handleStatusChange },
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
      width: 190,
      sortable: false,
      filter: false,
      cellRenderer: ActionsCellRenderer,
      cellRendererParams: {
        onEdit: handleEdit,
        onGenerate: handleTranslate,
        showGenerate: (d) => d.material_type === 'guide' && d.status === 'APPROVED' && !d.has_spanish,
        generateTitle: 'Translate to Spanish',
        onDownload: handleDownload,
        showPublish: (d) =>
          PUBLISHABLE_TYPES.includes(d.material_type) && d.status === 'APPROVED' && !d.published_at,
        isPublished: (d) => !!d.published_at,
        onPublish: handlePublish,
        onUnpublish: handleUnpublish,
        // Guides only, and only once they have structured blocks — the blocks
        // are what a correction can be applied to (#461). Decks are #464.
        // A guide is checked via its blocks, a deck via its slide rows — both
        // must exist, since they are the correctable target (#461/#464). A
        // video has nothing of its own: it renders its deck's narration.
        showFactCheck: (d) =>
          (d.material_type === 'guide' && d.has_blocks) ||
          (d.material_type === 'slides' && d.has_slides),
        onFactCheck: factCheck.run,
        isFactChecking: factCheck.isRunning,
        factCheckTitle: 'Fact-check: ground this material against its research',
      },
    },
  ], [handleEdit, handleStatusChange, handleDownload, handleTranslate, handlePublish, handleUnpublish, factCheck]);

  useEffect(() => {
    adminService.getEduMaterials(token)
      .then((rows) => setRowData(visibleMaterials(rows)))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [token]);

  if (error) return <div className="text-red-600">Error: {error}</div>;

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Educational Materials</h1>
          <p className="mt-2 text-sm text-gray-700">{rowData.length} materials</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => gridRef.current?.api?.setFilterModel(null)}
            className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
          >
            Clear Filters
          </button>
          <button
            onClick={() => setUploadModalOpen(true)}
            className="rounded border border-indigo-600 px-4 py-2 text-sm font-medium text-indigo-600 hover:bg-indigo-50"
          >
            Upload Material
          </button>
          <button
            onClick={() => setGenerateModalOpen(true)}
            className="rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
          >
            Generate Materials
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
        onClose={() => {
          setDrawerOpen(false);
          // Deck pipeline actions mutate rows (status/file changes; assemble-video
          // also adds a hidden sibling video material) — refresh so the grid
          // reflects them without a page reload.
          refreshGrid();
        }}
        record={selectedRecord}
        recordType="EduMaterial"
        token={token}
      />

      <GenerateMaterialsModal
        open={generateModalOpen}
        onClose={() => setGenerateModalOpen(false)}
        token={token}
        onSuccess={refreshGrid}
      />

      <UploadMaterialModal
        open={uploadModalOpen}
        onClose={() => setUploadModalOpen(false)}
        token={token}
        onSuccess={refreshGrid}
      />

      <ConfirmDialog
        open={!!confirmAction}
        onClose={() => setConfirmAction(null)}
        onConfirm={async () => {
          const { record, run } = confirmAction;
          setConfirmAction(null);
          await run(record);
        }}
        title={confirmAction?.title}
        message={confirmAction?.message}
        confirmLabel={confirmAction?.confirmLabel}
      />

      <FactCheckModal
        open={!!factCheck.modalRecord}
        onClose={() => factCheck.setModalRecord(null)}
        token={token}
        subjectType="EDU_MATERIAL"
        subjectId={factCheck.modalRecord?.id}
        title={factCheck.modalRecord?.title}
        onCountsChange={factCheck.updateCounts}
      />

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
