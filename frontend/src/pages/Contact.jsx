import { useState, useEffect, useRef } from 'react'
import { useSearchParams, useNavigate, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import * as Sentry from '@sentry/react'
import { EnvelopeIcon, PhoneIcon } from '@heroicons/react/24/outline'

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

function ContactInner() {
    const { t } = useTranslation()
    const [searchParams] = useSearchParams()
    const navigate = useNavigate()
    const location = useLocation()
    const referrerRef = useRef(null)
    const [interest, setInterest] = useState('')

    // Meta Pixel: track contact page view
    useEffect(() => {
        if (window.fbq) {
            window.fbq('track', 'Contact')
        }
    }, [])
    const [isSubmitting, setIsSubmitting] = useState(false)
    const [submitStatus, setSubmitStatus] = useState(null) // 'error' | null
    const [errorMessage, setErrorMessage] = useState('')
    const [formData, setFormData] = useState({
        firstName: '',
        lastName: '',
        email: '',
        // Field Trip fields
        schoolName: '',
        gradeLevels: [],
        fieldTrips: [],
        // Tour fields
        country: '',
        dateFrom: '',
        dateTo: '',
        adults: '',
        childrenUnder5: '',
        children5to17: '',
        tours: [],
        hotelAddress: '',
        needsTransport: false,
        // Common
        message: ''
    })

    // Capture the referrer path on mount (where the user was before /contacto)
    useEffect(() => {
        if (referrerRef.current === null) {
            const ref = document.referrer
            try {
                const refUrl = ref ? new URL(ref) : null
                // Only use referrer if it's from the same origin and not /contacto itself
                if (refUrl && refUrl.origin === window.location.origin && refUrl.pathname !== location.pathname) {
                    referrerRef.current = refUrl.pathname
                } else {
                    referrerRef.current = '/'
                }
            } catch {
                referrerRef.current = '/'
            }
        }
    }, [location.pathname])

    // Pre-select interest based on query param
    useEffect(() => {
        const interestParam = searchParams.get('interest')
        if (interestParam === 'field-trips') {
            setInterest('field-trips')
        } else if (interestParam === 'tours') {
            setInterest('tours')
        }
    }, [searchParams])

    const handleInputChange = (e) => {
        const { name, value, type, checked } = e.target
        if (type === 'checkbox') {
            if (name === 'needsTransport') {
                setFormData(prev => ({ ...prev, [name]: checked }))
            } else if (name.startsWith('fieldTrip-')) {
                const trip = name.replace('fieldTrip-', '')
                setFormData(prev => ({
                    ...prev,
                    fieldTrips: checked
                        ? [...prev.fieldTrips, trip]
                        : prev.fieldTrips.filter(t => t !== trip)
                }))
            } else if (name.startsWith('tour-')) {
                const tour = name.replace('tour-', '')
                setFormData(prev => ({
                    ...prev,
                    tours: checked
                        ? [...prev.tours, tour]
                        : prev.tours.filter(t => t !== tour)
                }))
            } else if (name.startsWith('grade-')) {
                const grade = name.replace('grade-', '')
                setFormData(prev => ({
                    ...prev,
                    gradeLevels: checked
                        ? [...prev.gradeLevels, grade]
                        : prev.gradeLevels.filter(g => g !== grade)
                }))
            }
        } else {
            setFormData(prev => ({ ...prev, [name]: value }))
        }
    }

    const handleSubmit = async (e) => {
        e.preventDefault()
        setIsSubmitting(true)
        setSubmitStatus(null)
        setErrorMessage('')

        // Build payload matching the backend schema
        const payload = {
            interest,
            first_name: formData.firstName,
            last_name: formData.lastName,
            email: formData.email,
            message: formData.message || null,
            // Field trip fields
            school_name: formData.schoolName || null,
            grade_levels: formData.gradeLevels,
            field_trips: formData.fieldTrips,
            // Tour fields
            country: formData.country || null,
            date_from: formData.dateFrom || null,
            date_to: formData.dateTo || null,
            adults: formData.adults ? parseInt(formData.adults) : null,
            children_under_5: formData.childrenUnder5 ? parseInt(formData.childrenUnder5) : null,
            children_5_to_17: formData.children5to17 ? parseInt(formData.children5to17) : null,
            tours: formData.tours,
            hotel_address: formData.hotelAddress || null,
            needs_transport: formData.needsTransport,
        }

        try {
            const response = await fetch(`${API_BASE_URL}/api/v1/contact`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(payload),
            })

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`)
            }

            // Redirect to confirmation page (distinct URL for Meta pixel tracking)
            const from = encodeURIComponent(referrerRef.current || '/')
            navigate(`/contacto/confirmacion?from=${from}`, { replace: true })
            return
        } catch (error) {
            console.error('Error submitting form:', error)
            setSubmitStatus('error')
            setErrorMessage(t('contact.form.error_message'))
        } finally {
            setIsSubmitting(false)
        }
    }

    const fieldTrips = [
        { key: 'casco-plazas', label: t('contact.form.field_trips.casco_plazas') },
        { key: 'casco-museums', label: t('contact.form.field_trips.casco_museums') },
        { key: 'panama-viejo', label: t('contact.form.field_trips.panama_viejo') },
        { key: 'biomuseo', label: t('contact.form.field_trips.biomuseo') },
        { key: 'miraflores', label: t('contact.form.field_trips.miraflores') },
        { key: 'religious', label: t('contact.form.field_trips.religious') }
    ]

    const tours = [
        { key: 'canal', label: t('contact.form.tours_list.canal') },
        { key: 'casco-plazas', label: t('contact.form.tours_list.casco_plazas') },
        { key: 'casco-museums', label: t('contact.form.tours_list.casco_museums') },
        { key: 'panama-viejo', label: t('contact.form.tours_list.panama_viejo') },
        { key: 'biomuseo', label: t('contact.form.tours_list.biomuseo') },
        { key: 'mi-pueblito', label: t('contact.form.tours_list.mi_pueblito') }
    ]

    const gradeOptions = [
        { key: '3-4', label: t('contact.form.grades.3_4') },
        { key: '5-6', label: t('contact.form.grades.5_6') },
        { key: '7-8', label: t('contact.form.grades.7_8') },
        { key: '9-10', label: t('contact.form.grades.9_10') },
        { key: '11-12', label: t('contact.form.grades.11_12') }
    ]

    const inputClasses = "block w-full rounded-md bg-white px-3.5 py-2 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600"
    const disabledInputClasses = "block w-full rounded-md bg-gray-100 px-3.5 py-2 text-base text-gray-400 outline-1 -outline-offset-1 outline-gray-200 cursor-not-allowed"

    const isFormDisabled = !interest

    return (
        <div className="relative isolate bg-white">
            <div className="mx-auto grid max-w-7xl grid-cols-1 lg:grid-cols-2">
                {/* Left side - Guide info */}
                <div className="relative px-6 pt-24 pb-20 sm:pt-32 lg:static lg:px-8 lg:py-48">
                    <div className="mx-auto max-w-xl lg:mx-0 lg:max-w-lg">
                        <h2 className="text-4xl font-semibold tracking-tight text-pretty text-gray-900 sm:text-5xl">
                            {t('contact.title')}
                        </h2>
                        <div className="mt-6">
                            <img
                                src="/images/foto-diego-feliu.avif"
                                alt="Diego Raúl Feliu - Head Guide"
                                className="h-32 w-32 rounded-full object-cover mb-4"
                            />
                            <p className="text-lg/8 text-gray-600">
                                {t('contact.bio')}
                            </p>
                        </div>

                        <dl className="mt-10 space-y-4 text-base/7 text-gray-600">
                            <div className="flex gap-x-4">
                                <dt className="flex-none">
                                    <span className="sr-only">WhatsApp</span>
                                    <PhoneIcon aria-hidden="true" className="h-7 w-6 text-gray-400" />
                                </dt>
                                <dd>
                                    <a href="https://wa.me/50769162888" className="hover:text-gray-900">
                                        +507 6916-2888 (WhatsApp)
                                    </a>
                                </dd>
                            </div>
                            <div className="flex gap-x-4">
                                <dt className="flex-none">
                                    <span className="sr-only">{t('contact.email_sr')}</span>
                                    <EnvelopeIcon aria-hidden="true" className="h-7 w-6 text-gray-400" />
                                </dt>
                                <dd>
                                    <a href="mailto:diego@panamaincontext.com" className="hover:text-gray-900">
                                        diego@panamaincontext.com
                                    </a>
                                </dd>
                            </div>
                        </dl>
                    </div>
                </div>

                {/* Right side - Form */}
                <form onSubmit={handleSubmit} className="px-6 pt-20 pb-24 sm:pb-32 lg:px-8 lg:py-48">
                    <div className="mx-auto max-w-xl lg:mr-0 lg:max-w-lg">
                        {/* Interest selector */}
                        <div className="mb-8">
                            <label className="block text-sm/6 font-semibold text-gray-900 mb-4">
                                {t('contact.form.interest_label')}
                            </label>
                            <div className="flex gap-4">
                                <button
                                    type="button"
                                    onClick={() => setInterest('field-trips')}
                                    className={`flex-1 rounded-md px-4 py-3 text-sm font-semibold transition-colors ${
                                        interest === 'field-trips'
                                            ? 'bg-indigo-600 text-white'
                                            : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                                    }`}
                                >
                                    {t('contact.form.interest_field_trips')}
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setInterest('tours')}
                                    className={`flex-1 rounded-md px-4 py-3 text-sm font-semibold transition-colors ${
                                        interest === 'tours'
                                            ? 'bg-indigo-600 text-white'
                                            : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                                    }`}
                                >
                                    {t('contact.form.interest_tours')}
                                </button>
                            </div>
                            {!interest && (
                                <p className="mt-2 text-sm text-amber-600">
                                    {t('contact.form.select_interest_hint')}
                                </p>
                            )}
                        </div>

                        <div className="grid grid-cols-1 gap-x-8 gap-y-6 sm:grid-cols-2">
                            {/* Common fields */}
                            <div>
                                <label htmlFor="firstName" className="block text-sm/6 font-semibold text-gray-900">
                                    {t('contact.form.first_name')}
                                </label>
                                <div className="mt-2.5">
                                    <input
                                        id="firstName"
                                        name="firstName"
                                        type="text"
                                        autoComplete="given-name"
                                        disabled={isFormDisabled}
                                        value={formData.firstName}
                                        onChange={handleInputChange}
                                        className={isFormDisabled ? disabledInputClasses : inputClasses}
                                    />
                                </div>
                            </div>
                            <div>
                                <label htmlFor="lastName" className="block text-sm/6 font-semibold text-gray-900">
                                    {t('contact.form.last_name')}
                                </label>
                                <div className="mt-2.5">
                                    <input
                                        id="lastName"
                                        name="lastName"
                                        type="text"
                                        autoComplete="family-name"
                                        disabled={isFormDisabled}
                                        value={formData.lastName}
                                        onChange={handleInputChange}
                                        className={isFormDisabled ? disabledInputClasses : inputClasses}
                                    />
                                </div>
                            </div>
                            <div className="sm:col-span-2">
                                <label htmlFor="email" className="block text-sm/6 font-semibold text-gray-900">
                                    {t('contact.form.email')}
                                </label>
                                <div className="mt-2.5">
                                    <input
                                        id="email"
                                        name="email"
                                        type="email"
                                        autoComplete="email"
                                        disabled={isFormDisabled}
                                        value={formData.email}
                                        onChange={handleInputChange}
                                        className={isFormDisabled ? disabledInputClasses : inputClasses}
                                    />
                                </div>
                            </div>

                            {/* Field Trip specific fields */}
                            {interest === 'field-trips' && (
                                <>
                                    <div className="sm:col-span-2">
                                        <label htmlFor="schoolName" className="block text-sm/6 font-semibold text-gray-900">
                                            {t('contact.form.school_name')}
                                        </label>
                                        <div className="mt-2.5">
                                            <input
                                                id="schoolName"
                                                name="schoolName"
                                                type="text"
                                                value={formData.schoolName}
                                                onChange={handleInputChange}
                                                className={inputClasses}
                                            />
                                        </div>
                                    </div>
                                    <div className="sm:col-span-2">
                                        <label className="block text-sm/6 font-semibold text-gray-900 mb-3">
                                            {t('contact.form.grade_levels')}
                                        </label>
                                        <div className="grid grid-cols-2 gap-3">
                                            {gradeOptions.map(grade => (
                                                <label key={grade.key} className="flex items-center gap-2 text-sm text-gray-700">
                                                    <input
                                                        type="checkbox"
                                                        name={`grade-${grade.key}`}
                                                        checked={formData.gradeLevels.includes(grade.key)}
                                                        onChange={handleInputChange}
                                                        className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-600"
                                                    />
                                                    {grade.label}
                                                </label>
                                            ))}
                                        </div>
                                    </div>
                                    <div className="sm:col-span-2">
                                        <label className="block text-sm/6 font-semibold text-gray-900 mb-3">
                                            {t('contact.form.field_trips_interested')}
                                        </label>
                                        <div className="grid grid-cols-1 gap-3">
                                            {fieldTrips.map(trip => (
                                                <label key={trip.key} className="flex items-center gap-2 text-sm text-gray-700">
                                                    <input
                                                        type="checkbox"
                                                        name={`fieldTrip-${trip.key}`}
                                                        checked={formData.fieldTrips.includes(trip.key)}
                                                        onChange={handleInputChange}
                                                        className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-600"
                                                    />
                                                    {trip.label}
                                                </label>
                                            ))}
                                        </div>
                                    </div>
                                </>
                            )}

                            {/* Tour specific fields */}
                            {interest === 'tours' && (
                                <>
                                    <div className="sm:col-span-2">
                                        <label htmlFor="country" className="block text-sm/6 font-semibold text-gray-900">
                                            {t('contact.form.country')}
                                        </label>
                                        <div className="mt-2.5">
                                            <input
                                                id="country"
                                                name="country"
                                                type="text"
                                                value={formData.country}
                                                onChange={handleInputChange}
                                                className={inputClasses}
                                            />
                                        </div>
                                    </div>
                                    <div>
                                        <label htmlFor="dateFrom" className="block text-sm/6 font-semibold text-gray-900">
                                            {t('contact.form.date_from')}
                                        </label>
                                        <div className="mt-2.5">
                                            <input
                                                id="dateFrom"
                                                name="dateFrom"
                                                type="date"
                                                value={formData.dateFrom}
                                                onChange={handleInputChange}
                                                className={inputClasses}
                                            />
                                        </div>
                                    </div>
                                    <div>
                                        <label htmlFor="dateTo" className="block text-sm/6 font-semibold text-gray-900">
                                            {t('contact.form.date_to')}
                                        </label>
                                        <div className="mt-2.5">
                                            <input
                                                id="dateTo"
                                                name="dateTo"
                                                type="date"
                                                value={formData.dateTo}
                                                onChange={handleInputChange}
                                                className={inputClasses}
                                            />
                                        </div>
                                    </div>
                                    <div className="sm:col-span-2">
                                        <label className="block text-sm/6 font-semibold text-gray-900 mb-3">
                                            {t('contact.form.group_size')}
                                        </label>
                                        <div className="grid grid-cols-3 gap-4">
                                            <div>
                                                <label htmlFor="adults" className="block text-xs text-gray-500 mb-1">
                                                    {t('contact.form.adults')}
                                                </label>
                                                <input
                                                    id="adults"
                                                    name="adults"
                                                    type="number"
                                                    min="0"
                                                    value={formData.adults}
                                                    onChange={handleInputChange}
                                                    className={inputClasses}
                                                />
                                            </div>
                                            <div>
                                                <label htmlFor="childrenUnder5" className="block text-xs text-gray-500 mb-1">
                                                    {t('contact.form.children_under_5')}
                                                </label>
                                                <input
                                                    id="childrenUnder5"
                                                    name="childrenUnder5"
                                                    type="number"
                                                    min="0"
                                                    value={formData.childrenUnder5}
                                                    onChange={handleInputChange}
                                                    className={inputClasses}
                                                />
                                            </div>
                                            <div>
                                                <label htmlFor="children5to17" className="block text-xs text-gray-500 mb-1">
                                                    {t('contact.form.children_5_17')}
                                                </label>
                                                <input
                                                    id="children5to17"
                                                    name="children5to17"
                                                    type="number"
                                                    min="0"
                                                    value={formData.children5to17}
                                                    onChange={handleInputChange}
                                                    className={inputClasses}
                                                />
                                            </div>
                                        </div>
                                    </div>
                                    <div className="sm:col-span-2">
                                        <label className="block text-sm/6 font-semibold text-gray-900 mb-3">
                                            {t('contact.form.tours_interested')}
                                        </label>
                                        <div className="grid grid-cols-1 gap-3">
                                            {tours.map(tour => (
                                                <label key={tour.key} className="flex items-center gap-2 text-sm text-gray-700">
                                                    <input
                                                        type="checkbox"
                                                        name={`tour-${tour.key}`}
                                                        checked={formData.tours.includes(tour.key)}
                                                        onChange={handleInputChange}
                                                        className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-600"
                                                    />
                                                    {tour.label}
                                                </label>
                                            ))}
                                        </div>
                                    </div>
                                    <div className="sm:col-span-2">
                                        <label htmlFor="hotelAddress" className="block text-sm/6 font-semibold text-gray-900">
                                            {t('contact.form.hotel_address')}
                                        </label>
                                        <div className="mt-2.5">
                                            <input
                                                id="hotelAddress"
                                                name="hotelAddress"
                                                type="text"
                                                placeholder={t('contact.form.hotel_address_placeholder')}
                                                value={formData.hotelAddress}
                                                onChange={handleInputChange}
                                                className={inputClasses}
                                            />
                                        </div>
                                    </div>
                                    <div className="sm:col-span-2">
                                        <label className="flex items-center gap-2 text-sm text-gray-700">
                                            <input
                                                type="checkbox"
                                                name="needsTransport"
                                                checked={formData.needsTransport}
                                                onChange={handleInputChange}
                                                className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-600"
                                            />
                                            {t('contact.form.needs_transport')}
                                        </label>
                                    </div>
                                </>
                            )}

                            {/* Message - common to both */}
                            <div className="sm:col-span-2">
                                <label htmlFor="message" className="block text-sm/6 font-semibold text-gray-900">
                                    {t('contact.form.message')}
                                </label>
                                <div className="mt-2.5">
                                    <textarea
                                        id="message"
                                        name="message"
                                        rows={4}
                                        disabled={isFormDisabled}
                                        value={formData.message}
                                        onChange={handleInputChange}
                                        className={isFormDisabled ? disabledInputClasses : inputClasses}
                                    />
                                </div>
                            </div>
                        </div>
                        {/* Error message */}
                        {submitStatus === 'error' && (
                            <div className="rounded-md bg-red-50 p-4">
                                <p className="text-sm font-medium text-red-800">
                                    {errorMessage}
                                </p>
                            </div>
                        )}

                        <div className="mt-8 flex justify-end">
                            <button
                                type="submit"
                                disabled={isFormDisabled || isSubmitting}
                                className={`rounded-md px-3.5 py-2.5 text-center text-sm font-semibold text-white shadow-xs focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 ${
                                    isFormDisabled || isSubmitting
                                        ? 'bg-gray-300 cursor-not-allowed'
                                        : 'bg-indigo-600 hover:bg-indigo-500'
                                }`}
                            >
                                {isSubmitting ? t('contact.form.submitting') : t('contact.form.submit')}
                            </button>
                        </div>
                    </div>
                </form>
            </div>
        </div>
    )
}

function ContactFallback() {
    const { t } = useTranslation()
    return (
        <div className="bg-gray-50 min-h-screen py-12">
            <div className="container mx-auto px-4 max-w-2xl text-center">
                <h1 className="text-2xl font-bold mb-4 text-gray-900">
                    {t('contact.error_title', 'Something went wrong')}
                </h1>
                <p className="text-gray-600 mb-6">
                    {t('contact.error_message', 'We encountered an unexpected error. Please try refreshing the page.')}
                </p>
                <button
                    onClick={() => window.location.reload()}
                    className="inline-flex items-center px-4 py-2 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700"
                >
                    {t('contact.error_retry', 'Refresh page')}
                </button>
            </div>
        </div>
    )
}

const Contact = Sentry.withErrorBoundary(ContactInner, {
    fallback: <ContactFallback />,
})

export default Contact
