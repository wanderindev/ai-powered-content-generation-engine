import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { articleImageUrl } from '../../../utils/articleImage'
import { SpeakerWaveIcon } from '@heroicons/react/20/solid'
import { getArticles } from '../../../services/articles'
import AskPicCallout from './AskPicCallout.jsx'

// Cut an excerpt at a word boundary near maxChars, appending an ellipsis
// only when something was actually trimmed.
function truncateExcerpt(text, maxChars = 250) {
    if (!text || text.length <= maxChars) return text || ''
    const cut = text.slice(0, maxChars)
    const lastSpace = cut.lastIndexOf(' ')
    return `${cut.slice(0, lastSpace > 0 ? lastSpace : maxChars).trimEnd()}…`
}

// Group consecutive articles by suggestion_id. The API list is already
// ordered category → suggestion → research → series, so one pass suffices;
// an article without a suggestion becomes its own group.
function groupBySuggestion(articles) {
    const groups = []
    for (const article of articles) {
        const last = groups[groups.length - 1]
        if (last && article.suggestion_id != null && last.id === article.suggestion_id) {
            last.articles.push(article)
        } else {
            groups.push({
                id: article.suggestion_id ?? `article-${article.id}`,
                title: article.suggestion_title || article.title,
                articles: [article],
            })
        }
    }
    return groups
}

export default function BlogList() {
    const { t, i18n } = useTranslation()
    const [articles, setArticles] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    // Series order only: the API list is oldest → newest history, and inside a
    // series the articles keep their series_order either way.
    const [newestFirst, setNewestFirst] = useState(false)

    useEffect(() => {
        async function fetchArticles() {
            try {
                setLoading(true)
                // API order is the blog order (historical chronology) — do not re-sort.
                const data = await getArticles(i18n.language, 100)
                setArticles(data)
                setError(null)
            } catch (err) {
                console.error('Failed to fetch articles:', err)
                setError(err.message)
            } finally {
                setLoading(false)
            }
        }
        fetchArticles()
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
            <div className="bg-white py-24 sm:py-32">
                <div className="mx-auto px-6 lg:px-12">
                    <div className="mx-auto max-w-2xl text-center animate-pulse">
                        <div className="h-10 bg-gray-200 rounded w-2/3 mx-auto"></div>
                        <div className="mt-4 h-5 bg-gray-200 rounded w-full"></div>
                    </div>
                    <div className="mt-16 grid grid-cols-1 gap-x-8 gap-y-12 sm:grid-cols-2 lg:grid-cols-3 animate-pulse">
                        {[1, 2, 3].map((i) => (
                            <div key={i} className="space-y-4">
                                <div className="aspect-video bg-gray-200 rounded-2xl"></div>
                                <div className="h-6 bg-gray-200 rounded w-3/4"></div>
                                <div className="h-4 bg-gray-200 rounded w-full"></div>
                                <div className="h-4 bg-gray-200 rounded w-5/6"></div>
                            </div>
                        ))}
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

    const groups = groupBySuggestion(articles)
    const orderedGroups = newestFirst ? [...groups].reverse() : groups

    return (
        <div className="bg-white py-24 sm:py-32">
            <div className="mx-auto px-6 lg:px-12">
                <div className="mx-auto max-w-2xl text-center">
                    <h2 className="text-4xl font-semibold tracking-tight text-pretty text-gray-900 sm:text-5xl">
                        {t('blog.title', 'Our Blog')}
                    </h2>
                    <p className="mt-2 text-lg/8 text-gray-600">
                        {t('blog.subtitle', 'Discover the rich history and culture of Panama through our articles.')}
                    </p>
                </div>

                <AskPicCallout className="mt-10" />

                <div className="mt-10 flex items-center justify-center gap-3">
                    <span id="blog-sort-label" className="text-sm text-gray-600">
                        {t('blog.sort_label', 'Series order')}
                    </span>
                    <div
                        role="group"
                        aria-labelledby="blog-sort-label"
                        className="inline-flex rounded-full bg-gray-50 p-1 ring-1 ring-inset ring-gray-200"
                    >
                        {[
                            { value: false, label: t('blog.sort_oldest', 'Oldest first') },
                            { value: true, label: t('blog.sort_newest', 'Newest first') },
                        ].map((option) => (
                            <button
                                key={String(option.value)}
                                type="button"
                                aria-pressed={newestFirst === option.value}
                                onClick={() => setNewestFirst(option.value)}
                                className={`rounded-full px-3 py-1 text-sm font-medium transition ${
                                    newestFirst === option.value
                                        ? 'bg-white text-gray-900 shadow-sm ring-1 ring-inset ring-gray-200'
                                        : 'text-gray-500 hover:text-gray-900'
                                }`}
                            >
                                {option.label}
                            </button>
                        ))}
                    </div>
                </div>

                <div className="mt-12 space-y-20 lg:mt-16">
                    {orderedGroups.map((group) => (
                        <section key={group.id}>
                            <h3 className="text-center text-2xl font-semibold tracking-tight text-gray-900">
                                {group.title}
                            </h3>
                            <div className="mt-8 grid grid-cols-1 gap-x-8 gap-y-12 sm:grid-cols-2 lg:grid-cols-3">
                                {group.articles.map((article) => (
                                    <article key={article.id} className="group relative flex flex-col">
                                        <div className="relative aspect-video">
                                            <img
                                                alt={article.title}
                                                src={articleImageUrl(article.feature_image_url)}
                                                loading="lazy"
                                                className="absolute inset-0 size-full rounded-2xl bg-gray-50 object-cover"
                                            />
                                            <div className="absolute inset-0 rounded-2xl ring-1 ring-inset ring-gray-900/10" />
                                        </div>
                                        <h4 className="mt-4 text-lg/6 font-semibold text-gray-900 group-hover:text-gray-600">
                                            <a href={getArticleUrl(article.slug)}>
                                                <span className="absolute inset-0" />
                                                {article.title}
                                            </a>
                                        </h4>
                                        <p className="mt-3 text-sm/6 text-gray-600">
                                            {truncateExcerpt(article.excerpt)}
                                        </p>
                                        {article.tags?.length > 0 && (
                                            <div className="mt-4 flex flex-wrap items-center gap-2">
                                                {article.tags.map(tag => (
                                                    <span key={tag.id} className="rounded-full bg-gray-50 px-2.5 py-1 text-xs text-gray-500 ring-1 ring-inset ring-gray-200">
                                                        {tag.name}
                                                    </span>
                                                ))}
                                            </div>
                                        )}
                                        {article.has_audio && (
                                            <p className="mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-indigo-600">
                                                <SpeakerWaveIcon className="size-4" />
                                                {t('blog.listen_available', 'Escuche este artículo, leído por el autor.')}
                                            </p>
                                        )}
                                    </article>
                                ))}
                            </div>
                        </section>
                    ))}
                </div>
            </div>
        </div>
    )
}
