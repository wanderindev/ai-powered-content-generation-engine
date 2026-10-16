// Controlled metadata inputs shared by the picker's import form, the library's
// import modal and the edit modal (#589). Plain text only — values are shown
// through React's escaping, never as HTML.

const FIELD_DEFS = {
  title: { label: 'Title', maxLength: 255, required: true },
  caption: { label: 'Description', maxLength: 2000, multiline: true },
  attribution: { label: 'Credit', maxLength: 1000, placeholder: 'Photographer / archive' },
  source_page_url: { label: 'Source page URL', maxLength: 512, type: 'url', placeholder: 'https://…' },
  source_url: { label: 'Source page URL', maxLength: 512, type: 'url', placeholder: 'https://…' },
  license: { label: 'License', maxLength: 100, placeholder: 'e.g. CC BY 4.0, public domain' },
  license_url: { label: 'License URL', maxLength: 512, type: 'url', placeholder: 'https://…' },
  keywords: {
    label: 'Keywords',
    maxLength: 1000,
    placeholder: 'Comma-separated; bilingual search terms are added automatically',
  },
  alt_text: { label: 'Alt text', maxLength: 255 },
  search_terms: {
    label: 'Search terms',
    maxLength: 4000,
    multiline: true,
    hint: 'Leave unchanged to regenerate them when the title, description or credit changes.',
  },
};

const INPUT_CLASS =
  'mt-1 w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500';

export default function MediaMetadataFields({ values, onChange, fields }) {
  return (
    <div className="space-y-3">
      {fields.map((field) => {
        const def = FIELD_DEFS[field] || { label: field };
        const id = `media-meta-${field}`;
        const common = {
          id,
          value: values[field] ?? '',
          onChange: (e) => onChange(field, e.target.value),
          maxLength: def.maxLength,
          placeholder: def.placeholder,
          className: INPUT_CLASS,
        };
        return (
          <div key={field}>
            <label htmlFor={id} className="block text-xs font-medium text-gray-700">
              {def.label}
              {def.required && <span className="text-red-500"> *</span>}
            </label>
            {def.multiline ? (
              <textarea rows={3} {...common} />
            ) : (
              <input type={def.type || 'text'} {...common} />
            )}
            {def.hint && <p className="mt-1 text-[11px] text-gray-400">{def.hint}</p>}
          </div>
        );
      })}
    </div>
  );
}
