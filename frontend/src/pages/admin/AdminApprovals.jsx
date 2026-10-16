import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useAdminAuth } from '../../contexts/AdminAuthContext';
import { adminService } from '../../services/admin';
import { ToastContainer } from '../../components/admin/Toast';
import { useToast } from '../../hooks/useToast';

// MCP approval gates (#642): an MCP agent can only *request* publishing,
// unpublishing or approving an article. Approve here runs the action through
// the same service as the Articles grid's publish (every precondition applies);
// a failed precondition is recorded as FAILED with its reason.

const statusColors = {
  PENDING: 'text-amber-700 bg-amber-50 ring-amber-600/20',
  EXECUTED: 'text-green-700 bg-green-50 ring-green-600/20',
  FAILED: 'text-red-700 bg-red-50 ring-red-600/20',
  REJECTED: 'text-gray-600 bg-gray-50 ring-gray-500/10',
};

const FILTERS = ['PENDING', ''];

function formatDate(value) {
  return value ? new Date(value).toLocaleString() : '';
}

function RequestRow({ item, busy, onDecide, t }) {
  const publishAt = item.params?.publish_at;
  return (
    <tr className="border-t border-gray-100 align-top">
      <td className="px-3 py-2 text-sm text-gray-400 tabular-nums">{item.request_id}</td>
      <td className="px-3 py-2 text-sm font-medium text-gray-900">
        {t(`admin.approvals.actions.${item.action}`, item.action)}
        {item.action === 'PUBLISH' && (
          <div className="text-xs font-normal text-gray-500">
            {publishAt ? formatDate(publishAt) : t('admin.approvals.auto_slot')}
          </div>
        )}
      </td>
      <td className="px-3 py-2 text-sm text-gray-700">
        <span className="text-gray-400 tabular-nums">#{item.article_id}</span>{' '}
        {item.article_title}
      </td>
      <td className="px-3 py-2 text-sm text-gray-600 max-w-md whitespace-pre-line">
        {item.rationale}
        <div className="mt-1 text-xs text-gray-400">
          {item.requested_by} · {formatDate(item.created_at)}
        </div>
      </td>
      <td className="px-3 py-2 text-sm">
        <span
          className={`inline-flex items-center rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset ${statusColors[item.status] || statusColors.REJECTED}`}
        >
          {t(`admin.approvals.status.${item.status}`, item.status)}
        </span>
        {item.result?.error && (
          <div className="mt-1 text-xs text-red-600">{item.result.error}</div>
        )}
        {item.decided_by && (
          <div className="mt-1 text-xs text-gray-400">
            {item.decided_by} · {formatDate(item.decided_at)}
          </div>
        )}
      </td>
      <td className="px-3 py-2">
        {item.status === 'PENDING' && (
          <div className="flex items-center justify-end gap-2">
            <button
              disabled={busy}
              onClick={() => onDecide(item, 'approve')}
              className="rounded bg-indigo-600 px-2 py-1 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
            >
              {t('admin.approvals.approve')}
            </button>
            <button
              disabled={busy}
              onClick={() => onDecide(item, 'reject')}
              className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-50"
            >
              {t('admin.approvals.reject')}
            </button>
          </div>
        )}
      </td>
    </tr>
  );
}

export default function AdminApprovals() {
  const { t } = useTranslation();
  const { token } = useAdminAuth();
  const [filter, setFilter] = useState('PENDING');
  const [data, setData] = useState({ items: [], total: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const { toasts, show: showToast, dismiss: dismissToast } = useToast();

  const refresh = useCallback(
    () => adminService.getMcpApprovals(token, filter).then(setData),
    [token, filter],
  );

  useEffect(() => {
    // Async IIFE: state updates stay out of the effect's synchronous body
    // (react-hooks/set-state-in-effect).
    (async () => {
      setLoading(true);
      try {
        await refresh();
        setError(null);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    })();
  }, [refresh]);

  const handleDecide = useCallback(async (item, decision) => {
    if (decision === 'approve' && !window.confirm(t('admin.approvals.confirm_approve'))) return;
    setBusyId(item.request_id);
    try {
      const result = await adminService.decideMcpApproval(token, item.request_id, decision);
      if (result.status === 'FAILED') {
        showToast({
          type: 'error',
          title: t('admin.approvals.failed_toast'),
          message: result.result?.error || '',
        });
      } else {
        showToast({
          type: 'success',
          title: t(`admin.approvals.status.${result.status}`, result.status),
        });
      }
      await refresh();
    } catch (err) {
      showToast({ type: 'error', title: t('admin.approvals.error_toast'), message: err.message });
    } finally {
      setBusyId(null);
    }
  }, [token, refresh, showToast, t]);

  return (
    <div>
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
      <div className="sm:flex sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">{t('admin.approvals.title')}</h1>
          <p className="mt-1 text-sm text-gray-500">{t('admin.approvals.description')}</p>
        </div>
        <div className="mt-4 flex gap-2 sm:mt-0">
          {FILTERS.map((value) => (
            <button
              key={value || 'all'}
              onClick={() => setFilter(value)}
              className={`rounded-md px-3 py-1.5 text-sm ${filter === value ? 'bg-gray-900 text-white' : 'border border-gray-300 text-gray-700 hover:bg-gray-50'}`}
            >
              {value ? t('admin.approvals.filter_pending') : t('admin.approvals.filter_all')}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="mt-4 text-sm text-red-600">{error}</p>}
      {loading ? (
        <p className="mt-6 text-sm text-gray-500">{t('admin.approvals.loading')}</p>
      ) : data.items.length === 0 ? (
        <p className="mt-6 text-sm text-gray-500">{t('admin.approvals.empty')}</p>
      ) : (
        <div className="mt-6 overflow-x-auto rounded-lg border border-gray-200 bg-white">
          <table className="min-w-full">
            <thead className="bg-gray-50 text-left text-xs font-medium uppercase text-gray-500">
              <tr>
                <th className="px-3 py-2">#</th>
                <th className="px-3 py-2">{t('admin.approvals.col_action')}</th>
                <th className="px-3 py-2">{t('admin.approvals.col_article')}</th>
                <th className="px-3 py-2">{t('admin.approvals.col_rationale')}</th>
                <th className="px-3 py-2">{t('admin.approvals.col_status')}</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {data.items.map((item) => (
                <RequestRow
                  key={item.request_id}
                  item={item}
                  busy={busyId !== null}
                  onDecide={handleDecide}
                  t={t}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
