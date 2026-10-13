import { useTranslation } from 'react-i18next'

export default function ToursCTA() {
    const { t } = useTranslation()

    return (
        <div className="overflow-hidden bg-white py-24 sm:py-32 border-t border-gray-200">
            <div className="mx-auto max-w-7xl px-6 lg:flex lg:px-8">
                <div className="mx-auto grid max-w-2xl grid-cols-1 gap-x-12 gap-y-16 lg:mx-0 lg:max-w-none lg:min-w-full lg:flex-none lg:gap-y-8">
                    <div className="lg:col-end-1 lg:w-full lg:max-w-lg lg:pb-8">
                        <h2 className="text-4xl font-semibold tracking-tight text-gray-900 sm:text-5xl">{t('tours.cta.title')}</h2>
                        <p className="mt-6 text-xl/8 text-gray-700">
                            {t('tours.cta.description')}
                        </p>
                        <div className="mt-10 flex">
                            <a
                                href="/contacto?interest=tours"
                                className="rounded-md bg-indigo-600 px-3.5 py-2.5 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
                            >
                                {t('tours.cta.button')}
                                <span aria-hidden="true">&rarr;</span>
                            </a>
                        </div>
                    </div>
                    {/* Mobile: stacked centered images */}
                    <div className="flex flex-col items-center gap-6 lg:hidden">
                        <img
                            alt=""
                            src="/images/megalodont.png"
                            loading="lazy"
                            className="aspect-7/5 w-full max-w-sm rounded-2xl bg-gray-50 object-cover"
                        />
                        <img
                            alt=""
                            src="/images/arco-chato.jpg"
                            loading="lazy"
                            className="aspect-4/3 w-full max-w-sm rounded-2xl bg-gray-50 object-cover"
                        />
                        <img
                            alt=""
                            src="/images/poblaciones-precolombinas.png"
                            loading="lazy"
                            className="aspect-7/5 w-full max-w-sm rounded-2xl bg-gray-50 object-cover"
                        />
                    </div>
                    {/* Desktop: complex layout */}
                    <div className="hidden lg:contents">
                        <div className="lg:ml-auto lg:w-auto lg:flex-none lg:self-end">
                            <img
                                alt=""
                                src="/images/megalodont.png"
                                loading="lazy"
                                className="aspect-7/5 w-[28rem] max-w-none rounded-2xl bg-gray-50 object-cover"
                            />
                        </div>
                        <div className="lg:col-span-2 lg:col-end-2 lg:ml-auto lg:flex lg:w-148 lg:items-start lg:justify-end lg:gap-x-8">
                            <div className="flex lg:w-auto">
                                <img
                                    alt=""
                                    src="/images/arco-chato.jpg"
                                    loading="lazy"
                                    className="aspect-4/3 w-[18rem] max-w-none flex-none rounded-2xl bg-gray-50 object-cover"
                                />
                            </div>
                            <div className="flex lg:w-auto lg:flex-none">
                                <img
                                    alt=""
                                    src="/images/poblaciones-precolombinas.png"
                                    loading="lazy"
                                    className="aspect-7/5 w-[28rem] max-w-none flex-none rounded-2xl bg-gray-50 object-cover"
                                />
                            </div>
                            <div className="lg:w-auto lg:flex-none">
                                <img
                                    alt=""
                                    src="/images/ship-in-locks.jpg"
                                    loading="lazy"
                                    className="aspect-4/3 w-[18rem] max-w-none rounded-2xl bg-gray-50 object-cover"
                                />
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    )
}
