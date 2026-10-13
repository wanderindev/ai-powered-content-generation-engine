import React, { useState, useEffect } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { bookingService } from '../services/booking';

export default function BookingRenewPayment() {
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
    const [renewing, setRenewing] = useState(false);
    const [renewed, setRenewed] = useState(false);

    useEffect(() => {
        async function loadOrder() {
            if (!token || !reference) {
                setError('missing_params');
                setLoading(false);
                return;
            }

            try {
                const tokenResult = await bookingService.verifyToken(token);
                if (!tokenResult.valid) {
                    setError('invalid_token');
                    setLoading(false);
                    return;
                }

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

    const handleRenew = async () => {
        setRenewing(true);
        try {
            await bookingService.renewPayment(reference, token);
            setRenewed(true);
        } catch {
            setError('renew_failed');
        } finally {
            setRenewing(false);
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

    if (renewed) {
        return (
            <div className="bg-white min-h-screen pt-24 pb-12">
                <div className="max-w-xl mx-auto px-4 text-center">
                    <div className="bg-green-50 rounded-lg p-8 border border-green-200">
                        <h2 className="text-xl font-bold text-green-800">{t('booking_renew.title')}</h2>
                        <p className="mt-2 text-green-700">{t('booking_renew.message')}</p>
                        <p className="mt-4 text-gray-600">{t('booking_renew.eta', { hours: 24 })}</p>
                        <p className="mt-4 text-sm text-gray-500">
                            {t('booking_renew.not_received')} diego@panamaincontext.com
                        </p>
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

    if (!order) return null;

    return (
        <div className="bg-white min-h-screen pt-24 pb-12">
            <div className="max-w-xl mx-auto px-4 sm:px-6 lg:px-8">
                <h1 className="text-2xl font-bold text-gray-900">{t('booking_manage.renew_payment')}</h1>

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

                {/* Renewal prompt */}
                <div className="mt-8 bg-blue-50 rounded-lg p-6 border border-blue-200">
                    <p className="text-blue-800">
                        {t('booking_renew.eta', { hours: 24 })}
                    </p>
                </div>

                <div className="mt-6 flex flex-col sm:flex-row gap-3">
                    <button
                        onClick={handleRenew}
                        disabled={renewing}
                        className="flex-1 bg-emerald-700 text-white px-4 py-3 rounded-lg hover:bg-emerald-800 font-medium disabled:opacity-50"
                    >
                        {renewing ? '...' : t('booking_manage.renew_payment')}
                    </button>
                    <Link
                        to={`/booking/manage?reference=${reference}#token=${token}`}
                        className="flex-1 text-center bg-white text-gray-700 border border-gray-300 px-4 py-3 rounded-lg hover:bg-gray-50 font-medium"
                    >
                        &larr; {t('order.confirmation.return_home')}
                    </Link>
                </div>
            </div>
        </div>
    );
}
