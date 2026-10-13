import { DocumentMagnifyingGlassIcon, MapIcon, PencilSquareIcon } from '@heroicons/react/24/outline'
import { useTranslation } from 'react-i18next'

const features = [
    {
        key: 'context',
        icon: DocumentMagnifyingGlassIcon,
    },
    {
        key: 'immersion',
        icon: MapIcon,
    },
    {
        key: 'reflection',
        icon: PencilSquareIcon,
    },
]

export default function Methodology() {
    const { t } = useTranslation()

    return (
        <div id="methodology" className="bg-white py-24 sm:py-32 border-t border-gray-200">
            <div className="mx-auto max-w-7xl px-6 lg:px-8">
                <div className="mx-auto max-w-2xl lg:mx-0">
                    <h2 className="text-4xl font-semibold tracking-tight text-pretty text-gray-900 sm:text-5xl">
                        {t('excursions.methodology.title')}
                    </h2>
                    <p className="mt-6 text-lg/8 text-gray-600">
                        {t('excursions.methodology.description')}
                    </p>
                </div>
                <div className="mx-auto mt-10 max-w-2xl sm:mt-12 lg:mt-16 lg:max-w-none">
                    <dl className="grid max-w-xl grid-cols-1 gap-x-8 gap-y-16 lg:max-w-none lg:grid-cols-3">
                        {features.map((feature) => (
                            <div key={feature.key} className="flex flex-col">
                                <dt className="text-base/7 font-semibold text-gray-900">
                                    <div className="mb-6 flex size-10 items-center justify-center rounded-lg bg-indigo-600">
                                        <feature.icon aria-hidden="true" className="size-6 text-white" />
                                    </div>
                                    {t(`excursions.methodology.steps.${feature.key}.title`)}
                                </dt>
                                <dd className="mt-1 flex flex-auto flex-col text-base/7 text-gray-600">
                                    <p className="flex-auto">{t(`excursions.methodology.steps.${feature.key}.desc`)}</p>

                                </dd>
                            </div>
                        ))}
                    </dl>
                </div>
            </div>
        </div>
    )
}
