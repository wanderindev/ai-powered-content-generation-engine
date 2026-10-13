import { useTranslation } from 'react-i18next';

/**
 * Top-level recovery UI rendered by the app-wide <Sentry.ErrorBoundary> in
 * main.jsx. This is the safety net for any uncaught render error outside the
 * page-level boundaries (Checkout / OrderConfirmation / Contact). It renders
 * outside <BrowserRouter>, so navigation uses a plain anchor, not <Link>.
 *
 * Bilingual via i18next (global instance), matching the rest of the site.
 */
function RootErrorFallback() {
    const { t } = useTranslation();
    return (
        <div className="bg-gray-50 min-h-screen flex items-center justify-center py-12">
            <div className="container mx-auto px-4 max-w-2xl text-center">
                <h1 className="text-2xl font-bold mb-4 text-gray-900">
                    {t('errorBoundary.title', 'Something went wrong')}
                </h1>
                <p className="text-gray-600 mb-6">
                    {t('errorBoundary.message', 'We hit an unexpected error. Please try reloading the page.')}
                </p>
                <div className="flex items-center justify-center gap-3">
                    <button
                        onClick={() => window.location.reload()}
                        className="inline-flex items-center px-4 py-2 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700"
                    >
                        {t('errorBoundary.reload', 'Reload page')}
                    </button>
                    <a
                        href="/"
                        className="inline-flex items-center px-4 py-2 border border-gray-300 rounded-md shadow-sm text-sm font-medium text-gray-700 bg-white hover:bg-gray-50"
                    >
                        {t('errorBoundary.home', 'Back to home')}
                    </a>
                </div>
            </div>
        </div>
    );
}

export default RootErrorFallback;
