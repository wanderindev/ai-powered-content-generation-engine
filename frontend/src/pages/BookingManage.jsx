import React, { useState, useEffect } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { bookingService } from '../services/booking';

const STATUS_COLORS = {
    PENDING: 'bg-yellow-100 text-yellow-800',
    PAYMENT_LINK_SENT: 'bg-blue-100 text-blue-800',
    PAID: 'bg-green-100 text-green-800',
    CONFIRMED: 'bg-green-100 text-green-800',
    CANCELLED: 'bg-red-100 text-red-800',
    EXPIRED: 'bg-gray-100 text-gray-800',
};

export default function BookingManage() {
    const [searchParams] = useSearchParams();
    const { t, i18n } = useTranslation();
    // Token arrives in the URL fragment (#token=...), which browsers never send
    // to the server (no access-log / Referer leak). Query is read as a fallback
    // for magic links already in flight before this change (#43).
    const token =
        new URLSearchParams(window.location.hash.slice(1)).get('token') ||
        searchParams.get('token');
    const reference = searchParams.get('reference');

    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [, setTokenValid] = useState(false);
    const [order, setOrder] = useState(null);

    useEffect(() => {
        async function loadOrder() {
            if (!token || !reference) {
                setError('missing_params');
                setLoading(false);
                return;
            }

            try {
                // Verify token first
                const tokenResult = await bookingService.verifyToken(token);
                if (!tokenResult.valid) {
                    setError('invalid_token');
                    setLoading(false);
                    return;
                }
                setTokenValid(true);

                // Fetch order details
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

    if (loading) {
        return (
            <div className="bg-white min-h-screen pt-24 pb-12">
                <div className="max-w-2xl mx-auto px-4 text-center">
                    <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600 mx-auto"></div>
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
                        <Link to="/" className="text-indigo-600 hover:text-indigo-500 font-medium">
                            &larr; {t('order.confirmation.return_home')}
                        </Link>
                    </div>
                </div>
            </div>
        );
    }

    if (!order) return null;

    const canCancel = ['PENDING', 'PAYMENT_LINK_SENT'].includes(order.status);
    const canRenewPayment = ['PENDING', 'PAYMENT_LINK_SENT', 'EXPIRED'].includes(order.status);
    const isPaid = order.status === 'PAID' || order.status === 'CONFIRMED';
    const lang = i18n.language;

    return (
        <div className="bg-white min-h-screen pt-24 pb-12">
            <div className="max-w-2xl mx-auto px-4 sm:px-6 lg:px-8">
                <h1 className="text-2xl font-bold text-gray-900">{t('booking_manage.title')}</h1>

                {/* Order Header */}
                <div className="mt-6 bg-gray-50 rounded-lg p-6 border border-gray-100">
                    <div className="flex items-center justify-between">
                        <div>
                            <p className="text-sm text-gray-500">{t('order.confirmation.reference')}</p>
                            <p className="text-xl font-mono font-bold text-gray-900">{order.reference}</p>
                        </div>
                        <span className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-medium ${STATUS_COLORS[order.status] || 'bg-gray-100 text-gray-800'}`}>
                            {t(`order.status.${order.status.toLowerCase()}`)}
                        </span>
                    </div>

                    <div className="mt-4 grid grid-cols-2 gap-4 text-sm">
                        <div>
                            <p className="text-gray-500">{t('checkout.name')}</p>
                            <p className="font-medium">{order.customer_name}</p>
                        </div>
                        <div>
                            <p className="text-gray-500">{t('checkout.email')}</p>
                            <p className="font-medium">{order.customer_email}</p>
                        </div>
                        <div>
                            <p className="text-gray-500">{t('checkout.phone')}</p>
                            <p className="font-medium">{order.customer_phone}</p>
                        </div>
                        <div>
                            <p className="text-gray-500">{t('checkout.payment_method')}</p>
                            <p className="font-medium">{order.payment_method || '-'}</p>
                        </div>
                    </div>
                </div>

                {/* Bookings */}
                <div className="mt-8">
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

                                <div className="mt-3 grid grid-cols-2 gap-2 text-sm">
                                    <div>
                                        <span className="text-gray-500">{t('booking.modal.participants')}:</span>{' '}
                                        <span className="font-medium">{booking.total_participants}</span>
                                    </div>
                                    {booking.hotel_name && (
                                        <div>
                                            <span className="text-gray-500">{t('booking.modal.hotel')}:</span>{' '}
                                            <span className="font-medium">{booking.hotel_name}</span>
                                        </div>
                                    )}
                                </div>

                                {/* Price Breakdown */}
                                <div className="mt-3 pt-3 border-t border-gray-100 text-sm space-y-1">
                                    <div className="flex justify-between">
                                        <span className="text-gray-500">{t('booking.modal.guide_service')}</span>
                                        <span>${Number(booking.service_total).toFixed(2)}</span>
                                    </div>
                                    {Number(booking.senior_discount) > 0 && (
                                        <div className="flex justify-between text-indigo-600">
                                            <span>{t('booking.modal.senior_discount')}</span>
                                            <span>-${Number(booking.senior_discount).toFixed(2)}</span>
                                        </div>
                                    )}
                                    {Number(booking.tickets_total) > 0 && (
                                        <div className="flex justify-between">
                                            <span className="text-gray-500">{t('booking.modal.tickets')}</span>
                                            <span>${Number(booking.tickets_total).toFixed(2)}</span>
                                        </div>
                                    )}
                                    {Number(booking.transport_total) > 0 && (
                                        <div className="flex justify-between">
                                            <span className="text-gray-500">{t('booking.modal.transport')}</span>
                                            <span>${Number(booking.transport_total).toFixed(2)}</span>
                                        </div>
                                    )}
                                    <div className="flex justify-between font-semibold pt-1 border-t border-gray-100">
                                        <span>{t('booking.modal.total')}</span>
                                        <span>${Number(booking.grand_total).toFixed(2)}</span>
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>

                {/* Order Total */}
                <div className="mt-6 bg-gray-50 rounded-lg p-4 border border-gray-100">
                    <div className="flex justify-between items-center text-lg font-bold">
                        <span>{t('booking.modal.total')}</span>
                        <span>${Number(order.grand_total).toFixed(2)}</span>
                    </div>
                </div>

                {/* Actions */}
                {(canCancel || canRenewPayment) && (
                    <div className="mt-8 flex flex-col sm:flex-row gap-3">
                        {canRenewPayment && (
                            <Link
                                to={`/booking/renew-payment?reference=${reference}#token=${token}`}
                                className="flex-1 text-center bg-indigo-600 text-white px-4 py-3 rounded-lg hover:bg-indigo-700 font-medium"
                            >
                                {t('booking_manage.renew_payment')}
                            </Link>
                        )}
                        {canCancel && (
                            <Link
                                to={`/booking/cancel?reference=${reference}#token=${token}`}
                                className="flex-1 text-center bg-white text-red-600 border border-red-300 px-4 py-3 rounded-lg hover:bg-red-50 font-medium"
                            >
                                {t('booking_manage.cancel')}
                            </Link>
                        )}
                    </div>
                )}

                {isPaid && (
                    <div className="mt-6 bg-green-50 rounded-lg p-4 border border-green-200 text-center">
                        <p className="text-green-800 font-medium">
                            {t('order.confirmation.step_3')}
                        </p>
                    </div>
                )}

                <div className="mt-8 text-center">
                    <Link to="/" className="text-indigo-600 hover:text-indigo-500 font-medium">
                        &larr; {t('order.confirmation.return_home')}
                    </Link>
                </div>
            </div>
        </div>
    );
}
