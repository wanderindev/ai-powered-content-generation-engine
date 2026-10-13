import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { MagnifyingGlassIcon, XMarkIcon } from '@heroicons/react/20/solid'
import { getArticles } from '../../../services/articles'

export default function BlogList() {
    const { t, i18n } = useTranslation()
    const [articles, setArticles] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [searchInput, setSearchInput] = useState('')
    const [searchQuery, setSearchQuery] = useState('')

    const handleSearch = () => {
        setSearchQuery(searchInput.trim())
    }

    const handleClear = () => {
        setSearchInput('')
        setSearchQuery('')
    }

    useEffect(() => {
        async function fetchArticles() {
            try {
                setLoading(true)
                const data = await getArticles(i18n.language, 100, searchQuery)
                // Sort by ID ascending (chronological order for history content)
                const sorted = [...data].sort((a, b) => a.id - b.id)
                setArticles(sorted)
                setError(null)
            } catch (err) {
                console.error('Failed to fetch articles:', err)
                setError(err.message)
            } finally {
                setLoading(false)
            }
        }
        fetchArticles()
    }, [i18n.language, searchQuery])

    // Build image URL (handle relative paths from API)
    const getImageUrl = (path) => {
        if (!path) return '/images/placeholder-article.jpg'
        if (path.startsWith('http')) return path
        const apiBase = import.meta.env.VITE_API_URL || 'https://api.panamaincontext.com'
        return `${apiBase}${path}`
    }

    // Build article URL with language prefix for SEO
    const getArticleUrl = (slug) => {
        if (i18n.language === 'es') {
            return `/es/articulos/${slug}`
        }
        return `/en/articles/${slug}`
    }

    if (loading) {
        return (
            <div className="bg-white py-24 sm:py-32">
                <div className="mx-auto max-w-7xl px-6 lg:px-8">
                    <div className="mx-auto max-w-2xl lg:max-w-4xl">
                        <div className="animate-pulse space-y-8">
                            {[1, 2, 3].map((i) => (
                                <div key={i} className="flex gap-8">
                                    <div className="w-80 h-44 bg-gray-200 rounded-2xl"></div>
                                    <div className="flex-1 space-y-4">
                                        <div className="h-4 bg-gray-200 rounded w-1/4"></div>
                                        <div className="h-6 bg-gray-200 rounded w-3/4"></div>
                                        <div className="h-4 bg-gray-200 rounded w-full"></div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            </div>
        )
    }

    if (error) {
        return (
            <div className="bg-white py-24 sm:py-32">
                <div className="mx-auto max-w-7xl px-6 lg:px-8 text-center">
                    <p className="text-gray-600">{t('blog.error', 'Failed to load articles')}</p>
                </div>
            </div>
        )
    }

    return (
        <div className="bg-white py-24 sm:py-32">
            <div className="mx-auto max-w-7xl px-6 lg:px-8">
                <div className="mx-auto max-w-2xl lg:max-w-4xl">
                    <h2 className="text-4xl font-semibold tracking-tight text-pretty text-gray-900 sm:text-5xl">
                        {t('blog.title', 'Our Blog')}
                    </h2>
                    <p className="mt-2 text-lg/8 text-gray-600">
                        {t('blog.subtitle', 'Discover the rich history and culture of Panama through our articles.')}
                    </p>
                    <div className="mt-8 flex max-w-md gap-2">
                        <div className="relative flex-1">
                            <input
                                type="text"
                                value={searchInput}
                                onChange={(e) => setSearchInput(e.target.value)}
                                onKeyDown={(e) => { if (e.key === 'Enter') handleSearch() }}
                                placeholder={t('blog.search_placeholder', 'Search articles...')}
                                className="block w-full rounded-lg border-0 py-2.5 pl-4 pr-9 text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 placeholder:text-gray-400 focus:ring-2 focus:ring-inset focus:ring-indigo-600 sm:text-sm/6"
                            />
                            {searchInput && (
                                <button
                                    onClick={handleClear}
                                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                                >
                                    <XMarkIcon className="size-5" />
                                </button>
                            )}
                        </div>
                        <button
                            onClick={handleSearch}
                            className="inline-flex items-center rounded-lg bg-indigo-600 px-3.5 py-2.5 text-white shadow-sm hover:bg-indigo-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
                        >
                            <MagnifyingGlassIcon className="size-5" />
                        </button>
                    </div>
                    {searchQuery && !loading && (
                        <p className="mt-3 text-sm text-gray-500">
                            {articles.length === 0
                                ? t('blog.no_results', 'No articles found for "{{query}}"', { query: searchQuery })
                                : t('blog.results_count', '{{count}} article(s) found', { count: articles.length })
                            }
                        </p>
                    )}
                    <div className="mt-16 space-y-20 lg:mt-20">
                        {articles.map((article) => (
                            <article key={article.id} className="relative isolate flex flex-col gap-8 lg:flex-row">
                                <div className="relative aspect-video lg:aspect-[16/9] lg:w-80 lg:shrink-0">
                                    <a href={getArticleUrl(article.slug)}>
                                        <img
                                            alt={article.title}
                                            src={getImageUrl(article.feature_image_url)}
                                            loading="lazy"
                                            className="absolute inset-0 size-full rounded-2xl bg-gray-50 object-cover"
                                        />
                                        <div className="absolute inset-0 rounded-2xl ring-1 ring-inset ring-gray-900/10" />
                                    </a>
                                </div>
                                <div>
                                    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs">
                                        <span className="rounded-full bg-indigo-50 px-3 py-1.5 font-medium text-indigo-600">
                                            {article.taxonomy_name}
                                        </span>
                                        <span className="rounded-full bg-gray-100 px-3 py-1.5 font-medium text-gray-600">
                                            {article.category_name}
                                        </span>
                                    </div>
                                    <div className="group relative max-w-xl">
                                        <h3 className="mt-3 text-lg/6 font-semibold text-gray-900 group-hover:text-gray-600">
                                            <a href={getArticleUrl(article.slug)}>
                                                <span className="absolute inset-0" />
                                                {article.title}
                                            </a>
                                        </h3>
                                        <p className="mt-5 text-sm/6 text-gray-600">{article.excerpt}</p>
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
                </div>
            </div>
        </div>
    )
}
