import React, { useState, useEffect } from 'react';
import { DayPicker } from 'react-day-picker';
import { format, startOfMonth, endOfMonth } from 'date-fns';
import { es, enUS } from 'date-fns/locale';
import { useTranslation } from 'react-i18next';
import { bookingService } from '../../services/booking';
import 'react-day-picker/style.css';

export default function DatePicker({ tourSlug, selectedDate, onChange, onAvailabilityChange, cartBookedSlots }) {
    const { t, i18n } = useTranslation();
    const [availability, setAvailability] = useState({});
    const [month, setMonth] = useState(new Date());

    const currentLocale = i18n.language === 'es' ? es : enUS;

    useEffect(() => {
        let isMounted = true;

        const fetchAvailability = async () => {
            const start = format(startOfMonth(month), 'yyyy-MM-dd');
            const end = format(endOfMonth(month), 'yyyy-MM-dd');

            try {
                const data = await bookingService.getAvailability(tourSlug, start, end);
                if (isMounted) {
                    // API returns an array: [{date, am_available, pm_available}, ...]
                    // Transform to map keyed by date string
                    const availMap = {};
                    if (Array.isArray(data)) {
                        data.forEach(entry => {
                            availMap[entry.date] = entry;
                        });
                    } else if (data && typeof data === 'object') {
                        // Fallback if API already returns an object
                        Object.assign(availMap, data);
                    }
                    setAvailability(prev => ({ ...prev, ...availMap }));
                }
            } catch (err) {
                console.error("Failed to fetch availability", err);
            }
        };

        if (tourSlug) {
            fetchAvailability();
        }

        return () => { isMounted = false; };
    }, [tourSlug, month]);

    // Notify parent of combined availability (API + cart blocks)
    useEffect(() => {
        if (onAvailabilityChange) {
            onAvailabilityChange(availability);
        }
    }, [availability, onAvailabilityChange]);

    const isDateDisabled = (date) => {
        if (date < new Date(new Date().setHours(0, 0, 0, 0))) return true;

        const dateStr = format(date, 'yyyy-MM-dd');
        const dayData = availability[dateStr];

        // Check API availability
        let amAvailable = true;
        let pmAvailable = true;

        if (dayData) {
            if (dayData.am_available === false) amAvailable = false;
            if (dayData.pm_available === false) pmAvailable = false;
        }

        // Check cart-booked slots
        if (cartBookedSlots && cartBookedSlots[dateStr]) {
            const cartSlots = cartBookedSlots[dateStr];
            if (cartSlots.has('morning')) amAvailable = false;
            if (cartSlots.has('afternoon')) pmAvailable = false;
        }

        // Disable date only if BOTH slots are unavailable
        if (!amAvailable && !pmAvailable) return true;

        return false;
    };

    return (
        <div className="p-4 bg-white rounded-lg border border-gray-200 flex justify-center">
            <style>{`
        .rdp { --rdp-cell-size: 40px; --rdp-accent-color: #4f46e5; --rdp-background-color: #eef2ff; }
        .rdp-day_selected:not([disabled]) { background-color: var(--rdp-accent-color); color: white; }
        .rdp-button:hover:not([disabled]):not(.rdp-day_selected) { background-color: #f3f4f6; }
      `}</style>
            <DayPicker
                mode="single"
                selected={selectedDate}
                onSelect={onChange}
                month={month}
                onMonthChange={setMonth}
                locale={currentLocale}
                disabled={isDateDisabled}
                footer={
                    selectedDate ? (
                        <p className="mt-2 text-sm text-gray-500 text-center">
                            {format(selectedDate, 'PPP', { locale: currentLocale })}
                        </p>
                    ) : (
                        <p className="mt-2 text-sm text-gray-500 text-center">{t('booking.modal.select_date')}</p>
                    )
                }
            />
        </div>
    );
}
