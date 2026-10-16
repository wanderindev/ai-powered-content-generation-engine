import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { articleImageUrl } from '../../../utils/articleImage'

export default function ArticleContent({ article }) {
    const { i18n } = useTranslation()
    const contentRef = useRef(null)

    // "23 min" / "45 s" from the manifest-computed narration length.
    const formatDuration = (ms) => {
        if (!ms) return null
        const minutes = Math.round(ms / 60000)
        return minutes >= 1 ? `${minutes} min` : `${Math.round(ms / 1000)} s`
    }

    // Style the "About this Article" and "Continue reading" sections
    useEffect(() => {
        if (!contentRef.current) return

        const headings = contentRef.current.querySelectorAll('h2')
        headings.forEach((h2) => {
            const text = h2.textContent.toLowerCase()
            // Match "about this article", "acerca de este artículo", "continue reading", "sigue leyendo"
            if (
                text.includes('about this article') ||
                text.includes('acerca de este artículo') ||
                text.includes('continue reading') ||
                text.includes('sigue leyendo')
            ) {
                // Create a wrapper div
                const wrapper = document.createElement('div')
                wrapper.className = 'article-series-section'

                // Collect all elements until the next h2 or hr
                const elementsToWrap = [h2]
                let sibling = h2.nextElementSibling

                while (sibling && sibling.tagName !== 'H2' && sibling.tagName !== 'HR') {
                    elementsToWrap.push(sibling)
                    sibling = sibling.nextElementSibling
                }

                // Insert wrapper before the h2
                h2.parentNode.insertBefore(wrapper, h2)

                // Move elements into wrapper
                elementsToWrap.forEach((el) => wrapper.appendChild(el))
            }
        })
    }, [article?.content_html])

    if (!article) return null

    return (
        <div className="bg-white">
            {/* Hero Section - Large Width */}
            <div className="mx-auto max-w-5xl px-6 pt-8 lg:px-8">
                {/* Title */}
                <h1 className="text-4xl font-semibold tracking-tight text-gray-900 sm:text-5xl">
                    {article.title}
                </h1>

                {/* Narration player — Spanish surface only (the API sends
                    audio_url only for lang=es requests, belt and suspenders).
                    preload="metadata" so the native controls show the real
                    duration instead of 0:00 before playback starts. */}
                {article.audio_url && i18n.language === 'es' && (
                    <figure className="mt-8 rounded-xl bg-gray-50 p-4">
                        <figcaption className="text-sm font-medium text-gray-700">
                            Escuche este artículo, leído por el autor.
                            {formatDuration(article.audio_duration_ms) && (
                                <span className="ml-2 font-normal text-gray-500">
                                    ({formatDuration(article.audio_duration_ms)})
                                </span>
                            )}
                        </figcaption>
                        <audio
                            controls
                            preload="metadata"
                            src={article.audio_url}
                            className="mt-3 w-full"
                        >
                            Su navegador no soporta el elemento de audio.
                        </audio>
                    </figure>
                )}

                {/* Feature Image */}
                {article.feature_image_url && (
                    <figure className="mt-8">
                        <img
                            src={articleImageUrl(article.feature_image_url)}
                            alt={article.feature_image_alt || article.title}
                            className="aspect-video w-full rounded-2xl bg-gray-100 object-cover"
                        />
                        {article.feature_image_caption && (
                            <figcaption className="mt-3 text-sm text-gray-500 text-center">
                                {article.feature_image_caption}
                            </figcaption>
                        )}
                    </figure>
                )}

                {/* Pills: taxonomy, category, then tags — one wrapping row */}
                <div className="mt-6 flex flex-wrap items-center gap-2 text-xs">
                    <span className="rounded-full bg-indigo-50 px-3 py-1.5 font-medium text-indigo-600">
                        {article.taxonomy_name}
                    </span>
                    <span className="rounded-full bg-gray-100 px-3 py-1.5 font-medium text-gray-600">
                        {article.category_name}
                    </span>
                    {article.tags?.map(tag => (
                        <span key={tag.id} className="rounded-full bg-gray-50 px-2.5 py-1 text-gray-500 ring-1 ring-inset ring-gray-200">
                            {tag.name}
                        </span>
                    ))}
                </div>
            </div>

            {/* Article Body - Narrower Width */}
            <div className="mx-auto max-w-3xl px-6 py-10 lg:px-8">
                {/* Excerpt */}
                {article.excerpt && (
                    <p className="text-xl text-gray-600 leading-8">
                        {article.excerpt}
                    </p>
                )}

                {/* Divider */}
                <hr className="mt-8 border-gray-200" />

                {/* Article Content */}
                <div
                    ref={contentRef}
                    className="article-content mt-10"
                    dangerouslySetInnerHTML={{ __html: article.content_html }}
                />
            </div>
        </div>
    )
}
