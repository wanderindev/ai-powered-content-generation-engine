import React, { useState, Fragment } from 'react';
import { Dialog, Transition } from '@headlessui/react';
import { XMarkIcon } from '@heroicons/react/24/outline';
import { useTranslation } from 'react-i18next';
import { format } from 'date-fns';
import { useCart } from '../../contexts/CartContext';
import DatePicker from './DatePicker';
import TimeSlotSelector from './TimeSlotSelector';
import ParticipantInput from './ParticipantInput';
import HotelAutocomplete from './HotelAutocomplete';
import PriceBreakdown from './PriceBreakdown';

const defaultParticipants = {
    seniorFemale: { resident: 0, tourist: 0 },
    seniorMale: { resident: 0, tourist: 0 },
    adult: { resident: 0, tourist: 0 },
    child10to17: { resident: 0, tourist: 0 },
    child5to10: { resident: 0, tourist: 0 },
    childUnder5: { resident: 0, tourist: 0 },
};

export default function BookingModal({ open, onClose, tour, editItem }) {
    const { t } = useTranslation();
    const { addItem, updateItem, getBookedSlots, openCart } = useCart();

    // BookingModal is remounted (via `key`) on every open by BookingModalProvider,
    // so the form state is derived from `editItem` here in useState initializers
    // instead of a reset effect (which would setState synchronously on open).
    const initialDetails = editItem?.bookingDetails;

    // State for Booking Form
    const [selectedDate, setSelectedDate] = useState(() =>
        initialDetails?.date ? new Date(initialDetails.date) : null,
    );
    const [selectedTime, setSelectedTime] = useState(() => initialDetails?.time || null);
    const [participants, setParticipants] = useState(() =>
        initialDetails ? { ...defaultParticipants, ...initialDetails.participants } : defaultParticipants,
    );
    const [selectedHotel, setSelectedHotel] = useState(() => initialDetails?.hotel || null);
    const [transportNeeded, setTransportNeeded] = useState(() => initialDetails?.transport?.needed || false);
    const [transportDirection, setTransportDirection] = useState(
        () => initialDetails?.transport?.direction || 'BOTH',
    );
    const [selectedSpecificTime, setSelectedSpecificTime] = useState(
        () => initialDetails?.specificTime || '',
    );
    const [calculatedPrice, setCalculatedPrice] = useState(() => editItem?.pricing || null);

    // Availability from API (repopulated by DatePicker once a date is picked)
    const [availability, setAvailability] = useState({});

    const isEditMode = !!editItem;

    // Cart-booked slots, excluding the item being edited
    const cartBookedSlots = getBookedSlots(editItem?.id || null);

    // Combined availability for the selected date
    const selectedDateStr = selectedDate ? format(selectedDate, 'yyyy-MM-dd') : null;
    const dayAvailability = selectedDateStr ? availability[selectedDateStr] : undefined;

    // Groups of 5+ are transported with a flat round-trip vehicle; one-way is
    // not offered at those sizes, so the direction is forced to BOTH.
    const totalPax = Object.values(participants).reduce(
        (sum, cat) => sum + (cat.resident || 0) + (cat.tourist || 0),
        0,
    );
    const forceRoundTrip = totalPax > 4;
    const effectiveDirection = forceRoundTrip ? 'BOTH' : transportDirection;

    // Validation
    const isValid = selectedDate && selectedTime &&
        (selectedTime === 'morning' || selectedTime === 'afternoon' ? !!selectedSpecificTime : true) &&
        Object.values(participants).some(cat => (cat.resident + cat.tourist) > 0) &&
        (transportNeeded ? selectedHotel : true) &&
        calculatedPrice;

    const handleSubmit = () => {
        if (!isValid) return;

        const itemData = {
            tour,
            bookingDetails: {
                date: selectedDate,
                time: selectedTime,
                specificTime: selectedSpecificTime,
                participants,
                hotel: selectedHotel,
                transport: transportNeeded ? { needed: true, direction: effectiveDirection } : { needed: false }
            },
            pricing: calculatedPrice,
        };

        if (isEditMode) {
            updateItem(editItem.id, itemData);
        } else {
            addItem(itemData);
        }
        onClose();
        // Expand the cart so the user can review and proceed to checkout. Delay
        // until the modal's close transition finishes to avoid two stacked dialogs.
        setTimeout(openCart, 300);
    };

    if (!tour) return null;

    return (
        <Transition.Root show={open} appear as={Fragment}>
            <Dialog as="div" className="relative z-50" onClose={onClose}>
                <Transition.Child
                    as={Fragment}
                    enter="ease-out duration-300"
                    enterFrom="opacity-0"
                    enterTo="opacity-100"
                    leave="ease-in duration-200"
                    leaveFrom="opacity-100"
                    leaveTo="opacity-0"
                >
                    <div className="fixed inset-0 bg-gray-500 bg-opacity-75 transition-opacity" />
                </Transition.Child>

                <div className="fixed inset-0 z-10 overflow-y-auto">
                    <div className="flex min-h-full items-end justify-center p-4 text-center sm:items-center sm:p-0">
                        <Transition.Child
                            as={Fragment}
                            enter="ease-out duration-300"
                            enterFrom="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
                            enterTo="opacity-100 translate-y-0 sm:scale-100"
                            leave="ease-in duration-200"
                            leaveFrom="opacity-100 translate-y-0 sm:scale-100"
                            leaveTo="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
                        >
                            <Dialog.Panel className="relative transform overflow-hidden rounded-lg bg-white text-left shadow-xl transition-all sm:my-8 sm:w-full sm:max-w-4xl">

                                {/* Close Button */}
                                <div className="absolute right-4 top-4 z-10">
                                    <button
                                        type="button"
                                        className="rounded-md bg-white text-gray-400 hover:text-gray-500 focus:outline-none"
                                        onClick={onClose}
                                    >
                                        <span className="sr-only">{t('booking.modal.close')}</span>
                                        <XMarkIcon className="h-6 w-6" aria-hidden="true" />
                                    </button>
                                </div>

                                <div className="grid grid-cols-1 lg:grid-cols-12">
                                    <div className="bg-gray-50 lg:col-span-4 p-6 flex flex-col">
                                        <div className="aspect-w-3 aspect-h-2 overflow-hidden rounded-lg bg-gray-100 mb-4">
                                            <img src={tour.image} alt={tour.title} className="object-cover object-center" />
                                        </div>
                                        <h2 className="text-xl font-bold text-gray-900">{tour.title}</h2>
                                        <p className="mt-2 text-sm text-gray-500">{tour.description}</p>
                                    </div>

                                    {/* Right Column: Booking Form */}
                                    <div className="lg:col-span-8 p-6 max-h-[80vh] overflow-y-auto">
                                        <h3 className="text-lg font-medium text-gray-900 mb-6">{t('booking.modal.title')}</h3>

                                        <div className="space-y-8">
                                            {/* Step 1: Date & Time */}
                                            <div className="grid grid-cols-1 gap-6">
                                                <div>
                                                    <DatePicker
                                                        tourSlug={tour.slug}
                                                        selectedDate={selectedDate}
                                                        onChange={setSelectedDate}
                                                        onAvailabilityChange={setAvailability}
                                                        cartBookedSlots={cartBookedSlots}
                                                    />
                                                </div>
                                                <div>
                                                    <TimeSlotSelector
                                                        selectedDate={selectedDate}
                                                        selectedTime={selectedTime}
                                                        onChange={setSelectedTime}
                                                        selectedSpecificTime={selectedSpecificTime}
                                                        onSpecificTimeChange={setSelectedSpecificTime}
                                                        availability={dayAvailability}
                                                        cartBookedSlots={cartBookedSlots}
                                                        selectedDateStr={selectedDateStr}
                                                    />
                                                </div>
                                            </div>

                                            <hr className="border-gray-200" />

                                            {/* Step 2: Participants */}
                                            <ParticipantInput
                                                participants={participants}
                                                onChange={setParticipants}
                                                maxParticipants={15}
                                            />

                                            <hr className="border-gray-200" />

                                            {/* Step 3: Hotel & Transport */}
                                            <div className="space-y-4">
                                                <div className="flex items-center">
                                                    <input
                                                        id="transport-needed"
                                                        type="checkbox"
                                                        checked={transportNeeded}
                                                        onChange={(e) => setTransportNeeded(e.target.checked)}
                                                        className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-600 focus:ring-offset-0"
                                                    />
                                                    <label htmlFor="transport-needed" className="ml-2 block text-sm text-gray-900">
                                                        {t('booking.modal.need_transport')}
                                                    </label>
                                                </div>

                                                {transportNeeded && (
                                                    <div className="space-y-4 pl-6 border-l-2 border-gray-100">
                                                        <HotelAutocomplete
                                                            selectedHotel={selectedHotel}
                                                            onChange={setSelectedHotel}
                                                        />

                                                        <div>
                                                            <label className="block text-sm font-medium text-gray-700 mb-1">{t('booking.modal.direction')}</label>
                                                            <div className="relative mt-1">
                                                                <select
                                                                    value={effectiveDirection}
                                                                    onChange={(e) => setTransportDirection(e.target.value)}
                                                                    disabled={forceRoundTrip}
                                                                    className="block w-full appearance-none rounded-md border-0 py-2.5 pl-3 pr-10 text-gray-900 ring-1 ring-inset ring-gray-300 focus:ring-2 focus:ring-inset focus:ring-indigo-600 disabled:bg-gray-50 disabled:text-gray-500 sm:text-sm sm:leading-6"
                                                                >
                                                                    <option value="BOTH">{t('booking.modal.both_ways')}</option>
                                                                    {!forceRoundTrip && <option value="TO">{t('booking.modal.to_only')}</option>}
                                                                    {!forceRoundTrip && <option value="FROM">{t('booking.modal.from_only')}</option>}
                                                                </select>
                                                                <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-3">
                                                                    <svg className="h-5 w-5 text-gray-400" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                                                                        <path fillRule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clipRule="evenodd" />
                                                                    </svg>
                                                                </div>
                                                            </div>
                                                            {forceRoundTrip && (
                                                                <p className="mt-1 text-xs text-gray-500 italic">
                                                                    {t('booking.modal.round_trip_only')}
                                                                </p>
                                                            )}
                                                        </div>
                                                    </div>
                                                )}
                                            </div>

                                            <hr className="border-gray-200" />

                                            {/* Step 4: Price & Actions */}
                                            <PriceBreakdown
                                                tourSlug={tour.slug}
                                                participants={participants}
                                                transport={transportNeeded ? { needed: true, hotel: selectedHotel, direction: effectiveDirection } : null}
                                                onPriceCalculated={setCalculatedPrice}
                                            />

                                            <div className="mt-6">
                                                <button
                                                    type="button"
                                                    onClick={handleSubmit}
                                                    disabled={!isValid}
                                                    className="flex w-full items-center justify-center rounded-md border border-transparent bg-indigo-600 px-8 py-3 text-base font-medium text-white hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
                                                >
                                                    {isEditMode ? t('booking.modal.update_cart') : t('booking.modal.add_to_cart')}
                                                </button>
                                            </div>

                                        </div>
                                    </div>
                                </div>

                            </Dialog.Panel>
                        </Transition.Child>
                    </div>
                </div>
            </Dialog>
        </Transition.Root>
    );
}
