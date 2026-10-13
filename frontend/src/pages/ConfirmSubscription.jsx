import { useState, useEffect } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { confirmSubscription } from '../services/subscribe'
import { CheckCircleIcon, XCircleIcon } from '@heroicons/react/24/outline'

export default function ConfirmSubscription() {
    const { t, i18n } = useTranslation()
    const [searchParams] = useSearchParams()
    const token = searchParams.get('token')
    const langParam = searchParams.get('lang')

    const [selectedLanguage, setSelectedLanguage] = useState(langParam || 'en')
    const [status, setStatus] = useState('loading') // 'loading' | 'success' | 'error'
    const [email, setEmail] = useState(null)
    const [message, setMessage] = useState(null)
    const [hasConfirmed, setHasConfirmed] = useState(false)

    // Set the page language based on URL param
    useEffect(() => {
        if (langParam && ['en', 'es'].includes(langParam)) {
            i18n.changeLanguage(langParam)
        }
    }, [langParam, i18n])

    // Confirm subscription when component mounts or language changes after initial confirmation
    useEffect(() => {
        async function confirm() {
            if (!token) {
                setStatus('error')
                setMessage('No token provided')
                return
            }

            try {
                const result = await confirmSubscription(token, selectedLanguage)
                if (result.success && result.confirmed) {
                    setStatus('success')
                    setEmail(result.email)
                    setMessage(result.message)
                    setHasConfirmed(true)
                } else {
                    setStatus('error')
                    setMessage(result.message || 'Invalid or expired token')
                }
            } catch (error) {
                console.error('Error confirming subscription:', error)
                setStatus('error')
                setMessage('Failed to confirm subscription')
            }
        }

        // Only confirm if we haven't confirmed yet, or if language changed after confirmation
        if (!hasConfirmed || (hasConfirmed && status === 'success')) {
            confirm()
        }
    }, [token, selectedLanguage, hasConfirmed, status])

    const handleLanguageChange = (lang) => {
        setSelectedLanguage(lang)
        i18n.changeLanguage(lang)
    }

    return (
        <div className="min-h-screen bg-gray-50 flex flex-col justify-center py-12 sm:px-6 lg:px-8">
            <div className="sm:mx-auto sm:w-full sm:max-w-md">
                <div className="bg-white py-8 px-4 shadow sm:rounded-lg sm:px-10">
                    {status === 'loading' && (
                        <div className="text-center">
                            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600 mx-auto"></div>
                            <p className="mt-4 text-gray-600">
                                {t('confirm_subscription.loading')}
                            </p>
                        </div>
                    )}

                    {status === 'success' && (
                        <div className="text-center">
                            <CheckCircleIcon className="mx-auto h-16 w-16 text-green-500" />
                            <h2 className="mt-4 text-2xl font-bold text-gray-900">
                                {t('confirm_subscription.success_title')}
                            </h2>
                            {email && (
                                <p className="mt-2 text-gray-600">{email}</p>
                            )}
                            <p className="mt-4 text-gray-600">
                                {t('confirm_subscription.success_message')}
                            </p>

                            {/* Language toggle */}
                            <div className="mt-6 p-4 bg-gray-50 rounded-lg">
                                <label className="block text-sm font-medium text-gray-700 mb-3">
                                    {t('confirm_subscription.language_label')}
                                </label>
                                <div className="flex justify-center gap-4">
                                    <button
                                        onClick={() => handleLanguageChange('en')}
                                        className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                                            selectedLanguage === 'en'
                                                ? 'bg-indigo-600 text-white'
                                                : 'bg-white text-gray-700 border border-gray-300 hover:bg-gray-50'
                                        }`}
                                    >
                                        English
                                    </button>
                                    <button
                                        onClick={() => handleLanguageChange('es')}
                                        className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                                            selectedLanguage === 'es'
                                                ? 'bg-indigo-600 text-white'
                                                : 'bg-white text-gray-700 border border-gray-300 hover:bg-gray-50'
                                        }`}
                                    >
                                        Español
                                    </button>
                                </div>
                            </div>

                            <Link
                                to="/"
                                className="mt-6 inline-block text-indigo-600 hover:text-indigo-500 font-medium"
                            >
                                {t('confirm_subscription.back_home')}
                            </Link>
                        </div>
                    )}

                    {status === 'error' && (
                        <div className="text-center">
                            <XCircleIcon className="mx-auto h-16 w-16 text-red-500" />
                            <h2 className="mt-4 text-2xl font-bold text-gray-900">
                                {t('confirm_subscription.error_title')}
                            </h2>
                            <p className="mt-4 text-gray-600">
                                {message || t('confirm_subscription.error_message')}
                            </p>
                            <Link
                                to="/"
                                className="mt-6 inline-block text-indigo-600 hover:text-indigo-500 font-medium"
                            >
                                {t('confirm_subscription.back_home')}
                            </Link>
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}
