import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { articleImageUrl } from '../../../utils/articleImage'
import { getLatestSeries } from '../../../services/articles'

export default function FeaturedArticles() {
    const { t, i18n } = useTranslation()
    const [seriesData, setSeriesData] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)

    useEffect(() => {
        async function fetchSeries() {
            try {
                setLoading(true)
                const data = await getLatestSeries(i18n.language)
                setSeriesData(data)
                setError(null)
            } catch (err) {
                console.error('Failed to fetch latest series:', err)
                setError(err.message)
            } finally {
                setLoading(false)
            }
        }
        fetchSeries()
    }, [i18n.language])

    // Build article URL with language prefix for SEO
    const getArticleUrl = (slug) => {
        if (i18n.language === 'es') {
            return `/es/articulos/${slug}`
        }
        return `/en/articles/${slug}`
    }

    if (loading) {
        return (
            <div className="bg-white py-24 sm:py-32 border-t border-gray-200">
                <div className="mx-auto max-w-7xl px-6 lg:px-8">
                    <div className="mx-auto max-w-2xl text-center">
                        <div className="animate-pulse">
                            <div className="h-4 bg-gray-200 rounded w-24 mx-auto mb-4"></div>
                            <div className="h-10 bg-gray-200 rounded w-64 mx-auto mb-4"></div>
                            <div className="h-6 bg-gray-200 rounded w-96 mx-auto"></div>
                        </div>
                    </div>
                </div>
            </div>
        )
    }

    if (error || !seriesData || !seriesData.articles?.length) {
        return null // Don't show section if no articles
    }

    return (
        <div className="bg-white py-24 sm:py-32 border-t border-gray-200">
            <div className="mx-auto max-w-7xl px-6 lg:px-8">
                <div className="mx-auto max-w-2xl text-center">
                    <h2 className="text-base/7 font-semibold text-indigo-600">{t('home.blog.eyebrow')}</h2>
                    <p className="mt-2 text-4xl font-semibold tracking-tight text-balance text-gray-900 sm:text-5xl">
                        {t('home.blog.title')}
                    </p>
                    <p className="mt-4 text-lg/8 text-gray-600">
                        {t('home.blog.subtitle')}
                    </p>
                </div>

                {/* Series Title Header */}
                <div className="mt-12 text-center">
                    <p className="text-lg text-gray-700">
                        {t('home.blog.seriesHeader')}
                        <br />
                        <span className="font-semibold text-indigo-600">{seriesData.series_title}</span>
                    </p>
                </div>

                <div className="mx-auto mt-10 grid max-w-2xl grid-cols-1 gap-x-8 gap-y-16 lg:mx-0 lg:max-w-none lg:grid-cols-2">
                    {seriesData.articles.map((article) => (
                        <article key={article.id} className="flex flex-col items-start justify-between">
                            <div className="relative w-full">
                                <a href={getArticleUrl(article.slug)}>
                                    <img
                                        alt={article.title}
                                        src={articleImageUrl(article.feature_image_url)}
                                        loading="lazy"
                                        className="aspect-video w-full rounded-2xl bg-gray-100 object-cover sm:aspect-2/1 lg:aspect-3/2"
                                    />
                                    <div className="absolute inset-0 rounded-2xl ring-1 ring-inset ring-gray-900/10" />
                                </a>
                            </div>
                            <div className="flex max-w-xl grow flex-col justify-between">
                                <div className="mt-6 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs">
                                    <span className="rounded-full bg-indigo-50 px-3 py-1.5 font-medium text-indigo-600">
                                        {article.taxonomy_name}
                                    </span>
                                    <span className="rounded-full bg-gray-100 px-3 py-1.5 font-medium text-gray-600">
                                        {article.category_name}
                                    </span>
                                </div>
                                <div className="group relative grow">
                                    <h3 className="mt-3 text-lg/6 font-semibold text-gray-900 group-hover:text-gray-600">
                                        <a href={getArticleUrl(article.slug)}>
                                            <span className="absolute inset-0" />
                                            {article.title}
                                        </a>
                                    </h3>
                                    <p className="mt-4 text-sm/6 text-gray-600">
                                        {article.excerpt}
                                    </p>
                                </div>
                                {article.tags?.length > 0 && (
                                    <div className="flex flex-wrap items-center gap-2 mt-4">
                                        {article.tags.map(tag => (
                                            <span key={tag.id} className="rounded-full bg-gray-50 px-2.5 py-1 text-xs text-gray-500 ring-1 ring-inset ring-gray-200">
                                                {tag.name}
                                            </span>
                                        ))}
                                    </div>
                                )}
                            </div>
                        </article>
                    ))}
                </div>
                <div className="mt-16 text-center">
                    <a
                        href={`${i18n.language === 'es' ? '/es' : '/en'}/blog`}
                        className="text-sm/6 font-semibold text-indigo-600 hover:text-indigo-500"
                    >
                        {t('home.blog.cta')} <span aria-hidden="true">&rarr;</span>
                    </a>
                </div>
            </div>
        </div>
    )
}
