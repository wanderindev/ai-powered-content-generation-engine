import { useState, useEffect, useRef } from 'react'
import { useParams, useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import ArticleContent from '../components/sections/blog/ArticleContent.jsx'
import { getArticleBySlug } from '../services/articles'

export default function Article() {
    const { slug } = useParams()
    const location = useLocation()
    const navigate = useNavigate()
    const { t, i18n } = useTranslation()
    const [article, setArticle] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const lastFetchedLang = useRef(null)

    // Extract language from URL path
    const getUrlLanguage = () => {
        const pathParts = location.pathname.split('/')
        if (pathParts[1] === 'en' || pathParts[1] === 'es') {
            return pathParts[1]
        }
        return null
    }

    // Build the correct article path
    const buildArticlePath = (lang, articleSlug) => {
        if (lang === 'es') {
            return `/es/articulos/${articleSlug}`
        }
        return `/en/articles/${articleSlug}`
    }

    // Sync i18n language with URL language on mount
    useEffect(() => {
        const urlLang = getUrlLanguage()
        if (urlLang && urlLang !== i18n.language) {
            i18n.changeLanguage(urlLang)
        }
    }, [location.pathname])

    // Fetch article and handle navigation
    useEffect(() => {
        async function fetchArticle() {
            if (!slug) return

            const targetLang = i18n.language

            // Skip if we just fetched this language (prevents double fetch)
            if (lastFetchedLang.current === targetLang && article) {
                return
            }

            try {
                setLoading(true)
                setError(null)
                const data = await getArticleBySlug(slug, targetLang)
                lastFetchedLang.current = targetLang
                setArticle(data)

                // Navigate to correct URL with proper slug for this language
                const correctPath = buildArticlePath(targetLang, data.slug)
                if (location.pathname !== correctPath) {
                    navigate(correctPath, { replace: true })
                }
            } catch (err) {
                console.error('Failed to fetch article:', err)
                setError(err.message)
            } finally {
                setLoading(false)
            }
        }
        fetchArticle()
    }, [slug, i18n.language])

    if (loading) {
        return (
            <div className="bg-white py-16">
                <div className="mx-auto max-w-5xl px-6 lg:px-8">
                    <div className="animate-pulse">
                        <div className="aspect-video w-full bg-gray-200 rounded-2xl mb-8"></div>
                    </div>
                </div>
                <div className="mx-auto max-w-3xl px-6 lg:px-8">
                    <div className="animate-pulse space-y-6">
                        <div className="flex gap-3">
                            <div className="h-6 w-20 bg-gray-200 rounded-full"></div>
                            <div className="h-6 w-24 bg-gray-200 rounded-full"></div>
                        </div>
                        <div className="h-12 bg-gray-200 rounded w-3/4"></div>
                        <div className="h-6 bg-gray-200 rounded w-full"></div>
                        <div className="h-6 bg-gray-200 rounded w-5/6"></div>
                        <div className="mt-10 space-y-4">
                            <div className="h-4 bg-gray-200 rounded w-full"></div>
                            <div className="h-4 bg-gray-200 rounded w-full"></div>
                            <div className="h-4 bg-gray-200 rounded w-4/5"></div>
                        </div>
                    </div>
                </div>
            </div>
        )
    }

    if (error || !article) {
        const langPrefix = i18n.language === 'es' ? '/es' : '/en'
        return (
            <div className="bg-white py-24 sm:py-32">
                <div className="mx-auto max-w-3xl px-6 lg:px-8 text-center">
                    <h1 className="text-3xl font-semibold text-gray-900 mb-4">
                        {t('article.notFound', 'Article not found')}
                    </h1>
                    <p className="text-gray-600 mb-8">
                        {t('article.notFoundDescription', 'The article you are looking for does not exist or has been removed.')}
                    </p>
                    <a
                        href={`${langPrefix}/blog`}
                        className="rounded-md bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500"
                    >
                        {t('article.backToBlog', 'Back to Blog')}
                    </a>
                </div>
            </div>
        )
    }

    return <ArticleContent article={article} />
}
