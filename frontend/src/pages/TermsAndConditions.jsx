import { useTranslation } from 'react-i18next'

export default function TermsAndConditions() {
    const { t } = useTranslation()

    const sections = [
        'acceptance',
        'intellectual_property',
        'edu_materials_disclaimer',
        'tour_bookings',
        'cancellation_refunds',
        'force_majeure',
        'photo_video',
        'liability',
        'school_excursions',
        'children',
        'governing_law',
        'changes',
        'contact',
    ]

    return (
        <div className="mx-auto max-w-3xl px-6 lg:px-8 py-24 sm:py-32">
            <h1 className="text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
                {t('terms.title')}
            </h1>
            <p className="mt-2 text-sm text-gray-500">
                {t('terms.effective_date')}
            </p>

            <div className="mt-10 space-y-8">
                {sections.map((key) => (
                    <section key={key}>
                        <h2 className="text-lg font-semibold text-gray-900">
                            {t(`terms.sections.${key}.title`)}
                        </h2>
                        <p className="mt-2 text-sm leading-6 text-gray-600 whitespace-pre-line">
                            {t(`terms.sections.${key}.body`)}
                        </p>
                    </section>
                ))}
            </div>
        </div>
    )
}