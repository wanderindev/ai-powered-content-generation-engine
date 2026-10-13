import React, { useState, useEffect } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { bookingService } from '../services/booking';

export default function BookingCancel() {
    const [searchParams] = useSearchParams();
    const { t, i18n } = useTranslation();
    // Token from the URL fragment (#token=...), with a query fallback for magic
    // links already in flight before this change (#43).
    const token =
        new URLSearchParams(window.location.hash.slice(1)).get('token') ||
        searchParams.get('token');
    const reference = searchParams.get('reference');

    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [order, setOrder] = useState(null);
    const [cancelling, setCancelling] = useState(false);
    const [cancelled, setCancelled] = useState(false);

    useEffect(() => {
        async function loadOrder() {
            if (!token || !reference) {
                setError('missing_params');
                setLoading(false);
                return;
            }

            try {
                // Verify token
                const tokenResult = await bookingService.verifyToken(token);
                if (!tokenResult.valid) {
                    setError('invalid_token');
                    setLoading(false);
                    return;
                }

                // Fetch order
                const orderData = await bookingService.getOrder(reference, token);
                setOrder(orderData);
            } catch {
                setError('fetch_failed');
            } finally {
                setLoading(false);
            }
        }

        loadOrder();
    }, [token, reference]);

    const handleCancel = async () => {
        setCancelling(true);
        try {
            await bookingService.cancelOrder(reference, token, 'Customer requested cancellation');
            setCancelled(true);
        } catch {
            setError('cancel_failed');
        } finally {
            setCancelling(false);
        }
    };

    const lang = i18n.language;

    if (loading) {
        return (
            <div className="bg-white min-h-screen pt-24 pb-12">
                <div className="max-w-xl mx-auto px-4 text-center">
                    <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-emerald-600 mx-auto"></div>
                    <p className="mt-4 text-gray-500">Loading...</p>
                </div>
            </div>
        );
    }

    if (error) {
        return (
            <div className="bg-white min-h-screen pt-24 pb-12">
                <div className="max-w-xl mx-auto px-4 text-center">
                    <div className="bg-red-50 rounded-lg p-8 border border-red-100">
                        <h2 className="text-xl font-bold text-red-800">{t('booking_manage.link_expired')}</h2>
                        <p className="mt-2 text-red-600">{t('booking_manage.link_expired_desc')}</p>
                    </div>
                    <div className="mt-8">
                        <Link to="/" className="text-emerald-700 hover:text-emerald-600 font-medium">
                            &larr; {t('order.confirmation.return_home')}
                        </Link>
                    </div>
                </div>
            </div>
        );
    }

    if (cancelled) {
        return (
            <div className="bg-white min-h-screen pt-24 pb-12">
                <div className="max-w-xl mx-auto px-4 text-center">
                    <div className="bg-green-50 rounded-lg p-8 border border-green-200">
                        <h2 className="text-xl font-bold text-green-800">{t('booking_cancel.success_title')}</h2>
                        <p className="mt-2 text-green-700">{t('booking_cancel.success_message')}</p>
                        <p className="mt-4 text-gray-600">{t('booking_cancel.sorry_message')}</p>
                    </div>
                    <div className="mt-8">
                        <Link
                            to="/tours-guiados"
                            className="inline-block bg-emerald-700 text-white px-6 py-3 rounded-lg hover:bg-emerald-800 font-medium"
                        >
                            {t('booking_cancel.browse_tours')}
                        </Link>
                    </div>
                </div>
            </div>
        );
    }

    if (!order) return null;

    return (
        <div className="bg-white min-h-screen pt-24 pb-12">
            <div className="max-w-xl mx-auto px-4 sm:px-6 lg:px-8">
                <h1 className="text-2xl font-bold text-gray-900">{t('booking_cancel.title')}</h1>

                {/* Order Summary */}
                <div className="mt-6 bg-gray-50 rounded-lg p-6 border border-gray-100">
                    <p className="text-sm text-gray-500">{t('order.confirmation.reference')}</p>
                    <p className="text-lg font-mono font-bold text-gray-900">{order.reference}</p>

                    <div className="mt-4 space-y-2">
                        {order.bookings.map((booking) => (
                            <div key={booking.reference} className="flex justify-between items-center text-sm">
                                <span className="text-gray-700">
                                    {lang === 'es' ? booking.tour_name_es : booking.tour_name}
                                    {' - '}
                                    {new Date(booking.tour_date).toLocaleDateString(lang === 'es' ? 'es-PA' : 'en-US')}
                                </span>
                                <span className="font-medium">${Number(booking.grand_total).toFixed(2)}</span>
                            </div>
                        ))}
                    </div>

                    <div className="mt-3 pt-3 border-t border-gray-200 flex justify-between font-semibold">
                        <span>{t('booking.modal.total')}</span>
                        <span>${Number(order.grand_total).toFixed(2)}</span>
                    </div>
                </div>

                {/* Confirmation */}
                <div className="mt-8 bg-red-50 rounded-lg p-6 border border-red-200">
                    <p className="text-red-800 font-medium">{t('booking_cancel.confirm_question')}</p>
                </div>

                <div className="mt-6 flex flex-col sm:flex-row gap-3">
                    <button
                        onClick={handleCancel}
                        disabled={cancelling}
                        className="flex-1 bg-red-600 text-white px-4 py-3 rounded-lg hover:bg-red-700 font-medium disabled:opacity-50"
                    >
                        {cancelling ? '...' : t('booking_cancel.yes_cancel')}
                    </button>
                    <Link
                        to={`/booking/manage?reference=${reference}#token=${token}`}
                        className="flex-1 text-center bg-white text-gray-700 border border-gray-300 px-4 py-3 rounded-lg hover:bg-gray-50 font-medium"
                    >
                        {t('booking_cancel.no_keep')}
                    </Link>
                </div>
            </div>
        </div>
    );
}
