import React, { Fragment } from 'react';
import { Dialog, Transition } from '@headlessui/react';
import { XMarkIcon } from '@heroicons/react/24/outline';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { useCart } from '../../contexts/CartContext';
import { useBookingModal } from '../../contexts/BookingModalContext';

// Calculate total participants from participants object
function getParticipantCount(participants) {
    if (!participants) return 0;
    return Object.values(participants).reduce(
        (sum, cat) => sum + (cat.resident || 0) + (cat.tourist || 0),
        0
    );
}

export default function CartDrawer({ open, onClose }) {
    const { t } = useTranslation();
    const { cart, removeItem, total } = useCart();
    const { openBookingModal } = useBookingModal();
    const navigate = useNavigate();

    const handleCheckout = () => {
        onClose();
        navigate('/checkout');
    };

    const handleEdit = (item) => {
        onClose();
        // Small delay to let the drawer close before opening the modal
        setTimeout(() => {
            openBookingModal({ tour: item.tour, editItem: item });
        }, 300);
    };

    return (
        <Transition.Root show={open} as={Fragment}>
            <Dialog as="div" className="relative z-50" onClose={onClose}>
                <Transition.Child
                    as={Fragment}
                    enter="ease-in-out duration-500"
                    enterFrom="opacity-0"
                    enterTo="opacity-100"
                    leave="ease-in-out duration-500"
                    leaveFrom="opacity-100"
                    leaveTo="opacity-0"
                >
                    <div className="fixed inset-0 bg-gray-500 bg-opacity-75 transition-opacity" />
                </Transition.Child>

                <div className="fixed inset-0 overflow-hidden">
                    <div className="absolute inset-0 overflow-hidden">
                        <div className="pointer-events-none fixed inset-y-0 right-0 flex max-w-full pl-10">
                            <Transition.Child
                                as={Fragment}
                                enter="transform transition ease-in-out duration-500 sm:duration-700"
                                enterFrom="translate-x-full"
                                enterTo="translate-x-0"
                                leave="transform transition ease-in-out duration-500 sm:duration-700"
                                leaveFrom="translate-x-0"
                                leaveTo="translate-x-full"
                            >
                                <Dialog.Panel className="pointer-events-auto w-screen max-w-md">
                                    <div className="flex h-full flex-col overflow-y-scroll bg-white shadow-xl">
                                        <div className="flex-1 overflow-y-auto px-4 py-6 sm:px-6">
                                            <div className="flex items-start justify-between">
                                                <Dialog.Title className="text-lg font-medium text-gray-900">
                                                    {t('cart.title')}
                                                </Dialog.Title>
                                                <div className="ml-3 flex h-7 items-center">
                                                    <button
                                                        type="button"
                                                        className="relative -m-2 p-2 text-gray-400 hover:text-gray-500"
                                                        onClick={onClose}
                                                    >
                                                        <span className="absolute -inset-0.5" />
                                                        <span className="sr-only">{t('cart.close_panel')}</span>
                                                        <XMarkIcon className="h-6 w-6" aria-hidden="true" />
                                                    </button>
                                                </div>
                                            </div>

                                            <div className="mt-8">
                                                <div className="flow-root">
                                                    {cart.items.length === 0 ? (
                                                        <p className="text-center text-gray-500 py-10">{t('cart.empty')}</p>
                                                    ) : (
                                                        <ul role="list" className="-my-6 divide-y divide-gray-200">
                                                            {cart.items.map((item) => (
                                                                <li key={item.id} className="flex py-6">
                                                                    <div className="h-24 w-24 flex-shrink-0 overflow-hidden rounded-md border border-gray-200">
                                                                        <img
                                                                            src={item.tour.image}
                                                                            alt={item.tour.title}
                                                                            className="h-full w-full object-cover object-center"
                                                                        />
                                                                    </div>

                                                                    <div className="ml-4 flex flex-1 flex-col">
                                                                        <div>
                                                                            <div className="flex justify-between text-base font-medium text-gray-900">
                                                                                <h3>
                                                                                    <a href={`/tours/${item.tour.slug}`}>{item.tour.title}</a>
                                                                                </h3>
                                                                                <p className="ml-4">${parseFloat(item.pricing.grand_total || 0).toFixed(2)}</p>
                                                                            </div>
                                                                            <p className="mt-1 text-sm text-gray-500">
                                                                                {item.bookingDetails.date ? new Date(item.bookingDetails.date).toLocaleDateString() : ''} - {t(`booking.modal.${item.bookingDetails.time}`)}
                                                                            </p>
                                                                        </div>
                                                                        <div className="flex flex-1 items-end justify-between text-sm">
                                                                            {(() => {
                                                                                const count = getParticipantCount(item.bookingDetails?.participants);
                                                                                return count > 0 && (
                                                                                    <p className="text-gray-500">
                                                                                        {count === 1
                                                                                            ? t('cart.participants_count', { count })
                                                                                            : t('cart.participants_count_plural', { count })}
                                                                                    </p>
                                                                                );
                                                                            })()}

                                                                            <div className="flex gap-3">
                                                                                <button
                                                                                    type="button"
                                                                                    className="font-medium text-indigo-600 hover:text-indigo-500"
                                                                                    onClick={() => handleEdit(item)}
                                                                                >
                                                                                    {t('cart.edit')}
                                                                                </button>
                                                                                <button
                                                                                    type="button"
                                                                                    className="font-medium text-indigo-600 hover:text-indigo-500"
                                                                                    onClick={() => removeItem(item.id)}
                                                                                >
                                                                                    {t('cart.remove')}
                                                                                </button>
                                                                            </div>
                                                                        </div>
                                                                    </div>
                                                                </li>
                                                            ))}
                                                        </ul>
                                                    )}
                                                </div>
                                            </div>
                                        </div>

                                        {cart.items.length > 0 && (
                                            <div className="border-t border-gray-200 px-4 py-6 sm:px-6">
                                                <div className="flex justify-between text-base font-medium text-gray-900">
                                                    <p>{t('cart.subtotal')}</p>
                                                    <p>${total.toFixed(2)}</p>
                                                </div>
                                                <div className="mt-6">
                                                    <button
                                                        onClick={handleCheckout}
                                                        className="flex w-full items-center justify-center rounded-md border border-transparent bg-indigo-600 px-6 py-3 text-base font-medium text-white shadow-sm hover:bg-indigo-700"
                                                    >
                                                        {t('cart.checkout')}
                                                    </button>
                                                </div>
                                                <div className="mt-6 flex justify-center text-center text-sm text-gray-500">
                                                    <p>
                                                        {t('cart.or')}{' '}
                                                        <button
                                                            type="button"
                                                            className="font-medium text-indigo-600 hover:text-indigo-500"
                                                            onClick={onClose}
                                                        >
                                                            {t('cart.continue_shopping')}
                                                            <span aria-hidden="true"> &rarr;</span>
                                                        </button>
                                                    </p>
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                </Dialog.Panel>
                            </Transition.Child>
                        </div>
                    </div>
                </div>
            </Dialog>
        </Transition.Root>
    );
}
