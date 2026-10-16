import { useState, useEffect, useCallback } from 'react';
import { format } from 'date-fns';
import { CartContext } from './CartContext';

const EMPTY_CART = { items: [], createdAt: null };

function getStoredCart() {
    try {
        const saved = localStorage.getItem('pic_cart');
        if (!saved) return EMPTY_CART;
        return JSON.parse(saved);
    } catch {
        return EMPTY_CART;
    }
}

export function CartProvider({ children }) {
    const [cart, setCart] = useState(getStoredCart);

    // Drawer open state lives here so any component (e.g. the booking modal on
    // "add to cart") can expand the cart, and a single drawer instance renders
    // globally instead of one per CartIcon.
    const [isCartOpen, setIsCartOpen] = useState(false);
    const openCart = useCallback(() => setIsCartOpen(true), []);
    const closeCart = useCallback(() => setIsCartOpen(false), []);

    useEffect(() => {
        localStorage.setItem('pic_cart', JSON.stringify(cart));
    }, [cart]);

    const addItem = (item) => {
        setCart((prev) => {
            const newItem = {
                ...item,
                id: item.id || crypto.randomUUID(),
                addedAt: new Date().toISOString(),
            };
            return { ...prev, items: [...prev.items, newItem] };
        });
    };

    const removeItem = (itemId) => {
        setCart((prev) => ({
            ...prev,
            items: prev.items.filter((item) => item.id !== itemId),
        }));
    };

    const updateItem = (itemId, updates) => {
        setCart((prev) => ({
            ...prev,
            items: prev.items.map((item) =>
                item.id === itemId ? { ...item, ...updates } : item
            ),
        }));
    };

    const clearCart = () => {
        setCart({ items: [], createdAt: null });
    };

    // Returns { [dateStr]: Set<'morning'|'afternoon'> } from current cart items
    // Tours that allow multiple bookings per slot (open group tours)
    const openGroupTours = new Set(['siete-iglesias', 'panama-viejo-507']);

    const getBookedSlots = useCallback((excludeItemId = null) => {
        const slots = {};
        cart.items.forEach((item) => {
            if (excludeItemId && item.id === excludeItemId) return;
            // Open group tours don't block slots
            if (openGroupTours.has(item.tour?.slug)) return;
            const dateVal = item.bookingDetails?.date;
            const time = item.bookingDetails?.time;
            if (!dateVal || !time) return;

            // Cart stores Date objects that serialize to ISO strings in localStorage
            const dateStr = typeof dateVal === 'string'
                ? format(new Date(dateVal), 'yyyy-MM-dd')
                : format(dateVal, 'yyyy-MM-dd');

            if (!slots[dateStr]) {
                slots[dateStr] = new Set();
            }
            slots[dateStr].add(time);
        });
        return slots;
    }, [cart.items]);

    const itemCount = cart.items.length;

    const total = cart.items.reduce((sum, item) => {
        return sum + parseFloat(item.pricing?.grand_total || 0);
    }, 0);

    return (
        <CartContext.Provider value={{
            cart,
            addItem,
            removeItem,
            updateItem,
            clearCart,
            getBookedSlots,
            itemCount,
            total,
            isCartOpen,
            openCart,
            closeCart
        }}>
            {children}
        </CartContext.Provider>
    );
}
