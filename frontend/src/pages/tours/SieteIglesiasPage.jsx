import { useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckCircleIcon } from '@heroicons/react/20/solid'
import { PlusIcon, MinusIcon, InformationCircleIcon, CalendarDaysIcon, ClockIcon, UserGroupIcon } from '@heroicons/react/20/solid'
import ImageGallery from '../../components/ui/ImageGallery'
import { getTourImages } from '../../data/tourImages'
import { useCart } from '../../contexts/CartContext'
import { useNavigate } from 'react-router-dom'

// --- Tour schedule: only these dates/times are available ---
const SCHEDULE = {
    '2026-04-02': { dayKey: 'thu', times: ['1:00 PM', '5:00 PM'] },
    '2026-04-03': { dayKey: 'fri', times: ['9:00 AM', '1:00 PM', '5:00 PM'] },
    '2026-04-04': { dayKey: 'sat', times: ['1:00 PM', '5:00 PM'] },
}

// --- Fixed pricing (no backend pricing engine) ---
const PRICES = {
    senior: 16.00,       // 20% senior discount on $20
    adult: 20.00,        // 10 years and older
    child5to10: 12.50,
    childUnder5: 0,
}

const ADULT_BASE = 20.00
const SENIOR_DISCOUNT_PCT = 20

// Map specific times to morning/afternoon for cart compatibility
function timeToSlot(time) {
    const hour = parseInt(time.split(':')[0])
    const isPM = time.includes('PM')
    const hour24 = isPM && hour !== 12 ? hour + 12 : hour
    return hour24 < 12 ? 'morning' : 'afternoon'
}

