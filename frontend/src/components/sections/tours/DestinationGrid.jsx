import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

export default function DestinationGrid() {
    const { t } = useTranslation()

    const tours = [
        {
            key: 'canal',
            image: '/images/miraflores.jpg',
            link: '/tours-guiados/canal-de-panama',
        },
        {
            key: 'casco_plazas',
            image: '/images/plazas-and-churches.png',
            link: '/tours-guiados/casco-antiguo-plazas',
        },
        {
            key: 'casco_museums',
            image: '/images/museum-circuit.png',
            link: '/tours-guiados/casco-antiguo-museos',
        },
        {
            key: 'panama_viejo',
            image: '/images/panama-viejo.png',
            link: '/tours-guiados/panama-viejo',
        },
        {
            key: 'mi_pueblito',
            image: '/images/mi-pueblito-capilla.jpg',
            link: '/tours-guiados/mi-pueblito',
        },
        {
            key: 'biomuseo',
            image: '/images/biomuseo.jpg',
            link: '/tours-guiados/biomuseo',
        },
    ]

    return (
        <div id="available-tours" className="bg-white py-24 sm:py-32 border-t border-gray-200">
            <div className="mx-auto max-w-2xl px-6 lg:max-w-7xl lg:px-8">
                <h2 className="text-base/7 font-semibold text-indigo-600">{t('tours.grid.eyebrow')}</h2>
                <p className="mt-2 max-w-lg text-4xl font-semibold tracking-tight text-pretty text-gray-950 sm:text-5xl">
                    {t('tours.grid.title')}
                </p>
                <div className="mt-10 grid grid-cols-1 gap-4 sm:mt-16 lg:grid-cols-6 lg:grid-rows-2">
                    {tours.map((tour) => (
                        <div key={tour.key} className={`relative lg:col-span-2`}>
                            <div className="absolute inset-0 rounded-lg bg-white" />
                            <Link to={tour.link || '#'} className={`relative flex h-full flex-col overflow-hidden rounded-[calc(var(--radius-lg)+1px)] ${tour.link ? 'cursor-pointer hover:opacity-95 transition-opacity' : ''}`}>
                                <img
                                    alt={t(`tours.${tour.key}.title`)}
                                    src={tour.image}
                                    loading="lazy"
                                    className="h-80 object-cover w-full"
                                />
                                <div className="p-10 pt-4">
                                    <p className="mt-2 text-lg font-medium tracking-tight text-gray-950">
                                        {t(`tours.${tour.key}.title`)}
                                    </p>
                                    <p className="mt-2 max-w-lg text-sm/6 text-gray-600">
                                        {t(`tours.${tour.key}.desc`)}
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
