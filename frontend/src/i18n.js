import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import Backend from 'i18next-http-backend'
import LanguageDetector from 'i18next-browser-languagedetector'

// Build-time cache buster so translation files are re-fetched after each deploy
const buildHash = import.meta.env.VITE_BUILD_HASH || Date.now()

i18n
    .use(Backend)
    .use(LanguageDetector)
    .use(initReactI18next)
    .init({
        fallbackLng: 'es',
        debug: import.meta.env.DEV,
        interpolation: {
            escapeValue: false, // not needed for react as it escapes by default
        },
        backend: {
            loadPath: '/locales/{{lng}}/{{ns}}.json',
            queryStringParams: { v: buildHash },
        },
    })

// Keep <html lang> in sync with the active i18n language so screen readers and
// search engines see the correct language for the page (index.html hardcodes
// `lang="en"`; this overrides it once i18n is initialized and on every switch).
if (typeof document !== 'undefined') {
    document.documentElement.lang = i18n.language
    i18n.on('languageChanged', (lng) => {
        document.documentElement.lang = lng
    })
}

export default i18n
