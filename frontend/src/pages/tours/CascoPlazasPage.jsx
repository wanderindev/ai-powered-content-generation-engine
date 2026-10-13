import { useTranslation } from 'react-i18next'
import { CheckCircleIcon } from '@heroicons/react/20/solid'
import ImageGallery from '../../components/ui/ImageGallery'
import { getTourImages } from '../../data/tourImages'
import { useBookingModal } from '../../contexts/BookingModalContext'

export default function CascoPlazasPage() {
    const images = getTourImages('casco-plazas')
    const { t } = useTranslation()
    const { openBookingModal } = useBookingModal()

    // Helper function to safely get array from translation
    const getList = (key) => {
        const items = t(key, { returnObjects: true });
        return Array.isArray(items) ? items : [];
    };

    const tourData = {
        slug: 'casco-antiguo-plazas',
        title: t('tours.casco_plazas.details.title'),
        description: t('tours.casco_plazas.details.description_1'),
        image: images[0]?.src || ''
    };

    return (
        <div className="relative isolate overflow-hidden bg-white py-24 sm:py-32 lg:overflow-visible">
            <div className="mx-auto max-w-7xl px-6 lg:px-8">
                <div className="grid grid-cols-1 gap-x-8 gap-y-16 lg:grid-cols-2 lg:items-start lg:gap-y-10">
                    <div className="lg:col-start-1 lg:row-start-1">
                        <div className="lg:max-w-lg">
                            <h1 className="mt-2 text-4xl font-semibold tracking-tight text-pretty text-gray-950 sm:text-5xl">
                                {t('tours.casco_plazas.details.title')}
                            </h1>
                            <p className="mt-6 text-xl/8 text-gray-700">
                                {t('tours.casco_plazas.details.description_1')}
                            </p>
                            <p className="mt-6 text-base/7 text-gray-600 italic">
                                {t('tours.casco_plazas.details.description_2')}
                            </p>
                        </div>
                    </div>
                    <div className="lg:sticky lg:top-4 lg:col-start-2 lg:row-span-2 lg:row-start-1">
                        <ImageGallery images={images} />
                    </div>
                    <div className="lg:col-start-1 lg:row-start-2">
                        <div className="max-w-xl text-base/7 text-gray-600 lg:max-w-lg">
                            <h2 className="mt-8 text-2xl font-bold tracking-tight text-gray-900">
                                {t('tours.casco_plazas.details.highlights_title')}
                            </h2>
                            <ul role="list" className="mt-8 space-y-8 text-gray-600">
                                {getList('tours.casco_plazas.details.highlights').map((highlight, index) => (
                                    <li key={index} className="flex gap-x-3">
                                        <CheckCircleIcon aria-hidden="true" className="mt-1 size-5 flex-none text-indigo-600" />
                                        <span>
                                            <strong className="font-semibold text-gray-900">{highlight.title}.</strong> {highlight.desc}
                                        </span>
                                    </li>
                                ))}
                            </ul>

                            <h2 className="mt-16 text-2xl font-bold tracking-tight text-gray-900">
                                {t('tours.casco_plazas.details.includes_title')}
                            </h2>
                            <ul role="list" className="mt-8 space-y-4 text-gray-600">
                                {getList('tours.casco_plazas.details.includes').map((item, index) => (
                                    <li key={index} className="flex gap-x-3">
                                        <span className="mt-1.5 h-2 w-2 flex-none rounded-full bg-indigo-600" />
                                        <span>{item}</span>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    </div>
                </div>

                {/* CTA Section */}
                <div className="mt-16 rounded-2xl bg-indigo-50 p-8 lg:p-12">
                    <h2 className="text-2xl font-bold tracking-tight text-gray-900 sm:text-3xl">
                        {t('tours.cta.title')}
                    </h2>
                    <p className="mt-4 text-lg text-gray-600">
                        {t('tours.cta.description')}
                    </p>
                    <div className="mt-6 flex flex-col sm:flex-row gap-4">
                        <button
                            onClick={() => openBookingModal({ tour: tourData })}
                            className="inline-flex justify-center rounded-md bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
                        >
                            {t('booking.modal.title')}
                        </button>
                        <a
                            href="/contacto?interest=tours"
                            className="inline-flex justify-center rounded-md bg-white px-4 py-2.5 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
                        >
                            {t('tours.cta.button')}
                            <span aria-hidden="true" className="ml-2">&rarr;</span>
                        </a>
                    </div>
                </div>
            </div>
        </div>
    )
}
