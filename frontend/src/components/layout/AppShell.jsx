import { useEffect, useRef } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import Navbar from './Navbar.jsx'
import Footer from './Footer.jsx'
import { trackPageView, trackViewContent } from '../../analytics.js'

function ScrollToTop() {
    const { pathname } = useLocation()

    useEffect(() => {
        window.scrollTo(0, 0)
    }, [pathname])

    return null
}

// Content route prefixes that should fire ViewContent
const CONTENT_PREFIXES = [
    '/excursiones-academicas/',
    '/tours-guiados/',
    '/en/articles/',
    '/es/articulos/',
    '/articles/',
]

function PageTracking() {
    const { pathname } = useLocation()
    const isFirstRender = useRef(true)

    useEffect(() => {
        // Skip the first render — initAnalytics() in main.jsx already fires
        // PageView on initial load
        if (isFirstRender.current) {
            isFirstRender.current = false
            return
        }

        // Fire PageView (GA + Meta Pixel) on every SPA navigation. The helper
        // no-ops in non-prod builds (VITE_ANALYTICS_ENABLED off).
        trackPageView(pathname)
    }, [pathname])

    useEffect(() => {
        // Fire ViewContent for detail/content pages
        const isContentPage = CONTENT_PREFIXES.some(prefix => pathname.startsWith(prefix))
        if (isContentPage) {
            trackViewContent(pathname)
        }
    }, [pathname])

    return null
}

export default function AppShell() {
    return (
        <div className="flex flex-col min-h-screen">
            <ScrollToTop />
            <PageTracking />
            <Navbar />
            <main className="flex-grow">
                <Outlet />
            </main>
            <Footer />
        </div>
    )
}
