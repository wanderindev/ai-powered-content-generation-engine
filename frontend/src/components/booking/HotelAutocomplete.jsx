import React, { useState, useEffect } from 'react';
import { Combobox } from '@headlessui/react';
import { CheckIcon, ChevronUpDownIcon } from '@heroicons/react/20/solid';
import { useTranslation } from 'react-i18next';
import { bookingService } from '../../services/booking';

// Helper for debounce
function useDebounce(value, delay) {
    const [debouncedValue, setDebouncedValue] = useState(value);
    useEffect(() => {
        const handler = setTimeout(() => {
            setDebouncedValue(value);
        }, delay);
        return () => {
            clearTimeout(handler);
        };
    }, [value, delay]);
    return debouncedValue;
}

export default function HotelAutocomplete({ selectedHotel, onChange }) {
    const { t } = useTranslation();
    const [query, setQuery] = useState('');
    const [hotels, setHotels] = useState([]);
    const [loading, setLoading] = useState(false);

    const debouncedQuery = useDebounce(query, 400);

    useEffect(() => {
        let isMounted = true;
        if (debouncedQuery.length < 2) {
            setHotels([]);
            return;
        }

        const search = async () => {
            setLoading(true);
            try {
                const results = await bookingService.searchHotels(debouncedQuery);
                if (isMounted) {
                    // Assuming results is array of { id, name, zone_id }
                    setHotels(results || []);
                }
            } catch (err) {
                console.error("Hotel search failed", err);
                if (isMounted) setHotels([]);
            } finally {
                if (isMounted) setLoading(false);
            }
        };

        search();

        return () => { isMounted = false; };
    }, [debouncedQuery]);

    return (
        <div className="w-full">
            <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('booking.modal.hotel')}
            </label>
            <Combobox value={selectedHotel} onChange={onChange} nullable>
                <div className="relative mt-1">
                    <div className="relative w-full cursor-default overflow-hidden rounded-md bg-white text-left ring-1 ring-inset ring-gray-300 focus-within:ring-2 focus-within:ring-inset focus-within:ring-indigo-600 sm:text-sm">
                        <Combobox.Input
                            className="w-full border-none py-2.5 pl-3 pr-10 text-sm leading-6 text-gray-900 focus:ring-0"
                            displayValue={(hotel) => hotel?.name}
                            onChange={(event) => setQuery(event.target.value)}
                            placeholder={t('booking.modal.hotel_placeholder')}
                        />
                        <Combobox.Button className="absolute inset-y-0 right-0 flex items-center pr-2">
                            <ChevronUpDownIcon
                                className="h-5 w-5 text-gray-400"
                                aria-hidden="true"
                            />
                        </Combobox.Button>
                    </div>

                    <Combobox.Options className="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md bg-white py-1 text-base shadow-lg ring-1 ring-black ring-opacity-5 focus:outline-none sm:text-sm empty:hidden">
                        {loading && (
                            <div className="relative cursor-default select-none py-2 px-4 text-gray-700">
                                Loading...
                            </div>
                        )}

                        {!loading && query.length > 0 && hotels.length === 0 && (
                            <div className="relative cursor-default select-none py-2 px-4 text-gray-700">
                                {t('booking.modal.hotel_not_found')}
                            </div>
                        )}

                        {hotels.map((hotel) => (
                            <Combobox.Option
                                key={hotel.id}
                                className={({ active }) =>
                                    `relative cursor-default select-none py-2 pl-10 pr-4 ${active ? 'bg-indigo-600 text-white' : 'text-gray-900'
                                    }`
                                }
                                value={hotel}
                            >
                                {({ selected, active }) => (
                                    <>
                                        <span
                                            className={`block truncate ${selected ? 'font-medium' : 'font-normal'
                                                }`}
                                        >
                                            {hotel.name}
                                        </span>
                                        {selected ? (
                                            <span
                                                className={`absolute inset-y-0 left-0 flex items-center pl-3 ${active ? 'text-white' : 'text-indigo-600'
                                                    }`}
                                            >
                                                <CheckIcon className="h-5 w-5" aria-hidden="true" />
                                            </span>
                                        ) : null}
                                    </>
                                )}
                            </Combobox.Option>
                        ))}
                    </Combobox.Options>
                </div>
            </Combobox>
        </div>
    );
}
