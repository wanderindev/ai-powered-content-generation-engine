import { useTranslation } from 'react-i18next'

export default function MeetGuideSection() {
    const { t } = useTranslation()

    return (
        <div className="bg-white py-24 sm:py-32 border-t border-gray-200">
            <div className="mx-auto max-w-7xl px-6 lg:px-8">
                <div className="mx-auto max-w-2xl lg:max-w-4xl">
                    <h2 className="text-base/7 font-semibold text-indigo-600 text-center">{t('home.guide.eyebrow')}</h2>
                    <p className="mt-2 text-4xl font-semibold tracking-tight text-pretty text-gray-900 text-center sm:text-5xl">
                        {t('home.guide.title')}
                    </p>

                    <div className="mt-12 flex flex-col items-center gap-8 lg:flex-row lg:items-start lg:gap-12">
                        <img
                            src="/images/foto-diego-feliu.avif"
                            alt="Diego Raúl Feliu - Panama In Context Guide"
                            loading="lazy"
                            className="h-64 w-64 rounded-full object-cover shadow-lg flex-shrink-0"
                        />
                        <div className="text-center lg:text-left">
                            <p className="text-lg/8 text-gray-600">
                                {t('home.guide.bio_1')}
                            </p>
                            <p className="mt-4 text-lg/8 text-gray-600">
                                {t('home.guide.bio_2')}
                            </p>
                            <div className="mt-8">
                                <a
                                    href="/contacto"
                                    className="text-sm/6 font-semibold text-indigo-600 hover:text-indigo-500"
                                >
                                    {t('home.guide.cta')} <span aria-hidden="true">→</span>
                                </a>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    )
}
