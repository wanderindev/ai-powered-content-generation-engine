import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { EnvelopeIcon, LockClosedIcon } from '@heroicons/react/24/outline';
import { useEducatorAuth } from '../../contexts/EducatorAuthContext';
import { educatorAccess, educatorSignup } from '../../services/educators';

/**
 * Wraps educational content behind a low-friction, email-based access gate.
 *
 * Flow (no email confirmation, no verification code):
 * 1. Default: enter your email.
 * 2. Email registered → access granted immediately (7-day token).
 * 3. Email not registered → show the short signup form.
 * 4. After signup → access granted immediately; if the mailing-list box was
 *    ticked, a separate subscription-confirmation email is sent (double opt-in),
 *    but it does not gate access.
 */
export default function EducatorAccessGate({ children }) {
  const { t, i18n } = useTranslation();
  const { hasAccess, loading: authLoading, verifyError, grantAccess } = useEducatorAuth();

  // Views: login | register
  const [view, setView] = useState('login');
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [institution, setInstitution] = useState('');
  const [mailingList, setMailingList] = useState(false);
  const [message, setMessage] = useState('');
  const [messageType, setMessageType] = useState(''); // error | info
  const [submitting, setSubmitting] = useState(false);

  const lang = () => (i18n.language?.startsWith('en') ? 'en' : 'es');

  if (authLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="text-gray-400">{t('edu_materials.access.loading')}</div>
      </div>
    );
  }

  if (hasAccess) return children;

  // --- Login handler (returning educator enters email) ---
  const handleLogin = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setMessage('');

    try {
      const result = await educatorAccess(email, lang());

      if (result.granted) {
        grantAccess(email, result.token, result.expires_at);
        return;
      }

      // Not registered → show the signup form.
      setView('register');
      setMessage(t('edu_materials.access.not_registered'));
      setMessageType('info');
    } catch {
      setMessage(t('edu_materials.access.error'));
      setMessageType('error');
    }
    setSubmitting(false);
  };

  // --- Register handler (new educator → instant access) ---
  const handleRegister = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setMessage('');

    try {
      const result = await educatorSignup({
        email,
        name,
        institution,
        language: lang(),
        mailingList,
        source: 'media_library',
      });

      if (result.granted) {
        grantAccess(email, result.token, result.expires_at);
        return;
      }
      setMessage(t('edu_materials.access.error'));
      setMessageType('error');
    } catch {
      setMessage(t('edu_materials.access.error'));
      setMessageType('error');
    }
    setSubmitting(false);
  };

  const messageColor = messageType === 'error' ? 'text-red-600' : 'text-indigo-600';

  // =================== REGISTER VIEW ===================
  if (view === 'register') {
    return (
      <div className="flex flex-col items-center justify-center py-16 px-4">
        <div className="w-full max-w-sm">
          <div className="rounded-xl bg-white shadow-lg border border-gray-200 p-8">
            <div className="flex justify-center mb-4">
              <LockClosedIcon className="h-10 w-10 text-indigo-500" />
            </div>
            <h3 className="text-lg font-semibold text-gray-900 text-center">
              {t('edu_materials.access.register_title')}
            </h3>
            <p className="mt-2 text-sm text-gray-600 text-center">
              {t('edu_materials.access.register_text')}
            </p>

            {message && <p className={`mt-3 text-sm text-center ${messageColor}`}>{message}</p>}

            <form onSubmit={handleRegister} className="mt-5 space-y-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  {t('edu_materials.access.email_label')} *
                </label>
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder={t('edu_materials.access.email_placeholder')}
                  className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  {t('edu_materials.access.name_label')}
                </label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={t('edu_materials.access.name_placeholder')}
                  className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  {t('edu_materials.access.institution_label')}
                </label>
                <input
                  type="text"
                  value={institution}
                  onChange={(e) => setInstitution(e.target.value)}
                  placeholder={t('edu_materials.access.institution_placeholder')}
                  className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                />
              </div>
              <div className="flex items-start gap-2">
                <input
                  type="checkbox"
                  id="mailing_list"
                  checked={mailingList}
                  onChange={(e) => setMailingList(e.target.checked)}
                  className="mt-1 h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                />
                <label htmlFor="mailing_list" className="text-sm text-gray-600">
                  {t('edu_materials.access.mailing_list_label')}
                </label>
              </div>
              <button
                type="submit"
                disabled={submitting}
                className="w-full rounded-md bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
              >
                {submitting ? t('edu_materials.access.sending') : t('edu_materials.access.register_submit')}
              </button>
            </form>

            <button
              onClick={() => { setView('login'); setMessage(''); }}
              className="mt-4 w-full text-sm text-gray-500 hover:text-indigo-600"
            >
              {t('edu_materials.access.back_to_login')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  // =================== LOGIN VIEW (default) ===================
  return (
    <div className="flex flex-col items-center justify-center py-16 px-4">
      <div className="w-full max-w-sm">
        <div className="rounded-xl bg-white shadow-lg border border-gray-200 p-8">
          <div className="flex justify-center mb-4">
            <LockClosedIcon className="h-10 w-10 text-indigo-500" />
          </div>
          <h3 className="text-lg font-semibold text-gray-900 text-center">
            {t('edu_materials.access.login_title')}
          </h3>
          <p className="mt-2 text-sm text-gray-600 text-center">
            {t('edu_materials.access.login_text')}
          </p>

          {message && <p className={`mt-3 text-sm text-center ${messageColor}`}>{message}</p>}
          {verifyError && (
            <p className="mt-3 text-sm text-center text-red-600">
              {t('edu_materials.access.verify_unavailable')}
            </p>
          )}

          <form onSubmit={handleLogin} className="mt-5 space-y-4">
            <div className="relative">
              <EnvelopeIcon className="pointer-events-none absolute left-3 top-1/2 h-5 w-5 -translate-y-1/2 text-gray-400" />
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder={t('edu_materials.access.email_placeholder')}
                className="w-full rounded-md border border-gray-300 pl-10 pr-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
              />
            </div>
            <button
              type="submit"
              disabled={submitting}
              className="w-full rounded-md bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
            >
              {submitting ? t('edu_materials.access.sending') : t('edu_materials.access.login_submit')}
            </button>
          </form>

          <div className="mt-4 text-center">
            <span className="text-sm text-gray-500">{t('edu_materials.access.no_account')} </span>
            <button
              onClick={() => { setView('register'); setMessage(''); }}
              className="text-sm font-medium text-indigo-600 hover:underline"
            >
              {t('edu_materials.access.register_link')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
