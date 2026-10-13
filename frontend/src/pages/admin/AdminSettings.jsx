import { useState, useEffect } from 'react';
import { TabGroup, TabList, Tab, TabPanels, TabPanel } from '@headlessui/react';
import { useAdminAuth } from '../../contexts/AdminAuthContext';
import { adminService } from '../../services/admin';

const PRICING_LABELS = {
  base_fee: 'Base Fee ($)',
  hourly_rate_pax_1: 'Hourly Rate — 1st Person ($)',
  hourly_rate_pax_2_3: 'Hourly Rate — 2nd-3rd Person ($)',
  hourly_rate_pax_4_5: 'Hourly Rate — 4th-5th Person ($)',
  hourly_rate_pax_6_7: 'Hourly Rate — 6th-7th Person ($)',
  senior_discount_percent: 'Senior Discount (%)',
};

function AdminEmailsPanel() {
  const { token } = useAdminAuth();
  const [emails, setEmails] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [adding, setAdding] = useState(false);
  const [newEmail, setNewEmail] = useState('');
  const [newName, setNewName] = useState('');
  const [saving, setSaving] = useState(false);

  const fetchEmails = async () => {
    try {
      setLoading(true);
      const data = await adminService.getAdminEmails(token);
      setEmails(data);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchEmails();
  }, [token]);

  const handleAdd = async () => {
    if (!newEmail.trim()) return;
    try {
      setSaving(true);
      await adminService.addAdminEmail(token, { email: newEmail.trim(), name: newName.trim() || null });
      setAdding(false);
      setNewEmail('');
      setNewName('');
      await fetchEmails();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id, email) => {
    if (!window.confirm(`Remove admin access for ${email}?`)) return;
    try {
      await adminService.deleteAdminEmail(token, id);
      await fetchEmails();
    } catch (err) {
      setError(err.message);
    }
  };

  if (loading) return <p className="text-gray-500 py-4">Loading...</p>;

  return (
    <div>
      {error && (
        <div className="mb-4 rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-gray-500">
          Users with these email addresses can request admin login links.
        </p>
        {!adding && (
          <button
            onClick={() => setAdding(true)}
            className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-indigo-500"
          >
            Add email
          </button>
        )}
      </div>

      <table className="min-w-full divide-y divide-gray-200">
        <thead>
          <tr>
            <th className="px-3 py-2 text-left text-xs font-medium uppercase text-gray-500">Email</th>
            <th className="px-3 py-2 text-left text-xs font-medium uppercase text-gray-500">Name</th>
            <th className="px-3 py-2 text-left text-xs font-medium uppercase text-gray-500">Status</th>
            <th className="px-3 py-2 text-right text-xs font-medium uppercase text-gray-500">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {adding && (
            <tr>
              <td className="px-3 py-2">
                <input
                  type="email"
                  value={newEmail}
                  onChange={(e) => setNewEmail(e.target.value)}
                  placeholder="email@example.com"
                  className="block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
                  autoFocus
                />
              </td>
              <td className="px-3 py-2">
                <input
                  type="text"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="Name (optional)"
                  className="block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
                />
              </td>
              <td className="px-3 py-2" />
              <td className="px-3 py-2 text-right space-x-2">
                <button
                  onClick={handleAdd}
                  disabled={saving || !newEmail.trim()}
                  className="text-sm font-medium text-indigo-600 hover:text-indigo-500 disabled:opacity-50"
                >
                  {saving ? 'Saving...' : 'Save'}
                </button>
                <button
                  onClick={() => { setAdding(false); setNewEmail(''); setNewName(''); }}
                  className="text-sm font-medium text-gray-500 hover:text-gray-700"
                >
                  Cancel
                </button>
              </td>
            </tr>
          )}
          {emails.map((item) => (
            <tr key={item.id}>
              <td className="px-3 py-2 text-sm text-gray-900">{item.email}</td>
              <td className="px-3 py-2 text-sm text-gray-500">{item.name || '—'}</td>
              <td className="px-3 py-2">
                <span
                  className={`inline-flex items-center rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset ${
                    item.is_active
                      ? 'text-green-700 bg-green-50 ring-green-600/20'
                      : 'text-gray-600 bg-gray-50 ring-gray-500/10'
                  }`}
                >
                  {item.is_active ? 'Active' : 'Inactive'}
                </span>
              </td>
              <td className="px-3 py-2 text-right">
                <button
                  onClick={() => handleDelete(item.id, item.email)}
                  className="text-sm font-medium text-red-600 hover:text-red-500"
                >
                  Delete
                </button>
              </td>
            </tr>
          ))}
          {emails.length === 0 && !adding && (
            <tr>
              <td colSpan={4} className="px-3 py-8 text-center text-sm text-gray-400">
                No admin emails configured.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function PricingPanel() {
  const { token } = useAdminAuth();
  const [, setConfig] = useState([]);
  const [values, setValues] = useState({});
  const [snapshot, setSnapshot] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        const data = await adminService.getPricingConfig(token);
        setConfig(data);
        const vals = {};
        data.forEach((item) => { vals[item.key] = item.value; });
        setValues(vals);
        setSnapshot({ ...vals });
        setError(null);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    })();
  }, [token]);

  const hasChanges = Object.keys(PRICING_LABELS).some(
    (key) => (values[key] ?? '') !== (snapshot[key] ?? ''),
  );

  const handleSave = async () => {
    try {
      setSaving(true);
      setSuccess(false);
      const items = Object.entries(values).map(([key, value]) => ({ key, value }));
      const data = await adminService.updatePricingConfig(token, items);
      const vals = {};
      data.forEach((item) => { vals[item.key] = item.value; });
      setValues(vals);
      setSnapshot({ ...vals });
      setSuccess(true);
      setError(null);
      setTimeout(() => setSuccess(false), 3000);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <p className="text-gray-500 py-4">Loading...</p>;

  return (
    <div>
      {error && (
        <div className="mb-4 rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}
      {success && (
        <div className="mb-4 rounded-md bg-green-50 p-3 text-sm text-green-700">
          Pricing updated successfully.
        </div>
      )}

      <div className="space-y-4 max-w-md">
        {Object.entries(PRICING_LABELS).map(([key, label]) => (
          <div key={key}>
            <label htmlFor={key} className="block text-sm font-medium text-gray-700">
              {label}
            </label>
            <input
              id={key}
              type="number"
              step="any"
              value={values[key] ?? ''}
              onChange={(e) => setValues((prev) => ({ ...prev, [key]: e.target.value }))}
              className="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
            />
          </div>
        ))}
      </div>

      <div className="mt-6 flex items-center gap-3">
        <button
          onClick={handleSave}
          disabled={!hasChanges || saving}
          className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {saving ? 'Saving...' : 'Save Changes'}
        </button>
        {hasChanges && (
          <button
            onClick={() => setValues({ ...snapshot })}
            className="rounded-md bg-white px-4 py-2 text-sm font-semibold text-gray-700 ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
          >
            Cancel
          </button>
        )}
      </div>
    </div>
  );
}

const TABS = ['Admin Access', 'Pricing'];

export default function AdminSettings() {
  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Settings</h1>

      <TabGroup>
        <TabList className="inline-flex rounded-lg bg-gray-100 p-1 mb-6">
          {TABS.map((name) => (
            <Tab
              key={name}
              className="rounded-md px-4 py-2 text-sm font-medium text-gray-600 hover:text-gray-900 data-[selected]:bg-white data-[selected]:text-gray-900 data-[selected]:shadow-sm focus:outline-none"
            >
              {name}
            </Tab>
          ))}
        </TabList>

        <TabPanels>
          <TabPanel>
            <AdminEmailsPanel />
          </TabPanel>
          <TabPanel>
            <PricingPanel />
          </TabPanel>
        </TabPanels>
      </TabGroup>
    </div>
  );
}
