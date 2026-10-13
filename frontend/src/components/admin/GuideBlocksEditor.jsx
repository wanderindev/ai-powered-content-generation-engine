import { useState } from 'react';
import {
  ArrowDownIcon,
  ArrowUpIcon,
  PhotoIcon,
  TrashIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline';
import MediaPickerModal from './MediaPickerModal';

// Structured study-guide block editor (study-guide redesign #7/#8).
//
// Renders one card per block with type-specific fields, so a reviewer can
// correct any block type — including the ones with no markdown representation
// (timelines, callouts, MC questions) — without hand-editing JSON. The parent
// owns the blocks array; every edit flows up through `onChange` immutably.
// Backend saves re-validate with the strict normalizer, so structural damage
// here is rejected rather than silently persisted.

const BLOCK_LABELS = {
  cover: 'Cover',
  section: 'Section',
  paragraph: 'Paragraph',
  bullets: 'Bullets',
  ordered_list: 'Numbered list',
  quote: 'Quote',
  timeline: 'Timeline',
  map: 'Map',
  image: 'Image',
  callout: 'Callout',
  mc_question: 'Multiple choice',
};

// Fresh scaffolds for the "Add block" control (cover excluded — one per guide).
const NEW_BLOCKS = {
  section: { type: 'section', title: '' },
  paragraph: { type: 'paragraph', text: '' },
  bullets: { type: 'bullets', items: [''] },
  ordered_list: { type: 'ordered_list', items: [''] },
  quote: { type: 'quote', text: '', attribution: '' },
  timeline: { type: 'timeline', events: [{ label: '', text: '' }] },
  callout: { type: 'callout', label: '', text: '', terms: [] },
  mc_question: { type: 'mc_question', question: '', choices: ['', ''] },
  image: { type: 'image', image_query: '', caption: '' },
};

const inputCls =
  'block w-full rounded-md border-0 py-1.5 text-sm text-gray-900 shadow-sm ring-1 ring-inset ' +
  'ring-gray-300 placeholder:text-gray-400 focus:ring-2 focus:ring-inset focus:ring-indigo-600';
const textareaCls = `${inputCls} resize-y`;
const labelCls = 'block text-xs font-medium text-gray-500 mb-1';

function Field({ label, children }) {
  return (
    <div>
      <span className={labelCls}>{label}</span>
      {children}
    </div>
  );
}

// Line-per-item editing for plain string lists (bullets, choices, locations).
function LinesField({ label, items, onChange, placeholder }) {
  return (
    <Field label={`${label} (one per line)`}>
      <textarea
        className={textareaCls}
        rows={Math.max(2, (items || []).length)}
        value={(items || []).join('\n')}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value.split('\n'))}
      />
    </Field>
  );
}

