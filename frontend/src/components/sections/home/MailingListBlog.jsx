import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { subscribe } from '../../../services/subscribe'

export default function MailingListBlog() {
    const { t, i18n } = useTranslation()
    const [email, setEmail] = useState('')
    const [isSubmitting, setIsSubmitting] = useState(false)
    const [submitStatus, setSubmitStatus] = useState(null) // 'success' | 'error' | null

    const handleSubmit = async (e) => {
        e.preventDefault()
        setIsSubmitting(true)
        setSubmitStatus(null)

        try {
            const language = i18n.language?.startsWith('es') ? 'es' : 'en'
            const result = await subscribe(email, 'blog', language)

            if (result.success) {
                setSubmitStatus('success')
                setEmail('')
            } else {
                setSubmitStatus('error')
            }
        } catch (error) {
            console.error('Error submitting to mailing list:', error)
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
                        {t('home.mailing_blog.title')}
                    </h2>
                    <p className="mt-4 text-lg text-indigo-100">
                        {t('home.mailing_blog.subtitle')}
                    </p>

                    {submitStatus === 'success' ? (
                        <div className="mt-8 rounded-md bg-indigo-800 p-4">
                            <p className="text-indigo-100">
                                {t('home.mailing_blog.success')}
                            </p>
                        </div>
                    ) : (
                        <form onSubmit={handleSubmit} className="mt-8 flex flex-col gap-4 sm:flex-row sm:gap-3 sm:justify-center">
                            <label htmlFor="email-blog" className="sr-only">
                                {t('home.mailing_blog.email_label')}
                            </label>
                            <input
                                id="email-blog"
                                name="email"
                                type="email"
                                required
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                placeholder={t('home.mailing_blog.placeholder')}
                                className="min-w-0 flex-auto rounded-md bg-white/10 px-3.5 py-2 text-base text-white outline-1 -outline-offset-1 outline-white/10 placeholder:text-indigo-200 focus:outline-2 focus:-outline-offset-2 focus:outline-white sm:w-64 sm:text-sm/6"
                            />
                            <button
                                type="submit"
                                disabled={isSubmitting}
                                className="flex-none rounded-md bg-white px-3.5 py-2.5 text-sm font-semibold text-indigo-600 shadow-xs hover:bg-indigo-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                                {isSubmitting ? t('home.mailing_blog.submitting') : t('home.mailing_blog.button')}
                            </button>
                        </form>
                    )}

                    {submitStatus === 'error' && (
                        <p className="mt-4 text-sm text-red-200">
                            {t('home.mailing_blog.error')}
                        </p>
                    )}
                </div>
            </div>
        </div>
    )
}