export default function SieteIglesiasPage() {
    const images = getTourImages('siete-iglesias')
    const { t, i18n } = useTranslation()
    const { addItem } = useCart()
    const navigate = useNavigate()
    const isSpanish = i18n.language === 'es'

    // Booking state
    const [selectedDate, setSelectedDate] = useState(null)
    const [selectedTime, setSelectedTime] = useState(null)
    const [participants, setParticipants] = useState({
        seniorFemale: 0,
        seniorMale: 0,
        adult: 0,
        child5to10: 0,
        childUnder5: 0,
    })
    const [bookingSuccess, setBookingSuccess] = useState(false)

    const getList = (key) => {
        const items = t(key, { returnObjects: true })
        return Array.isArray(items) ? items : []
    }

    // Available times for selected date
    const availableTimes = selectedDate ? SCHEDULE[selectedDate]?.times || [] : []

    // Reset time when date changes
    const handleDateSelect = (date) => {
        setSelectedDate(date)
        setSelectedTime(null)
        setBookingSuccess(false)
    }

    // Participant helpers
    const totalParticipants = Object.values(participants).reduce((s, v) => s + v, 0)
    const maxParticipants = 15

    const updateParticipant = (key, delta) => {
        const newVal = participants[key] + delta
        if (newVal < 0) return
        if (delta > 0 && totalParticipants >= maxParticipants) return
        setParticipants(prev => ({ ...prev, [key]: newVal }))
        setBookingSuccess(false)
    }

    // Price calculation
    const pricing = useMemo(() => {
        const seniorCount = participants.seniorFemale + participants.seniorMale
        const seniorTotal = seniorCount * PRICES.senior
        const seniorDiscount = seniorCount * (ADULT_BASE - PRICES.senior)
        const adultTotal = participants.adult * PRICES.adult
        const childTotal = participants.child5to10 * PRICES.child5to10
        const grandTotal = seniorTotal + adultTotal + childTotal

        return {
            service_total: String((grandTotal + seniorDiscount).toFixed(2)),
            service_per_pax: String(ADULT_BASE.toFixed(2)),
            tickets_total: '0.00',
            senior_discount: String(seniorDiscount.toFixed(2)),
            transport_total: '0.00',
            grand_total: String(grandTotal.toFixed(2)),
        }
    }, [participants])

    const payingParticipants = totalParticipants - participants.childUnder5
    const isBookingValid = selectedDate && selectedTime && payingParticipants > 0

    const handleAddToCart = () => {
        if (!isBookingValid) return

        const dateObj = new Date(selectedDate + 'T12:00:00')

        const cartItem = {
            tour: {
                slug: 'siete-iglesias',
                title: t('tours.siete_iglesias.details.title'),
                description: t('tours.siete_iglesias.details.description_short'),
                image: images[0]?.src || '',
            },
            bookingDetails: {
                date: dateObj,
                time: timeToSlot(selectedTime),
                specificTime: selectedTime,
                participants: {
                    senior_female: { resident: 0, tourist: participants.seniorFemale },
                    senior_male: { resident: 0, tourist: participants.seniorMale },
                    adult: { resident: 0, tourist: participants.adult },
                    child_10_17: { resident: 0, tourist: 0 },
                    child_5_10: { resident: 0, tourist: participants.child5to10 },
                    child_under_5: { resident: 0, tourist: participants.childUnder5 },
                },
                transport: { needed: false },
            },
            pricing,
        }

        addItem(cartItem)
        setBookingSuccess(true)
    }

    // Date display helpers
    const formatDateLabel = (dateStr) => {
        const date = new Date(dateStr + 'T12:00:00')
        if (isSpanish) {
            const days = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado']
            const months = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']
            return {
                day: days[date.getDay()],
                date: `${date.getDate()} ${months[date.getMonth()]}`,
            }
        }
        const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
        const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        return {
            day: days[date.getDay()],
            date: `${months[date.getMonth()]} ${date.getDate()}`,
        }
    }

    // Participant category definitions for the booking form
    const participantCategories = [
        { key: 'seniorFemale', label: t('tours.siete_iglesias.booking.senior_female'), price: ADULT_BASE, isSenior: true },
        { key: 'seniorMale', label: t('tours.siete_iglesias.booking.senior_male'), price: ADULT_BASE, isSenior: true },
        { key: 'adult', label: t('tours.siete_iglesias.booking.adult'), price: PRICES.adult },
        { key: 'child5to10', label: t('tours.siete_iglesias.booking.child_5_10'), price: PRICES.child5to10 },
        { key: 'childUnder5', label: t('tours.siete_iglesias.booking.child_under_5'), price: PRICES.childUnder5 },
    ]

    return (
        <div className="relative isolate overflow-hidden bg-white">
            {/* Hero Banner */}
            <div className="relative bg-gradient-to-r from-indigo-900 via-purple-900 to-indigo-800 py-16 sm:py-24">
                <div className="absolute inset-0 bg-black/20" />
                <div className="relative mx-auto max-w-7xl px-6 lg:px-8 text-center">
                    <p className="text-sm font-semibold uppercase tracking-widest text-indigo-200">
                        {t('tours.siete_iglesias.hero.badge')}
                    </p>
                    <h1 className="mt-4 text-4xl font-bold tracking-tight text-white sm:text-6xl">
                        {t('tours.siete_iglesias.details.title')}
                    </h1>
                    <p className="mt-6 text-lg text-indigo-100 max-w-2xl mx-auto">
                        {t('tours.siete_iglesias.hero.subtitle')}
                    </p>
                    <div className="mt-8 flex flex-wrap justify-center gap-4 text-sm text-indigo-200">
                        <span className="flex items-center gap-1.5">
                            <CalendarDaysIcon className="h-4 w-4" />
                            {t('tours.siete_iglesias.hero.dates')}
                        </span>
                        <span className="flex items-center gap-1.5">
                            <ClockIcon className="h-4 w-4" />
                            {t('tours.siete_iglesias.hero.duration')}
                        </span>
                        <span className="flex items-center gap-1.5">
                            <UserGroupIcon className="h-4 w-4" />
                            {t('tours.siete_iglesias.hero.type')}
                        </span>
                    </div>
                    <div className="mt-8">
                        <a
                            href="#reservar"
                            className="inline-flex items-center rounded-full bg-white px-8 py-3 text-sm font-semibold text-indigo-900 shadow-lg hover:bg-indigo-50 transition-colors"
                        >
                            {t('tours.siete_iglesias.hero.cta')}
                        </a>
                    </div>
                </div>
            </div>

            {/* Spanish-only notice for English visitors */}
            {!isSpanish && (
                <div className="bg-amber-50 border-b border-amber-200">
                    <div className="mx-auto max-w-7xl px-6 lg:px-8 py-4">
                        <div className="flex items-start gap-3">
                            <InformationCircleIcon className="h-5 w-5 text-amber-600 flex-shrink-0 mt-0.5" />
                            <p className="text-sm text-amber-800">
                                {t('tours.siete_iglesias.spanish_notice')}
                            </p>
                        </div>
                    </div>
                </div>
            )}

            {/* Main Content */}
            <div className="mx-auto max-w-7xl px-6 lg:px-8 py-16 sm:py-24">
                <div className="grid grid-cols-1 gap-x-8 gap-y-16 lg:grid-cols-2 lg:items-start lg:gap-y-10">
                    {/* Left Column: Content */}
                    <div className="lg:col-start-1 lg:row-start-1">
                        <div className="lg:max-w-lg">
                            <h2 className="text-3xl font-semibold tracking-tight text-gray-950 sm:text-4xl">
                                {t('tours.siete_iglesias.details.overview_title')}
                            </h2>
                            <p className="mt-6 text-lg/8 text-gray-700">
                                {t('tours.siete_iglesias.details.description_1')}
                            </p>
                            <p className="mt-4 text-base/7 text-gray-600">
                                {t('tours.siete_iglesias.details.description_2')}
                            </p>
                        </div>
                    </div>

                    {/* Right Column: Gallery */}
                    <div className="lg:sticky lg:top-4 lg:col-start-2 lg:row-span-2 lg:row-start-1">
                        <ImageGallery images={images} />
                    </div>

                    {/* Left Column continued: The Route */}
                    <div className="lg:col-start-1 lg:row-start-2">
                        <div className="max-w-xl text-base/7 text-gray-600 lg:max-w-lg">
                            <h2 className="mt-2 text-2xl font-bold tracking-tight text-gray-900">
                                {t('tours.siete_iglesias.details.route_title')}
                            </h2>
                            <ul role="list" className="mt-8 space-y-8 text-gray-600">
                                {getList('tours.siete_iglesias.details.churches').map((church, index) => (
                                    <li key={index} className="flex gap-x-3">
                                        <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-indigo-600 text-sm font-bold text-white">
                                            {index + 1}
                                        </span>
                                        <span>
                                            <strong className="font-semibold text-gray-900">{church.name}.</strong>{' '}
                                            {church.desc}
                                        </span>
                                    </li>
                                ))}
                            </ul>

                            {/* Why Choose This Tour */}
                            <h2 className="mt-16 text-2xl font-bold tracking-tight text-gray-900">
                                {t('tours.siete_iglesias.details.why_title')}
                            </h2>
                            <ul role="list" className="mt-8 space-y-6 text-gray-600">
                                {getList('tours.siete_iglesias.details.why_items').map((item, index) => (
                                    <li key={index} className="flex gap-x-3">
                                        <CheckCircleIcon aria-hidden="true" className="mt-1 size-5 flex-none text-indigo-600" />
                                        <span>
                                            <strong className="font-semibold text-gray-900">{item.title}.</strong> {item.desc}
                                        </span>
                                    </li>
                                ))}
                            </ul>

                            {/* Quick Facts */}
                            <h2 className="mt-16 text-2xl font-bold tracking-tight text-gray-900">
                                {t('tours.siete_iglesias.details.facts_title')}
                            </h2>
                            <ul role="list" className="mt-6 space-y-3 text-gray-600">
                                {getList('tours.siete_iglesias.details.facts').map((fact, index) => (
                                    <li key={index} className="flex items-baseline gap-x-3">
                                        <span className="h-2 w-2 flex-none rounded-full bg-indigo-600 translate-y-[-1px]" />
                                        <span>{fact}</span>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    </div>
                </div>
            </div>

            {/* ============================================ */}
            {/* BOOKING SECTION                              */}
            {/* ============================================ */}
            <div id="reservar" className="bg-gray-50 border-t border-gray-200">
                <div className="mx-auto max-w-4xl px-6 lg:px-8 py-16 sm:py-20">
                    <div className="text-center mb-12">
                        <h2 className="text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
                            {t('tours.siete_iglesias.booking.title')}
                        </h2>
                        <p className="mt-4 text-lg text-gray-600">
                            {t('tours.siete_iglesias.booking.subtitle')}
                        </p>
                    </div>

                    <div className="bg-white rounded-2xl shadow-lg border border-gray-200 overflow-hidden">
                        {/* Step 1: Date Selection */}
                        <div className="p-6 sm:p-8">
                            <h3 className="text-sm font-semibold text-gray-900 uppercase tracking-wide flex items-center gap-2">
                                <CalendarDaysIcon className="h-5 w-5 text-indigo-600" />
                                {t('tours.siete_iglesias.booking.step_date')}
                            </h3>
                            <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-3">
                                {Object.entries(SCHEDULE).map(([dateStr]) => {
                                    const label = formatDateLabel(dateStr)
                                    const isSelected = selectedDate === dateStr
                                    return (
                                        <button
                                            key={dateStr}
                                            onClick={() => handleDateSelect(dateStr)}
                                            className={`flex flex-col items-center p-4 rounded-xl border-2 transition-all ${
                                                isSelected
                                                    ? 'border-indigo-600 bg-indigo-50 text-indigo-700'
                                                    : 'border-gray-200 bg-white text-gray-700 hover:border-gray-300 hover:bg-gray-50'
                                            }`}
                                        >
                                            <span className="text-xs font-medium uppercase tracking-wide opacity-75">{label.day}</span>
                                            <span className="mt-1 text-lg font-bold">{label.date}</span>
                                        </button>
                                    )
                                })}
                            </div>
                        </div>

                        {/* Step 2: Time Selection */}
                        {selectedDate && (
                            <div className="p-6 sm:p-8 border-t border-gray-100">
                                <h3 className="text-sm font-semibold text-gray-900 uppercase tracking-wide flex items-center gap-2">
                                    <ClockIcon className="h-5 w-5 text-indigo-600" />
                                    {t('tours.siete_iglesias.booking.step_time')}
                                </h3>
                                <div className="mt-4 flex flex-wrap gap-3">
                                    {availableTimes.map((time) => {
                                        const isSelected = selectedTime === time
                                        return (
                                            <button
                                                key={time}
                                                onClick={() => { setSelectedTime(time); setBookingSuccess(false) }}
                                                className={`px-6 py-3 rounded-xl border-2 font-semibold text-sm transition-all ${
                                                    isSelected
                                                        ? 'border-indigo-600 bg-indigo-50 text-indigo-700'
                                                        : 'border-gray-200 bg-white text-gray-700 hover:border-gray-300 hover:bg-gray-50'
                                                }`}
                                            >
                                                {time}
                                            </button>
                                        )
                                    })}
                                </div>
                            </div>
                        )}

                        {/* Step 3: Participants */}
                        {selectedTime && (
                            <div className="p-6 sm:p-8 border-t border-gray-100">
                                <h3 className="text-sm font-semibold text-gray-900 uppercase tracking-wide flex items-center gap-2 mb-1">
                                    <UserGroupIcon className="h-5 w-5 text-indigo-600" />
                                    {t('tours.siete_iglesias.booking.step_participants')}
                                </h3>
                                <p className="text-xs text-gray-500 mb-6">
                                    {t('tours.siete_iglesias.booking.meeting_point')}
                                </p>

                                <div className="space-y-4">
                                    {participantCategories.map((cat) => (
                                        <div key={cat.key} className="flex items-center justify-between py-2">
                                            <div>
                                                <span className="text-sm font-medium text-gray-900">{cat.label}</span>
                                                <span className="ml-2 text-sm text-gray-500">
                                                    {cat.price > 0 ? `$${cat.price.toFixed(2)}` : t('tours.siete_iglesias.booking.free')}
                                                </span>
                                                {cat.isSenior && (
                                                    <span className="block text-xs text-green-600">
                                                        {t('tours.siete_iglesias.booking.senior_discount_note', { pct: SENIOR_DISCOUNT_PCT })}
                                                    </span>
                                                )}
                                            </div>
                                            <div className="flex items-center space-x-3">
                                                <button
                                                    type="button"
                                                    onClick={() => updateParticipant(cat.key, -1)}
                                                    disabled={participants[cat.key] <= 0}
                                                    className="p-1.5 rounded-full text-gray-400 hover:text-gray-600 hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed"
                                                >
                                                    <MinusIcon className="h-4 w-4" />
                                                </button>
                                                <span className="w-6 text-center text-sm font-semibold text-gray-900">
                                                    {participants[cat.key]}
                                                </span>
                                                <button
                                                    type="button"
                                                    onClick={() => updateParticipant(cat.key, 1)}
                                                    disabled={totalParticipants >= maxParticipants}
                                                    className="p-1.5 rounded-full text-indigo-600 hover:text-indigo-700 hover:bg-indigo-50 disabled:opacity-30 disabled:cursor-not-allowed"
                                                >
                                                    <PlusIcon className="h-4 w-4" />
                                                </button>
                                            </div>
                                        </div>
                                    ))}
                                </div>

                                {totalParticipants > 0 && (
                                    <div className="mt-4 text-xs text-gray-500 text-right">
                                        {t('tours.siete_iglesias.booking.total_participants', { count: totalParticipants })}
                                    </div>
                                )}
                            </div>
                        )}

                        {/* Price Summary & CTA */}
                        {selectedTime && payingParticipants > 0 && (
                            <div className="p-6 sm:p-8 border-t border-gray-200 bg-gray-50">
                                <div className="space-y-2 text-sm text-gray-600">
                                    {parseFloat(pricing.senior_discount) > 0 && (
                                        <div className="flex justify-between text-green-600">
                                            <span>{t('tours.siete_iglesias.booking.senior_discount')}</span>
                                            <span>-${parseFloat(pricing.senior_discount).toFixed(2)}</span>
                                        </div>
                                    )}
                                    <div className="flex justify-between items-center pt-2 border-t border-gray-200">
                                        <span className="text-lg font-bold text-gray-900">{t('booking.modal.total')}</span>
                                        <span className="text-2xl font-bold text-gray-900">${parseFloat(pricing.grand_total).toFixed(2)}</span>
                                    </div>
                                </div>

                                {bookingSuccess ? (
                                    <div className="mt-6 space-y-3">
                                        <div className="flex items-center justify-center gap-2 text-green-700 bg-green-50 border border-green-200 rounded-xl p-4">
                                            <CheckCircleIcon className="h-5 w-5" />
                                            <span className="font-medium">{t('tours.siete_iglesias.booking.added_to_cart')}</span>
                                        </div>
                                        <div className="flex flex-col sm:flex-row gap-3">
                                            <button
                                                onClick={() => {
                                                    setBookingSuccess(false)
                                                    setSelectedDate(null)
                                                    setSelectedTime(null)
                                                    setParticipants({ seniorFemale: 0, seniorMale: 0, adult: 0, child5to10: 0, childUnder5: 0 })
                                                }}
                                                className="flex-1 rounded-xl border border-gray-300 bg-white px-6 py-3 text-sm font-semibold text-gray-700 hover:bg-gray-50"
                                            >
                                                {t('tours.siete_iglesias.booking.add_another')}
                                            </button>
                                            <button
                                                onClick={() => navigate('/checkout')}
                                                className="flex-1 rounded-xl bg-indigo-600 px-6 py-3 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500"
                                            >
                                                {t('tours.siete_iglesias.booking.go_to_checkout')}
                                            </button>
                                        </div>
                                    </div>
                                ) : (
                                    <button
                                        onClick={handleAddToCart}
                                        disabled={!isBookingValid}
                                        className="mt-6 w-full rounded-xl bg-indigo-600 px-6 py-4 text-base font-semibold text-white shadow-sm hover:bg-indigo-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                                    >
                                        {t('tours.siete_iglesias.booking.reserve_button')}
                                    </button>
                                )}
                            </div>
                        )}
                    </div>

                    {/* Dress code reminder */}
                    <div className="mt-8 bg-amber-50 border border-amber-200 rounded-xl p-4 flex items-start gap-3">
                        <InformationCircleIcon className="h-5 w-5 text-amber-600 flex-shrink-0 mt-0.5" />
                        <p className="text-sm text-amber-800">
                            {t('tours.siete_iglesias.booking.dress_code')}
                        </p>
                    </div>
                </div>
            </div>
        </div>
    )
}