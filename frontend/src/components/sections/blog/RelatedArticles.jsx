import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { articleImageUrl } from '../../../utils/articleImage'
import { getRelatedArticles } from '../../../services/articles'

const RELATED_COUNT = 3

// "Related articles" under an article (#629). Renders nothing while loading,
// on an empty list and on any error. Mount it with a key per article + language
// so a change starts from an empty list instead of resetting state in an effect.
export default function RelatedArticles({ articleId, language }) {
    const { t } = useTranslation()
    const [items, setItems] = useState([])

    useEffect(() => {
        let cancelled = false
        // Async IIFE: setState stays out of the effect's synchronous body
        // (react-hooks/set-state-in-effect).
        ;(async () => {
            try {
                const data = await getRelatedArticles(articleId, language, RELATED_COUNT)
                if (!cancelled && Array.isArray(data)) setItems(data)
            } catch (err) {
                console.error('Failed to fetch related articles:', err)
            }
        })()
        return () => {
            cancelled = true
        }
    }, [articleId, language])

    const getArticleUrl = (slug) =>
        language === 'es' ? `/es/articulos/${slug}` : `/en/articles/${slug}`

    if (!items.length) return null

    return (
        <section
            aria-labelledby="related-articles-title"
            className="bg-white border-t border-gray-200 py-16 sm:py-20"
        >
            <div className="mx-auto max-w-5xl px-6 lg:px-8">
                <h2
                    id="related-articles-title"
                    className="text-2xl font-semibold tracking-tight text-gray-900 sm:text-3xl"
                >
                    {t('blog.related_title')}
                </h2>
                <p className="mt-2 text-base/7 text-gray-600">{t('blog.related_subtitle')}</p>

                <div className="mt-10 grid grid-cols-1 gap-x-8 gap-y-12 sm:grid-cols-2 lg:grid-cols-3">
                    {items.map((article) => (
                        <article key={article.id} className="flex flex-col items-start">
                            <div className="relative w-full">
                                <a href={getArticleUrl(article.slug)}>
                                    <img
                                        alt={article.feature_image_alt || article.title}
                                        src={articleImageUrl(article.feature_image_url)}
                                        loading="lazy"
                                        className="aspect-3/2 w-full rounded-2xl bg-gray-100 object-cover"
                                    />
                                    <div className="absolute inset-0 rounded-2xl ring-1 ring-inset ring-gray-900/10" />
                                </a>
                            </div>
                            {article.category_name && (
                                <span className="mt-5 rounded-full bg-gray-100 px-3 py-1.5 text-xs font-medium text-gray-600">
                                    {article.category_name}
                                </span>
                            )}
                            <div className="group relative">
                                <h3 className="mt-3 text-lg/6 font-semibold text-gray-900 group-hover:text-gray-600">
                                    <a href={getArticleUrl(article.slug)}>
                                        <span className="absolute inset-0" />
                                        {article.title}
                                    </a>
                                </h3>
                                {article.excerpt && (
                                    <p className="mt-3 line-clamp-3 text-sm/6 text-gray-600">
                                        {article.excerpt}
                                    </p>
                                )}
                            </div>
                        </article>
                    ))}
                </div>
            </div>
        </section>
    )
}
