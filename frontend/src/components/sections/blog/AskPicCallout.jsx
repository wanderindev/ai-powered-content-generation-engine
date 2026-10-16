import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChatBubbleLeftRightIcon } from '@heroicons/react/24/outline'
import { useAskStatus } from '../../../hooks/useAskStatus'

/**
 * Entry point to Ask PIC from the blog list and article pages (#636).
 * Renders nothing until GET /ask/status says the feature is on, so the
 * kill switch hides it (and a failed status call reads as off).
 */
export default function AskPicCallout({ className = '' }) {
    const { t, i18n } = useTranslation()
    const status = useAskStatus()

    if (!status?.enabled) return null

    const to = i18n.language === 'en' ? '/ask' : '/preguntar'

    return (
        <div className={className}>
            <div className="mx-auto flex max-w-3xl flex-col items-start gap-4 rounded-lg bg-gray-50 p-6 ring-1 ring-inset ring-gray-200 sm:flex-row sm:items-center">
                <ChatBubbleLeftRightIcon aria-hidden="true" className="size-8 shrink-0 text-indigo-600" />
                <div className="flex-1">
                    <p className="text-base font-semibold text-gray-900">{t('ask_pic.callout.title')}</p>
                    <p className="mt-1 text-sm text-gray-600">{t('ask_pic.callout.body')}</p>
                </div>
                <Link
                    to={to}
                    className="rounded-md bg-indigo-600 px-3.5 py-2.5 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
                >
                    {t('ask_pic.callout.cta')}
                </Link>
            </div>
        </div>
    )
}
