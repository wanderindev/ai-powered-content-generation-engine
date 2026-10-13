import { useState, useEffect } from 'react'
import { useSearchParams, useNavigate, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { CheckCircleIcon } from '@heroicons/react/24/outline'

const REDIRECT_SECONDS = 5

export default function ContactConfirmation() {
    const { t } = useTranslation()
    const [searchParams] = useSearchParams()
    const navigate = useNavigate()
    const [countdown, setCountdown] = useState(REDIRECT_SECONDS)

    const fromParam = searchParams.get('from') || '/'
    const returnTo = /^\/(?!\/)/.test(fromParam) ? fromParam : '/'

    useEffect(() => {
        if (window.fbq) {
            window.fbq('track', 'Lead')
        }
    }, [])

    useEffect(() => {
        const timer = setInterval(() => {
            setCountdown((prev) => {
                if (prev <= 1) {
                    clearInterval(timer)
                    navigate(returnTo, { replace: true })
                    return 0
                }
                return prev - 1
            })
        }, 1000)
        return () => clearInterval(timer)
    }, [navigate, returnTo])

    return (
        <div className="bg-white min-h-screen pt-24 pb-12">
            <div className="max-w-xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
                <CheckCircleIcon className="mx-auto h-24 w-24 text-green-500" />
                <h1 className="mt-4 text-3xl font-bold tracking-tight text-gray-900">
                    {t('contact.confirmation.title')}
                </h1>
                <p className="mt-4 text-base text-gray-500">
                    {t('contact.confirmation.message')}
                </p>

                <p className="mt-8 text-sm text-gray-500">
                    {t('contact.confirmation.redirect', { seconds: countdown })}{' '}
                    <Link to={returnTo} className="text-indigo-600 hover:text-indigo-500 font-medium">
                        {returnTo === '/' ? t('contact.confirmation.home') : returnTo}
                    </Link>
                </p>
            </div>
        </div>
    )
}
