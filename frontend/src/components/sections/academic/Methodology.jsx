import { DocumentMagnifyingGlassIcon, MapIcon, PencilSquareIcon } from '@heroicons/react/24/outline'
import { Trans, useTranslation } from 'react-i18next'

// Sample deliverables linked inline from the step copy: the Miraflores
// 4th–8th grade deck and study guide. Both live at deterministic Spaces keys
// (see CLAUDE.md, "Edu Media Production"), so re-rendering them keeps these
// links valid. Spanish only for now; the same assets are linked from both
// languages until the English versions are produced.
const SAMPLE_VIDEO_URL = 'https://panamaincontext.nyc3.digitaloceanspaces.com/materials/video/4/4th_8th.mp4'
const SAMPLE_GUIDE_URL = 'https://panamaincontext.nyc3.digitaloceanspaces.com/materials/guide/4/4th_8th_es.pdf'

function SampleLink({ href, children }) {
    return (
        <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="font-semibold text-indigo-600 hover:text-indigo-500"
        >
            {children}
        </a>
    )
}

// The <video>/<guide> tags in the locale strings map onto these components.
const descComponents = {
    video: <SampleLink href={SAMPLE_VIDEO_URL} />,
    guide: <SampleLink href={SAMPLE_GUIDE_URL} />,
}

const features = [
    { key: 'context', icon: DocumentMagnifyingGlassIcon },
    { key: 'immersion', icon: MapIcon },
    { key: 'reflection', icon: PencilSquareIcon },
]

export default function Methodology() {
    const { t } = useTranslation()

    return (
        <div id="methodology" className="bg-white py-24 sm:py-32 border-t border-gray-200">
            <div className="mx-auto max-w-7xl px-6 lg:px-8">
                <div className="mx-auto max-w-2xl lg:mx-0">
                    <h2 className="text-4xl font-semibold tracking-tight text-pretty text-gray-900 sm:text-5xl">
                        {t('excursions.methodology.title')}
                    </h2>
                    <p className="mt-6 text-lg/8 text-gray-600">
                        {t('excursions.methodology.description')}
                    </p>
                </div>
                <div className="mx-auto mt-10 max-w-2xl sm:mt-12 lg:mt-16 lg:max-w-none">
                    <dl className="grid max-w-xl grid-cols-1 gap-x-8 gap-y-16 lg:max-w-none lg:grid-cols-3">
                        {features.map((feature) => (
                            <div key={feature.key} className="flex flex-col">
                                <dt className="text-base/7 font-semibold text-gray-900">
                                    <div className="mb-6 flex size-10 items-center justify-center rounded-lg bg-indigo-600">
                                        <feature.icon aria-hidden="true" className="size-6 text-white" />
                                    </div>
                                    {t(`excursions.methodology.steps.${feature.key}.title`)}
                                </dt>
                                <dd className="mt-1 flex flex-auto flex-col text-base/7 text-gray-600">
                                    <p className="flex-auto">
                                        <Trans
                                            i18nKey={`excursions.methodology.steps.${feature.key}.desc`}
                                            components={descComponents}
                                        />
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
