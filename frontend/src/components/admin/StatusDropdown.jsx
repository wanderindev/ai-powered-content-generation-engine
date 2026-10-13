import { Menu, MenuButton, MenuItems, MenuItem } from '@headlessui/react';
import { ChevronDownIcon } from '@heroicons/react/24/outline';

const STATUS_COLORS = {
  PENDING: 'bg-yellow-400',
  APPROVED: 'bg-green-400',
  REJECTED: 'bg-red-400',
};

const TRANSITIONS = {
  PENDING: [
    { label: 'Approve', value: 'APPROVED' },
    { label: 'Reject', value: 'REJECTED' },
  ],
  APPROVED: [
    { label: 'Make Pending', value: 'PENDING' },
    { label: 'Reject', value: 'REJECTED' },
  ],
  REJECTED: [
    { label: 'Make Pending', value: 'PENDING' },
    { label: 'Approve', value: 'APPROVED' },
  ],
};

export default function StatusDropdown({ currentStatus, onStatusChange }) {
  const options = TRANSITIONS[currentStatus] || [];

  if (options.length === 0) return null;

  return (
    <Menu as="div" className="relative inline-block">
      <MenuButton className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-gray-500 hover:bg-gray-100 hover:text-gray-700">
        <span className={`inline-block h-2.5 w-2.5 rounded-full ${STATUS_COLORS[currentStatus] || 'bg-gray-400'}`} />
        <ChevronDownIcon className="h-3.5 w-3.5" />
      </MenuButton>
      <MenuItems
        portal
        anchor="bottom end"
        className="z-50 w-36 rounded-md bg-white py-1 shadow-lg ring-1 ring-black/5 outline-none [--anchor-gap:4px]"
      >
        {options.map((opt) => (
          <MenuItem key={opt.value}>
            <button
              onClick={() => onStatusChange(opt.value)}
              className="block w-full px-3 py-1.5 text-left text-sm text-gray-700 data-focus:bg-gray-50 data-focus:text-gray-900"
            >
              {opt.label}
            </button>
          </MenuItem>
        ))}
      </MenuItems>
    </Menu>
  );
}
