import { useState, useEffect } from 'react';
import { Combobox, ComboboxInput, ComboboxOption, ComboboxOptions } from '@headlessui/react';
import { XMarkIcon } from '@heroicons/react/20/solid';
import { adminService } from '../../services/admin';
import { ToastContainer } from './Toast';
import { useToast } from '../../hooks/useToast';

function useDebounce(value, delay) {
  const [debouncedValue, setDebouncedValue] = useState(value);
  useEffect(() => {
    const handler = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(handler);
  }, [value, delay]);
  return debouncedValue;
}

export default function TagInput({ tags, onChange, token }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const { toasts, show: showToast, dismiss: dismissToast } = useToast();

  const debouncedQuery = useDebounce(query, 300);

  useEffect(() => {
    let isMounted = true;

    const search = async () => {
      setLoading(true);
      try {
        const data = await adminService.searchTags(token, debouncedQuery);
        if (isMounted) setResults(data || []);
      } catch {
        if (isMounted) setResults([]);
      } finally {
        if (isMounted) setLoading(false);
      }
    };

    search();
    return () => { isMounted = false; };
  }, [debouncedQuery, token]);

  const selectedIds = new Set(tags.map((t) => t.id));
  const filtered = results.filter((r) => !selectedIds.has(r.id));
  const exactMatch = results.some((r) => r.name.toLowerCase() === query.trim().toLowerCase());
  const showCreate = query.trim().length > 0 && !exactMatch && !loading;

  const handleSelect = async (value) => {
    if (!value) return;
    // Sentinel value from the "Create" option
    if (value.__create) {
      if (creating) return;
      setCreating(true);
      try {
        const newTag = await adminService.createTag(token, value.name);
        onChange([...tags, newTag]);
        setQuery('');
      } catch (err) {
        console.error('Failed to create tag:', err);
        showToast({ type: 'error', title: 'Failed to create tag', message: err.message || 'Unexpected error' });
      } finally {
        setCreating(false);
      }
      return;
    }
    onChange([...tags, value]);
    setQuery('');
  };

  const handleRemove = (tagId) => {
    onChange(tags.filter((t) => t.id !== tagId));
  };

  return (
    <div>
      {/* Selected tags as pills */}
      {tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {tags.map((tag) => (
            <span
              key={tag.id}
              className="inline-flex items-center gap-1 rounded-full bg-gray-50 px-2.5 py-1 text-xs font-medium text-gray-600 ring-1 ring-inset ring-gray-200"
            >
              {tag.name}
              <button
                type="button"
                onClick={() => handleRemove(tag.id)}
                className="text-gray-400 hover:text-gray-600"
              >
                <XMarkIcon className="h-3.5 w-3.5" />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Autocomplete input */}
      <Combobox onChange={handleSelect}>
        <div className="relative">
          <ComboboxInput
            className="w-full rounded-md border-0 py-1.5 pl-3 pr-3 text-sm text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 placeholder:text-gray-400 focus:ring-2 focus:ring-inset focus:ring-indigo-600"
            placeholder="Search or create tags..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />

          <ComboboxOptions className="absolute z-10 mt-1 max-h-48 w-full overflow-auto rounded-md bg-white py-1 text-sm shadow-lg ring-1 ring-black/5 focus:outline-none empty:hidden">
            {loading && (
              <div className="px-3 py-2 text-gray-500">Searching...</div>
            )}

            {filtered.map((tag) => (
              <ComboboxOption
                key={tag.id}
                value={tag}
                className="cursor-pointer select-none px-3 py-2 data-[focus]:bg-indigo-600 data-[focus]:text-white text-gray-900"
              >
                {tag.name}
              </ComboboxOption>
            ))}

            {showCreate && (
              <ComboboxOption
                value={{ __create: true, name: query.trim() }}
                className="cursor-pointer select-none px-3 py-2 text-indigo-600 data-[focus]:bg-indigo-50 border-t border-gray-100"
              >
                {creating ? 'Creating...' : `Create "${query.trim()}"`}
              </ComboboxOption>
            )}

            {!loading && filtered.length === 0 && !showCreate && query.length > 0 && (
              <div className="px-3 py-2 text-gray-500">No tags found</div>
            )}
          </ComboboxOptions>
        </div>
      </Combobox>

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
