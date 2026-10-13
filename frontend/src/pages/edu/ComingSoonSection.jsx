import { useTranslation } from 'react-i18next';
import { ClockIcon } from '@heroicons/react/24/outline';

// Placeholder for Educational Materials sections that don't have content yet
// (Presentations, Infographics, Audio Guides, Historical Trivia). Each route
// passes its own section title key; the "coming soon" copy is shared (#309).
export default function ComingSoonSection({ titleKey }) {
  const { t } = useTranslation();
  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight text-gray-900">{t(titleKey)}</h1>
      <div className="mt-8 flex flex-col items-center justify-center rounded-lg border border-dashed border-gray-300 bg-gray-50 px-6 py-20 text-center">
        <ClockIcon aria-hidden="true" className="size-10 text-indigo-600" />
        <h2 className="mt-4 text-lg font-semibold text-gray-900">
          {t('edu_materials.placeholder.heading')}
        </h2>
        <p className="mt-2 max-w-md text-sm/6 text-gray-600">
          {t('edu_materials.placeholder.text')}
        </p>
      </div>
    </div>
  );
}
