import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'

const heroImages = [
    '/images/ship-in-locks.jpg',
    '/images/plaza-herrera.jpg',
    '/images/arco-chato-tour.jpg',
    '/images/biomuseo.jpg',
    '/images/catedral-interior.jpg',
    '/images/cupula-san-francisco.jpg',
    '/images/iglesia-de-san-jose.jpg',
    '/images/museo-del-canal-exterior.jpg',
    '/images/museum-circuit.png',
    '/images/panama-viejo.png',
    '/images/poblaciones-precolombinas.png',
    '/images/remolcador.jpg',
]

export default function HeroSection() {
    const { t } = useTranslation()
    const [currentImageIndex, setCurrentImageIndex] = useState(0)

    useEffect(() => {
        const interval = setInterval(() => {
            setCurrentImageIndex((prevIndex) => (prevIndex + 1) % heroImages.length)
        }, 5000)

        return () => clearInterval(interval)
    }, [])

    return (
        <div className="bg-gray-900">
            <div className="relative isolate overflow-hidden pt-14">
                {/* Rotating background images — render only active + preloaded next */}
                {heroImages.map((image, index) => {
                    const nextIndex = (currentImageIndex + 1) % heroImages.length
                    if (index !== currentImageIndex && index !== nextIndex) {
                        return null
                    }
                    return (
                        <img
                            key={image}
                            alt=""
                            src={image}
                            className={`absolute inset-0 -z-10 size-full object-cover transition-opacity duration-1000 ${
                                index === currentImageIndex ? 'opacity-60' : 'opacity-0'
                            }`}
                        />
                    )
                })}

                {/* Gradient overlays for better text readability */}
                <div className="absolute inset-0 -z-10 bg-gradient-to-b from-gray-900/50 via-gray-900/30 to-gray-900/50" />

                <div
                    aria-hidden="true"
                    className="absolute inset-x-0 -top-40 -z-10 transform-gpu overflow-hidden blur-3xl sm:-top-80"
                >
                    <div
                        style={{
                            clipPath:
                                'polygon(74.1% 44.1%, 100% 61.6%, 97.5% 26.9%, 85.5% 0.1%, 80.7% 2%, 72.5% 32.5%, 60.2% 62.4%, 52.4% 68.1%, 47.5% 58.3%, 45.2% 34.5%, 27.5% 76.7%, 0.1% 64.9%, 17.9% 100%, 27.6% 76.8%, 76.1% 97.7%, 74.1% 44.1%)',
                        }}
                        className="relative left-[calc(50%-11rem)] aspect-1155/678 w-144.5 -translate-x-1/2 rotate-30 bg-linear-to-tr from-[#ff80b5] to-[#9089fc] opacity-20 sm:left-[calc(50%-30rem)] sm:w-288.75"
                    />
                </div>

                <div className="mx-auto max-w-7xl px-6 lg:px-8">
                    <div className="mx-auto max-w-2xl py-32 sm:py-48 lg:py-56">
                        <div className="text-center">
                            <h1 className="text-5xl font-semibold tracking-tight text-balance text-white sm:text-7xl">
                                {t('home.hero.title')}
                            </h1>
                            <p className="mt-8 text-lg font-medium text-pretty text-gray-300 sm:text-xl/8">
                                {t('home.hero.subtitle')}
                            </p>
                            <div className="mt-10 flex items-center justify-center gap-x-6">
                                <a
                                    href="/excursiones-academicas"
                                    className="rounded-md bg-indigo-600 px-3.5 py-2.5 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
                                >
                                    {t('home.hero.cta_schools')}
                                </a>
                                <a href="/tours-guiados" className="text-sm/6 font-semibold text-white">
                                    {t('home.hero.cta_tourists')} <span aria-hidden="true">→</span>
                                </a>
                            </div>
                        </div>
                    </div>
                </div>

                <div
                    aria-hidden="true"
                    className="absolute inset-x-0 top-[calc(100%-13rem)] -z-10 transform-gpu overflow-hidden blur-3xl sm:top-[calc(100%-30rem)]"
                >
                    <div
                        style={{
                            clipPath:
                                'polygon(74.1% 44.1%, 100% 61.6%, 97.5% 26.9%, 85.5% 0.1%, 80.7% 2%, 72.5% 32.5%, 60.2% 62.4%, 52.4% 68.1%, 47.5% 58.3%, 45.2% 34.5%, 27.5% 76.7%, 0.1% 64.9%, 17.9% 100%, 27.6% 76.8%, 76.1% 97.7%, 74.1% 44.1%)',
                        }}
                        className="relative left-[calc(50%+3rem)] aspect-1155/678 w-144.5 -translate-x-1/2 bg-linear-to-tr from-[#ff80b5] to-[#9089fc] opacity-20 sm:left-[calc(50%+36rem)] sm:w-288.75"
                    />
                </div>
            </div>
        </div>
    )
}
