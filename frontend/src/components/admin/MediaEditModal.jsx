import { Fragment, useState } from 'react';
import { Dialog, DialogPanel, DialogTitle, Transition, TransitionChild } from '@headlessui/react';
import { adminService } from '../../services/admin';
import MediaMetadataFields from './MediaMetadataFields';
import { EDIT_FIELDS, changedFields, valuesFromItem } from './mediaMetadataUtils';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

function mediaUrl(path) {
  if (!path) return '';
  if (path.startsWith('http')) return path;
  return `${API_BASE}${path}`;
}

// Edit an imported image's metadata (#589). Mounted only while open and keyed
// by the item, so the form initialises from it (no reset effect). Save sends
// only the changed fields — an unchanged search_terms must stay out of the
// body, or the server would not regenerate it.
export default function MediaEditModal({ item, onClose, token, onSaved }) {
  const [values, setValues] = useState(() => valuesFromItem(item));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const changes = changedFields(item, values);
  const dirty = Object.keys(changes).length > 0;
  const canSave = dirty && values.title.trim() && !saving;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!canSave) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await adminService.updateMedia(token, item.id, changes);
      onSaved(updated);
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  };

  return (
    <Transition appear show as={Fragment}>
      <Dialog as="div" className="relative z-50" onClose={saving ? () => {} : onClose}>
        <TransitionChild
          as={Fragment}
          enter="ease-out duration-200" enterFrom="opacity-0" enterTo="opacity-100"
          leave="ease-in duration-150" leaveFrom="opacity-100" leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-black/40" />
        </TransitionChild>
        <div className="fixed inset-0 overflow-y-auto">
          <div className="flex min-h-full items-center justify-center p-4">
            <TransitionChild
              as={Fragment}
              enter="ease-out duration-200" enterFrom="opacity-0 scale-95" enterTo="opacity-100 scale-100"
              leave="ease-in duration-150" leaveFrom="opacity-100 scale-100" leaveTo="opacity-0 scale-95"
            >
              <DialogPanel className="w-full max-w-2xl rounded-xl bg-white shadow-2xl">
                <form onSubmit={handleSubmit}>
                  <div className="px-5 py-4 border-b border-gray-200 flex items-center gap-3">
                    <img
                      src={mediaUrl(item.thumbnail_path || item.file_path)}
                      alt={item.alt_text || item.title || ''}
                      className="h-14 w-14 rounded object-cover shrink-0"
                    />
                    <div className="min-w-0">
                      <DialogTitle className="text-lg font-semibold text-gray-900 truncate">Edit image details</DialogTitle>
                      <a
                        href={mediaUrl(item.file_path)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs text-indigo-600 hover:underline"
                      >
                        Open full image
                      </a>
                    </div>
                  </div>
                  <div className="px-5 py-4 max-h-[60vh] overflow-y-auto">
                    <MediaMetadataFields
                      values={values}
                      onChange={(field, value) => setValues((prev) => ({ ...prev, [field]: value }))}
                      fields={EDIT_FIELDS}
                    />
                    {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
                  </div>
                  <div className="px-5 py-3 border-t border-gray-200 flex justify-end gap-2">
                    <button
                      type="button"
                      onClick={onClose}
                      disabled={saving}
                      className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50 disabled:opacity-40"
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      disabled={!canSave}
                      className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-40"
                    >
                      {saving ? 'Saving...' : 'Save'}
                    </button>
                  </div>
                </form>
              </DialogPanel>
            </TransitionChild>
          </div>
        </div>
      </Dialog>
    </Transition>
  );
}
