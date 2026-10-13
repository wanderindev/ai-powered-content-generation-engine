import React, { useEffect, useState, useRef, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { bookingService } from '../../services/booking';

// Transform frontend participant keys (camelCase) to backend format (snake_case)
function transformParticipantsForApi(participants) {
    return {
        senior_female: participants.seniorFemale || { resident: 0, tourist: 0 },
        senior_male: participants.seniorMale || { resident: 0, tourist: 0 },
        adult: participants.adult || { resident: 0, tourist: 0 },
        child_10_17: participants.child10to17 || { resident: 0, tourist: 0 },
        child_5_10: participants.child5to10 || { resident: 0, tourist: 0 },
        child_under_5: participants.childUnder5 || { resident: 0, tourist: 0 },
    };
}

// Transform frontend transport to backend format
function transformTransportForApi(transport) {
    if (!transport || !transport.needed) return null;
    const hotelId = transport.hotel?.id || null;
    const hotelName = transport.hotel?.id ? null : transport.hotel?.name || null;
    // Hotel not chosen yet: omit transport so the rest of the breakdown still
    // computes. The backend requires a hotel reference, and without it the
    // transport cost is unknown — the UI prompts the user to pick a hotel.
    if (!hotelId && !hotelName) return null;
    return {
        direction: transport.direction, // Already uppercase: TO, FROM, BOTH
        hotel_id: hotelId,
        hotel_name_manual: hotelName,
    };
}

// Calculate total participants
function getTotalParticipants(participants) {
    return Object.values(participants).reduce(
        (sum, cat) => sum + (cat.resident || 0) + (cat.tourist || 0), 0
    );
}

export default function PriceBreakdown({ tourSlug, participants, transport, onPriceCalculated }) {
    const { t } = useTranslation();
    const [breakdown, setBreakdown] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const debounceRef = useRef(null);

    // Memoize transformed payload to avoid recreating on every render
    const apiPayload = useMemo(() => ({
        tour_slug: tourSlug,
        participants: transformParticipantsForApi(participants),
        transport: transformTransportForApi(transport),
    }), [tourSlug, participants, transport]);

    // Serialize for comparison (debounce by value, not reference)
    const payloadKey = useMemo(() => JSON.stringify(apiPayload), [apiPayload]);

    useEffect(() => {
        // Clear any pending debounce
        if (debounceRef.current) {
            clearTimeout(debounceRef.current);
        }

        const totalParticipants = getTotalParticipants(apiPayload.participants);

        // Don't calculate if no participants
        if (totalParticipants === 0) {
            setBreakdown(null);
            setError(null);
            onPriceCalculated(null);
            return;
        }

        // Debounce the API call
        setLoading(true);
        debounceRef.current = setTimeout(async () => {
            try {
                const result = await bookingService.calculatePrice(apiPayload);
                setBreakdown(result);
                setError(null);
                onPriceCalculated(result);
            } catch (err) {
                console.error('Price calculation failed:', err);
                setError(err.message);
                setBreakdown(null);
                onPriceCalculated(null);
            } finally {
                setLoading(false);
            }
        }, 500);

        return () => {
            if (debounceRef.current) {
                clearTimeout(debounceRef.current);
            }
        };
    }, [payloadKey]); // Only re-run when serialized payload changes

    if (!breakdown && !loading && !error) return null;

    return (
        <div className="bg-gray-50 rounded-lg p-4 space-y-3 border border-gray-100">
            <h3 className="text-sm font-medium text-gray-900">{t('booking.modal.price_breakdown')}</h3>

            {/* Loading Skeleton */}
            {loading && (
                <div className="space-y-2 animate-pulse">
                    <div className="h-4 bg-gray-200 rounded w-3/4"></div>
                    <div className="h-4 bg-gray-200 rounded w-1/2"></div>
                    <div className="h-10 bg-gray-200 rounded w-full mt-2"></div>
                </div>
            )}

            {/* Error Message */}
            {error && (
                <div className="text-sm text-red-600 bg-red-50 p-2 rounded">
                    {t('booking.availability.unavailable')}
                </div>
            )}

            {/* Breakdown Display */}
            {!loading && breakdown && (
                <div className="space-y-3 text-sm text-gray-600">
                    {/* Guide Service */}
                    <div className="flex justify-between">
                        <span>
                            {t('booking.modal.guide_service')}
                            {parseFloat(breakdown.service_per_pax) > 0 && (
                                <span className="block text-xs text-gray-400">
                                    ${parseFloat(breakdown.service_per_pax).toFixed(2)} {t('booking.modal.per_person')}
                                </span>
                            )}
                        </span>
                        <span className="font-medium text-gray-900">${parseFloat(breakdown.service_total).toFixed(2)}</span>
                    </div>

                    {/* Discounts */}
                    {parseFloat(breakdown.senior_discount) > 0 && (
                        <div className="flex justify-between text-indigo-600">
                            <span>{t('booking.modal.senior_discount')}</span>
                            <span>-${parseFloat(breakdown.senior_discount).toFixed(2)}</span>
                        </div>
                    )}

                    {/* Attraction Tickets */}
                    {parseFloat(breakdown.tickets_total) > 0 && (
                        <div className="pt-2 border-t border-gray-200">
                            <div className="flex justify-between">
                                <span>{t('booking.modal.tickets')}</span>
                                <span className="font-medium text-gray-900">${parseFloat(breakdown.tickets_total).toFixed(2)}</span>
                            </div>
                            {/* Show breakdown by attraction if available */}
                            {breakdown.tickets_breakdown && breakdown.tickets_breakdown.length > 0 && (
                                <div className="mt-1 space-y-1">
                                    {breakdown.tickets_breakdown.map((attraction, idx) => (
                                        <div key={idx} className="pl-2 text-xs text-gray-500">
                                            <span>{attraction.attraction}</span>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}

                    {/* Transportation */}
                    {transport?.needed && (
                        <div className="pt-2 border-t border-gray-200 flex justify-between gap-4">
                            <span>{t('booking.modal.transport')}</span>
                            {!transport.hotel ? (
                                <span className="text-gray-500 text-xs italic text-right">{t('booking.modal.select_hotel_for_transport')}</span>
                            ) : breakdown.transport_quote_required ? (
                                <span className="text-amber-600 text-xs italic">{t('booking.modal.contact_for_quote')}</span>
                            ) : (
                                <span className="font-medium text-gray-900">${parseFloat(breakdown.transport_total).toFixed(2)}</span>
                            )}
                        </div>
                    )}

                    {/* Total */}
                    <div className="pt-3 border-t border-gray-200 flex justify-between items-center">
                        <span className="text-base font-semibold text-gray-900">{t('booking.modal.total')}</span>
                        <span className="text-lg font-bold text-gray-900">${parseFloat(breakdown.grand_total).toFixed(2)}</span>
                    </div>
                </div>
            )}
        </div>
    );
}