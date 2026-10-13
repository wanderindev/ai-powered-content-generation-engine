import React from 'react';
import { ShoppingBagIcon } from '@heroicons/react/24/outline';
import { useTranslation } from 'react-i18next';
import { useCart } from '../../contexts/CartContext';

export default function CartIcon() {
    const { t } = useTranslation();
    const { itemCount, openCart } = useCart();

    return (
        <button
            className="group -m-2 flex items-center p-2"
            onClick={openCart}
        >
            <ShoppingBagIcon
                className="h-6 w-6 flex-shrink-0 text-gray-400 group-hover:text-gray-500"
                aria-hidden="true"
            />
            <span className="ml-2 text-sm font-medium text-gray-700 group-hover:text-gray-800">
                {itemCount}
            </span>
            <span className="sr-only">{t('cart.view_bag_sr')}</span>
        </button>
    );
}
