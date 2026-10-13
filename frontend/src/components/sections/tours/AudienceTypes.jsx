import { UserGroupIcon, HeartIcon, BuildingOffice2Icon } from '@heroicons/react/20/solid'
import { useTranslation } from 'react-i18next'

export default function AudienceTypes() {
    const { t } = useTranslation()

    return (
        <div className="relative isolate overflow-hidden bg-white py-24 sm:py-32 border-t border-gray-200">
            <div className="mx-auto max-w-7xl px-6 lg:px-8">
                <div className="mx-auto max-w-2xl lg:mx-0 lg:grid lg:max-w-none lg:grid-cols-2 lg:gap-x-16 lg:gap-y-6 xl:grid-cols-1 xl:grid-rows-1 xl:gap-x-8">
                    <h1 className="max-w-2xl text-4xl font-semibold tracking-tight text-balance text-gray-900 sm:text-5xl lg:col-span-2 xl:col-auto">
                        {t('tours.audience.title')}
                    </h1>
                    <div className="mt-6 max-w-xl lg:mt-0 xl:col-end-1 xl:row-start-1">
                        <p className="text-base/7 font-semibold text-indigo-600">{t('tours.audience.eyebrow')}</p>
                        <p className="mt-4 text-xl/8 text-gray-700">
                            {t('tours.audience.description')}
                        </p>
                        <div className="mt-10 max-w-xl text-base/7 text-gray-600 lg:max-w-none">
                            <ul role="list" className="mt-8 space-y-8 text-gray-600">
                                <li className="flex gap-x-3">
                                    <UserGroupIcon aria-hidden="true" className="mt-1 size-5 flex-none text-indigo-600" />
                                    <span>
                                        <strong className="font-semibold text-gray-900">{t('tours.audience.features.travelers.title')}</strong> {t('tours.audience.features.travelers.desc')}
                                    </span>
                                </li>
                                <li className="flex gap-x-3">
                                    <HeartIcon aria-hidden="true" className="mt-1 size-5 flex-none text-indigo-600" />
                                    <span>
                                        <strong className="font-semibold text-gray-900">{t('tours.audience.features.wedding.title')}</strong> {t('tours.audience.features.wedding.desc')}
                                    </span>
                                </li>
                                <li className="flex gap-x-3">
                                    <BuildingOffice2Icon aria-hidden="true" className="mt-1 size-5 flex-none text-indigo-600" />
                                    <span>
                                        <strong className="font-semibold text-gray-900">{t('tours.audience.features.corporate.title')}</strong> {t('tours.audience.features.corporate.desc')}
                                    </span>
                                </li>
                            </ul>
                            <p className="mt-8">
                                {t('tours.audience.closing')}
                            </p>
                        </div>
                    </div>
                    <img
                        alt=""
                        src="/images/casco-2x3.jpg"
                        loading="lazy"
                        className="mt-10 aspect-6/5 w-full max-w-lg rounded-2xl object-cover sm:mt-16 lg:mt-0 lg:max-w-none xl:row-span-2 xl:row-end-2 xl:mt-36"
                    />
                </div>
            </div>
        </div>
    )
}
