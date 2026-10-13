// Google Analytics + Meta Pixel bootstrap and event helpers.
//
// All IDs come from build-time env vars and every call is gated on
// VITE_ANALYTICS_ENABLED. The flag is unset (OFF) in dev, CI, and any
// staging/preview build — only the prod Dockerfile bakes it ON — so non-prod
// traffic never reports into the production GA property or Meta Pixel (#112).
// Mirrors the Sentry-DSN-via-build-arg pattern in main.jsx / Dockerfile.prod.

const ANALYTICS_ENABLED = import.meta.env.VITE_ANALYTICS_ENABLED === 'true'
const GA_ID = import.meta.env.VITE_GA_ID
const META_PIXEL_ID = import.meta.env.VITE_META_PIXEL_ID

const gaEnabled = ANALYTICS_ENABLED && !!GA_ID
const pixelEnabled = ANALYTICS_ENABLED && !!META_PIXEL_ID

function initGoogleAnalytics() {
  // Standard gtag.js bootstrap, formerly inline in index.html.
  const script = document.createElement('script')
  script.async = true
  script.src = `https://www.googletagmanager.com/gtag/js?id=${GA_ID}`
  document.head.appendChild(script)

  window.dataLayer = window.dataLayer || []
  function gtag() {
    window.dataLayer.push(arguments)
  }
  window.gtag = gtag
  gtag('js', new Date())
  gtag('config', GA_ID)
}

function initMetaPixel() {
  // Standard Meta Pixel bootstrap, formerly inline in index.html.
  !(function (f, b, e, v, n, t, s) {
    if (f.fbq) return
    n = f.fbq = function () {
      n.callMethod ? n.callMethod.apply(n, arguments) : n.queue.push(arguments)
    }
    if (!f._fbq) f._fbq = n
    n.push = n
    n.loaded = !0
    n.version = '2.0'
    n.queue = []
    t = b.createElement(e)
    t.async = !0
    t.src = v
    s = b.getElementsByTagName(e)[0]
    s.parentNode.insertBefore(t, s)
  })(window, document, 'script', 'https://connect.facebook.net/en_US/fbevents.js')
  window.fbq('init', META_PIXEL_ID)
  window.fbq('track', 'PageView')
}

// Initialize analytics once at app startup. No-ops entirely when the flag is
// off or the relevant ID is missing.
export function initAnalytics() {
  if (gaEnabled) initGoogleAnalytics()
  if (pixelEnabled) initMetaPixel()
}

// Fire PageView on an SPA navigation (both GA and Pixel).
export function trackPageView(pathname) {
  if (gaEnabled && window.gtag) {
    window.gtag('config', GA_ID, { page_path: pathname })
  }
  if (pixelEnabled && window.fbq) {
    window.fbq('track', 'PageView')
  }
}

// Fire ViewContent for detail/content pages (Pixel only).
export function trackViewContent(pathname) {
  if (pixelEnabled && window.fbq) {
    window.fbq('track', 'ViewContent', { content_name: pathname })
  }
}
