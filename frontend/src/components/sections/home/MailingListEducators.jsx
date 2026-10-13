import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { educatorSignup } from '../../../services/educators'

export default function MailingListEducators() {
    const { t, i18n } = useTranslation()
    const navigate = useNavigate()
    const [email, setEmail] = useState('')
    const [isSubmitting, setIsSubmitting] = useState(false)
    const [submitStatus, setSubmitStatus] = useState(null) // 'success' | 'error' | null

    const handleSubmit = async (e) => {
        e.preventDefault()
        setIsSubmitting(true)
        setSubmitStatus(null)

        try {
            const language = i18n.language?.startsWith('es') ? 'es' : 'en'
            const result = await educatorSignup({
                email,
                language,
                source: 'home_cta',
                mailingList: true,
            })

            if (result.success) {
                setSubmitStatus('success')
                setEmail('')
                // Redirect to educational materials after a brief delay
                setTimeout(() => navigate('/materiales-educativos'), 2000)
            } else {
                setSubmitStatus('error')
            }
        } catch (error) {
            console.error('Error registering educator:', error)
            setSubmitStatus('error')
        } finally {
            setIsSubmitting(false)
        }
    }

    return (
        <div className="bg-indigo-700 py-16 sm:py-24 border-t border-gray-200">
            <div className="mx-auto max-w-7xl px-6 lg:px-8">
                <div className="mx-auto max-w-2xl text-center">
                    <h2 className="text-3xl font-semibold tracking-tight text-white sm:text-4xl">
                        {t('home.mailing_educators.title')}
                    </h2>
                    <p className="mt-4 text-lg text-indigo-100">
                        {t('home.mailing_educators.subtitle')}
                    </p>

                    {submitStatus === 'success' ? (
                        <div className="mt-8 rounded-md bg-indigo-800 p-4">
                            <p className="text-indigo-100">
                                {t('home.mailing_educators.success')}
                            </p>
                        </div>
                    ) : (
                        <form onSubmit={handleSubmit} className="mt-8 flex flex-col gap-4 sm:flex-row sm:gap-3 sm:justify-center">
                            <label htmlFor="email-educators" className="sr-only">
                                {t('home.mailing_educators.email_label')}
                            </label>
                            <input
                                id="email-educators"
                                name="email"
                                type="email"
                                required
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                placeholder={t('home.mailing_educators.placeholder')}
                                className="min-w-0 flex-auto rounded-md bg-white/10 px-3.5 py-2 text-base text-white outline-1 -outline-offset-1 outline-white/10 placeholder:text-indigo-200 focus:outline-2 focus:-outline-offset-2 focus:outline-white sm:w-64 sm:text-sm/6"
                            />
                            <button
                                type="submit"
                                disabled={isSubmitting}
                                className="flex-none rounded-md bg-white px-3.5 py-2.5 text-sm font-semibold text-indigo-600 shadow-xs hover:bg-indigo-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                                {isSubmitting ? t('home.mailing_educators.submitting') : t('home.mailing_educators.button')}
                            </button>
                        </form>
                    )}

                    {submitStatus === 'error' && (
                        <p className="mt-4 text-sm text-red-200">
                            {t('home.mailing_educators.error')}
                        </p>
                    )}
                </div>
            </div>
        </div>
    )
}