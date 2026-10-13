import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import BlogList from '../components/sections/blog/BlogList.jsx'

export default function Blog() {
    const location = useLocation()
    const navigate = useNavigate()
    const { i18n } = useTranslation()

    // Extract language from URL path (e.g., /en/blog or /es/blog)
    const getUrlLanguage = () => {
        const pathParts = location.pathname.split('/')
        if (pathParts[1] === 'en' || pathParts[1] === 'es') {
            return pathParts[1]
        }
        return null
    }

    // Sync i18n language with URL language on mount
    useEffect(() => {
        const urlLang = getUrlLanguage()
        if (urlLang && urlLang !== i18n.language) {
            i18n.changeLanguage(urlLang)
        }
    }, [location.pathname])

    // Update URL when language changes
    useEffect(() => {
        const urlLang = getUrlLanguage()
        const currentLang = i18n.language

        // Only update if URL has a language prefix and it differs from current language
        if (urlLang && urlLang !== currentLang) {
            const newPath = `/${currentLang}/blog`
            navigate(newPath, { replace: true })
        }
    }, [i18n.language])

    return (
        <>
            <BlogList />
        </>
    )
}
