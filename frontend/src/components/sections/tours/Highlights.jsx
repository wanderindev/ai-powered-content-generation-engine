import { GlobeAmericasIcon, UserIcon, AcademicCapIcon } from '@heroicons/react/24/outline'
import { useTranslation } from 'react-i18next'

export default function Highlights() {
    const { t } = useTranslation()

    const features = [
        {
            key: 'context',
            icon: GlobeAmericasIcon,
        },
        {
            key: 'private',
            icon: UserIcon,
        },
        {
            key: 'guides',
            icon: AcademicCapIcon,
        },
    ]

    return (
        <div className="bg-white py-24 sm:py-32 border-t border-gray-200">
            <div className="mx-auto max-w-7xl px-6 lg:px-8">
                <div className="mx-auto max-w-2xl lg:mx-0">
                    <h2 className="text-4xl font-semibold tracking-tight text-pretty text-gray-900 sm:text-5xl">
                        {t('tours.highlights.title')}
                    </h2>
                    <p className="mt-6 text-lg/8 text-gray-600">
                        {t('tours.highlights.subtitle')}
                    </p>
                </div>
                <div className="mx-auto mt-16 max-w-2xl sm:mt-20 lg:mt-24 lg:max-w-none">
                    <dl className="grid max-w-xl grid-cols-1 gap-x-8 gap-y-16 lg:max-w-none lg:grid-cols-3">
                        {features.map((feature) => (
                            <div key={feature.key} className="flex flex-col">
                                <dt className="text-base/7 font-semibold text-gray-900">
                                    <div className="mb-6 flex size-10 items-center justify-center rounded-lg bg-indigo-600">
                                        <feature.icon aria-hidden="true" className="size-6 text-white" />
                                    </div>
                                    {t(`tours.highlights.features.${feature.key}.title`)}
                                </dt>
                                <dd className="mt-1 flex flex-auto flex-col text-base/7 text-gray-600">
                                    <p className="flex-auto">{t(`tours.highlights.features.${feature.key}.desc`)}</p>
                                </dd>
                            </div>
                        ))}
                    </dl>
                </div>
            </div>
        </div>
    )
}
