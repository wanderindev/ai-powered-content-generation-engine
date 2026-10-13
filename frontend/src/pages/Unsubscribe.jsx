import { useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { unsubscribe } from '../services/subscribe'
import { CheckCircleIcon, XCircleIcon, ExclamationTriangleIcon } from '@heroicons/react/24/outline'

export default function Unsubscribe() {
    const { t } = useTranslation()
    const [searchParams] = useSearchParams()
    const emailParam = searchParams.get('email')

    // Decode email from base64 if it looks like base64, otherwise use as-is
    const [email] = useState(() => {
        if (!emailParam) return null
        try {
            // Check if it looks like base64 (no @ symbol and only valid base64 chars)
            if (!emailParam.includes('@') && /^[A-Za-z0-9+/=]+$/.test(emailParam)) {
                return atob(emailParam)
            }
            return emailParam
        } catch {
            return emailParam
        }
    })

    const [status, setStatus] = useState('confirm') // 'confirm' | 'loading' | 'success' | 'error'
    const [message, setMessage] = useState(null)

    const handleUnsubscribe = async () => {
        if (!email) {
            setStatus('error')
            setMessage('No email provided')
            return
        }

        setStatus('loading')

        try {
            const result = await unsubscribe(email)
            if (result.success && result.unsubscribed) {
                setStatus('success')
                setMessage(result.message)
            } else {
                setStatus('error')
                setMessage(result.message || 'Failed to unsubscribe')
            }
        } catch (error) {
            console.error('Error unsubscribing:', error)
            setStatus('error')
            setMessage('Failed to unsubscribe')
        }
    }

    // If no email provided, show error
    if (!email) {
        return (
            <div className="min-h-screen bg-gray-50 flex flex-col justify-center py-12 sm:px-6 lg:px-8">
                <div className="sm:mx-auto sm:w-full sm:max-w-md">
                    <div className="bg-white py-8 px-4 shadow sm:rounded-lg sm:px-10 text-center">
                        <XCircleIcon className="mx-auto h-16 w-16 text-red-500" />
                        <h2 className="mt-4 text-2xl font-bold text-gray-900">
                            {t('unsubscribe.error_title')}
                        </h2>
                        <p className="mt-4 text-gray-600">
                            {t('unsubscribe.error_message')}
                        </p>
                        <Link
                            to="/"
                            className="mt-6 inline-block text-indigo-600 hover:text-indigo-500 font-medium"
                        >
                            {t('confirm_subscription.back_home')}
                        </Link>
                    </div>
                </div>
            </div>
        )
    }

    return (
        <div className="min-h-screen bg-gray-50 flex flex-col justify-center py-12 sm:px-6 lg:px-8">
            <div className="sm:mx-auto sm:w-full sm:max-w-md">
                <div className="bg-white py-8 px-4 shadow sm:rounded-lg sm:px-10">
                    {status === 'confirm' && (
                        <div className="text-center">
                            <ExclamationTriangleIcon className="mx-auto h-16 w-16 text-amber-500" />
                            <h2 className="mt-4 text-2xl font-bold text-gray-900">
                                {t('unsubscribe.title')}
                            </h2>
                            <p className="mt-4 text-gray-600">
                                {t('unsubscribe.message')}
                            </p>
                            <div className="mt-4 p-3 bg-gray-100 rounded-md">
                                <span className="text-sm text-gray-500">{t('unsubscribe.email_label')}</span>
                                <p className="font-medium text-gray-900">{email}</p>
                            </div>
                            <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:justify-center">
                                <button
                                    onClick={handleUnsubscribe}
                                    className="inline-flex justify-center rounded-md bg-red-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-red-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600"
                                >
                                    {t('unsubscribe.confirm_button')}
                                </button>
                                <Link
                                    to="/"
                                    className="inline-flex justify-center rounded-md bg-white px-4 py-2 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
                                >
                                    {t('unsubscribe.cancel_button')}
                                </Link>
                            </div>
                        </div>
                    )}

                    {status === 'loading' && (
                        <div className="text-center">
                            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600 mx-auto"></div>
                            <p className="mt-4 text-gray-600">
                                {t('home.mailing_educators.submitting')}
                            </p>
                        </div>
                    )}

                    {status === 'success' && (
                        <div className="text-center">
                            <CheckCircleIcon className="mx-auto h-16 w-16 text-green-500" />
                            <h2 className="mt-4 text-2xl font-bold text-gray-900">
                                {t('unsubscribe.success_title')}
                            </h2>
                            <p className="mt-4 text-gray-600">
                                {t('unsubscribe.success_message')}
                            </p>
                            <Link
                                to="/"
                                className="mt-6 inline-block text-indigo-600 hover:text-indigo-500 font-medium"
                            >
                                {t('unsubscribe.resubscribe')}
                            </Link>
                        </div>
                    )}

                    {status === 'error' && (
                        <div className="text-center">
                            <XCircleIcon className="mx-auto h-16 w-16 text-red-500" />
                            <h2 className="mt-4 text-2xl font-bold text-gray-900">
                                {t('unsubscribe.error_title')}
                            </h2>
                            <p className="mt-4 text-gray-600">
                                {message || t('unsubscribe.error_message')}
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
