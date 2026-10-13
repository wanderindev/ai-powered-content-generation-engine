import React, { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { SunIcon, MoonIcon } from '@heroicons/react/24/outline';

export default function TimeSlotSelector({ selectedTime, onChange, availability, selectedDate, selectedSpecificTime, onSpecificTimeChange, cartBookedSlots, selectedDateStr }) {
    const { t } = useTranslation();

    // Compute which slots are disabled
    const isMorningDisabled = availability?.am_available === false ||
        (cartBookedSlots && selectedDateStr && cartBookedSlots[selectedDateStr]?.has('morning'));
    const isAfternoonDisabled = availability?.pm_available === false ||
        (cartBookedSlots && selectedDateStr && cartBookedSlots[selectedDateStr]?.has('afternoon'));

    // Auto-clear selectedTime if the slot becomes disabled (e.g. date change)
    useEffect(() => {
        if (selectedTime === 'morning' && isMorningDisabled) {
            onChange(null);
            if (onSpecificTimeChange) onSpecificTimeChange('');
        } else if (selectedTime === 'afternoon' && isAfternoonDisabled) {
            onChange(null);
            if (onSpecificTimeChange) onSpecificTimeChange('');
        }
    }, [isMorningDisabled, isAfternoonDisabled, selectedTime]);

    if (!selectedDate) return null;

    const slots = [
        { id: 'morning', label: t('booking.modal.morning'), icon: SunIcon, sub: '7:00 AM - 10:30 AM', disabled: isMorningDisabled },
        { id: 'afternoon', label: t('booking.modal.afternoon'), icon: MoonIcon, sub: '1:00 PM - 5:00 PM', disabled: isAfternoonDisabled },
    ];

    const getTimeOptions = () => {
        const options = [];
        if (selectedTime === 'morning') {
            let hour = 7;
            let min = 0;
            while (hour < 10 || (hour === 10 && min <= 30)) {
                const timeLabel = `${hour}:${min === 0 ? '00' : min} AM`;
                options.push(timeLabel);
                min += 30;
                if (min === 60) { min = 0; hour += 1; }
            }
        } else if (selectedTime === 'afternoon') {
            let hour = 1;
            let min = 0;
            while (hour < 5 || (hour === 5 && min === 0)) {
                const timeLabel = `${hour}:${min === 0 ? '00' : min} PM`;
                options.push(timeLabel);
                min += 30;
                if (min === 60) { min = 0; hour += 1; }
            }
        }
        return options;
    };

    const timeOptions = getTimeOptions();

    return (
        <div className="space-y-3">
            <label className="text-sm font-medium text-gray-900 block">{t('booking.modal.select_time')}</label>
            <div className="grid grid-cols-2 gap-3">
                {slots.map((slot) => {
                    const Icon = slot.icon;
                    const isSelected = selectedTime === slot.id;
                    const isDisabled = slot.disabled;

                    return (
                        <button
                            key={slot.id}
                            type="button"
                            disabled={isDisabled}
                            onClick={() => {
                                onChange(slot.id);
                                if (onSpecificTimeChange) onSpecificTimeChange('');
                            }}
                            className={`
                            relative flex flex-col items-center justify-center p-4 rounded-lg border-2 transition-all
                            ${isDisabled
                                    ? 'border-gray-100 bg-gray-50 text-gray-300 cursor-not-allowed opacity-50'
                                    : isSelected
                                        ? 'border-indigo-600 bg-indigo-50 text-indigo-700'
                                        : 'border-gray-200 bg-white text-gray-700 hover:border-gray-300 hover:bg-gray-50'
                                }
                        `}
                        >
                            <Icon className={`w-6 h-6 mb-2 ${isDisabled ? 'text-gray-300' : isSelected ? 'text-indigo-600' : 'text-gray-400'}`} />
                            <span className="font-semibold text-sm">{slot.label}</span>
                            <span className="text-xs opacity-75">{slot.sub}</span>
                            {isDisabled && (
                                <span className="absolute top-1 right-1 text-xs text-gray-400">{t('booking.availability.unavailable')}</span>
                            )}
                        </button>
                    );
                })}
            </div>

            {/* Specific Time Selection */}
            {selectedTime && (
                <div className="mt-4">
                    <label className="text-xs text-gray-500 mb-1 block">{t('booking.modal.preferred_time')}</label>
                    <div className="relative">
                        <select
                            value={selectedSpecificTime}
                            onChange={(e) => onSpecificTimeChange && onSpecificTimeChange(e.target.value)}
                            className="block w-full appearance-none rounded-md border-0 py-2.5 pl-3 pr-10 text-gray-900 ring-1 ring-inset ring-gray-300 focus:ring-2 focus:ring-inset focus:ring-indigo-600 sm:text-sm sm:leading-6"
                        >
                            <option value="">{t('booking.modal.select_exact_time')}</option>
                            {timeOptions.map((time) => (
                                <option key={time} value={time}>{time}</option>
                            ))}
                        </select>
                        <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-3">
                            <svg className="h-5 w-5 text-gray-400" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                                <path fillRule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clipRule="evenodd" />
                            </svg>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
