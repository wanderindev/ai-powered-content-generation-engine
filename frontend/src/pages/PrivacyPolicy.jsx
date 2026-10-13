import { useTranslation } from 'react-i18next'

export default function PrivacyPolicy() {
    const { t } = useTranslation()

    const sections = [
        'info_we_collect',
        'analytics',
        'contact_form',
        'mailing_list',
        'educator_access',
        'third_party',
        'data_retention',
        'children',
        'your_rights',
        'changes',
        'contact',
    ]

    return (
        <div className="mx-auto max-w-3xl px-6 lg:px-8 py-24 sm:py-32">
            <h1 className="text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
                {t('privacy.title')}
            </h1>
            <p className="mt-2 text-sm text-gray-500">
                {t('privacy.effective_date')}
            </p>

            <div className="mt-10 space-y-8">
                {sections.map((key) => (
                    <section key={key}>
                        <h2 className="text-lg font-semibold text-gray-900">
                            {t(`privacy.sections.${key}.title`)}
                        </h2>
                        <p className="mt-2 text-sm leading-6 text-gray-600 whitespace-pre-line">
                            {t(`privacy.sections.${key}.body`)}
                        </p>
                    </section>
                ))}
            </div>
        </div>
    )
}