function ImageSlot({ block, onPatch, onPick }) {
  return (
    <div className="rounded-md border border-dashed border-gray-300 bg-gray-50 p-3">
      <p className="text-xs text-gray-500 mb-2">
        <span className="font-medium text-gray-600">Wanted photo:</span> {block.image_query || '—'}
      </p>
      {block.media_path ? (
        <div className="flex items-center gap-3">
          <img
            src={block.media_path}
            alt={block.caption || ''}
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

function BlockFields({ block, onPatch, onPick }) {
  switch (block.type) {
    case 'cover':
      return (
        <div className="space-y-3">
          <Field label="Title">
            <input
              className={inputCls}
              value={block.title || ''}
              onChange={(e) => onPatch({ title: e.target.value })}
            />
          </Field>
          <Field label="Subtitle">
            <input
              className={inputCls}
              value={block.subtitle || ''}
              onChange={(e) => onPatch({ subtitle: e.target.value })}
            />
          </Field>
          <ImageSlot
            block={{ ...block, image_query: block.image_query || 'cover photo' }}
            onPatch={onPatch}
            onPick={onPick}
          />
        </div>
      );
    case 'section':
      return (
        <Field label="Section title">
          <input
            className={inputCls}
            value={block.title || ''}
            onChange={(e) => onPatch({ title: e.target.value })}
          />
        </Field>
      );
    case 'paragraph':
      return (
        <Field label="Text">
          <textarea
            className={textareaCls}
            rows={4}
            value={block.text || ''}
            onChange={(e) => onPatch({ text: e.target.value })}
          />
        </Field>
      );
    case 'bullets':
    case 'ordered_list':
      return (
        <LinesField
          label="Items"
          items={block.items}
          onChange={(items) => onPatch({ items })}
        />
      );
    case 'quote':
      return (
        <div className="space-y-3">
          <Field label="Quote">
            <textarea
              className={textareaCls}
              rows={2}
              value={block.text || ''}
              onChange={(e) => onPatch({ text: e.target.value })}
            />
          </Field>
          <Field label="Attribution">
            <input
              className={inputCls}
              value={block.attribution || ''}
              onChange={(e) => onPatch({ attribution: e.target.value })}
            />
          </Field>
        </div>
      );
    case 'timeline':
      return (
        <div className="space-y-2">
          <span className={labelCls}>Events</span>
          {(block.events || []).map((ev, i) => (
            <div key={i} className="flex items-start gap-2">
              <input
                className={`${inputCls} w-28 shrink-0`}
                placeholder="1519"
                value={ev.label || ''}
                onChange={(e) => {
                  const events = block.events.map((x, j) =>
                    j === i ? { ...x, label: e.target.value } : x,
                  );
                  onPatch({ events });
                }}
              />
              <input
                className={inputCls}
                placeholder="What happened"
                value={ev.text || ''}
                onChange={(e) => {
                  const events = block.events.map((x, j) =>
                    j === i ? { ...x, text: e.target.value } : x,
                  );
                  onPatch({ events });
                }}
              />
              <button
                type="button"
                onClick={() => onPatch({ events: block.events.filter((_, j) => j !== i) })}
                className="mt-1 rounded p-1 text-gray-400 hover:text-red-600"
                title="Remove event"
              >
                <TrashIcon className="h-4 w-4" />
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={() => onPatch({ events: [...(block.events || []), { label: '', text: '' }] })}
            className="text-xs font-medium text-indigo-600 hover:text-indigo-500"
          >
            + Add event
          </button>
        </div>
      );
    case 'callout':
      return (
        <div className="space-y-3">
          <Field label="Label">
            <input
              className={inputCls}
              placeholder="Key Terms / For Reflection / Did you know?"
              value={block.label || ''}
              onChange={(e) => onPatch({ label: e.target.value })}
            />
          </Field>
          <Field label="Text (asides & reflection prompts)">
            <textarea
              className={textareaCls}
              rows={2}
              value={block.text || ''}
              onChange={(e) => onPatch({ text: e.target.value })}
            />
          </Field>
          <div className="space-y-2">
            <span className={labelCls}>Terms (vocabulary boxes)</span>
            {(block.terms || []).map((t, i) => (
              <div key={i} className="flex items-start gap-2">
                <input
                  className={`${inputCls} w-40 shrink-0`}
                  placeholder="Term"
                  value={t.term || ''}
                  onChange={(e) => {
                    const terms = block.terms.map((x, j) =>
                      j === i ? { ...x, term: e.target.value } : x,
                    );
                    onPatch({ terms });
                  }}
                />
                <input
                  className={inputCls}
                  placeholder="Definition"
                  value={t.definition || ''}
                  onChange={(e) => {
                    const terms = block.terms.map((x, j) =>
                      j === i ? { ...x, definition: e.target.value } : x,
                    );
                    onPatch({ terms });
                  }}
                />
                <button
                  type="button"
                  onClick={() => onPatch({ terms: block.terms.filter((_, j) => j !== i) })}
                  className="mt-1 rounded p-1 text-gray-400 hover:text-red-600"
                  title="Remove term"
                >
                  <TrashIcon className="h-4 w-4" />
                </button>
              </div>
            ))}
            <button
              type="button"
              onClick={() =>
                onPatch({ terms: [...(block.terms || []), { term: '', definition: '' }] })
              }
              className="text-xs font-medium text-indigo-600 hover:text-indigo-500"
            >
              + Add term
            </button>
          </div>
        </div>
      );
    case 'mc_question':
      return (
        <div className="space-y-3">
          <Field label="Question">
            <input
              className={inputCls}
              value={block.question || ''}
              onChange={(e) => onPatch({ question: e.target.value })}
            />
          </Field>
          <LinesField
            label="Choices"
            items={block.choices}
            onChange={(choices) => onPatch({ choices })}
          />
        </div>
      );
    case 'map':
      return (
        <div className="space-y-3">
          <ImageSlot block={block} onPatch={onPatch} onPick={onPick} />
          <Field label="Caption">
            <input
              className={inputCls}
              value={block.caption || ''}
              onChange={(e) => onPatch({ caption: e.target.value })}
            />
          </Field>
          <LinesField
            label="Locations"
            items={block.locations}
            onChange={(locations) => onPatch({ locations })}
          />
        </div>
      );
    case 'image':
      return (
        <div className="space-y-3">
          <ImageSlot block={block} onPatch={onPatch} onPick={onPick} />
          <Field label="Caption">
            <input
              className={inputCls}
              value={block.caption || ''}
              onChange={(e) => onPatch({ caption: e.target.value })}
            />
          </Field>
        </div>
      );
    default:
      return <p className="text-xs text-gray-400">Unknown block type.</p>;
  }
}

export default function GuideBlocksEditor({ blocks, onChange, token }) {
  const [pickerFor, setPickerFor] = useState(null); // block index awaiting a photo
  const [addType, setAddType] = useState('paragraph');

  const patchBlock = (index, patch) => {
    onChange(
      blocks.map((b, i) => {
        if (i !== index) return b;
        const next = { ...b, ...patch };
        // `undefined` values mean "remove the key" (Clear image).
        Object.keys(next).forEach((k) => next[k] === undefined && delete next[k]);
        return next;
      }),
    );
  };

  const move = (index, delta) => {
    const target = index + delta;
    if (target < 0 || target >= blocks.length) return;
    const next = [...blocks];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  };

  const remove = (index) => onChange(blocks.filter((_, i) => i !== index));

  const addBlock = () => onChange([...blocks, structuredClone(NEW_BLOCKS[addType])]);

  return (
    <div className="space-y-3">
      {blocks.map((block, i) => (
        <div key={i} className="rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
          <div className="mb-2 flex items-center justify-between">
            <span
              className={`rounded px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ${
                block.type === 'section'
                  ? 'bg-indigo-600 text-white'
                  : 'bg-gray-100 text-gray-600'
              }`}
            >
              {BLOCK_LABELS[block.type] || block.type}
            </span>
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
                disabled={i === blocks.length - 1}
                className="rounded p-1 text-gray-400 hover:text-gray-700 disabled:opacity-30"
                title="Move down"
              >
                <ArrowDownIcon className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={() => remove(i)}
                className="rounded p-1 text-gray-400 hover:text-red-600"
                title="Delete block"
              >
                <TrashIcon className="h-4 w-4" />
              </button>
            </div>
          </div>
          <BlockFields
            block={block}
            onPatch={(patch) => patchBlock(i, patch)}
            onPick={() => setPickerFor(i)}
          />
        </div>
      ))}

      <div className="flex items-center gap-2 pt-1">
        <select
          value={addType}
          onChange={(e) => setAddType(e.target.value)}
          className="rounded-md border-0 py-1.5 pl-2 pr-8 text-sm text-gray-900 ring-1 ring-inset ring-gray-300 focus:ring-2 focus:ring-indigo-600"
        >
          {Object.keys(NEW_BLOCKS).map((t) => (
            <option key={t} value={t}>
              {BLOCK_LABELS[t]}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={addBlock}
          className="rounded-md bg-white px-3 py-1.5 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
        >
          + Add block
        </button>
      </div>

      <MediaPickerModal
        open={pickerFor !== null}
        onClose={() => setPickerFor(null)}
        token={token}
        excludeFeatureImages={false}
        initialSearch={pickerFor !== null ? blocks[pickerFor]?.image_query || '' : ''}
        onSelect={({ id, filePath, src }) => {
          if (pickerFor !== null) {
            // media_path must be the value the PDF renderer can load directly;
            // file_path is already an absolute CDN URL in this project, but fall
            // back to the resolved src if it ever isn't.
            patchBlock(pickerFor, {
              media_id: id,
              media_path: /^https?:\/\//.test(filePath || '') ? filePath : src,
            });
          }
        }}
      />
    </div>
  );
}
