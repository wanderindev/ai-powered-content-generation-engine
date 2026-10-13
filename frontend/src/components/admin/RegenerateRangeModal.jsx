import { useState, useMemo, Fragment } from 'react';
import { Dialog, Transition } from '@headlessui/react';
import { parseSlideRange } from '../../utils/slideRange';

// Scope-picker for a slide-deck regeneration step (narration / audio / render /
// content). Returns the chosen 1-based positions to `onConfirm`, or `null` for
// the whole deck. Three modes:
//   - all       → onConfirm(null)
//   - changed   → onConfirm(changed.positions)   (hidden when `changed` is null,
//                 e.g. content re-roll, which has no "stale" signal)
//   - specific  → onConfirm(parsed positions from the "3-6, 12" text box)
export default function RegenerateRangeModal({
  open,
  onClose,
  onConfirm,
  title,
  actionLabel = 'Regenerate',
  description = null,
  slideCount,
  changed = null,
  allowAll = true,
  busy = false,
}) {
  const hasChanged = !!changed && changed.positions.length > 0;
  // The modal is mounted fresh each time it opens (parent renders it
  // conditionally), so the initial state below is the per-open default.
  const defaultMode = hasChanged ? 'changed' : allowAll ? 'all' : 'specific';
  const [mode, setMode] = useState(defaultMode);
  const [rangeText, setRangeText] = useState('');

  const parsed = useMemo(
    () => parseSlideRange(rangeText, slideCount),
    [rangeText, slideCount],
  );

  const specificReady = mode !== 'specific' || (!parsed.error && parsed.positions.length > 0);

  const confirm = () => {
    if (mode === 'all') return onConfirm(null);
    if (mode === 'changed') return onConfirm(changed.positions);
    if (!specificReady) return undefined;
    return onConfirm(parsed.positions);
  };

  const radio = (value, label, hint) => (
    <label className="flex cursor-pointer items-start gap-2 rounded-md px-1 py-1.5">
      <input
        type="radio"
        name="regen-scope"
        checked={mode === value}
        onChange={() => setMode(value)}
        className="mt-0.5 h-4 w-4 text-indigo-600 focus:ring-indigo-500"
      />
      <span className="text-sm">
        <span className="font-medium text-gray-900">{label}</span>
        {hint && <span className="block text-xs text-gray-500">{hint}</span>}
      </span>
    </label>
  );

  return (
    <Transition show={open} as={Fragment}>
      <Dialog onClose={busy ? () => {} : onClose} className="relative z-50">
        <Transition.Child
          as={Fragment}
          enter="ease-out duration-150" enterFrom="opacity-0" enterTo="opacity-100"
          leave="ease-in duration-100" leaveFrom="opacity-100" leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-black/40" />
        </Transition.Child>

        <div className="fixed inset-0 flex items-center justify-center p-4">
          <Transition.Child
            as={Fragment}
            enter="ease-out duration-150" enterFrom="opacity-0 scale-95" enterTo="opacity-100 scale-100"
            leave="ease-in duration-100" leaveFrom="opacity-100 scale-100" leaveTo="opacity-0 scale-95"
          >
            <Dialog.Panel className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl">
              <Dialog.Title className="text-base font-semibold text-gray-900">
                {title}
              </Dialog.Title>
              {description && <p className="mt-1 text-sm text-gray-600">{description}</p>}

              <div className="mt-4 space-y-1">
                {allowAll &&
                  radio('all', 'All slides', `Regenerate the whole deck (${slideCount} slides).`)}
                {hasChanged &&
                  radio(
                    'changed',
                    changed.label || 'Only changed slides',
                    `${changed.positions.length} slide(s): ${changed.positions.join(', ')}`,
                  )}
                {radio('specific', 'Specific slides', 'Pick a range, e.g. "3-6, 12".')}
              </div>

              {mode === 'specific' && (
                <div className="mt-2 pl-7">
                  <input
                    autoFocus
                    value={rangeText}
                    onChange={(e) => setRangeText(e.target.value)}
                    placeholder="3-6, 12, 15"
                    className="block w-full rounded-md border-0 py-1.5 text-sm text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 focus:ring-2 focus:ring-inset focus:ring-indigo-600"
                  />
                  {parsed.error ? (
                    <p className="mt-1 text-xs text-red-600">{parsed.error}</p>
                  ) : parsed.positions.length > 0 ? (
                    <p className="mt-1 text-xs text-gray-500">
                      → will regenerate {parsed.positions.length} slide(s):{' '}
                      {parsed.positions.join(', ')}
                    </p>
                  ) : (
                    <p className="mt-1 text-xs text-gray-400">
                      Enter slide numbers to regenerate.
                    </p>
                  )}
                </div>
              )}

              <div className="mt-5 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={onClose}
                  disabled={busy}
                  className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50 disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={confirm}
                  disabled={busy || !specificReady}
                  className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50"
                >
                  {busy ? 'Working…' : actionLabel}
                </button>
              </div>
            </Dialog.Panel>
          </Transition.Child>
        </div>
      </Dialog>
    </Transition>
  );
}
