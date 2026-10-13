import { useTranslation } from 'react-i18next'

export default function CTASection() {
    const { t } = useTranslation()

    return (
        <div className="bg-white border-t border-gray-200">
            <div className="px-6 py-24 sm:py-32 lg:px-8">
                <div className="mx-auto max-w-2xl text-center">
                    <h2 className="text-4xl font-semibold tracking-tight text-balance text-gray-900 sm:text-5xl">
                        {t('home.cta.title')}
                    </h2>
                    <p className="mx-auto mt-6 max-w-xl text-lg/8 text-pretty text-gray-600">
                        {t('home.cta.subtitle')}
                    </p>
                    <div className="mt-10 flex flex-col items-center justify-center gap-4 sm:flex-row sm:gap-x-6">
                        <a
                            href="/contacto"
                            className="rounded-md bg-indigo-600 px-5 py-3 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
                        >
                            {t('home.cta.cta_contact')}
                        </a>
                        <a
                            href="/excursiones-academicas"
                            className="rounded-md bg-white px-5 py-3 text-sm font-semibold text-gray-900 shadow-xs ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
                        >
                            {t('home.cta.cta_schools')}
                        </a>
                        <a
                            href="/tours-guiados"
                            className="rounded-md bg-white px-5 py-3 text-sm font-semibold text-gray-900 shadow-xs ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
                        >
                            {t('home.cta.cta_tourists')}
                        </a>
                    </div>
                </div>
            </div>
        </div>
    )
}
