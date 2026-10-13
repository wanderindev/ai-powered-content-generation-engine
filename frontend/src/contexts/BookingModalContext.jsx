import { createContext, useContext } from 'react';

export const BookingModalContext = createContext();

export function useBookingModal() {
    return useContext(BookingModalContext);
}
