import { useState } from 'react';
import {
  ArrowDownIcon,
  ArrowUpIcon,
  PhotoIcon,
  TrashIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline';
import MediaPickerModal from './MediaPickerModal';

// Structured slide-deck editor (slides redesign — mirror of GuideBlocksEditor).
//
// One card per slide: template, title, on-screen lines, the human photo pick
// (the LLM only ever emits an image_query placeholder), and — once the
// narration pass has run — Diego's per-slide script. The parent owns the
// slides array; every edit flows up through `onChange` immutably. Backend
// saves re-validate with the strict normalizer and handle artifact staleness
// (an edited slide loses its rendered frame / audio server-side).

const TEMPLATE_LABELS = {
  title: 'Title',
  section: 'Section',
  bullets: 'Bullets',
  'two-col-image-left': 'Two col (img left)',
  'two-col-image-right': 'Two col (img right)',
  'full-image': 'Full image',
  timeline: 'Timeline',
  map: 'Map',
  closing: 'Closing',
};

const BODY_PLACEHOLDER = {
  timeline: '1519 — Fundación de la ciudad',
  map: 'Plaza Mayor',
  'two-col-image-left': 'Lead line, then bullets',
  'two-col-image-right': 'Lead line, then bullets',
};

const inputCls =
  'block w-full rounded-md border-0 py-1.5 text-sm text-gray-900 shadow-sm ring-1 ring-inset ' +
  'ring-gray-300 placeholder:text-gray-400 focus:ring-2 focus:ring-inset focus:ring-indigo-600';
const textareaCls = `${inputCls} resize-y`;
const labelCls = 'block text-xs font-medium text-gray-500 mb-1';

function ImageSlot({ slide, onPatch, onPick }) {
  return (
    <div className="rounded-md border border-dashed border-gray-300 bg-gray-50 p-3">
      <p className="text-xs text-gray-500 mb-2">
        <span className="font-medium text-gray-600">Wanted photo:</span> {slide.image_query || '—'}
      </p>
      {slide.media_path ? (
        <div className="flex items-center gap-3">
          <img
            src={slide.media_path}
            alt={slide.title || ''}
            className="h-16 w-24 rounded object-cover ring-1 ring-gray-200"
          />
          <button
            type="button"
            onClick={onPick}
            className="rounded bg-white px-2.5 py-1.5 text-xs font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
          >
            Change…
          </button>
          <button
            type="button"
            onClick={() => onPatch({ media_path: undefined, media_id: undefined })}
            className="inline-flex items-center gap-1 rounded px-2 py-1.5 text-xs text-gray-500 hover:bg-gray-200"
          >
            <XMarkIcon className="h-3.5 w-3.5" /> Clear
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={onPick}
          className="inline-flex items-center gap-1.5 rounded bg-indigo-50 px-3 py-1.5 text-xs font-semibold text-indigo-700 hover:bg-indigo-100"
        >
          <PhotoIcon className="h-4 w-4" /> Choose image…
        </button>
      )}
    </div>
  );
}

export default function SlideDeckEditor({ slides, onChange, token }) {
  const [pickerFor, setPickerFor] = useState(null); // slide index awaiting a photo
  const [addType, setAddType] = useState('bullets');

  const deckHasNarration = slides.some((s) => s.narration != null);

  const patchSlide = (index, patch) => {
    onChange(
      slides.map((s, i) => {
        if (i !== index) return s;
        const next = { ...s, ...patch };
        // `undefined` values mean "remove the key" (Clear image).
        Object.keys(next).forEach((k) => next[k] === undefined && delete next[k]);
        return next;
      }),
    );
  };

  const move = (index, delta) => {
    const target = index + delta;
    if (target < 0 || target >= slides.length) return;
    const next = [...slides];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  };

  const remove = (index) => onChange(slides.filter((_, i) => i !== index));

  // New slides carry no `id`, so the backend gives them no stale artifacts. On
  // a narrated deck, seed narration as '' so the script textarea shows.
  const addSlide = () =>
    onChange([
      ...slides,
      {
        template: addType,
        title: '',
        body: '',
        image_query: '',
        narration: deckHasNarration ? '' : null,
      },
    ]);

  return (
    <div className="space-y-3">
      {slides.map((slide, i) => (
        <div key={slide.id ?? `new-${i}`} className="rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="rounded bg-gray-800 px-2 py-0.5 text-xs font-semibold text-white">
                {i + 1}
              </span>
              <select
                value={slide.template}
                onChange={(e) => patchSlide(i, { template: e.target.value })}
                className="rounded-md border-0 py-1 pl-2 pr-8 text-xs font-semibold uppercase tracking-wide text-gray-700 ring-1 ring-inset ring-gray-300 focus:ring-2 focus:ring-indigo-600"
              >
                {Object.keys(TEMPLATE_LABELS).map((t) => (
                  <option key={t} value={t}>
                    {TEMPLATE_LABELS[t]}
                  </option>
                ))}
              </select>
              {slide.image_url && (
                <span className="rounded bg-green-50 px-1.5 py-0.5 text-[10px] font-medium text-green-700 ring-1 ring-inset ring-green-200" title="Rendered frame exists — cleared automatically if you edit this slide's content">
                  frame ✓
                </span>
              )}
              {slide.audio_url && (
                <span className="rounded bg-green-50 px-1.5 py-0.5 text-[10px] font-medium text-green-700 ring-1 ring-inset ring-green-200" title="Narration audio exists — cleared automatically if you edit this slide's script">
                  audio ✓
                </span>
              )}
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => move(i, -1)}
                disabled={i === 0}
                className="rounded p-1 text-gray-400 hover:text-gray-700 disabled:opacity-30"
                title="Move up"
              >
                <ArrowUpIcon className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={() => move(i, 1)}
                disabled={i === slides.length - 1}
                className="rounded p-1 text-gray-400 hover:text-gray-700 disabled:opacity-30"
                title="Move down"
              >
                <ArrowDownIcon className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={() => remove(i)}
                className="rounded p-1 text-gray-400 hover:text-red-600"
                title="Delete slide"
              >
                <TrashIcon className="h-4 w-4" />
              </button>
            </div>
          </div>

          <div className="space-y-3">
            <div>
              <span className={labelCls}>Title</span>
              <input
                className={inputCls}
                value={slide.title || ''}
                onChange={(e) => patchSlide(i, { title: e.target.value })}
              />
            </div>
            <div>
              <span className={labelCls}>On-screen lines (one per line)</span>
              <textarea
                className={textareaCls}
                rows={Math.max(2, (slide.body || '').split('\n').length)}
                value={slide.body || ''}
                placeholder={BODY_PLACEHOLDER[slide.template] || ''}
                onChange={(e) => patchSlide(i, { body: e.target.value })}
              />
            </div>
            <ImageSlot
              slide={slide}
              onPatch={(patch) => patchSlide(i, patch)}
              onPick={() => setPickerFor(i)}
            />
            {slide.narration != null && (
              <div>
                <span className={labelCls}>Narration (what Diego says over this slide)</span>
                <textarea
                  className={textareaCls}
                  rows={3}
                  value={slide.narration || ''}
                  onChange={(e) => patchSlide(i, { narration: e.target.value })}
                />
              </div>
            )}
          </div>
        </div>
      ))}

      <div className="flex items-center gap-2 pt-1">
        <select
          value={addType}
          onChange={(e) => setAddType(e.target.value)}
          className="rounded-md border-0 py-1.5 pl-2 pr-8 text-sm text-gray-900 ring-1 ring-inset ring-gray-300 focus:ring-2 focus:ring-indigo-600"
        >
          {Object.keys(TEMPLATE_LABELS).map((t) => (
            <option key={t} value={t}>
              {TEMPLATE_LABELS[t]}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={addSlide}
          className="rounded-md bg-white px-3 py-1.5 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
        >
          + Add slide
        </button>
      </div>

      <MediaPickerModal
        open={pickerFor !== null}
        onClose={() => setPickerFor(null)}
        token={token}
        excludeFeatureImages={false}
        initialSearch={pickerFor !== null ? slides[pickerFor]?.image_query || '' : ''}
        onSelect={({ id, filePath, src }) => {
          if (pickerFor !== null) {
            // media_path must be the value the slide renderer can load directly;
            // file_path is already an absolute CDN URL in this project, but fall
            // back to the resolved src if it ever isn't.
            patchSlide(pickerFor, {
              media_id: id,
              media_path: /^https?:\/\//.test(filePath || '') ? filePath : src,
            });
          }
        }}
      />
    </div>
  );
}
