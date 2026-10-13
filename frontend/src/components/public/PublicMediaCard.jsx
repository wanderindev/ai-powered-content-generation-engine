import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowDownTrayIcon } from '@heroicons/react/24/outline';
import { downloadPublicMedia } from '../../services/publicMedia';

export default function PublicMediaCard({ item, onClick }) {
  const { t } = useTranslation();
  const [failed, setFailed] = useState(false);
  const imgSrc = item.thumbnail_url || null;

  const handleDownload = (e) => {
    e.stopPropagation();
    if (!item.download_url) return;
    // Gated endpoint — fetch+blob with the auth header (see publicMedia service).
    downloadPublicMedia(item.id, item.title);
  };

  const handleCardKeyDown = (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onClick(item);
    }
  };

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onClick(item)}
      onKeyDown={handleCardKeyDown}
      className="group relative flex flex-col overflow-hidden rounded-lg bg-white ring-1 ring-gray-900/5 shadow-sm transition hover:shadow-md hover:ring-gray-900/10 cursor-pointer text-left"
    >
      {/* Thumbnail */}
      <div className="aspect-[4/3] w-full overflow-hidden bg-gray-100 relative">
        {imgSrc && !failed ? (
          <img
            src={imgSrc}
            alt={item.alt_text || item.title}
            loading="lazy"
            onError={() => setFailed(true)}
            className="h-full w-full object-cover group-hover:scale-105 transition-transform duration-200"
          />
        ) : (
          <div className="flex h-full items-center justify-center text-gray-400 text-xs">
            {t('edu_materials.media_library.no_preview')}
          </div>
        )}

        {/* Download overlay icon */}
        {item.download_url && (
          <button
            type="button"
            onClick={handleDownload}
            aria-label={t('edu_materials.media_library.download_aria', { title: item.title })}
            className="absolute bottom-1.5 right-1.5 rounded-full bg-white/80 p-1.5 text-gray-600 opacity-0 group-hover:opacity-100 transition-opacity hover:bg-white hover:text-indigo-600"
          >
            <ArrowDownTrayIcon className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Info */}
      <div className="flex flex-col gap-1 p-2">
        <p className="text-xs font-medium text-gray-900 line-clamp-2 leading-tight">
          {item.title}
        </p>
        {item.license && (
          <span className="text-[10px] text-gray-400 truncate">
            {item.license}
          </span>
        )}
      </div>
    </div>
  );
}