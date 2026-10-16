import { Fragment, useState } from 'react';
import { Dialog, DialogPanel, DialogTitle, Transition, TransitionChild } from '@headlessui/react';
import { adminService } from '../../services/admin';
import MediaMetadataFields from './MediaMetadataFields';
import { IMPORT_FIELDS, importMetadata, titleFromUrl } from './mediaMetadataUtils';

// "Add from link" on the library page (#589): an image URL plus the same
// metadata the picker's import form captures. Mounted only while open, so the
// form state starts fresh each time (no reset effect).
export default function MediaImportModal({ onClose, token, onImported }) {
  const [url, setUrl] = useState('');
  const [values, setValues] = useState({ title: '' });
  const [titleTouched, setTitleTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const handleUrlChange = (next) => {
    setUrl(next);
    setError(null);
    // Prefill the title from the filename until the editor types their own.
    if (!titleTouched) setValues((prev) => ({ ...prev, title: titleFromUrl(next) }));
  };

  const handleFieldChange = (field, value) => {
    if (field === 'title') setTitleTouched(true);
    setValues((prev) => ({ ...prev, [field]: value }));
  };

  const canSubmit = url.trim() && values.title?.trim() && !saving;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!canSubmit) return;
    setSaving(true);
    setError(null);
    try {
      const item = await adminService.importMediaFromUrl(token, url.trim(), importMetadata(values));
      onImported(item);
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
              <DialogPanel className="w-full max-w-lg rounded-xl bg-white shadow-2xl">
                <form onSubmit={handleSubmit}>
                  <div className="px-5 py-4 border-b border-gray-200">
                    <DialogTitle className="text-lg font-semibold text-gray-900">Add image from link</DialogTitle>
                    <p className="text-sm text-gray-500 mt-0.5">
                      The image is downloaded and re-hosted; bilingual search terms are generated automatically.
                    </p>
                  </div>
                  <div className="px-5 py-4 space-y-3">
                    <div>
                      <label htmlFor="media-import-url" className="block text-xs font-medium text-gray-700">
                        Image URL<span className="text-red-500"> *</span>
                      </label>
                      <input
                        id="media-import-url"
                        type="url"
                        value={url}
                        onChange={(e) => handleUrlChange(e.target.value)}
                        placeholder="https://…/photo.jpg"
                        className="mt-1 w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                      />
                    </div>
                    <MediaMetadataFields values={values} onChange={handleFieldChange} fields={IMPORT_FIELDS} />
                    {error && <p className="text-sm text-red-600">{error}</p>}
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
                      disabled={!canSubmit}
                      className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-40"
                    >
                      {saving ? 'Importing...' : 'Import image'}
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
