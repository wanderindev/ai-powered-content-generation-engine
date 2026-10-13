import { AcademicCapIcon, SpeakerWaveIcon, UserGroupIcon } from '@heroicons/react/20/solid'
import { useTranslation } from 'react-i18next'

export default function ValueProp() {
    const { t } = useTranslation()

    return (
        <div className="relative isolate overflow-hidden bg-white py-24 sm:py-32 border-t border-gray-200">
            <div className="mx-auto max-w-7xl px-6 lg:px-8">
                <div className="mx-auto max-w-2xl lg:mx-0">
                    <p className="text-base/7 font-semibold text-indigo-600">{t('excursions.value_prop.eyebrow')}</p>
                    <h1 className="mt-2 text-4xl font-semibold tracking-tight text-pretty text-gray-900 sm:text-5xl">
                        {t('excursions.value_prop.title')}
                    </h1>
                    <p className="mt-6 text-xl/8 text-gray-700">
                        {t('excursions.value_prop.description')}
                    </p>
                </div>
                <div className="mx-auto mt-16 grid max-w-2xl grid-cols-1 gap-x-8 gap-y-16 lg:mx-0 lg:mt-10 lg:max-w-none lg:grid-cols-12">
                    <div className="relative lg:order-last lg:col-span-5">
                        <figure className="border-l border-indigo-600 pl-8">
                            <blockquote className="text-xl/8 font-semibold tracking-tight text-gray-900">
                                <p>
                                    "{t('excursions.value_prop.quote')}"
                                </p>
                            </blockquote>
                            <figcaption className="mt-8 flex gap-x-4">
                                <div className="text-sm/6">
                                    <div className="font-semibold text-gray-900">{t('excursions.value_prop.author')}</div>
                                </div>
                            </figcaption>
                        </figure>
                    </div>
                    <div className="max-w-xl text-base/7 text-gray-600 lg:col-span-7">
                        <p>
                            {t('excursions.value_prop.columns_intro')}
                        </p>
                        <ul role="list" className="mt-8 max-w-xl space-y-8 text-gray-600">
                            <li className="flex gap-x-3">
                                <SpeakerWaveIcon aria-hidden="true" className="mt-1 size-5 flex-none text-indigo-600" />
                                <span>
                                    <strong className="font-semibold text-gray-900">{t('excursions.value_prop.features.audio.title')}</strong> {t('excursions.value_prop.features.audio.desc')}
                                </span>
                            </li>
                            <li className="flex gap-x-3">
                                <UserGroupIcon aria-hidden="true" className="mt-1 size-5 flex-none text-indigo-600" />
                                <span>
                                    <strong className="font-semibold text-gray-900">{t('excursions.value_prop.features.engagement.title')}</strong> {t('excursions.value_prop.features.engagement.desc')}
                                </span>
                            </li>
                            <li className="flex gap-x-3">
                                <AcademicCapIcon aria-hidden="true" className="mt-1 size-5 flex-none text-indigo-600" />
                                <span>
                                    <strong className="font-semibold text-gray-900">{t('excursions.value_prop.features.curriculum.title')}</strong> {t('excursions.value_prop.features.curriculum.desc')}
                                </span>
                            </li>
                        </ul>
                        <p className="mt-8">
                            {t('excursions.value_prop.columns_outro')}
                        </p>
                    </div>
                </div>
            </div>
        </div>
    )
}
