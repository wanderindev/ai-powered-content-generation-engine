import React, { useEffect, useRef, useState } from 'react';
import { useParams, useLocation, Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import * as Sentry from '@sentry/react';
import { CheckCircleIcon } from '@heroicons/react/24/outline';
import { bookingService } from '../services/booking';

const STATUS_COLORS = {
    PENDING: 'bg-yellow-100 text-yellow-800',
    PAYMENT_LINK_SENT: 'bg-blue-100 text-blue-800',
    PAID: 'bg-green-100 text-green-800',
    CONFIRMED: 'bg-green-100 text-green-800',
    CANCELLED: 'bg-red-100 text-red-800',
    EXPIRED: 'bg-gray-100 text-gray-800',
};

function OrderConfirmationInner() {
    const { reference } = useParams();
    const location = useLocation();
    const { t, i18n } = useTranslation();
    const lang = i18n.language;

    // The token arrives via router state (never the URL) on the first render
    // after checkout, so the page can fetch + display the order. A hard refresh
    // drops it; we then degrade to the static "check your email" view. (#36)
    const token = location.state?.token || null;
    const [order, setOrder] = useState(null);
    const [loading, setLoading] = useState(Boolean(token));

    useEffect(() => {
        if (!token) return;
        let active = true;
        bookingService
            .getOrder(reference, token)
            .then((data) => { if (active) setOrder(data); })
            .catch(() => { /* fall back to the static view */ })
            .finally(() => { if (active) setLoading(false); });
        return () => { active = false; };
    }, [reference, token]);

    // Meta Pixel: Schedule on mount; Purchase once with the real order value
    // (falls back to 0 only if the order couldn't be loaded). (#36 / closes #47)
    useEffect(() => {
        if (window.fbq) window.fbq('track', 'Schedule');
    }, []);
    const purchaseFired = useRef(false);
    useEffect(() => {
        if (loading || purchaseFired.current) return;
        purchaseFired.current = true;
        if (window.fbq) {
            window.fbq('track', 'Purchase', {
                currency: 'USD',
                value: order ? Number(order.grand_total) : 0,
            });
        }
    }, [loading, order]);

    const manageUrl = token
        ? `/booking/manage?reference=${encodeURIComponent(reference)}#token=${encodeURIComponent(token)}`
        : null;
    const discountTotal = order ? Number(order.subtotal) - Number(order.grand_total) : 0;

    return (
        <div className="bg-white min-h-screen pt-24 pb-12">
            <div className="max-w-xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
                <CheckCircleIcon className="mx-auto h-24 w-24 text-green-500" />
                <h1 className="mt-4 text-3xl font-bold tracking-tight text-gray-900">{t('order.confirmation.title')}</h1>
                <p className="mt-4 text-base text-gray-500">{t('order.confirmation.thank_you')}</p>

                <div className="mt-8 bg-gray-50 rounded-lg p-6 border border-gray-100">
                    <p className="text-sm font-medium text-gray-500">{t('order.confirmation.reference')}</p>
                    <p className="text-2xl font-mono font-bold text-gray-900 mt-1">{reference}</p>
                </div>

                {loading && (
                    <div className="mt-8" aria-live="polite">
                        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600 mx-auto"></div>
                    </div>
                )}

                {/* Booking summary (only when the order was fetched) */}
                {order && (
                    <div className="mt-8 text-left">
                        <h2 className="text-lg font-medium text-gray-900 mb-4">{t('order.confirmation.your_bookings')}</h2>
                        <div className="space-y-4">
                            {order.bookings.map((booking) => (
                                <div key={booking.reference} className="bg-white border border-gray-200 rounded-lg p-4 shadow-sm">
                                    <div className="flex items-start justify-between">
                                        <div>
                                            <h3 className="font-semibold text-gray-900">
                                                {lang === 'es' ? booking.tour_name_es : booking.tour_name}
                                            </h3>
                                            <p className="text-sm text-gray-500 mt-1">
                                                {new Date(booking.tour_date).toLocaleDateString(lang === 'es' ? 'es-PA' : 'en-US', {
                                                    weekday: 'long',
                                                    year: 'numeric',
                                                    month: 'long',
                                                    day: 'numeric',
                                                })}
                                                {' - '}
                                                {booking.time_slot === 'AM' ? t('booking.modal.morning') : t('booking.modal.afternoon')}
                                                {booking.preferred_time && ` (${booking.preferred_time})`}
                                            </p>
                                        </div>
                                        <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${STATUS_COLORS[booking.status] || 'bg-gray-100 text-gray-800'}`}>
                                            {t(`order.status.${booking.status.toLowerCase()}`)}
                                        </span>
                                    </div>
                                    <div className="mt-2 text-sm text-gray-500">
                                        {t('booking.modal.participants')}: <span className="font-medium text-gray-700">{booking.total_participants}</span>
                                        {booking.hotel_name && (
                                            <span> · {t('booking.modal.hotel')}: <span className="font-medium text-gray-700">{booking.hotel_name}</span></span>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>

                        {/* Order totals with a meaningful discount line (#39) */}
                        <div className="mt-4 bg-gray-50 rounded-lg p-4 border border-gray-100 text-sm space-y-1">
                            <div className="flex justify-between text-gray-500">
                                <span>{t('order.confirmation.subtotal')}</span>
                                <span>${Number(order.subtotal).toFixed(2)}</span>
                            </div>
                            {discountTotal > 0 && (
                                <div className="flex justify-between text-indigo-600">
                                    <span>{t('booking.modal.senior_discount')}</span>
                                    <span>-${discountTotal.toFixed(2)}</span>
                                </div>
                            )}
                            <div className="flex justify-between font-bold text-base text-gray-900 pt-1 border-t border-gray-200">
                                <span>{t('booking.modal.total')}</span>
                                <span>${Number(order.grand_total).toFixed(2)}</span>
                            </div>
                        </div>

                        {manageUrl && (
                            <Link
                                to={manageUrl}
                                className="mt-4 block w-full text-center bg-indigo-600 text-white px-4 py-3 rounded-lg hover:bg-indigo-700 font-medium"
                            >
                                {t('order.confirmation.manage_booking')}
                            </Link>
                        )}
                    </div>
                )}

                <div className="mt-10 text-left">
                    <h2 className="text-lg font-medium text-gray-900 mb-4">{t('order.confirmation.what_next')}</h2>
                    <ul className="space-y-4">
                        <li className="flex items-start">
                            <span className="flex-shrink-0 h-6 w-6 flex items-center justify-center rounded-full bg-indigo-100 text-indigo-600 font-bold text-xs">1</span>
                            <span className="ml-3 text-sm text-gray-600">{t('order.confirmation.step_1', { hours: 24 })}</span>
                        </li>
                        <li className="flex items-start">
                            <span className="flex-shrink-0 h-6 w-6 flex items-center justify-center rounded-full bg-indigo-100 text-indigo-600 font-bold text-xs">2</span>
                            <span className="ml-3 text-sm text-gray-600">{t('order.confirmation.step_2')}</span>
                        </li>
                        <li className="flex items-start">
                            <span className="flex-shrink-0 h-6 w-6 flex items-center justify-center rounded-full bg-indigo-100 text-indigo-600 font-bold text-xs">3</span>
                            <span className="ml-3 text-sm text-gray-600">{t('order.confirmation.step_3')}</span>
                        </li>
                    </ul>
                </div>

                <div className="mt-10">
                    <Link to="/" className="text-indigo-600 hover:text-indigo-500 font-medium">
                        {t('order.confirmation.return_home')} &rarr;
                    </Link>
                </div>
            </div>
        </div>
    );
}

function OrderConfirmationFallback() {
    const { t } = useTranslation();
    return (
        <div className="bg-gray-50 min-h-screen py-12">
            <div className="container mx-auto px-4 max-w-2xl text-center">
                <CheckCircleIcon className="mx-auto h-24 w-24 text-green-500" />
                <h1 className="mt-4 text-2xl font-bold mb-4 text-gray-900">
                    {t('order.confirmation.title', 'Booking Confirmed!')}
                </h1>
                <p className="text-gray-600 mb-6">
                    {t('order.confirmation.error_fallback', 'Your booking was confirmed successfully. If the page isn\'t loading correctly, please try refreshing.')}
                </p>
                <button
                    onClick={() => window.location.reload()}
                    className="inline-flex items-center px-4 py-2 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700"
                >
                    {t('common.refresh', 'Refresh page')}
                </button>
            </div>
        </div>
    );
}

const OrderConfirmation = Sentry.withErrorBoundary(OrderConfirmationInner, {
    fallback: <OrderConfirmationFallback />,
});

export default OrderConfirmation;
