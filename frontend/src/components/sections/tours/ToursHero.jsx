import { useTranslation } from 'react-i18next'

export default function ToursHero() {
    const { t } = useTranslation()

    return (
        <div className="bg-white">
            <main>
                <div className="relative isolate">


                    <div className="overflow-hidden">
                        <div className="mx-auto max-w-7xl px-6 pt-24 pb-16 sm:pt-40 lg:px-8 lg:pt-24">
                            <div className="mx-auto max-w-2xl gap-x-14 lg:mx-0 lg:flex lg:max-w-none lg:items-center lg:gap-x-8">
                                <div className="relative w-full lg:max-w-lg lg:shrink-0 xl:max-w-xl">
                                    <h1 className="text-5xl font-semibold tracking-tight text-pretty text-gray-900 sm:text-7xl">
                                        {t('tours.hero.title')}
                                    </h1>
                                    <p className="mt-8 text-lg font-medium text-pretty text-gray-500 sm:max-w-md sm:text-xl/8 lg:max-w-none">
                                        {t('tours.hero.subtitle')}
                                    </p>
                                    <div className="mt-10 flex items-center gap-x-6">
                                        <a
                                            href="#available-tours"
                                            className="rounded-md bg-indigo-600 px-3.5 py-2.5 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
                                        >
                                            {t('tours.hero.cta_primary')}
                                        </a>
                                        <a href="/contacto?interest=tours" className="text-sm/6 font-semibold text-gray-900">
                                            {t('tours.hero.cta_secondary')} <span aria-hidden="true">→</span>
                                        </a>
                                    </div>
                                </div>
                                <div className="mt-14 flex justify-end gap-8 sm:-mt-44 sm:justify-start sm:pl-20 lg:mt-0 lg:pl-0">
                                    <div className="ml-auto w-44 flex-none space-y-8 pt-32 sm:ml-0 sm:pt-80 lg:order-last lg:pt-36 xl:order-0 xl:pt-80">
                                        <div className="relative">
                                            <img
                                                alt=""
                                                src="/images/ship-in-canal-2x3.jpg"
                                                loading="lazy"
                                                className="aspect-2/3 w-full rounded-xl bg-gray-900/5 object-cover shadow-lg"
                                            />
                                            <div className="pointer-events-none absolute inset-0 rounded-xl ring-1 ring-gray-900/10 ring-inset" />
                                        </div>
                                    </div>
                                    <div className="mr-auto w-44 flex-none space-y-8 sm:mr-0 sm:pt-52 lg:pt-36">
                                        <div className="relative">
                                            <img
                                                alt=""
                                                src="/images/altar-de-oro-2x3.jpg"
                                                loading="lazy"
                                                className="aspect-2/3 w-full rounded-xl bg-gray-900/5 object-cover shadow-lg"
                                            />
                                            <div className="pointer-events-none absolute inset-0 rounded-xl ring-1 ring-gray-900/10 ring-inset" />
                                        </div>
                                        <div className="relative">
                                            <img
                                                alt=""
                                                src="/images/mi-pueblito-2x3.jpg"
                                                loading="lazy"
                                                className="aspect-2/3 w-full rounded-xl bg-gray-900/5 object-cover shadow-lg"
                                            />
                                            <div className="pointer-events-none absolute inset-0 rounded-xl ring-1 ring-gray-900/10 ring-inset" />
                                        </div>
                                    </div>
                                    <div className="w-44 flex-none space-y-8 pt-32 sm:pt-0">
                                        <div className="relative">
                                            <img
                                                alt=""
                                                src="/images/panama-viejo-torre-2x3.jpg"
                                                loading="lazy"
                                                className="aspect-2/3 w-full rounded-xl bg-gray-900/5 object-cover shadow-lg"
                                            />
                                            <div className="pointer-events-none absolute inset-0 rounded-xl ring-1 ring-gray-900/10 ring-inset" />
                                        </div>
                                        <div className="relative">
                                            <img
                                                alt=""
                                                src="/images/casco-2x3.jpg"
                                                loading="lazy"
                                                className="aspect-2/3 w-full rounded-xl bg-gray-900/5 object-cover shadow-lg"
                                            />
                                            <div className="pointer-events-none absolute inset-0 rounded-xl ring-1 ring-gray-900/10 ring-inset" />
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </main>
        </div>
    )
}
