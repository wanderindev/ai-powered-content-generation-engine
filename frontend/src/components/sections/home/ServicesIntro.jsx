import { AcademicCapIcon, GlobeAmericasIcon, MapIcon } from '@heroicons/react/20/solid'

const features = [
    {
        name: 'Academic Excursions',
        description:
            'Immersive field trips designed for students, aligning with curriculum standards to bring history and culture to life.',
        href: '/excursiones-academicas',
        icon: AcademicCapIcon,
    },
    {
        name: 'Tourist Tours',
        description:
            'Guided experiences for travelers seeking a deeper understanding of Panama, beyond the typical tourist spots.',
        href: '/tours-guiados',
        icon: GlobeAmericasIcon,
    },
    {
        name: 'Custom Experiences',
        description:
            'Tailored itineraries for groups, families, or institutions looking for specific educational or cultural themes.',
        href: '/contacto',
        icon: MapIcon,
    },
]

export default function ServicesIntro() {
    return (
        <div className="bg-white py-24 sm:py-32">
            <div className="mx-auto max-w-7xl px-6 lg:px-8">
                <div className="mx-auto max-w-2xl lg:text-center">
                    <h2 className="text-base/7 font-semibold text-indigo-600">Discover Panama</h2>
                    <p className="mt-2 text-4xl font-semibold tracking-tight text-pretty text-gray-900 sm:text-5xl lg:text-balance">
                        Education and Adventure Combined
                    </p>
                    <p className="mt-6 text-lg/8 text-gray-600">
                        We provide specialized context to your visit, whether you are a school looking for educational value or a traveler seeking authentic connection.
                    </p>
                </div>
                <div className="mx-auto mt-16 max-w-2xl sm:mt-20 lg:mt-24 lg:max-w-none">
                    <dl className="grid max-w-xl grid-cols-1 gap-x-8 gap-y-16 lg:max-w-none lg:grid-cols-3">
                        {features.map((feature) => (
                            <div key={feature.name} className="flex flex-col">
                                <dt className="flex items-center gap-x-3 text-base/7 font-semibold text-gray-900">
                                    <feature.icon aria-hidden="true" className="size-5 flex-none text-indigo-600" />
                                    {feature.name}
                                </dt>
                                <dd className="mt-4 flex flex-auto flex-col text-base/7 text-gray-600">
                                    <p className="flex-auto">{feature.description}</p>
                                    <p className="mt-6">
                                        <a href={feature.href} className="text-sm/6 font-semibold text-indigo-600 hover:text-indigo-500">
                                            Learn more <span aria-hidden="true">→</span>
                                        </a>
                                    </p>
                                </dd>
                            </div>
                        ))}
                    </dl>
                </div>
            </div>
        </div>
    )
}
