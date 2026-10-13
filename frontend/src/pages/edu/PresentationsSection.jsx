import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowDownTrayIcon, ClockIcon, DocumentTextIcon } from '@heroicons/react/24/outline';
import { getPublicDeliverables } from '../../services/publicEdu';

// Presentations section of the Educational Materials shell (#326): the pre-visit
// deliverables (narrated video + study-guide PDF) for each excursion × grade band.
// Public / no login — sources /public-edu/deliverables (published materials only).
export default function PresentationsSection() {
  const { t, i18n } = useTranslation();
  const lang = i18n.language?.startsWith('en') ? 'en' : 'es';

  const [excursions, setExcursions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await getPublicDeliverables();
        if (active) setExcursions(data.excursions || []);
      } catch (err) {
        if (active) setError(err.message);
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight text-gray-900">
        {t('edu_materials.nav.presentations')}
      </h1>
      <p className="mt-2 max-w-3xl text-sm leading-relaxed text-gray-600">
        {t('edu_materials.deliverables.intro')}
      </p>

      {loading ? (
        <div className="flex items-center justify-center py-20 text-gray-400">
          {t('edu_materials.deliverables.loading')}
        </div>
      ) : error ? (
        <div className="mt-8 rounded-md bg-red-50 p-4">
          <p className="text-sm text-red-700">{t('edu_materials.deliverables.error')}</p>
        </div>
      ) : excursions.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="mt-8 space-y-12">
          {excursions.map((exc) => (
            <section key={exc.excursion_key}>
              <h2 className="text-lg font-semibold tracking-tight text-gray-900">
                {lang === 'en' ? exc.display_name : exc.display_name_es}
              </h2>
              <div className="mt-4 grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
                {exc.bands.map((band) => (
                  <DeliverableCard key={band.grade_band} band={band} />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

function DeliverableCard({ band }) {
  const { t } = useTranslation();
  const minutes = band.video?.duration_ms
    ? Math.max(1, Math.round(band.video.duration_ms / 60000))
    : null;

  return (
    <div className="flex flex-col overflow-hidden rounded-lg bg-white ring-1 ring-gray-900/5 shadow-sm">
      {band.video && (
        <video
          controls
          preload="none"
          controlsList="nodownload"
          poster={band.video.thumbnail_url || undefined}
          src={band.video.url}
          className="aspect-video w-full bg-gray-900"
        />
      )}

      <div className="flex flex-1 flex-col gap-3 p-4">
        <div className="flex items-center justify-between">
          <span className="inline-flex items-center rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-semibold text-indigo-700">
            {t('edu_materials.deliverables.grade_band', { band: band.grade_band })}
          </span>
          {minutes != null && (
            <span className="text-xs text-gray-500">
              {t('edu_materials.deliverables.minutes', { count: minutes })}
            </span>
          )}
        </div>

        {/* Study guide: its cover thumbnail, clickable to open the PDF. */}
        {band.guide_pdf_url && (
          <a
            href={band.guide_pdf_url}
            target="_blank"
            rel="noopener noreferrer"
            className="group mt-auto flex items-center gap-3 rounded-lg border border-gray-200 p-2 transition-colors hover:border-indigo-300 hover:bg-indigo-50/50"
          >
            {band.guide_thumbnail_url ? (
              <img
                src={band.guide_thumbnail_url}
                alt=""
                className="h-14 w-20 shrink-0 rounded object-cover ring-1 ring-gray-900/5"
              />
            ) : (
              <span className="flex h-14 w-20 shrink-0 items-center justify-center rounded bg-gray-100 text-gray-400 ring-1 ring-gray-900/5">
                <DocumentTextIcon className="h-6 w-6" />
              </span>
            )}
            <span className="flex items-center gap-1.5 text-sm font-semibold text-indigo-700">
              <ArrowDownTrayIcon className="h-4 w-4" />
              {t('edu_materials.deliverables.download_guide')}
            </span>
          </a>
        )}
      </div>
    </div>
  );
}

function EmptyState() {
  const { t } = useTranslation();
  return (
    <div className="mt-8 flex flex-col items-center justify-center rounded-lg border border-dashed border-gray-300 bg-gray-50 px-6 py-20 text-center">
      <ClockIcon aria-hidden="true" className="size-10 text-indigo-600" />
      <h2 className="mt-4 text-lg font-semibold text-gray-900">
        {t('edu_materials.deliverables.empty_heading')}
      </h2>
      <p className="mt-2 max-w-md text-sm/6 text-gray-600">
        {t('edu_materials.deliverables.empty_text')}
      </p>
    </div>
  );
}
