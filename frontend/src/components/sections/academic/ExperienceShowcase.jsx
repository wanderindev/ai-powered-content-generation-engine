import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

export default function ExperienceShowcase() {
    const { t } = useTranslation()

    const excursions = [
        {
            key: 'casco_plazas',
            image: '/images/plazas-and-churches.png',
            link: '/excursiones-academicas/casco-antiguo-plazas',
        },
        {
            key: 'casco_museums',
            image: '/images/museum-circuit.png',
            link: '/excursiones-academicas/casco-antiguo-museos',
        },
        {
            key: 'panama_viejo',
            image: '/images/panama-viejo.png',
            link: '/excursiones-academicas/panama-viejo',
        },
        {
            key: 'biomuseo',
            image: '/images/biomuseo.jpg',
            link: '/excursiones-academicas/biomuseo',
        },
        {
            key: 'miraflores',
            image: '/images/miraflores.jpg',
            link: '/excursiones-academicas/miraflores',
        },
        {
            key: 'religious',
            image: '/images/inter-religious.png',
            link: '/excursiones-academicas/inter-religiosa',
        },
    ]

    return (
        <div id="featured-excursions" className="bg-white py-24 sm:py-32 border-t border-gray-200">
            <div className="mx-auto max-w-2xl px-6 lg:max-w-7xl lg:px-8">
                <h2 className="text-base/7 font-semibold text-indigo-600">{t('excursions.eyebrow')}</h2>
                <p className="mt-2 max-w-lg text-4xl font-semibold tracking-tight text-pretty text-gray-950 sm:text-5xl">
                    {t('excursions.title')}
                </p>
                <div className="mt-10 grid grid-cols-1 gap-4 sm:mt-16 lg:grid-cols-6 lg:grid-rows-2">
                    {excursions.map((excursion, index) => (
                        <div key={excursion.key} className={`relative max-lg:row-start-${index + 1} lg:col-span-2 lg:row-span-1`}>
                            <div className="absolute inset-0 rounded-lg bg-white" />
                            <Link to={excursion.link || '#'} className={`relative flex h-full flex-col overflow-hidden rounded-[calc(var(--radius-lg)+1px)] ${excursion.link ? 'cursor-pointer hover:opacity-95 transition-opacity' : ''}`}>
                                <img
                                    alt={t(`excursions.${excursion.key}.title`)}
                                    src={excursion.image}
                                    loading="lazy"
                                    className="h-80 object-cover w-full"
                                />
                                <div className="p-10 pt-4">
                                    <p className="mt-2 text-lg font-medium tracking-tight text-gray-950">
                                        {t(`excursions.${excursion.key}.title`)}
                                    </p>
                                    <p className="mt-2 max-w-lg text-sm/6 text-gray-600">
                                        {t(`excursions.${excursion.key}.desc`)}
                                    </p>
                                </div>
                            </Link>
                            <div className="pointer-events-none absolute inset-0 rounded-lg shadow-sm outline outline-black/5" />
                        </div>
                    ))}
                </div>
            </div>
        </div>
    )
}
