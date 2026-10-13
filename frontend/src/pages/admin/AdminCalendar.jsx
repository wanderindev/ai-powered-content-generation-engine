import { useState, useEffect, useMemo, Fragment } from 'react';
import { Dialog, DialogPanel, DialogTitle, Transition, TransitionChild } from '@headlessui/react';
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  XMarkIcon,
  CalendarDaysIcon,
  TruckIcon,
  ExclamationTriangleIcon,
} from '@heroicons/react/24/outline';
import { useAdminAuth } from '../../contexts/AdminAuthContext';
import { bookingService } from '../../services/booking';
import { ToastContainer } from '../../components/admin/Toast';
import { useToast } from '../../hooks/useToast';

// ─── Helpers ────────────────────────────────────────────────────────────────

const STATUS_COLORS = {
  PENDING_PAYMENT: 'bg-yellow-100 border-yellow-300 text-yellow-800',
  PAYMENT_LINK_SENT: 'bg-indigo-100 border-indigo-300 text-indigo-800',
  PAID: 'bg-green-100 border-green-300 text-green-800',
  CONFIRMED: 'bg-green-100 border-green-300 text-green-800',
  EXPIRED: 'bg-gray-100 border-gray-300 text-gray-500',
};

const STATUS_BADGE = {
  PENDING: 'text-yellow-700 bg-yellow-50 ring-yellow-600/20',
  PENDING_PAYMENT: 'text-yellow-700 bg-yellow-50 ring-yellow-600/20',
  PAYMENT_LINK_SENT: 'text-indigo-700 bg-indigo-50 ring-indigo-600/20',
  PAID: 'text-green-700 bg-green-50 ring-green-600/20',
  CONFIRMED: 'text-green-700 bg-green-50 ring-green-600/20',
  CANCELLED: 'text-red-700 bg-red-50 ring-red-600/20',
  EXPIRED: 'text-gray-600 bg-gray-50 ring-gray-500/10',
};

