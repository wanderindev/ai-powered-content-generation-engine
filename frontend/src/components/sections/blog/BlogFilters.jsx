import { useState } from 'react'
import {
    Dialog,
    DialogBackdrop,
    DialogPanel,
    Disclosure,
    DisclosureButton,
    DisclosurePanel,
    Menu,
    MenuButton,
    MenuItem,
    MenuItems,
    Popover,
    PopoverButton,
    PopoverGroup,
    PopoverPanel,
} from '@headlessui/react'
import { XMarkIcon, MagnifyingGlassIcon } from '@heroicons/react/24/outline'
import { ChevronDownIcon } from '@heroicons/react/20/solid'

const sortOptions = [
    { name: 'Most Popular', href: '#' },
    { name: 'Newest', href: '#' },
    { name: 'Oldest', href: '#' },
]
const filters = [
    {
        id: 'category',
        name: 'Category',
        options: [
            { value: 'education', label: 'Education' },
            { value: 'history', label: 'History' },
            { value: 'travel', label: 'Travel' },
        ],
    },
]

export default function BlogFilters() {
    const [open, setOpen] = useState(false)

    return (
        <div className="bg-gray-50">
            {/* Mobile filter dialog */}
            <Dialog open={open} onClose={setOpen} className="relative z-40 sm:hidden">
                <DialogBackdrop
                    transition
                    className="fixed inset-0 bg-black/25 transition-opacity duration-300 ease-linear data-closed:opacity-0"
                />

                <div className="fixed inset-0 z-40 flex">
                    <DialogPanel
                        transition
                        className="relative ml-auto flex size-full max-w-xs transform flex-col overflow-y-auto bg-white pt-4 pb-6 shadow-xl transition duration-300 ease-in-out data-closed:translate-x-full"
                    >
                        <div className="flex items-center justify-between px-4">
                            <h2 className="text-lg font-medium text-gray-900">Filters</h2>
                            <button
                                type="button"
                                onClick={() => setOpen(false)}
                                className="relative -mr-2 flex size-10 items-center justify-center rounded-md bg-white p-2 text-gray-400 hover:bg-gray-50 focus:ring-2 focus:ring-indigo-500 focus:outline-hidden"
                            >
                                <span className="absolute -inset-0.5" />
                                <span className="sr-only">Close menu</span>
                                <XMarkIcon aria-hidden="true" className="size-6" />
                            </button>
                        </div>

                        {/* Filters */}
                        <form className="mt-4">
                            {filters.map((section) => (
                                <Disclosure key={section.name} as="div" className="border-t border-gray-200 px-4 py-6">
                                    <h3 className="-mx-2 -my-3 flow-root">
                                        <DisclosureButton className="group flex w-full items-center justify-between bg-white px-2 py-3 text-sm text-gray-400">
                                            <span className="font-medium text-gray-900">{section.name}</span>
                                            <span className="ml-6 flex items-center">
                                                <ChevronDownIcon
                                                    aria-hidden="true"
                                                    className="size-5 rotate-0 transform group-data-open:-rotate-180"
                                                />
                                            </span>
                                        </DisclosureButton>
                                    </h3>
                                    <DisclosurePanel className="pt-6">
                                        <div className="space-y-6">
                                            {section.options.map((option, optionIdx) => (
                                                <div key={option.value} className="flex gap-3">
                                                    <div className="flex h-5 shrink-0 items-center">
                                                        <div className="group grid size-4 grid-cols-1">
                                                            <input
                                                                defaultValue={option.value}
                                                                id={`filter-mobile-${section.id}-${optionIdx}`}
                                                                name={`${section.id}[]`}
                                                                type="checkbox"
                                                                className="col-start-1 row-start-1 appearance-none rounded-sm border border-gray-300 bg-white checked:border-indigo-600 checked:bg-indigo-600 indeterminate:border-indigo-600 indeterminate:bg-indigo-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:border-gray-300 disabled:bg-gray-100 disabled:checked:bg-gray-100 forced-colors:appearance-auto"
                                                            />
                                                        </div>
                                                    </div>
                                                    <label htmlFor={`filter-mobile-${section.id}-${optionIdx}`} className="text-sm text-gray-500">
                                                        {option.label}
                                                    </label>
                                                </div>
                                            ))}
                                        </div>
                                    </DisclosurePanel>
                                </Disclosure>
                            ))}
                        </form>
                    </DialogPanel>
                </div>
            </Dialog>

            <div className="mx-auto max-w-3xl px-4 text-center sm:px-6 lg:max-w-7xl lg:px-8">

                {/* Search Box - Integrated here for layout simplicity */}
                <div className="py-10 max-w-md mx-auto">
                    <div className="relative mt-2 rounded-md shadow-sm">
                        <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3">
                            <MagnifyingGlassIcon aria-hidden="true" className="size-5 text-gray-400" />
                        </div>
                        <input
                            id="search"
                            name="search"
                            type="text"
                            placeholder="Search articles..."
                            className="block w-full rounded-md border-0 py-1.5 pl-10 text-gray-900 ring-1 ring-inset ring-gray-300 placeholder:text-gray-400 focus:ring-2 focus:ring-inset focus:ring-indigo-600 sm:text-sm/6"
                        />
                    </div>
                </div>


                <section aria-labelledby="filter-heading" className="border-t border-gray-200 py-6">
                    <h2 id="filter-heading" className="sr-only">
                        Product filters
                    </h2>

                    <div className="flex items-center justify-between">
                        <Menu as="div" className="relative inline-block text-left">
                            <MenuButton className="group inline-flex justify-center text-sm font-medium text-gray-700 hover:text-gray-900">
                                Sort
                                <ChevronDownIcon
                                    aria-hidden="true"
                                    className="-mr-1 ml-1 size-5 shrink-0 text-gray-400 group-hover:text-gray-500"
                                />
                            </MenuButton>

                            <MenuItems
                                transition
                                className="absolute left-0 z-10 mt-2 w-40 origin-top-left rounded-md bg-white shadow-2xl ring-1 ring-black/5 transition focus:outline-hidden data-closed:scale-95 data-closed:transform data-closed:opacity-0 data-enter:duration-100 data-enter:ease-out data-leave:duration-75 data-leave:ease-in"
                            >
                                <div className="py-1">
                                    {sortOptions.map((option) => (
                                        <MenuItem key={option.name}>
                                            <a
                                                href={option.href}
                                                className="block px-4 py-2 text-sm font-medium text-gray-900 data-focus:bg-gray-100 data-focus:outline-hidden"
                                            >
                                                {option.name}
                                            </a>
                                        </MenuItem>
                                    ))}
                                </div>
                            </MenuItems>
                        </Menu>

                        <button
                            type="button"
                            onClick={() => setOpen(true)}
                            className="inline-block text-sm font-medium text-gray-700 hover:text-gray-900 sm:hidden"
                        >
                            Filters
                        </button>

                        <PopoverGroup className="hidden sm:flex sm:items-baseline sm:space-x-8">
                            {filters.map((section, sectionIdx) => (
                                <Popover key={section.name} className="relative inline-block text-left">
                                    <div>
                                        <PopoverButton className="group inline-flex items-center justify-center text-sm font-medium text-gray-700 hover:text-gray-900">
                                            <span>{section.name}</span>
                                            {sectionIdx === 0 ? (
                                                <span className="ml-1.5 rounded-sm bg-gray-200 px-1.5 py-0.5 text-xs font-semibold text-gray-700 tabular-nums">
                                                    1
                                                </span>
                                            ) : null}
                                            <ChevronDownIcon
                                                aria-hidden="true"
                                                className="-mr-1 ml-1 size-5 shrink-0 text-gray-400 group-hover:text-gray-500"
                                            />
                                        </PopoverButton>
                                    </div>

                                    <PopoverPanel
                                        transition
                                        className="absolute right-0 z-10 mt-2 origin-top-right rounded-md bg-white p-4 shadow-2xl ring-1 ring-black/5 transition focus:outline-hidden data-closed:scale-95 data-closed:transform data-closed:opacity-0 data-enter:duration-100 data-enter:ease-out data-leave:duration-75 data-leave:ease-in"
                                    >
                                        <form className="space-y-4">
                                            {section.options.map((option, optionIdx) => (
                                                <div key={option.value} className="flex gap-3">
                                                    <div className="flex h-5 shrink-0 items-center">
                                                        <div className="group grid size-4 grid-cols-1">
                                                            <input
                                                                defaultValue={option.value}
                                                                id={`filter-${section.id}-${optionIdx}`}
                                                                name={`${section.id}[]`}
                                                                type="checkbox"
                                                                className="col-start-1 row-start-1 appearance-none rounded-sm border border-gray-300 bg-white checked:border-indigo-600 checked:bg-indigo-600 indeterminate:border-indigo-600 indeterminate:bg-indigo-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:border-gray-300 disabled:bg-gray-100 disabled:checked:bg-gray-100 forced-colors:appearance-auto"
                                                            />
                                                        </div>
                                                    </div>
                                                    <label
                                                        htmlFor={`filter-${section.id}-${optionIdx}`}
                                                        className="pr-6 text-sm font-medium whitespace-nowrap text-gray-900"
                                                    >
                                                        {option.label}
                                                    </label>
                                                </div>
                                            ))}
                                        </form>
                                    </PopoverPanel>
                                </Popover>
                            ))}
                        </PopoverGroup>
                    </div>
                </section>
            </div>
        </div>
    )
}
