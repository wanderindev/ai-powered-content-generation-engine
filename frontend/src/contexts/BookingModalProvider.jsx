import { useState, useCallback } from 'react';
import BookingModal from '../components/booking/BookingModal';
import CartDrawer from '../components/cart/CartDrawer';
import { BookingModalContext } from './BookingModalContext';
import { useCart } from './CartContext';

export function BookingModalProvider({ children }) {
    // `key` is bumped on every open so BookingModal remounts fresh each time and
    // derives its form state from props in useState initializers — no reset
    // effect, which would otherwise setState synchronously (set-state-in-effect).
    const [modalState, setModalState] = useState({
        open: false,
        tour: null,
        editItem: null,
        key: 0,
    });

    const openBookingModal = useCallback(({ tour, editItem = null }) => {
        setModalState((prev) => ({ open: true, tour, editItem, key: prev.key + 1 }));
    }, []);

    const closeBookingModal = useCallback(() => {
        setModalState(prev => ({ ...prev, open: false }));
    }, []);

    // Single global cart drawer. Hosted here (rather than per CartIcon) so it can
    // be opened from anywhere via cart context and so CartDrawer's useBookingModal
    // (edit-from-cart) resolves against this provider.
    const { isCartOpen, closeCart } = useCart();

    return (
        <BookingModalContext.Provider value={{ openBookingModal, closeBookingModal }}>
            {children}
            <BookingModal
                key={modalState.key}
                open={modalState.open}
                onClose={closeBookingModal}
                tour={modalState.tour}
                editItem={modalState.editItem}
            />
            <CartDrawer open={isCartOpen} onClose={closeCart} />
        </BookingModalContext.Provider>
    );
}
