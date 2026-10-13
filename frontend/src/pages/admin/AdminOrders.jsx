import { useState, useEffect, useMemo, useRef, Fragment } from 'react';
import { AllCommunityModule, themeAlpine } from 'ag-grid-community';
import { AgGridProvider, AgGridReact } from 'ag-grid-react';
import { Dialog, DialogPanel, DialogTitle, Transition, TransitionChild } from '@headlessui/react';
import { XMarkIcon } from '@heroicons/react/24/outline';
import { useAdminAuth } from '../../contexts/AdminAuthContext';
import { bookingService } from '../../services/booking';

const modules = [AllCommunityModule];

const StatusBadge = ({ value }) => {
  const colors = {
    PENDING: 'text-yellow-700 bg-yellow-50 ring-yellow-600/20',
    PAYMENT_LINK_SENT: 'text-indigo-700 bg-indigo-50 ring-indigo-600/20',
    PAID: 'text-green-700 bg-green-50 ring-green-600/20',
    CONFIRMED: 'text-indigo-700 bg-indigo-50 ring-indigo-600/20',
    CANCELLED: 'text-red-700 bg-red-50 ring-red-600/20',
    EXPIRED: 'text-gray-600 bg-gray-50 ring-gray-500/10',
  };
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset ${colors[value] || 'text-gray-600 bg-gray-50 ring-gray-500/10'}`}>
      {value}
    </span>
  );
};

function OrderDetailPanel({ reference, onClose, onOrderUpdated }) {
  const { token } = useAdminAuth();
  const [order, setOrder] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [actionLoading, setActionLoading] = useState(null);
  const [invoicePreview, setInvoicePreview] = useState(null);
  const [notes, setNotes] = useState('');

  useEffect(() => {
    if (!reference) return;
    setLoading(true);
    setError(null);
    setInvoicePreview(null);
    bookingService.getAdminOrderDetail(reference, token)
      .then((data) => {
        setOrder(data);
        setNotes(data.admin_notes || '');
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [reference, token]);

  const reload = () => {
    bookingService.getAdminOrderDetail(reference, token)
      .then((data) => {
        setOrder(data);
        setNotes(data.admin_notes || '');
        onOrderUpdated();
      });
  };

  const handleAction = async (action, label) => {
    // Confirmation gates for consequential actions (issue #121).
    // create-invoice is intentionally not gated — it produces a draft the
    // admin can still cancel before sending.
    let confirmPrompt = null;
    if (action === 'mark-paid') {
      confirmPrompt = 'Mark this order as paid? This action affects accounting state and is hard to reverse.';
    } else if (action === 'cancel-invoice') {
      confirmPrompt = "Cancel this invoice? The customer's existing PayPal invoice will be cancelled.";
    } else if (action === 'send-invoice') {
      confirmPrompt = `Send this invoice to ${order.customer_email}? This emails the customer.`;
    }
    if (confirmPrompt && !window.confirm(confirmPrompt)) {
      return;
    }

    setActionLoading(label);
    setError(null);
    try {
      if (action === 'create-invoice') {
        const preview = await bookingService.createInvoice(reference, token);
        setInvoicePreview(preview);
        reload();
      } else if (action === 'send-invoice') {
        await bookingService.sendInvoice(reference, token);
        setInvoicePreview(null);
        reload();
      } else if (action === 'cancel-invoice') {
        await bookingService.cancelInvoice(reference, token);
        setInvoicePreview(null);
        reload();
      } else if (action === 'mark-paid') {
        await bookingService.markPaid(reference, token, 'Manually marked as paid');
        reload();
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setActionLoading(null);
    }
  };

  const handleSaveNotes = async () => {
    setActionLoading('notes');
    try {
      await bookingService.updateOrderNotes(reference, token, notes);
      reload();
    } catch (err) {
      setError(err.message);
    } finally {
      setActionLoading(null);
    }
  };

  if (loading) {
    return <div className="p-6 text-gray-500">Loading order details...</div>;
  }

  if (!order) {
    return <div className="p-6 text-red-600">Order not found</div>;
  }

  const isPayPal = order.payment_method === 'PAYPAL';
  const isPending = order.status === 'PENDING';
  const isPaymentSent = order.status === 'PAYMENT_LINK_SENT';
  const isPaid = order.status === 'PAID';
  const hasDraft = !!order.paypal_invoice_id && isPending;

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="border-b px-6 py-4">
        <div className="flex items-center justify-between">
          <DialogTitle className="text-lg font-semibold text-gray-900">
            Order {order.reference}
          </DialogTitle>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <XMarkIcon className="h-5 w-5" />
          </button>
        </div>
        <div className="mt-1 flex items-center gap-3">
          <StatusBadge value={order.status} />
          {order.payment_method && (
            <span className="text-xs text-gray-500">{order.payment_method}</span>
          )}
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-6">
        {error && (
          <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</div>
        )}

        {/* Customer info */}
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

        {/* Bookings */}
        <section>
          <h3 className="text-sm font-medium text-gray-500 mb-2">Bookings</h3>
          <div className="space-y-3">
            {order.bookings.map((b) => (
              <div key={b.reference} className="rounded-lg border p-3 text-sm">
                <div className="flex justify-between">
                  <span className="font-medium text-gray-900">{b.tour_name}</span>
                  <span className="font-medium text-gray-900">${b.grand_total}</span>
                </div>
                <div className="mt-1 text-gray-500">
                  {new Date(b.tour_date).toLocaleDateString()} &middot; {b.time_slot}
                  {b.preferred_time && ` (${b.preferred_time})`}
                  &middot; {b.total_participants} guests
                </div>
                {b.needs_transport && (
                  <div className="mt-1 text-gray-400 text-xs">
                    Transport: {b.transport_direction} {b.hotel_name && `(${b.hotel_name})`}
                    {b.transport_quote_required && ' — quote required'}
                  </div>
                )}
              </div>
            ))}
          </div>
          <div className="mt-3 text-right text-sm font-semibold text-gray-900">
            Total: ${order.grand_total}
          </div>
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
                <dt className="text-indigo-600">To</dt>
                <dd className="text-indigo-900">{invoicePreview.customer_name} ({invoicePreview.customer_email})</dd>
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
            {invoicePreview.items.map((item, i) => (
              <div key={i} className="mt-2 rounded bg-white p-2 text-xs text-gray-700">
                <div className="font-medium">{item.name}</div>
                <div className="text-gray-500">{item.description}</div>
                <div className="text-right font-medium">${item.unit_amount.value}</div>
              </div>
            ))}
          </section>
        )}

        {/* Payment info */}
        {(order.paypal_invoice_id || order.paid_at) && (
          <section>
            <h3 className="text-sm font-medium text-gray-500 mb-2">Payment</h3>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
              {order.paypal_invoice_id && (
                <>
                  <dt className="text-gray-500">Invoice ID</dt>
                  <dd className="font-mono text-gray-900 text-xs">{order.paypal_invoice_id}</dd>
                </>
              )}
              {order.paypal_invoice_url && (
                <>
                  <dt className="text-gray-500">Payment Link</dt>
                  <dd>
                    <a href={order.paypal_invoice_url} target="_blank" rel="noopener noreferrer"
                       className="text-indigo-600 hover:underline text-xs">
                      View Invoice
                    </a>
                  </dd>
                </>
              )}
              {order.payment_link_sent_at && (
                <>
                  <dt className="text-gray-500">Link Sent</dt>
                  <dd className="text-gray-900">{new Date(order.payment_link_sent_at).toLocaleString()}</dd>
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
        )}

        {/* Dates */}
        <section>
          <h3 className="text-sm font-medium text-gray-500 mb-2">Dates</h3>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
            <dt className="text-gray-500">Created</dt>
            <dd className="text-gray-900">{new Date(order.created_at).toLocaleString()}</dd>
            {order.reservation_expires_at && (
              <>
                <dt className="text-gray-500">Reservation Expires</dt>
                <dd className="text-gray-900">{new Date(order.reservation_expires_at).toLocaleString()}</dd>
              </>
            )}
          </dl>
        </section>

        {/* Admin Notes */}
        <section>
          <h3 className="text-sm font-medium text-gray-500 mb-2">Admin Notes</h3>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            className="w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
            placeholder="Internal notes..."
          />
          <button
            onClick={handleSaveNotes}
            disabled={actionLoading === 'notes' || notes === (order.admin_notes || '')}
            className="mt-1 rounded bg-gray-100 px-3 py-1 text-xs text-gray-700 hover:bg-gray-200 disabled:opacity-50"
          >
            {actionLoading === 'notes' ? 'Saving...' : 'Save Notes'}
          </button>
        </section>
      </div>

      {/* Actions footer */}
      {!isPaid && order.status !== 'CANCELLED' && (
        <div className="border-t px-6 py-4 flex flex-wrap gap-2">
          {isPayPal && isPending && !hasDraft && (
            <button
              onClick={() => handleAction('create-invoice', 'Create Invoice')}
              disabled={!!actionLoading}
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
            >
              {actionLoading === 'Create Invoice' ? 'Creating...' : 'Create Invoice'}
            </button>
          )}
          {isPayPal && isPending && hasDraft && (
            <>
              <button
                onClick={() => handleAction('send-invoice', 'Send Invoice')}
                disabled={!!actionLoading}
                className="rounded-md bg-green-600 px-4 py-2 text-sm font-semibold text-white hover:bg-green-500 disabled:opacity-50"
              >
                {actionLoading === 'Send Invoice' ? 'Sending...' : 'Send Invoice'}
              </button>
              <button
                onClick={() => handleAction('cancel-invoice', 'Cancel Invoice')}
                disabled={!!actionLoading}
                className="rounded-md bg-red-100 px-4 py-2 text-sm font-semibold text-red-700 hover:bg-red-200 disabled:opacity-50"
              >
                {actionLoading === 'Cancel Invoice' ? 'Cancelling...' : 'Cancel Invoice'}
              </button>
            </>
          )}
          {isPayPal && isPaymentSent && (
            <button
              onClick={() => handleAction('cancel-invoice', 'Cancel Invoice')}
              disabled={!!actionLoading}
              className="rounded-md bg-red-100 px-4 py-2 text-sm font-semibold text-red-700 hover:bg-red-200 disabled:opacity-50"
            >
              {actionLoading === 'Cancel Invoice' ? 'Cancelling...' : 'Cancel Invoice'}
            </button>
          )}
          {(isPending || isPaymentSent) && (
            <button
              onClick={() => handleAction('mark-paid', 'Mark Paid')}
              disabled={!!actionLoading}
              className="rounded-md bg-gray-100 px-4 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-200 disabled:opacity-50"
            >
              {actionLoading === 'Mark Paid' ? 'Marking...' : 'Mark Paid Manually'}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default function AdminOrders() {
  const { token } = useAdminAuth();
  const gridRef = useRef(null);
  const [rowData, setRowData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedRef, setSelectedRef] = useState(null);

  const colDefs = useMemo(() => [
    { field: 'reference', headerName: 'Reference', width: 140, filter: true },
    { field: 'customer_name', headerName: 'Customer', flex: 1, minWidth: 150, filter: true },
    { field: 'customer_email', headerName: 'Email', flex: 1, minWidth: 180, filter: true },
    { field: 'status', headerName: 'Status', width: 160, filter: true, cellRenderer: StatusBadge },
    { field: 'payment_method', headerName: 'Payment', width: 100 },
    { field: 'grand_total', headerName: 'Total', width: 110, valueFormatter: ({ value }) => value != null ? `$${value}` : '' },
    { field: 'bookings_count', headerName: 'Bookings', width: 100 },
    { field: 'first_tour_date', headerName: 'Tour Date', width: 120, valueFormatter: ({ value }) => value ? new Date(value).toLocaleDateString() : '' },
    { field: 'created_at', headerName: 'Created', width: 120, valueFormatter: ({ value }) => value ? new Date(value).toLocaleDateString() : '' },
  ], []);

  const loadOrders = () => {
    bookingService.getAdminOrders(token)
      .then(setRowData)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { loadOrders(); }, [token]);

  if (error && !rowData.length) return <div className="text-red-600">Error: {error}</div>;

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Orders</h1>
          <p className="mt-2 text-sm text-gray-700">{rowData.length} total orders</p>
        </div>
        <button
          onClick={() => gridRef.current?.api?.setFilterModel(null)}
          className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
        >
          Clear Filters
        </button>
      </div>

      <AgGridProvider modules={modules}>
        <div className="mt-6" style={{ height: 'calc(100vh - 240px)', minHeight: 400 }}>
          <AgGridReact
            ref={gridRef}
            theme={themeAlpine}
            rowData={rowData}
            columnDefs={colDefs}
            loading={loading}
            pagination={true}
            paginationPageSize={25}
            paginationPageSizeSelector={[25, 50, 100]}
            defaultColDef={{ sortable: true, resizable: true }}
            onRowClicked={(e) => setSelectedRef(e.data.reference)}
            rowClass="cursor-pointer"
          />
        </div>
      </AgGridProvider>

      {/* Order detail slide-over */}
      <Transition appear show={!!selectedRef} as={Fragment}>
        <Dialog as="div" className="relative z-50" onClose={() => setSelectedRef(null)}>
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
                      {selectedRef && (
                        <OrderDetailPanel
                          reference={selectedRef}
                          onClose={() => setSelectedRef(null)}
                          onOrderUpdated={loadOrders}
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
    </div>
  );
}