function StatusBadge({ value }) {
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset ${STATUS_BADGE[value] || 'text-gray-600 bg-gray-50 ring-gray-500/10'}`}>
      {value?.replace(/_/g, ' ')}
    </span>
  );
}

function getDaysInMonth(year, month) {
  return new Date(year, month + 1, 0).getDate();
}

function getFirstDayOfMonth(year, month) {
  return new Date(year, month, 1).getDay();
}

function formatDate(year, month, day) {
  return `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

function isoToDisplay(iso) {
  if (!iso) return '';
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}

// ─── Booking Card (inside calendar cell) ────────────────────────────────────

function BookingCard({ booking, onClick }) {
  const colors = STATUS_COLORS[booking.status] || 'bg-gray-100 border-gray-300 text-gray-700';
  return (
    <button
      onClick={() => onClick(booking)}
      className={`w-full rounded border px-1.5 py-1 text-left text-xs leading-tight ${colors} hover:opacity-80 transition-opacity`}
    >
      <div className="font-medium truncate">{booking.tour_name}</div>
      <div className="flex items-center gap-1 text-[10px] opacity-75">
        <span>{booking.customer_name.split(' ')[0]}</span>
        <span>&middot;</span>
        <span>{booking.total_participants}p</span>
        {booking.needs_transport && <TruckIcon className="h-3 w-3" />}
      </div>
    </button>
  );
}

// ─── Calendar Grid ──────────────────────────────────────────────────────────

function CalendarGrid({ year, month, bookingMap, onBookingClick }) {
  const daysInMonth = getDaysInMonth(year, month);
  const firstDay = getFirstDayOfMonth(year, month);
  const today = new Date();
  const todayStr = formatDate(today.getFullYear(), today.getMonth(), today.getDate());

  const weeks = [];
  let currentWeek = new Array(firstDay).fill(null);

  for (let day = 1; day <= daysInMonth; day++) {
    currentWeek.push(day);
    if (currentWeek.length === 7) {
      weeks.push(currentWeek);
      currentWeek = [];
    }
  }
  if (currentWeek.length > 0) {
    while (currentWeek.length < 7) currentWeek.push(null);
    weeks.push(currentWeek);
  }

  return (
    <div className="border rounded-lg overflow-hidden">
      {/* Header */}
      <div className="grid grid-cols-7 bg-gray-50 border-b">
        {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((d) => (
          <div key={d} className="px-2 py-2 text-xs font-medium text-gray-500 text-center">
            {d}
          </div>
        ))}
      </div>

      {/* Weeks */}
      {weeks.map((week, wi) => (
        <div key={wi} className="grid grid-cols-7 border-b last:border-b-0">
          {week.map((day, di) => {
            if (!day) {
              return <div key={di} className="min-h-[100px] bg-gray-50 border-r last:border-r-0" />;
            }

            const dateStr = formatDate(year, month, day);
            const isToday = dateStr === todayStr;
            const dayBookings = bookingMap.get(dateStr) || { AM: [], PM: [] };
            const isPast = new Date(dateStr) < new Date(todayStr);

            return (
              <div
                key={di}
                className={`min-h-[100px] border-r last:border-r-0 ${isPast ? 'bg-gray-50/50' : 'bg-white'}`}
              >
                {/* Day number */}
                <div className="px-2 pt-1 flex justify-between items-center">
                  <span className={`text-xs font-medium ${isToday ? 'bg-indigo-600 text-white rounded-full w-6 h-6 flex items-center justify-center' : isPast ? 'text-gray-400' : 'text-gray-700'}`}>
                    {day}
                  </span>
                </div>

                {/* AM / PM blocks */}
                <div className="px-1 pb-1 space-y-0.5 mt-0.5">
                  {['AM', 'PM'].map((slot) => (
                    <div key={slot}>
                      {dayBookings[slot].length > 0 ? (
                        <div className="space-y-0.5">
                          <div className="text-[9px] font-semibold text-gray-400 uppercase px-0.5">{slot}</div>
                          {dayBookings[slot].map((b) => (
                            <BookingCard key={b.booking_reference} booking={b} onClick={onBookingClick} />
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

// ─── Booking Detail Modal ───────────────────────────────────────────────────

function BookingDetailModal({ booking, onClose, onUpdated }) {
  const { token } = useAdminAuth();
  const [order, setOrder] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [actionLoading, setActionLoading] = useState(null);
  const [invoicePreview, setInvoicePreview] = useState(null);

  // Cancel state
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  const [cancelReason, setCancelReason] = useState('');

  // Reschedule state
  const [showReschedule, setShowReschedule] = useState(false);
  const [newDate, setNewDate] = useState('');
  const [newSlot, setNewSlot] = useState('AM');
  const [newPreferredTime, setNewPreferredTime] = useState('');
  const [rescheduleReason, setRescheduleReason] = useState('');

  // Payment link paste state (for CUANTO)
  const [paymentLink, setPaymentLink] = useState('');

  useEffect(() => {
    if (!booking) return;
    loadOrder();
  }, [booking]);

  const loadOrder = () => {
    setLoading(true);
    setError(null);
    bookingService.getAdminOrderDetail(booking.order_reference, token)
      .then(setOrder)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  const handleAction = async (action) => {
    setActionLoading(action);
    setError(null);
    try {
      if (action === 'create-invoice') {
        const preview = await bookingService.createInvoice(booking.order_reference, token);
        setInvoicePreview(preview);
        loadOrder();
      } else if (action === 'send-invoice') {
        await bookingService.sendInvoice(booking.order_reference, token);
        setInvoicePreview(null);
        loadOrder();
        onUpdated();
      } else if (action === 'cancel-invoice') {
        await bookingService.cancelInvoice(booking.order_reference, token);
        setInvoicePreview(null);
        loadOrder();
        onUpdated();
      } else if (action === 'mark-paid') {
        await bookingService.markPaid(booking.order_reference, token, 'Manually marked as paid');
        loadOrder();
        onUpdated();
      } else if (action === 'mark-payment-sent') {
        await bookingService.markPaymentSent(booking.order_reference, token, paymentLink ? `Payment link: ${paymentLink}` : null);
        setPaymentLink('');
        loadOrder();
        onUpdated();
      } else if (action === 'cancel-order') {
        await bookingService.adminCancelOrder(booking.order_reference, token, cancelReason || null);
        setShowCancelConfirm(false);
        loadOrder();
        onUpdated();
      } else if (action === 'reschedule') {
        await bookingService.rescheduleBooking(booking.booking_reference, token, {
          new_date: newDate,
          new_time_slot: newSlot,
          new_preferred_time: newPreferredTime || null,
          reason: rescheduleReason || null,
          notify_customer: true,
        });
        setShowReschedule(false);
        loadOrder();
        onUpdated();
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setActionLoading(null);
    }
  };

  const currentBooking = order?.bookings?.find((b) => b.reference === booking.booking_reference);
  const isPayPal = order?.payment_method === 'PAYPAL';
  const isCuanto = order?.payment_method === 'CUANTO';
  const isPending = order?.status === 'PENDING';
  const isPaymentSent = order?.status === 'PAYMENT_LINK_SENT';
  const isPaid = order?.status === 'PAID';
  const isCancelled = order?.status === 'CANCELLED';
  const hasDraft = !!order?.paypal_invoice_id && isPending;

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="border-b px-6 py-4">
        <div className="flex items-center justify-between">
          <DialogTitle className="text-lg font-semibold text-gray-900">
            {booking.tour_name}
          </DialogTitle>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <XMarkIcon className="h-5 w-5" />
          </button>
        </div>
        <div className="mt-1 flex items-center gap-2 text-sm text-gray-500">
          <CalendarDaysIcon className="h-4 w-4" />
          {isoToDisplay(booking.date)} &middot; {booking.time_slot}
          {currentBooking?.preferred_time && ` (${currentBooking.preferred_time})`}
        </div>
        <div className="mt-2 flex items-center gap-2">
          <StatusBadge value={booking.order_status} />
          {order?.payment_method && (
            <span className="text-xs text-gray-500">{order.payment_method}</span>
          )}
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-5">
        {error && (
          <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</div>
        )}

        {loading ? (
          <div className="text-gray-500 text-sm">Loading...</div>
        ) : order ? (
          <>
            {/* Customer */}
            <section>
              <h3 className="text-sm font-medium text-gray-500 mb-2">Customer</h3>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                <dt className="text-gray-500">Name</dt>
                <dd className="text-gray-900">{order.customer_name}</dd>
                <dt className="text-gray-500">Email</dt>
                <dd className="text-gray-900">{order.customer_email}</dd>
                <dt className="text-gray-500">Phone</dt>
                <dd className="text-gray-900">{order.customer_phone}</dd>
                {order.customer_hotel && (
                  <>
                    <dt className="text-gray-500">Hotel</dt>
                    <dd className="text-gray-900">{order.customer_hotel}</dd>
                  </>
                )}
                <dt className="text-gray-500">Language</dt>
                <dd className="text-gray-900">{order.customer_language?.toUpperCase()}</dd>
              </dl>
            </section>

            {/* Booking details */}
            {currentBooking && (
              <section>
                <h3 className="text-sm font-medium text-gray-500 mb-2">Booking Details</h3>
                <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                  <dt className="text-gray-500">Reference</dt>
                  <dd className="font-mono text-gray-900">{currentBooking.reference}</dd>
                  <dt className="text-gray-500">Participants</dt>
                  <dd className="text-gray-900">{currentBooking.total_participants} ({currentBooking.total_residents}R / {currentBooking.total_tourists}T)</dd>
                  <dt className="text-gray-500">Transport</dt>
                  <dd className="text-gray-900">
                    {currentBooking.needs_transport ? (
                      <span>{currentBooking.transport_direction} {currentBooking.hotel_name && `(${currentBooking.hotel_name})`}</span>
                    ) : 'No'}
                  </dd>
                  <dt className="text-gray-500">Subtotal</dt>
                  <dd className="text-gray-900">${currentBooking.grand_total}</dd>
                </dl>
              </section>
            )}

            {/* Order total */}
            <section>
              <h3 className="text-sm font-medium text-gray-500 mb-2">Order ({order.reference})</h3>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                <dt className="text-gray-500">Total</dt>
                <dd className="font-semibold text-gray-900">${order.grand_total}</dd>
                {order.bookings.length > 1 && (
                  <>
                    <dt className="text-gray-500">Bookings</dt>
                    <dd className="text-gray-900">{order.bookings.length} in this order</dd>
                  </>
                )}
                {order.paypal_invoice_url && (
                  <>
                    <dt className="text-gray-500">Invoice</dt>
                    <dd>
                      <a href={order.paypal_invoice_url} target="_blank" rel="noopener noreferrer"
                         className="text-indigo-600 hover:underline text-xs">View Invoice</a>
                    </dd>
                  </>
                )}
                {order.paid_at && (
                  <>
                    <dt className="text-gray-500">Paid At</dt>
                    <dd className="text-green-700 font-medium">{new Date(order.paid_at).toLocaleString()}</dd>
                  </>
                )}
              </dl>
            </section>

            {/* Invoice preview */}
            {invoicePreview && (
              <section className="rounded-lg border-2 border-indigo-200 bg-indigo-50 p-4">
                <h3 className="text-sm font-medium text-indigo-800 mb-2">Invoice Draft Preview</h3>
                <dl className="text-sm space-y-1">
                  <div className="flex justify-between">
                    <dt className="text-indigo-600">Invoice ID</dt>
                    <dd className="font-mono text-indigo-900">{invoicePreview.invoice_id}</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-indigo-600">Due Date</dt>
                    <dd className="text-indigo-900">{invoicePreview.due_date}</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-indigo-600">Total</dt>
                    <dd className="font-semibold text-indigo-900">${invoicePreview.total}</dd>
                  </div>
                </dl>
              </section>
            )}

            {/* CUANTO payment link input */}
            {isCuanto && isPending && (
              <section>
                <h3 className="text-sm font-medium text-gray-500 mb-2">Payment Link</h3>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={paymentLink}
                    onChange={(e) => setPaymentLink(e.target.value)}
                    placeholder="Paste payment link here..."
                    className="flex-1 rounded-md border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
                  />
                  <button
                    onClick={() => handleAction('mark-payment-sent')}
                    disabled={!!actionLoading}
                    className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50 whitespace-nowrap"
                  >
                    {actionLoading === 'mark-payment-sent' ? 'Sending...' : 'Mark Sent'}
                  </button>
                </div>
              </section>
            )}

            {/* Reschedule form */}
            {showReschedule && (
              <section className="rounded-lg border-2 border-blue-200 bg-blue-50 p-4 space-y-3">
                <h3 className="text-sm font-medium text-blue-800">Reschedule Booking</h3>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-blue-700 mb-1">New Date</label>
                    <input
                      type="date"
                      value={newDate}
                      onChange={(e) => setNewDate(e.target.value)}
                      className="w-full rounded-md border-gray-300 px-3 py-2 text-sm"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-blue-700 mb-1">Time Slot</label>
                    <select
                      value={newSlot}
                      onChange={(e) => setNewSlot(e.target.value)}
                      className="w-full rounded-md border-gray-300 px-3 py-2 text-sm"
                    >
                      <option value="AM">AM</option>
                      <option value="PM">PM</option>
                    </select>
                  </div>
                </div>
                <div>
                  <label className="block text-xs font-medium text-blue-700 mb-1">Preferred Time (optional)</label>
                  <input
                    type="text"
                    value={newPreferredTime}
                    onChange={(e) => setNewPreferredTime(e.target.value)}
                    placeholder="e.g. 9:00 AM"
                    className="w-full rounded-md border-gray-300 px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-blue-700 mb-1">Reason (sent to customer)</label>
                  <input
                    type="text"
                    value={rescheduleReason}
                    onChange={(e) => setRescheduleReason(e.target.value)}
                    placeholder="e.g. as requested by customer"
                    className="w-full rounded-md border-gray-300 px-3 py-2 text-sm"
                  />
                </div>
                {isPayPal && !isPaid && order?.paypal_invoice_id && (
                  <div className="flex items-start gap-2 rounded-md bg-yellow-50 p-2 text-xs text-yellow-700">
                    <ExclamationTriangleIcon className="h-4 w-4 shrink-0 mt-0.5" />
                    <span>The existing PayPal invoice will be cancelled and a new one created with the updated date.</span>
                  </div>
                )}
                <div className="flex gap-2">
                  <button
                    onClick={() => handleAction('reschedule')}
                    disabled={!newDate || !!actionLoading}
                    className="rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-500 disabled:opacity-50"
                  >
                    {actionLoading === 'reschedule' ? 'Rescheduling...' : 'Confirm Reschedule'}
                  </button>
                  <button
                    onClick={() => setShowReschedule(false)}
                    className="rounded-md bg-gray-100 px-4 py-2 text-sm text-gray-700 hover:bg-gray-200"
                  >
                    Cancel
                  </button>
                </div>
              </section>
            )}

            {/* Cancel confirmation */}
            {showCancelConfirm && (
              <section className="rounded-lg border-2 border-red-200 bg-red-50 p-4 space-y-3">
                <h3 className="text-sm font-medium text-red-800">Cancel Order</h3>
                <p className="text-xs text-red-600">This will cancel the order and all its bookings.</p>
                <div>
                  <label className="block text-xs font-medium text-red-700 mb-1">Reason (optional)</label>
                  <input
                    type="text"
                    value={cancelReason}
                    onChange={(e) => setCancelReason(e.target.value)}
                    placeholder="Reason for cancellation..."
                    className="w-full rounded-md border-gray-300 px-3 py-2 text-sm"
                  />
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => handleAction('cancel-order')}
                    disabled={!!actionLoading}
                    className="rounded-md bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-500 disabled:opacity-50"
                  >
                    {actionLoading === 'cancel-order' ? 'Cancelling...' : 'Confirm Cancel'}
                  </button>
                  <button
                    onClick={() => setShowCancelConfirm(false)}
                    className="rounded-md bg-gray-100 px-4 py-2 text-sm text-gray-700 hover:bg-gray-200"
                  >
                    Go Back
                  </button>
                </div>
              </section>
            )}
          </>
        ) : null}
      </div>

      {/* Actions footer */}
      {order && !isCancelled && !showReschedule && !showCancelConfirm && (
        <div className="border-t px-6 py-4 flex flex-wrap gap-2">
          {/* PayPal invoice actions */}
          {isPayPal && isPending && !hasDraft && (
            <button
              onClick={() => handleAction('create-invoice')}
              disabled={!!actionLoading}
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
            >
              {actionLoading === 'create-invoice' ? 'Creating...' : 'Create Invoice'}
            </button>
          )}
          {isPayPal && isPending && hasDraft && (
            <>
              <button
                onClick={() => handleAction('send-invoice')}
                disabled={!!actionLoading}
                className="rounded-md bg-green-600 px-4 py-2 text-sm font-semibold text-white hover:bg-green-500 disabled:opacity-50"
              >
                {actionLoading === 'send-invoice' ? 'Sending...' : 'Send Invoice'}
              </button>
              <button
                onClick={() => handleAction('cancel-invoice')}
                disabled={!!actionLoading}
                className="rounded-md bg-red-100 px-4 py-2 text-sm font-semibold text-red-700 hover:bg-red-200 disabled:opacity-50"
              >
                Cancel Invoice
              </button>
            </>
          )}
          {isPayPal && isPaymentSent && (
            <button
              onClick={() => handleAction('cancel-invoice')}
              disabled={!!actionLoading}
              className="rounded-md bg-red-100 px-4 py-2 text-sm font-semibold text-red-700 hover:bg-red-200 disabled:opacity-50"
            >
              Cancel Invoice
            </button>
          )}

          {/* Mark paid (any unpaid) */}
          {!isPaid && (
            <button
              onClick={() => handleAction('mark-paid')}
              disabled={!!actionLoading}
              className="rounded-md bg-gray-100 px-4 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-200 disabled:opacity-50"
            >
              {actionLoading === 'mark-paid' ? 'Marking...' : 'Mark Paid'}
            </button>
          )}

          {/* Reschedule */}
          <button
            onClick={() => {
              setNewDate(booking.date);
              setNewSlot(booking.time_slot);
              setShowReschedule(true);
            }}
            className="rounded-md bg-blue-100 px-4 py-2 text-sm font-semibold text-blue-700 hover:bg-blue-200"
          >
            Reschedule
          </button>

          {/* Cancel */}
          <button
            onClick={() => setShowCancelConfirm(true)}
            className="rounded-md bg-red-50 px-4 py-2 text-sm font-semibold text-red-600 hover:bg-red-100"
          >
            Cancel Order
          </button>
        </div>
      )}
    </div>
  );
}

// ─── Main Page ──────────────────────────────────────────────────────────────

export default function AdminCalendar() {
  const { token } = useAdminAuth();
  const [currentDate, setCurrentDate] = useState(() => {
    const now = new Date();
    return { year: now.getFullYear(), month: now.getMonth() };
  });
  const [bookings, setBookings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedBooking, setSelectedBooking] = useState(null);
  const { toasts, show: showToast, dismiss: dismissToast } = useToast();

  const { year, month } = currentDate;
  const monthLabel = new Date(year, month).toLocaleString('default', { month: 'long', year: 'numeric' });

  // Build date -> { AM: [], PM: [] } map
  const bookingMap = useMemo(() => {
    const map = new Map();
    for (const b of bookings) {
      if (!map.has(b.date)) {
        map.set(b.date, { AM: [], PM: [] });
      }
      map.get(b.date)[b.time_slot]?.push(b);
    }
    return map;
  }, [bookings]);

  const fetchBookings = () => {
    const startDate = formatDate(year, month, 1);
    const endDate = formatDate(year, month, getDaysInMonth(year, month));
    setLoading(true);
    bookingService.getCalendarBookings(token, startDate, endDate)
      .then(setBookings)
      .catch((err) => {
        console.error('Failed to load bookings:', err);
        showToast({ type: 'error', title: 'Failed to load bookings', message: err.message || 'Unexpected error' });
      })
      .finally(() => setLoading(false));
  };

  // Async IIFE so fetchBookings' synchronous setLoading(true) isn't in the
  // effect's sync body (set-state-in-effect).
  useEffect(() => { (async () => { fetchBookings(); })(); }, [year, month, token]);

  const navigate = (delta) => {
    setCurrentDate((prev) => {
      let m = prev.month + delta;
      let y = prev.year;
      if (m < 0) { m = 11; y--; }
      if (m > 11) { m = 0; y++; }
      return { year: y, month: m };
    });
  };

  const goToday = () => {
    const now = new Date();
    setCurrentDate({ year: now.getFullYear(), month: now.getMonth() });
  };

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Bookings Calendar</h1>
          <p className="mt-1 text-sm text-gray-500">
            {bookings.length} active booking{bookings.length !== 1 ? 's' : ''} this month
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={goToday}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
          >
            Today
          </button>
          <button
            onClick={() => navigate(-1)}
            className="rounded-md border border-gray-300 p-1.5 hover:bg-gray-50"
          >
            <ChevronLeftIcon className="h-4 w-4" />
          </button>
          <span className="text-sm font-semibold text-gray-900 min-w-[140px] text-center">
            {monthLabel}
          </span>
          <button
            onClick={() => navigate(1)}
            className="rounded-md border border-gray-300 p-1.5 hover:bg-gray-50"
          >
            <ChevronRightIcon className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Calendar */}
      {loading ? (
        <div className="text-gray-500 text-sm py-12 text-center">Loading calendar...</div>
      ) : (
        <CalendarGrid
          year={year}
          month={month}
          bookingMap={bookingMap}
          onBookingClick={setSelectedBooking}
        />
      )}

      {/* Booking detail slide-over */}
      <Transition appear show={!!selectedBooking} as={Fragment}>
        <Dialog as="div" className="relative z-50" onClose={() => setSelectedBooking(null)}>
          <TransitionChild
            as={Fragment}
            enter="ease-out duration-200" enterFrom="opacity-0" enterTo="opacity-100"
            leave="ease-in duration-150" leaveFrom="opacity-100" leaveTo="opacity-0"
          >
            <div className="fixed inset-0 bg-gray-900/40" />
          </TransitionChild>

          <div className="fixed inset-0 overflow-hidden">
            <div className="absolute inset-0 overflow-hidden">
              <div className="pointer-events-none fixed inset-y-0 right-0 flex max-w-full pl-10">
                <TransitionChild
                  as={Fragment}
                  enter="transform transition ease-in-out duration-300" enterFrom="translate-x-full" enterTo="translate-x-0"
                  leave="transform transition ease-in-out duration-200" leaveFrom="translate-x-0" leaveTo="translate-x-full"
                >
                  <DialogPanel className="pointer-events-auto w-screen max-w-lg">
                    <div className="flex h-full flex-col bg-white shadow-xl">
                      {selectedBooking && (
                        <BookingDetailModal
                          booking={selectedBooking}
                          onClose={() => setSelectedBooking(null)}
                          onUpdated={fetchBookings}
                        />
                      )}
                    </div>
                  </DialogPanel>
                </TransitionChild>
              </div>
            </div>
          </div>
        </Dialog>
      </Transition>

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}