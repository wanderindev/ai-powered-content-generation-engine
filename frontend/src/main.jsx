import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import * as Sentry from '@sentry/react'
import './index.css'
import App from './App.jsx'
import RootErrorFallback from './components/RootErrorFallback.jsx'
import { initAnalytics } from './analytics.js'
import './i18n'

// Bootstrap GA + Meta Pixel. No-ops unless VITE_ANALYTICS_ENABLED is on, so
// dev/CI/staging traffic never reports to the prod property (#112).
initAnalytics()

// DSN is read from the build-time env (baked in via --build-arg at deploy).
// Unset in dev/CI/forks → init is a no-op so non-prod runs stay silent.
const sentryDsn = import.meta.env.VITE_SENTRY_DSN

// Magic-link tokens are bearer credentials for an order. They travel via router
// state, but the getOrder/cancel/renew API calls still carry ?token=... in the
// request URL, which the SDK would otherwise record in fetch/navigation
// breadcrumbs and on event.request.url. Redact them everywhere before send (#36;
// see also #43/#57 on the broader URL-token leak surface).
const redactToken = (url) =>
  typeof url === 'string' ? url.replace(/([?&]token=)[^&]+/gi, '$1[redacted]') : url

if (sentryDsn) {
  // sendDefaultPii: false stops the browser SDK from attaching IPs, cookies,
  // and request headers. beforeSend strips request data and user context for
  // symmetry with the backend scrubber. See issue #9.
  Sentry.init({
    dsn: sentryDsn,
    sendDefaultPii: false,
    beforeBreadcrumb(breadcrumb) {
      const data = breadcrumb.data
      if (data) {
        if (data.url) data.url = redactToken(data.url)
        if (data.to) data.to = redactToken(data.to)
        if (data.from) data.from = redactToken(data.from)
      }
      return breadcrumb
    },
    beforeSend(event) {
      if (event.request) {
        delete event.request.data
        delete event.request.cookies
        delete event.request.query_string
        if (event.request.url) event.request.url = redactToken(event.request.url)
      }
      delete event.user
      return event
    },
  })
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Sentry.ErrorBoundary fallback={<RootErrorFallback />} showDialog={false}>
      <App />
    </Sentry.ErrorBoundary>
  </StrictMode>,
)
