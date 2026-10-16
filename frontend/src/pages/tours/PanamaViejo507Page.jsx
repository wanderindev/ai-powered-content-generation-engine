import { useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import {
    CheckCircleIcon,
    PlusIcon,
    MinusIcon,
    InformationCircleIcon,
    CalendarDaysIcon,
    ClockIcon,
    UserGroupIcon,
} from '@heroicons/react/20/solid'
import ImageGallery from '../../components/ui/ImageGallery'
import { getTourImages } from '../../data/tourImages'
import { useCart } from '../../contexts/CartContext'

// --- Tour schedule: only these dates/times are available ---
const SCHEDULE = {
    '2026-08-15': { times: ['10:00 AM', '1:00 PM', '4:00 PM'] },
    '2026-08-16': { times: ['10:00 AM', '1:00 PM', '4:00 PM'] },
}

// --- Fixed pricing (no backend pricing engine) ---
// Mirrors the tour's fixed_price_config in the DB, which is authoritative:
// the server reprices every order, so these values are for display only.
const PRICES = {
    adult: 15.0,
    senior: 10.0,
    child5to17: 5.0,
    childUnder5: 0,
}

const ADULT_BASE = PRICES.adult

// Map a specific time to the AM/PM slot the booking API stores. The exact time
// rides along as `specificTime` -> `preferred_time`, which is what the run sheet
// reads — 1:00 PM and 4:00 PM both land in "PM".
function timeToSlot(time) {
    const hour = parseInt(time.split(':')[0])
    const isPM = time.includes('PM')
    const hour24 = isPM && hour !== 12 ? hour + 12 : hour
    return hour24 < 12 ? 'morning' : 'afternoon'
}

export default function PanamaViejo507Page() {
    // Reuses the year-round Panamá Viejo gallery — same site, same photographs.
    const images = getTourImages('panama-viejo')
    const { t, i18n } = useTranslation()
    const { addItem } = useCart()
    const navigate = useNavigate()
    const isSpanish = i18n.language?.startsWith('es')

    // Booking state
    const [selectedDate, setSelectedDate] = useState(null)
    const [selectedTime, setSelectedTime] = useState(null)
    const [participants, setParticipants] = useState({
        adult: 0,
        senior: 0,
        child5to17: 0,
        childUnder5: 0,
    })
    const [bookingSuccess, setBookingSuccess] = useState(false)

    const getList = (key) => {
        const items = t(key, { returnObjects: true })
        return Array.isArray(items) ? items : []
    }

    const availableTimes = selectedDate ? SCHEDULE[selectedDate]?.times || [] : []

    const handleDateSelect = (date) => {
        setSelectedDate(date)
        setSelectedTime(null)
        setBookingSuccess(false)
    }

    const totalParticipants = Object.values(participants).reduce((s, v) => s + v, 0)
    const maxParticipants = 15

    const updateParticipant = (key, delta) => {
        const newVal = participants[key] + delta
        if (newVal < 0) return
        if (delta > 0 && totalParticipants >= maxParticipants) return
        setParticipants((prev) => ({ ...prev, [key]: newVal }))
        setBookingSuccess(false)
    }

    // Price calculation. `service_total` is the pre-discount figure and the
    // jubilado saving is shown separately, matching what the server returns.
    const pricing = useMemo(() => {
        const seniorDiscount = participants.senior * (ADULT_BASE - PRICES.senior)
        const grandTotal =
            participants.adult * PRICES.adult +
            participants.senior * PRICES.senior +
            participants.child5to17 * PRICES.child5to17 +
            participants.childUnder5 * PRICES.childUnder5

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

        // This tour sells three price bands (adulto / jubilado / menor) while the
        // booking API has a finer age vocabulary, so each band maps onto one
        // representative category. Headcount and price are exact; the sub-buckets
        // are not, so read `senior_count` / totals rather than these fields.
        // Both child bands are priced identically in fixed_price_config precisely
        // so this collapse cannot change what anyone is charged.
        const cartItem = {
            tour: {
                slug: 'panama-viejo-507',
                title: t('tours.panama_viejo_507.hero.title'),
                description: t('tours.panama_viejo_507.desc'),
                image: images[0]?.src || '',
            },
            bookingDetails: {
                date: dateObj,
                time: timeToSlot(selectedTime),
                specificTime: selectedTime,
                participants: {
                    senior_female: { resident: 0, tourist: participants.senior },
                    senior_male: { resident: 0, tourist: 0 },
                    adult: { resident: 0, tourist: participants.adult },
                    child_10_17: { resident: 0, tourist: participants.child5to17 },
                    child_5_10: { resident: 0, tourist: 0 },
                    child_under_5: { resident: 0, tourist: participants.childUnder5 },
                },
                transport: { needed: false },
            },
            pricing,
        }

        addItem(cartItem)
        setBookingSuccess(true)
    }

    const formatDateLabel = (dateStr) => {
        const date = new Date(dateStr + 'T12:00:00')
        const days = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado']
        const months = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']
        return {
            day: days[date.getDay()],
            date: `${date.getDate()} ${months[date.getMonth()]}`,
        }
    }

    const participantCategories = [
        { key: 'adult', label: t('tours.panama_viejo_507.booking.adult'), price: PRICES.adult },
        { key: 'senior', label: t('tours.panama_viejo_507.booking.senior'), price: PRICES.senior },
        {
            key: 'child5to17',
            label: t('tours.panama_viejo_507.booking.child_5_17'),
            price: PRICES.child5to17,
        },
        {
            key: 'childUnder5',
            label: t('tours.panama_viejo_507.booking.child_under_5'),
            price: PRICES.childUnder5,
        },
    ]

    return (
        <div className="relative isolate overflow-hidden bg-white">
            {/* Hero Banner */}
            <div className="relative isolate overflow-hidden bg-gray-900 py-20 sm:py-28">
                <img
                    alt=""
                    src="/images/panama-viejo.png"
                    className="absolute inset-0 -z-10 size-full object-cover opacity-40"
                />
                <div className="absolute inset-0 -z-10 bg-gradient-to-b from-gray-900/60 via-gray-900/40 to-gray-900/70" />
                <div className="relative mx-auto max-w-7xl px-6 lg:px-8 text-center">
                    <p className="text-sm font-semibold uppercase tracking-widest text-indigo-200">
                        {t('tours.panama_viejo_507.hero.badge')}
                    </p>
                    <h1 className="mt-3 text-5xl font-semibold tracking-tight text-white sm:text-7xl">
                        {t('tours.panama_viejo_507.hero.title')}
                    </h1>
                    <p className="mt-6 text-lg text-gray-200 max-w-2xl mx-auto">
                        {t('tours.panama_viejo_507.hero.subtitle')}
                    </p>
                    <p className="mt-6 text-xl font-semibold text-white">
                        {t('tours.panama_viejo_507.hero.prices')}
                    </p>
                    <div className="mt-8 flex flex-wrap justify-center gap-x-6 gap-y-3 text-sm text-gray-300">
                        <span className="flex items-center gap-1.5">
                            <CalendarDaysIcon className="h-4 w-4" />
                            {t('tours.panama_viejo_507.hero.dates')}
                        </span>
                        <span className="flex items-center gap-1.5">
                            <ClockIcon className="h-4 w-4" />
                            {t('tours.panama_viejo_507.hero.duration')}
                        </span>
                        <span className="flex items-center gap-1.5">
                            <UserGroupIcon className="h-4 w-4" />
                            {t('tours.panama_viejo_507.hero.type')}
                        </span>
                    </div>
                    <div className="mt-10">
                        <a
                            href="#reservar"
                            className="inline-flex items-center rounded-md bg-indigo-600 px-8 py-3 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 transition-colors"
                        >
                            {t('tours.panama_viejo_507.hero.cta')}
                        </a>
                    </div>
                </div>
            </div>

            {/* The tour runs in Spanish only, so the page stays in Spanish in
                every language. This is the one string that addresses an English
                visitor, explaining why. */}
            {!isSpanish && (
                <div className="bg-amber-50 border-b border-amber-200">
                    <div className="mx-auto max-w-7xl px-6 lg:px-8 py-4">
                        <div className="flex items-start gap-3">
                            <InformationCircleIcon className="h-5 w-5 text-amber-600 flex-shrink-0 mt-0.5" />
                            <p className="text-sm text-amber-800">
                                {t('tours.panama_viejo_507.spanish_notice')}
                            </p>
                        </div>
                    </div>
                </div>
            )}

            {/* Main Content */}
            <div className="mx-auto max-w-7xl px-6 lg:px-8 py-16 sm:py-24">
                <div className="grid grid-cols-1 gap-x-8 gap-y-16 lg:grid-cols-2 lg:items-start lg:gap-y-10">
                    <div className="lg:col-start-1 lg:row-start-1">
                        <div className="lg:max-w-lg">
                            <h2 className="text-3xl font-semibold tracking-tight text-gray-950 sm:text-4xl">
                                {t('tours.panama_viejo_507.details.title')}
                            </h2>
                            <p className="mt-6 text-lg/8 text-gray-700">
                                {t('tours.panama_viejo_507.details.description_1')}
                            </p>
                            <p className="mt-4 text-base/7 text-gray-600 italic">
                                {t('tours.panama_viejo_507.details.description_2')}
                            </p>
                        </div>
                    </div>

                    <div className="lg:sticky lg:top-4 lg:col-start-2 lg:row-span-2 lg:row-start-1">
                        <ImageGallery images={images} />
                    </div>

                    <div className="lg:col-start-1 lg:row-start-2">
                        <div className="max-w-xl text-base/7 text-gray-600 lg:max-w-lg">
                            <h2 className="mt-2 text-2xl font-bold tracking-tight text-gray-900">
                                {t('tours.panama_viejo_507.details.highlights_title')}
                            </h2>
                            <ul role="list" className="mt-8 space-y-8 text-gray-600">
                                {getList('tours.panama_viejo_507.details.highlights').map((highlight, index) => (
                                    <li key={index} className="flex gap-x-3">
                                        <CheckCircleIcon
                                            aria-hidden="true"
                                            className="mt-1 size-5 flex-none text-indigo-600"
                                        />
                                        <span>
                                            <strong className="font-semibold text-gray-900">
                                                {highlight.title}.
                                            </strong>{' '}
                                            {highlight.desc}
                                        </span>
                                    </li>
                                ))}
                            </ul>

                            <h2 className="mt-16 text-2xl font-bold tracking-tight text-gray-900">
                                {t('tours.panama_viejo_507.details.includes_title')}
                            </h2>
                            <ul role="list" className="mt-8 space-y-4 text-gray-600">
                                {getList('tours.panama_viejo_507.details.includes').map((item, index) => (
                                    <li key={index} className="flex gap-x-3">
                                        <span className="mt-1.5 h-2 w-2 flex-none rounded-full bg-indigo-600" />
                                        <span>{item}</span>
                                    </li>
                                ))}
                            </ul>

                            <h2 className="mt-16 text-2xl font-bold tracking-tight text-gray-900">
                                {t('tours.panama_viejo_507.details.facts_title')}
                            </h2>
                            <ul role="list" className="mt-6 space-y-3 text-gray-600">
                                {getList('tours.panama_viejo_507.details.facts').map((fact, index) => (
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
                            {t('tours.panama_viejo_507.booking.title')}
                        </h2>
                        <p className="mt-4 text-lg text-gray-600">
                            {t('tours.panama_viejo_507.booking.subtitle')}
                        </p>
                    </div>

                    <div className="bg-white rounded-2xl shadow-sm ring-1 ring-gray-200 overflow-hidden">
                        {/* Step 1: Date */}
                        <div className="p-6 sm:p-8">
                            <h3 className="text-sm font-semibold text-gray-900 uppercase tracking-wide flex items-center gap-2">
                                <CalendarDaysIcon className="h-5 w-5 text-indigo-600" />
                                {t('tours.panama_viejo_507.booking.step_date')}
                            </h3>
                            <div className="mt-4 grid grid-cols-2 gap-3 sm:max-w-md">
                                {Object.keys(SCHEDULE).map((dateStr) => {
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
                                            <span className="text-xs font-medium uppercase tracking-wide opacity-75">
                                                {label.day}
                                            </span>
                                            <span className="mt-1 text-lg font-bold">{label.date}</span>
                                        </button>
                                    )
                                })}
                            </div>
                        </div>

                        {/* Step 2: Time */}
                        {selectedDate && (
                            <div className="p-6 sm:p-8 border-t border-gray-100">
                                <h3 className="text-sm font-semibold text-gray-900 uppercase tracking-wide flex items-center gap-2">
                                    <ClockIcon className="h-5 w-5 text-indigo-600" />
                                    {t('tours.panama_viejo_507.booking.step_time')}
                                </h3>
                                <div className="mt-4 flex flex-wrap gap-3">
                                    {availableTimes.map((time) => {
                                        const isSelected = selectedTime === time
                                        return (
                                            <button
                                                key={time}
                                                onClick={() => {
                                                    setSelectedTime(time)
                                                    setBookingSuccess(false)
                                                }}
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
                                    {t('tours.panama_viejo_507.booking.step_participants')}
                                </h3>
                                <p className="text-xs text-gray-500 mb-6">
                                    {t('tours.panama_viejo_507.booking.meeting_point')}
                                </p>

                                <div className="space-y-4">
                                    {participantCategories.map((cat) => (
                                        <div key={cat.key} className="flex items-center justify-between py-2">
                                            <div>
                                                <span className="text-sm font-medium text-gray-900">{cat.label}</span>
                                                <span className="ml-2 text-sm text-gray-500">
                                                    {cat.price > 0
                                                        ? `$${cat.price.toFixed(2)}`
                                                        : t('tours.panama_viejo_507.booking.free')}
                                                </span>
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
                                        {t('tours.panama_viejo_507.booking.total_participants', {
                                            count: totalParticipants,
                                        })}
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
                                            <span>{t('tours.panama_viejo_507.booking.senior_discount')}</span>
                                            <span>-${parseFloat(pricing.senior_discount).toFixed(2)}</span>
                                        </div>
                                    )}
                                    <div className="flex justify-between items-center pt-2 border-t border-gray-200">
                                        <span className="text-lg font-bold text-gray-900">
                                            {t('booking.modal.total')}
                                        </span>
                                        <span className="text-2xl font-bold text-gray-900">
                                            ${parseFloat(pricing.grand_total).toFixed(2)}
                                        </span>
                                    </div>
                                </div>

                                {bookingSuccess ? (
                                    <div className="mt-6 space-y-3">
                                        <div className="flex items-center justify-center gap-2 text-green-700 bg-green-50 border border-green-200 rounded-xl p-4">
                                            <CheckCircleIcon className="h-5 w-5" />
                                            <span className="font-medium">
                                                {t('tours.panama_viejo_507.booking.added_to_cart')}
                                            </span>
                                        </div>
                                        <div className="flex flex-col sm:flex-row gap-3">
                                            <button
                                                onClick={() => {
                                                    setBookingSuccess(false)
                                                    setSelectedDate(null)
                                                    setSelectedTime(null)
                                                    setParticipants({
                                                        adult: 0,
                                                        senior: 0,
                                                        child5to17: 0,
                                                        childUnder5: 0,
                                                    })
                                                }}
                                                className="flex-1 rounded-xl border border-gray-300 bg-white px-6 py-3 text-sm font-semibold text-gray-700 hover:bg-gray-50"
                                            >
                                                {t('tours.panama_viejo_507.booking.add_another')}
                                            </button>
                                            <button
                                                onClick={() => navigate('/checkout')}
                                                className="flex-1 rounded-xl bg-indigo-600 px-6 py-3 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500"
                                            >
                                                {t('tours.panama_viejo_507.booking.go_to_checkout')}
                                            </button>
                                        </div>
                                    </div>
                                ) : (
                                    <button
                                        onClick={handleAddToCart}
                                        disabled={!isBookingValid}
                                        className="mt-6 w-full rounded-xl bg-indigo-600 px-6 py-4 text-base font-semibold text-white shadow-sm hover:bg-indigo-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                                    >
                                        {t('tours.panama_viejo_507.booking.reserve_button')}
                                    </button>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    )
}
