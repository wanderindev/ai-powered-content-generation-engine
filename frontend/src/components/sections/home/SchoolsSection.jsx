import { AcademicCapIcon, BookOpenIcon, UserGroupIcon } from '@heroicons/react/24/outline'
import { useTranslation } from 'react-i18next'

export default function SchoolsSection() {
    const { t } = useTranslation()

    const features = [
        {
            key: 'expert',
            icon: AcademicCapIcon,
        },
        {
            key: 'curriculum',
            icon: BookOpenIcon,
        },
        {
            key: 'adaptable',
            icon: UserGroupIcon,
        },
    ]

    return (
        <div className="bg-white py-24 sm:py-32 border-t border-gray-200">
            <div className="mx-auto max-w-7xl px-6 lg:px-8">
                <div className="mx-auto max-w-2xl lg:text-center">
                    <h2 className="text-base/7 font-semibold text-indigo-600">{t('home.schools.eyebrow')}</h2>
                    <p className="mt-2 text-4xl font-semibold tracking-tight text-pretty text-gray-900 sm:text-5xl lg:text-balance">
                        {t('home.schools.title')}
                    </p>
                    <p className="mt-6 text-lg/8 text-gray-600">
                        {t('home.schools.subtitle')}
                    </p>
                </div>
                <div className="mx-auto mt-16 max-w-2xl sm:mt-20 lg:mt-24 lg:max-w-none">
                    <dl className="grid max-w-xl grid-cols-1 gap-x-8 gap-y-16 lg:max-w-none lg:grid-cols-3">
                        {features.map((feature) => (
                            <div key={feature.key} className="flex flex-col">
                                <dt className="flex items-center gap-x-3 text-base/7 font-semibold text-gray-900">
                                    <feature.icon aria-hidden="true" className="size-5 flex-none text-indigo-600" />
                                    {t(`home.schools.features.${feature.key}.title`)}
                                </dt>
                                <dd className="mt-4 flex flex-auto flex-col text-base/7 text-gray-600">
                                    <p className="flex-auto">{t(`home.schools.features.${feature.key}.desc`)}</p>
                                </dd>
                            </div>
                        ))}
                    </dl>
                </div>
                <div className="mt-16 flex flex-col items-center justify-center gap-4 sm:flex-row sm:gap-x-6">
                    <a
                        href="/excursiones-academicas"
                        className="rounded-md bg-indigo-600 px-3.5 py-2.5 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
                    >
                        {t('home.schools.cta_learn')}
                    </a>
                    <a
                        href="/contacto?interest=field-trips"
                        className="text-sm/6 font-semibold text-gray-900 hover:text-indigo-600"
                    >
                        {t('home.schools.cta_contact')} <span aria-hidden="true">→</span>
                    </a>
                </div>
            </div>
        </div>
    )
}
