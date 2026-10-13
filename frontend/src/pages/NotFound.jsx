import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

export default function NotFound() {
    const { t } = useTranslation()

    return (
        <div className="mx-auto max-w-3xl px-6 lg:px-8 py-24 sm:py-32 text-center">
            <p className="text-base font-semibold text-emerald-700">404</p>
            <h1 className="mt-4 text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
                {t('notFound.title')}
            </h1>
            <p className="mt-6 text-base leading-7 text-gray-600">
                {t('notFound.message')}
            </p>
            <div className="mt-10">
                <Link
                    to="/"
                    className="text-emerald-700 hover:text-emerald-600 font-medium"
                >
                    &larr; {t('notFound.backHome')}
                </Link>
            </div>
        </div>
    )
}
