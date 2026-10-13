import { Menu, MenuButton, MenuItems, MenuItem } from '@headlessui/react';
import {
  PencilSquareIcon,
  SparklesIcon,
  ArrowDownTrayIcon,
  CalendarIcon,
  ShieldCheckIcon,
  TrashIcon,
} from '@heroicons/react/24/outline';

export default function ActionsCellRenderer({ data, colDef }) {
  const {
    onEdit, onGenerate, generateTitle = 'Generate',
    showGenerate, onDownload, downloadOptions, isDownloadDisabled,
    // Optional publish action (calendar icon; green once published). Gated
    // behind showPublish/isPublished so grids that don't pass them are unaffected.
    showPublish, isPublished, onPublish, onUnpublish, publishTitle = 'Publish',
    // Optional fact-check action (#378). Gated behind showFactCheck; grids
    // that don't pass it are unaffected. isFactChecking greys the icon while
    // a run is in flight for that row. factCheckTitle lets grids describe
    // their tier (extract + deep-research queueing vs article grounding).
    showFactCheck, onFactCheck, isFactChecking, factCheckTitle = 'Run fact-check',
    // Optional delete action (Instagram drafts). Gated behind showDelete.
    showDelete, onDelete,
  } = colDef.cellRendererParams || {};

  const generateVisible = showGenerate ? showGenerate(data) : false;
  const publishVisible = showPublish ? showPublish(data) : false;
  const publishedVisible = isPublished ? isPublished(data) : false;
  const factCheckVisible = showFactCheck ? showFactCheck(data) : false;
  const factChecking = isFactChecking ? isFactChecking(data) : false;
  const deleteVisible = showDelete ? showDelete(data) : false;
  const hasDownload = !!onDownload;
  const hasDropdown = downloadOptions && downloadOptions.length > 0;

  // Simple download (no dropdown) — check if disabled
  const simpleDisabled = isDownloadDisabled ? isDownloadDisabled(data) : false;

  return (
    <div className="flex items-center gap-1">
      <button
        onClick={() => onEdit?.(data)}
        className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-indigo-600"
        title="Edit"
      >
        <PencilSquareIcon className="h-4.5 w-4.5" />
      </button>

      {generateVisible && (
        <button
          onClick={() => onGenerate?.(data)}
          className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-amber-600"
          title={generateTitle}
        >
          <SparklesIcon className="h-4.5 w-4.5" />
        </button>
      )}

      {factCheckVisible && (
        <button
          onClick={() => !factChecking && onFactCheck?.(data)}
          disabled={factChecking}
          className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-blue-600 disabled:animate-pulse disabled:cursor-wait disabled:text-blue-400"
          title={factChecking ? 'Fact-check running...' : factCheckTitle}
        >
          <ShieldCheckIcon className="h-4.5 w-4.5" />
        </button>
      )}

      {publishVisible && (
        <button
          onClick={() => onPublish?.(data)}
          className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-green-600"
          title={publishTitle}
        >
          <CalendarIcon className="h-4.5 w-4.5" />
        </button>
      )}

      {deleteVisible && (
        <button
          onClick={() => onDelete?.(data)}
          className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-red-600"
          title="Delete"
        >
          <TrashIcon className="h-4.5 w-4.5" />
        </button>
      )}

      {publishedVisible && (
        <button
          onClick={() => onUnpublish?.(data)}
          className="rounded p-1 text-green-500 hover:bg-gray-100 hover:text-red-600"
          title={
            data.published_at
              ? `Published ${new Date(data.published_at).toLocaleDateString()} — click to unpublish`
              : 'Unpublish'
          }
        >
          <CalendarIcon className="h-4.5 w-4.5" />
        </button>
      )}

      {hasDownload && !hasDropdown && (
        <button
          onClick={() => !simpleDisabled && onDownload?.(data)}
          disabled={simpleDisabled}
          className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-green-600 disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-gray-400"
          title={simpleDisabled ? 'No content to download' : 'Download'}
        >
          <ArrowDownTrayIcon className="h-4.5 w-4.5" />
        </button>
      )}

      {hasDownload && hasDropdown && (
        <Menu as="div" className="relative inline-block">
          <MenuButton className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-green-600" title="Download">
            <ArrowDownTrayIcon className="h-4.5 w-4.5" />
          </MenuButton>
          <MenuItems
            portal
            anchor="bottom end"
            className="z-50 w-36 rounded-md bg-white py-1 shadow-lg ring-1 ring-black/5 outline-none [--anchor-gap:4px]"
          >
            {downloadOptions.map((opt) => {
              const disabled = opt.isDisabled ? opt.isDisabled(data) : false;
              return (
                <MenuItem key={opt.value} disabled={disabled}>
                  <button
                    onClick={() => !disabled && onDownload?.(data, opt.value)}
                    disabled={disabled}
                    className="block w-full px-3 py-1.5 text-left text-sm text-gray-700 data-focus:bg-gray-50 data-focus:text-gray-900 disabled:cursor-not-allowed disabled:text-gray-300"
                  >
                    {opt.label}
                  </button>
                </MenuItem>
              );
            })}
          </MenuItems>
        </Menu>
      )}
    </div>
  );
}
