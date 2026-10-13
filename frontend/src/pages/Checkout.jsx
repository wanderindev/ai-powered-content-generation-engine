import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import * as Sentry from '@sentry/react';
import { useCart } from '../contexts/CartContext';
import { bookingService } from '../services/booking';
import { useNavigate } from 'react-router-dom';
import HotelAutocomplete from '../components/booking/HotelAutocomplete';

function CheckoutFallback() {
    const { t } = useTranslation();
    return (
        <div className="bg-gray-50 min-h-screen py-12">
            <div className="container mx-auto px-4 max-w-2xl text-center">
                <h1 className="text-2xl font-bold mb-4 text-gray-900">
                    {t('checkout.error_title', 'Something went wrong')}
                </h1>
                <p className="text-gray-600 mb-6">
                    {t('checkout.error_message', 'We encountered an unexpected error loading checkout. Your cart is saved — please try refreshing the page.')}
                </p>
                <button
                    onClick={() => window.location.reload()}
                    className="inline-flex items-center px-4 py-2 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700"
                >
                    {t('checkout.error_retry', 'Refresh page')}
                </button>
            </div>
        </div>
    );
}

function CheckoutInner() {
    const { t, i18n } = useTranslation();
    const { cart, total, clearCart } = useCart();
    const navigate = useNavigate();

    // Meta Pixel: track checkout initiation
    useEffect(() => {
        if (window.fbq) {
            window.fbq('track', 'InitiateCheckout', {
                num_items: cart.items.length,
                value: total,
                currency: 'USD',
            });
        }
    }, []);

    const [formData, setFormData] = useState({
        name: '',
        email: '',
        phone: '',
        hotel: ''
    });
    const [selectedHotel, setSelectedHotel] = useState(null);
    const [paymentMethod, setPaymentMethod] = useState('credit_card');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);

    // Validation states
    const [emailError, setEmailError] = useState('');
    const [phoneError, setPhoneError] = useState('');

    // Validation patterns
    const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    const phonePattern = /^[\d\s\-+()]{7,20}$/;

    // Prepopulate hotel from cart if transport was booked
    useEffect(() => {
        const itemWithHotel = cart.items.find(item =>
            item.bookingDetails?.transport?.hotel
        );
        if (itemWithHotel?.bookingDetails?.transport?.hotel) {
            setSelectedHotel(itemWithHotel.bookingDetails.transport.hotel);
        }
    }, [cart.items]);

    const handleEmailChange = (e) => {
        const value = e.target.value;
        setFormData({ ...formData, email: value });
        if (value && !emailPattern.test(value)) {
            setEmailError(t('checkout.invalid_email'));
        } else {
            setEmailError('');
        }
    };

    const handlePhoneChange = (e) => {
        const value = e.target.value;
        setFormData({ ...formData, phone: value });
        if (value && !phonePattern.test(value)) {
            setPhoneError(t('checkout.invalid_phone'));
        } else {
            setPhoneError('');
        }
    };

    const handleSubmit = async (e) => {
        e.preventDefault();

        // Validate before submit
        if (formData.email && !emailPattern.test(formData.email)) {
            setEmailError(t('checkout.invalid_email'));
            return;
        }
        if (formData.phone && !phonePattern.test(formData.phone)) {
            setPhoneError(t('checkout.invalid_phone'));
            return;
        }

        setLoading(true);
        setError(null);

        // Map frontend payment method to backend enum
        const paymentMethodMap = {
            credit_card: 'CUANTO',
            paypal: 'PAYPAL',
            yappy: 'YAPPY'
        };

        // Map frontend time to backend time_slot
        const timeSlotMap = {
            morning: 'AM',
            afternoon: 'PM'
        };

        // Transform camelCase participant keys to snake_case for the backend
        const transformParticipants = (p) => ({
            senior_female: p.senior_female || p.seniorFemale || { resident: 0, tourist: 0 },
            senior_male: p.senior_male || p.seniorMale || { resident: 0, tourist: 0 },
            adult: p.adult || { resident: 0, tourist: 0 },
            child_10_17: p.child_10_17 || p.child10to17 || { resident: 0, tourist: 0 },
            child_5_10: p.child_5_10 || p.child5to10 || { resident: 0, tourist: 0 },
            child_under_5: p.child_under_5 || p.childUnder5 || { resident: 0, tourist: 0 },
        });

        // Build bookings array in the format the backend expects
        const bookings = cart.items.map(item => {
            // Ensure date is in YYYY-MM-DD format (not a full datetime)
            const dateObj = new Date(item.bookingDetails.date);
            const tourDate = dateObj.toISOString().split('T')[0];

            const booking = {
                tour_slug: item.tour.slug,
                tour_date: tourDate,
                time_slot: timeSlotMap[item.bookingDetails.time] || item.bookingDetails.time,
                preferred_time: item.bookingDetails.specificTime || item.bookingDetails.preferredTime || null,
                participants: transformParticipants(item.bookingDetails.participants)
            };

            // Add transport if present
            if (item.bookingDetails.transport?.enabled) {
                const transport = item.bookingDetails.transport;
                // Map frontend direction to backend enum
                const directionMap = {
                    to_only: 'TO',
                    from_only: 'FROM',
                    both_ways: 'BOTH'
                };
                booking.transport = {
                    direction: directionMap[transport.direction] || transport.direction,
                    hotel_id: transport.hotel?.id || null,
                    hotel_name_manual: transport.hotel?.id ? null : transport.hotel?.name || null
                };
            }

            return booking;
        });

        const orderPayload = {
            customer: {
                name: formData.name,
                email: formData.email,
                phone: formData.phone,
                hotel: selectedHotel?.name || formData.hotel || null,
                language: i18n.language?.slice(0, 2) || 'es'
            },
            payment_method: paymentMethodMap[paymentMethod] || paymentMethod.toUpperCase(),
            bookings
        };

        try {
            const response = await bookingService.createOrder(orderPayload);
            // Success. Hand the order's magic-link token to the confirmation page
            // via router state (never the URL) so it can fetch + display the
            // booking and offer a "Manage" link without leaking the token into
            // logs/Referer/Sentry. Lost on refresh -> page falls back to the
            // static "check your email" view, which is the durable channel. (#36)
            clearCart();
            navigate(`/order-confirmation/${response.reference}`, {
                state: { token: response.magic_link_token },
            });
        } catch (err) {
            console.error(err);
            setError(err.message || 'Failed to create order');
        } finally {
            setLoading(false);
        }
    };

    if (cart.items.length === 0) {
        return (
            <div className="max-w-2xl mx-auto py-16 px-4 sm:py-24 sm:px-6 lg:max-w-7xl lg:px-8 text-center">
                <h2 className="text-2xl font-bold tracking-tight text-gray-900">{t('cart.empty')}</h2>
                <a href="/" className="mt-4 text-indigo-600 hover:text-indigo-500">{t('cart.continue_shopping')}</a>
            </div>
        );
    }

    return (
        <div className="bg-gray-50 min-h-screen py-12">
            <div className="container mx-auto px-4 max-w-2xl">
                <h1 className="text-3xl font-bold mb-8 text-gray-900">{t('checkout.title')}</h1>

                {error && (
                    <div className="mb-6 bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded">
                        {error}
                    </div>
                )}

                <form onSubmit={handleSubmit} className="space-y-8">
                    {/* Contact Info */}
                    <div className="bg-white p-6 rounded-lg shadow-sm border border-gray-200">
                        <h2 className="text-xl font-medium mb-6">{t('checkout.contact_info')}</h2>
                        <div className="grid grid-cols-1 gap-y-6">
                            <div>
                                <label className="block text-sm font-medium text-gray-700">{t('checkout.name')}</label>
                                <input
                                    type="text"
                                    required
                                    className="mt-1 block w-full rounded-md border-0 py-2.5 pl-3 pr-3 text-gray-900 ring-1 ring-inset ring-gray-300 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-indigo-600 sm:text-sm sm:leading-6"
                                    value={formData.name}
                                    onChange={e => setFormData({ ...formData, name: e.target.value })}
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-gray-700">{t('checkout.email')}</label>
                                <input
                                    type="email"
                                    required
                                    className={`mt-1 block w-full rounded-md border-0 py-2.5 pl-3 pr-3 text-gray-900 ring-1 ring-inset ${emailError ? 'ring-red-500' : 'ring-gray-300'} focus:outline-none focus:ring-2 focus:ring-inset focus:ring-indigo-600 sm:text-sm sm:leading-6`}
                                    value={formData.email}
                                    onChange={handleEmailChange}
                                />
                                {emailError && (
                                    <p className="mt-1 text-sm text-red-600">{emailError}</p>
                                )}
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-gray-700">{t('checkout.phone')}</label>
                                <input
                                    type="tel"
                                    required
                                    className={`mt-1 block w-full rounded-md border-0 py-2.5 pl-3 pr-3 text-gray-900 ring-1 ring-inset ${phoneError ? 'ring-red-500' : 'ring-gray-300'} focus:outline-none focus:ring-2 focus:ring-inset focus:ring-indigo-600 sm:text-sm sm:leading-6`}
                                    value={formData.phone}
                                    onChange={handlePhoneChange}
                                />
                                {phoneError && (
                                    <p className="mt-1 text-sm text-red-600">{phoneError}</p>
                                )}
                            </div>
                            <div>
                                <HotelAutocomplete
                                    selectedHotel={selectedHotel}
                                    onChange={setSelectedHotel}
                                />
                                <p className="mt-1 text-xs text-gray-500">{t('checkout.hotel_note')}</p>
                            </div>
                        </div>
                    </div>

                    {/* Payment Method */}
                    <div className="bg-white p-6 rounded-lg shadow-sm border border-gray-200">
                        <h2 className="text-xl font-medium mb-4">{t('checkout.payment_method')}</h2>
                        <div className="space-y-4">
                            <div className="flex items-start">
                                <input
                                    id="cc"
                                    name="payment"
                                    type="radio"
                                    checked={paymentMethod === 'credit_card'}
                                    onChange={() => setPaymentMethod('credit_card')}
                                    className="h-4 w-4 text-indigo-600 border-gray-300 focus:ring-indigo-500 mt-1"
                                />
                                <label htmlFor="cc" className="ml-3 block text-sm font-medium text-gray-700">
                                    {t('checkout.credit_card')}
                                    <span className="block text-gray-500 font-normal">{t('checkout.credit_card_desc')}</span>
                                </label>
                            </div>
                            <div className="flex items-start">
                                <input
                                    id="paypal"
                                    name="payment"
                                    type="radio"
                                    checked={paymentMethod === 'paypal'}
                                    onChange={() => setPaymentMethod('paypal')}
                                    className="h-4 w-4 text-indigo-600 border-gray-300 focus:ring-indigo-500 mt-1"
                                />
                                <label htmlFor="paypal" className="ml-3 block text-sm font-medium text-gray-700">
                                    {t('checkout.paypal')}
                                    <span className="block text-gray-500 font-normal">{t('checkout.paypal_desc')}</span>
                                </label>
                            </div>
                            <div className="flex items-start">
                                <input
                                    id="yappy"
                                    name="payment"
                                    type="radio"
                                    checked={paymentMethod === 'yappy'}
                                    onChange={() => setPaymentMethod('yappy')}
                                    className="h-4 w-4 text-indigo-600 border-gray-300 focus:ring-indigo-500 mt-1"
                                />
                                <label htmlFor="yappy" className="ml-3 block text-sm font-medium text-gray-700">
                                    {t('checkout.yappy')}
                                    <span className="block text-gray-500 font-normal">{t('checkout.yappy_desc')}</span>
                                </label>
                            </div>
                        </div>
                    </div>

                    {/* Order Summary */}
                    <div className="bg-white p-6 rounded-lg shadow-sm border border-gray-200">
                        <h2 className="text-xl font-medium mb-4">{t('checkout.order_summary')}</h2>

                        {/* Item list */}
                        <ul className="divide-y divide-gray-200 mb-4">
                            {cart.items.map((item) => (
                                <li key={item.id} className="py-3 flex justify-between">
                                    <div>
                                        <p className="font-medium text-gray-900">{item.tour.title}</p>
                                        <p className="text-sm text-gray-500">
                                            {new Date(item.bookingDetails.date).toLocaleDateString(i18n.language === 'es' ? 'es-PA' : 'en-US', {
                                                weekday: 'short',
                                                month: 'short',
                                                day: 'numeric'
                                            })} - {item.bookingDetails.specificTime || t(`booking.modal.${item.bookingDetails.time}`)}
                                        </p>
                                    </div>
                                    <span className="font-medium text-gray-900">
                                        ${parseFloat(item.pricing.grand_total).toFixed(2)}
                                    </span>
                                </li>
                            ))}
                        </ul>

                        {/* Total */}
                        <div className="flex justify-between text-lg font-bold pt-4 border-t border-gray-200">
                            <span>{t('booking.modal.total')}</span>
                            <span>${total.toFixed(2)}</span>
                        </div>
                    </div>

                    <button
                        type="submit"
                        disabled={loading}
                        className="w-full flex justify-center py-3 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 disabled:opacity-50"
                    >
                        {loading ? t('checkout.processing') : t('checkout.place_order')}
                    </button>
                    <p className="text-xs text-center text-gray-500">{t('checkout.terms_agreement')}</p>
                </form>
            </div>
        </div>
    );
}

const Checkout = Sentry.withErrorBoundary(CheckoutInner, {
    fallback: <CheckoutFallback />,
});

export default Checkout;
