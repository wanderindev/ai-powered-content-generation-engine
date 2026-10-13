import { useState, useEffect } from 'react';
import { useAdminAuth } from '../../contexts/AdminAuthContext';
import { adminService } from '../../services/admin';

const statusColors = {
  PENDING: 'text-yellow-700 bg-yellow-50 ring-yellow-600/20',
  APPROVED: 'text-green-700 bg-green-50 ring-green-600/20',
  PAID: 'text-green-700 bg-green-50 ring-green-600/20',
  CONFIRMED: 'text-indigo-700 bg-indigo-50 ring-indigo-600/20',
  CANCELLED: 'text-red-700 bg-red-50 ring-red-600/20',
  EXPIRED: 'text-gray-600 bg-gray-50 ring-gray-500/10',
  PENDING_PAYMENT: 'text-yellow-700 bg-yellow-50 ring-yellow-600/20',
  PAYMENT_LINK_SENT: 'text-indigo-700 bg-indigo-50 ring-indigo-600/20',
  REJECTED: 'text-red-700 bg-red-50 ring-red-600/20',
  // Instagram derived publication states (mirror the grid/calendar colors)
  DRAFT: 'text-gray-600 bg-gray-50 ring-gray-500/10',
  SCHEDULED: 'text-orange-700 bg-orange-50 ring-orange-600/20',
  PUBLISHING: 'text-blue-700 bg-blue-50 ring-blue-600/20',
  PUBLISHED: 'text-green-700 bg-green-50 ring-green-600/20',
  FAILED: 'text-red-700 bg-red-50 ring-red-600/20',
};

function StatCard({ name, value }) {
  return (
    <div className="border-t border-gray-200/50 px-4 py-6 sm:px-6">
      <p className="text-sm/6 font-medium text-gray-500">{name}</p>
      <p className="mt-2 flex items-baseline gap-x-2">
        <span className="text-4xl font-semibold tracking-tight text-gray-900">{value}</span>
      </p>
    </div>
  );
}

function StatusBreakdown({ data }) {
  if (!data || !data.by_status || Object.keys(data.by_status).length === 0) return null;

  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {Object.entries(data.by_status).map(([status, count]) => (
        <span
          key={status}
          className={`inline-flex items-center rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset ${statusColors[status] || 'text-gray-600 bg-gray-50 ring-gray-500/10'}`}
        >
          {status}: {count}
        </span>
      ))}
    </div>
  );
}

export default function AdminHome() {
  const { token } = useAdminAuth();
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    adminService.getDashboardStats(token)
      .then(setStats)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [token]);

  if (loading) return <div className="text-gray-500">Loading stats...</div>;
  if (error) return <div className="text-red-600">Error: {error}</div>;
  if (!stats) return null;

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>

      {/* Bookings & Orders */}
      <h2 className="mt-8 text-lg font-semibold text-gray-900">Bookings & Orders</h2>
      <div className="mt-4 grid grid-cols-1 bg-white sm:grid-cols-2 lg:grid-cols-4 rounded-lg shadow-xs ring-1 ring-gray-200">
        <StatCard name="Total Bookings" value={stats.bookings.total} />
        <StatCard name="Total Orders" value={stats.orders.total} />
        <div className="border-t border-gray-200/50 px-4 py-6 sm:px-6 sm:col-span-2">
          <p className="text-sm/6 font-medium text-gray-500">Orders by Status</p>
          <StatusBreakdown data={stats.orders} />
        </div>
      </div>

      {/* Content Pipeline */}
      <h2 className="mt-8 text-lg font-semibold text-gray-900">Content Pipeline</h2>
      <div className="mt-4 grid grid-cols-1 bg-white sm:grid-cols-2 lg:grid-cols-4 rounded-lg shadow-xs ring-1 ring-gray-200">
        <div className="border-t border-gray-200/50 px-4 py-6 sm:px-6">
          <p className="text-sm/6 font-medium text-gray-500">Suggestions</p>
          <p className="mt-2 text-4xl font-semibold tracking-tight text-gray-900">{stats.suggestions.total}</p>
          <StatusBreakdown data={stats.suggestions} />
        </div>
        <div className="border-t border-gray-200/50 px-4 py-6 sm:px-6">
          <p className="text-sm/6 font-medium text-gray-500">Research</p>
          <p className="mt-2 text-4xl font-semibold tracking-tight text-gray-900">{stats.research.total}</p>
          <StatusBreakdown data={stats.research} />
        </div>
        <div className="border-t border-gray-200/50 px-4 py-6 sm:px-6">
          <p className="text-sm/6 font-medium text-gray-500">Articles</p>
          <p className="mt-2 text-4xl font-semibold tracking-tight text-gray-900">{stats.articles.total}</p>
          <StatusBreakdown data={stats.articles} />
        </div>
        <div className="border-t border-gray-200/50 px-4 py-6 sm:px-6">
          <p className="text-sm/6 font-medium text-gray-500">Instagram Posts</p>
          <p className="mt-2 text-4xl font-semibold tracking-tight text-gray-900">{stats.instagram?.total ?? 0}</p>
          <StatusBreakdown data={stats.instagram} />
        </div>
      </div>

      {/* Educational Pipeline */}
      <h2 className="mt-8 text-lg font-semibold text-gray-900">Educational Pipeline</h2>
      <div className="mt-4 grid grid-cols-1 bg-white sm:grid-cols-2 lg:grid-cols-4 rounded-lg shadow-xs ring-1 ring-gray-200">
        <div className="border-t border-gray-200/50 px-4 py-6 sm:px-6">
          <p className="text-sm/6 font-medium text-gray-500">Suggestions</p>
          <p className="mt-2 text-4xl font-semibold tracking-tight text-gray-900">{stats.edu_suggestions?.total ?? 0}</p>
          <StatusBreakdown data={stats.edu_suggestions} />
        </div>
        <div className="border-t border-gray-200/50 px-4 py-6 sm:px-6">
          <p className="text-sm/6 font-medium text-gray-500">Research</p>
          <p className="mt-2 text-4xl font-semibold tracking-tight text-gray-900">{stats.edu_research?.total ?? 0}</p>
          <StatusBreakdown data={stats.edu_research} />
        </div>
        <div className="border-t border-gray-200/50 px-4 py-6 sm:px-6">
          <p className="text-sm/6 font-medium text-gray-500">Materials</p>
          <p className="mt-2 text-4xl font-semibold tracking-tight text-gray-900">{stats.edu_materials?.total ?? 0}</p>
          <StatusBreakdown data={stats.edu_materials} />
        </div>
        <div className="border-t border-gray-200/50 px-4 py-6 sm:px-6">
          <p className="text-sm/6 font-medium text-gray-500">Published Deliverables</p>
          <p className="mt-2 flex items-baseline gap-x-2">
            <span className="text-4xl font-semibold tracking-tight text-gray-900">{stats.edu_published_total ?? 0}</span>
            <span className="text-sm text-gray-500">live on the portal</span>
          </p>
          <StatusBreakdown data={stats.edu_materials_by_type} />
        </div>
      </div>
    </div>
  );
}
