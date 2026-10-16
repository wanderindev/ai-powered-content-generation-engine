import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChatBubbleLeftRightIcon, ExclamationTriangleIcon } from '@heroicons/react/24/outline'
import { askQuestion } from '../services/ask'
import { useAskStatus } from '../hooks/useAskStatus'
import { trackEvent } from '../analytics'

// Public Ask PIC page (#636, epic #619): /ask (EN) and /preguntar (ES).
//
// The answer is untrusted model output and is rendered as a plain React text
// node only — never dangerouslySetInnerHTML, never markdown-to-HTML — so a
// prompt-injection attempt cannot produce markup.

const PATH_LANG = { '/ask': 'en', '/preguntar': 'es' }
const LANG_PATH = { en: '/ask', es: '/preguntar' }
const DEFAULT_MAX_CHARS = 500 // backend ask_pic_max_question_chars default

// Citation urls come from the backend as site paths (/en/articles/<slug>,
// /es/articulos/<slug>); refuse anything that is not a same-site path. A
// backslash is refused too: browsers normalise `/\evil.com` to `//evil.com`.
function isSitePath(url) {
    return (
        typeof url === 'string' &&
        url.startsWith('/') &&
        !url.startsWith('//') &&
        !url.includes('\\')
    )
}

export default function AskPic() {
    const { t, i18n } = useTranslation()
    const location = useLocation()
    const navigate = useNavigate()
    const status = useAskStatus()
    const [question, setQuestion] = useState('')
    const [result, setResult] = useState(null) // null = idle
    const [loading, setLoading] = useState(false)

    const lang = i18n.language === 'en' ? 'en' : 'es'
    const urlLang = PATH_LANG[location.pathname.replace(/\/+$/, '')]

    // The path decides the language on arrival (like /en/blog and /es/blog) ...
    useEffect(() => {
        if (urlLang && urlLang !== i18n.language) i18n.changeLanguage(urlLang)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [location.pathname])

    // ... and a later language switch (the navbar toggle) moves to the other
    // path. Only on a real change: on mount the language may still be the
    // stored one, and navigating then would undo the path's choice above.
    const prevLang = useRef(lang)
    useEffect(() => {
        if (prevLang.current === lang) return
        prevLang.current = lang
        if (urlLang && urlLang !== lang) navigate(LANG_PATH[lang], { replace: true })
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [lang])

    const maxChars = status?.max_question_chars || DEFAULT_MAX_CHARS
    const trimmed = question.trim()
    const canSubmit = !loading && trimmed.length > 0 && trimmed.length <= maxChars

    async function handleSubmit(e) {
        e.preventDefault()
        if (!canSubmit) return
        setLoading(true)
        const outcome = await askQuestion(trimmed, lang)
        setResult(outcome)
        setLoading(false)
        // Outcome and UI language only: never the question text (PII).
        trackEvent('ask_pic_question', { outcome: outcome.kind, language: lang })
    }

    const disabled = status !== null && !status.enabled
    const unavailable = disabled || result?.kind === 'unavailable'

    return (
        <div className="bg-white py-24 sm:py-32">
            <div className="mx-auto max-w-3xl px-6 lg:px-8">
                <p className="text-base font-semibold text-indigo-600">{t('ask_pic.eyebrow')}</p>
                <h1 className="mt-2 text-4xl font-semibold tracking-tight text-pretty text-gray-900 sm:text-5xl">
                    {t('ask_pic.title')}
                </h1>
                <p className="mt-6 text-lg/8 text-gray-600">{t('ask_pic.subtitle')}</p>

                {unavailable ? (
                    <Notice title={t('ask_pic.unavailable.title')} body={t('ask_pic.unavailable.body')}>
                        <BlogLink lang={lang} label={t('ask_pic.browse_blog')} />
                    </Notice>
                ) : (
                    <>
                        <form onSubmit={handleSubmit} className="mt-10">
                            <label htmlFor="ask-question" className="block text-sm/6 font-medium text-gray-900">
                                {t('ask_pic.form.label')}
                            </label>
                            <div className="mt-2">
                                <textarea
                                    id="ask-question"
                                    name="question"
                                    rows={3}
                                    maxLength={maxChars}
                                    value={question}
                                    onChange={(e) => setQuestion(e.target.value)}
                                    placeholder={t('ask_pic.form.placeholder')}
                                    className="block w-full rounded-md bg-white px-3 py-2 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6"
                                />
                            </div>
                            <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                                <p className="text-xs text-gray-500">
                                    {t('ask_pic.form.counter', { count: question.length, max: maxChars })}
                                </p>
                                <button
                                    type="submit"
                                    disabled={!canSubmit}
                                    className="rounded-md bg-indigo-600 px-3.5 py-2.5 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                    {loading ? t('ask_pic.form.submitting') : t('ask_pic.form.submit')}
                                </button>
                            </div>
                            <p className="mt-4 text-xs text-gray-500">{t('ask_pic.form.disclaimer')}</p>
                        </form>

                        <div aria-live="polite" className="mt-10">
                            {loading ? (
                                <div className="animate-pulse space-y-3 rounded-lg bg-gray-50 p-6 ring-1 ring-inset ring-gray-200">
                                    <p className="text-sm text-gray-600">{t('ask_pic.loading')}</p>
                                    <div className="h-4 w-full rounded bg-gray-200" />
                                    <div className="h-4 w-5/6 rounded bg-gray-200" />
                                    <div className="h-4 w-2/3 rounded bg-gray-200" />
                                </div>
                            ) : (
                                result && <Result result={result} lang={lang} t={t} />
                            )}
                        </div>
                    </>
                )}
            </div>
        </div>
    )
}

function Result({ result, lang, t }) {
    if (result.kind === 'answered') {
        const citations = result.citations.filter((c) => isSitePath(c?.url))
        return (
            <div className="rounded-lg bg-gray-50 p-6 ring-1 ring-inset ring-gray-200">
                <div className="flex items-center gap-2 text-sm font-semibold text-indigo-600">
                    <ChatBubbleLeftRightIcon aria-hidden="true" className="size-5" />
                    {t('ask_pic.answered.heading')}
                </div>
                {/* Plain text on purpose: untrusted model output. */}
                <p className="mt-4 text-base/7 whitespace-pre-line text-gray-700">{result.answer}</p>
                {citations.length > 0 && (
                    <div className="mt-6 border-t border-gray-200 pt-4">
                        <p className="text-sm font-semibold text-gray-900">{t('ask_pic.answered.sources')}</p>
                        <ul className="mt-2 space-y-1">
                            {citations.map((c) => (
                                <li key={`${c.article_id}-${c.url}`}>
                                    <Link to={c.url} className="text-sm font-medium text-indigo-600 hover:text-indigo-500">
                                        {c.title || t('ask_pic.answered.untitled')}
                                    </Link>
                                </li>
                            ))}
                        </ul>
                    </div>
                )}
            </div>
        )
    }

    const copy = {
        not_covered: 'not_covered',
        refused: 'refused',
        invalid: 'invalid',
        rate_limited: 'rate_limited',
        budget: 'budget',
    }[result.kind] || 'error'
    const showBlog = copy === 'not_covered' || copy === 'refused'

    return (
        <Notice title={t(`ask_pic.${copy}.title`)} body={t(`ask_pic.${copy}.body`)}>
            {showBlog && <BlogLink lang={lang} label={t('ask_pic.browse_blog')} />}
        </Notice>
    )
}

function Notice({ title, body, children }) {
    return (
        <div className="mt-10 rounded-lg bg-gray-50 p-6 ring-1 ring-inset ring-gray-200">
            <div className="flex gap-3">
                <ExclamationTriangleIcon aria-hidden="true" className="size-6 shrink-0 text-indigo-600" />
                <div>
                    <p className="text-base font-semibold text-gray-900">{title}</p>
                    <p className="mt-1 text-sm text-gray-600">{body}</p>
                    {children && <div className="mt-4">{children}</div>}
                </div>
            </div>
        </div>
    )
}

function BlogLink({ lang, label }) {
    return (
        <Link to={`/${lang}/blog`} className="text-sm font-semibold text-indigo-600 hover:text-indigo-500">
            {label} <span aria-hidden="true">&rarr;</span>
        </Link>
    )
}
