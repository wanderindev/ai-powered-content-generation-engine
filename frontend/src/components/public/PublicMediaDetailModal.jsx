import { Fragment, useState, useEffect } from 'react';
import { Dialog, DialogPanel, DialogTitle, Transition, TransitionChild } from '@headlessui/react';
import {
  XMarkIcon,
  ArrowTopRightOnSquareIcon,
  ArrowDownTrayIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
} from '@heroicons/react/24/outline';
import { useTranslation } from 'react-i18next';
import { downloadPublicMedia } from '../../services/publicMedia';

export default function PublicMediaDetailModal({
  open,
  onClose,
  item,
  onNavigate,
  hasPrev,
  hasNext,
}) {
  const { t } = useTranslation();
  const [imgLoaded, setImgLoaded] = useState(false);

  // Reset the load state when the displayed item changes by adjusting state
  // during render (React's recommended alternative to a reset effect, which
  // would setState synchronously — set-state-in-effect).
  const [loadedItemId, setLoadedItemId] = useState(item?.id);
  if (item?.id !== loadedItemId) {
    setLoadedItemId(item?.id);
    setImgLoaded(false);
  }

  // Keyboard navigation
  useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      if (e.key === 'ArrowLeft' && hasPrev) onNavigate(-1);
      if (e.key === 'ArrowRight' && hasNext) onNavigate(1);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, hasPrev, hasNext, onNavigate]);

  const d = item;

  return (
    <Transition appear show={open} as={Fragment}>
      <Dialog as="div" className="relative z-50" onClose={onClose}>
        <TransitionChild
          as={Fragment}
          enter="ease-out duration-200"
          enterFrom="opacity-0"
          enterTo="opacity-100"
          leave="ease-in duration-150"
          leaveFrom="opacity-100"
          leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-gray-900/60" />
        </TransitionChild>

        <div className="fixed inset-0 overflow-y-auto">
          <div className="flex min-h-full items-center justify-center p-4">
            <TransitionChild
              as={Fragment}
              enter="ease-out duration-200"
              enterFrom="opacity-0 scale-95"
              enterTo="opacity-100 scale-100"
              leave="ease-in duration-150"
              leaveFrom="opacity-100 scale-100"
              leaveTo="opacity-0 scale-95"
            >
              <DialogPanel className="relative w-full max-w-4xl transform overflow-hidden rounded-xl bg-white shadow-2xl transition-all">
                {/* Close button */}
                <button
                  onClick={onClose}
                  className="absolute top-3 right-3 z-10 rounded-full bg-white/80 p-1 text-gray-500 hover:text-gray-700 hover:bg-white"
                >
                  <XMarkIcon className="h-5 w-5" />
                </button>

                {/* Nav arrows */}
                {hasPrev && (
                  <button
                    onClick={() => onNavigate(-1)}
                    className="absolute left-3 top-1/2 -translate-y-1/2 z-10 rounded-full bg-white/80 p-1.5 text-gray-500 hover:text-gray-700 hover:bg-white shadow"
                  >
                    <ChevronLeftIcon className="h-5 w-5" />
                  </button>
                )}
                {hasNext && (
                  <button
                    onClick={() => onNavigate(1)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 z-10 rounded-full bg-white/80 p-1.5 text-gray-500 hover:text-gray-700 hover:bg-white shadow"
                  >
                    <ChevronRightIcon className="h-5 w-5" />
                  </button>
                )}

                {d ? (
                  <div className="flex flex-col md:flex-row">
                    {/* Image */}
                    <div className="md:w-1/2 bg-gray-50 flex items-center justify-center min-h-[300px] max-h-[500px]">
                      {!imgLoaded && (
                        <div className="absolute text-gray-300 text-sm">{t('edu_materials.media_library.loading_image')}</div>
                      )}
                      <img
                        src={d.thumbnail_url || d.download_url}
                        alt={d.alt_text || d.title}
                        onLoad={() => setImgLoaded(true)}
                        className={`max-h-[500px] w-full object-contain transition-opacity ${imgLoaded ? 'opacity-100' : 'opacity-0'}`}
                      />
                    </div>

                    {/* Details */}
                    <div className="md:w-1/2 p-5 flex flex-col gap-4 overflow-y-auto max-h-[500px]">
                      <DialogTitle className="text-lg font-semibold text-gray-900 pr-8">
                        {d.title}
                      </DialogTitle>

                      {/* Description */}
                      {d.description && (
                        <div>
                          <p className="text-xs font-medium text-gray-500 mb-1">
                            {t('edu_materials.media_library.detail.description')}
                          </p>
                          <p className="text-sm text-gray-600 leading-relaxed">{d.description}</p>
                        </div>
                      )}

                      {/* Metadata grid */}
                      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                        {d.attribution && (
                          <>
                            <dt className="text-gray-500">{t('edu_materials.media_library.detail.attribution')}</dt>
                            <dd className="text-gray-900 truncate" title={d.attribution}>{d.attribution}</dd>
                          </>
                        )}
                        {d.license && (
                          <>
                            <dt className="text-gray-500">{t('edu_materials.media_library.detail.license')}</dt>
                            <dd className="text-gray-900">
                              {d.license_url ? (
                                <a href={d.license_url} target="_blank" rel="noopener noreferrer" className="text-indigo-600 hover:underline">
                                  {d.license}
                                </a>
                              ) : d.license}
                            </dd>
                          </>
                        )}
                        {d.width && d.height && (
                          <>
                            <dt className="text-gray-500">{t('edu_materials.media_library.detail.dimensions')}</dt>
                            <dd className="text-gray-900">{d.width} x {d.height}</dd>
                          </>
                        )}
                        {d.source && (
                          <>
                            <dt className="text-gray-500">{t('edu_materials.media_library.detail.source')}</dt>
                            <dd className="text-gray-900">{d.source}</dd>
                          </>
                        )}
                      </dl>

                      {/* Source link */}
                      {d.source_url && (
                        <a
                          href={d.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-sm text-indigo-600 hover:underline"
                        >
                          {t('edu_materials.media_library.detail.view_source')}
                          <ArrowTopRightOnSquareIcon className="h-3.5 w-3.5" />
                        </a>
                      )}

                      {/* Download button */}
                      {d.download_url && (
                        <div className="border-t pt-3 mt-auto">
                          <button
                            type="button"
                            onClick={() => downloadPublicMedia(d.id, d.title)}
                            className="inline-flex items-center gap-2 rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500"
                          >
                            <ArrowDownTrayIcon className="h-4 w-4" />
                            {t('edu_materials.media_library.detail.download_full')}
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                ) : null}
              </DialogPanel>
            </TransitionChild>
          </div>
        </div>
      </Dialog>
    </Transition>
  );
}