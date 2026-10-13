import { useState } from 'react';

function scoreBadgeColor(score) {
  if (score == null) return 'bg-gray-100 text-gray-600';
  if (score >= 0.8) return 'bg-green-100 text-green-700';
  if (score >= 0.5) return 'bg-yellow-100 text-yellow-700';
  return 'bg-red-100 text-red-700';
}

function statusBorder(status) {
  if (status === 'APPROVED') return 'ring-2 ring-green-400';
  if (status === 'REJECTED') return 'ring-2 ring-red-300 opacity-60';
  return '';
}

export default function MediaCard({ candidate, onClick }) {
  const [failed, setFailed] = useState(false);

  // Prefer CDN thumbnail (from DO Spaces), fall back to external thumbnail_url
  const imgSrc = candidate.thumbnail_cdn_url
    || candidate.thumbnail_url
    || null;

  return (
    <button
      onClick={() => onClick(candidate)}
      className={`group relative flex flex-col overflow-hidden rounded-lg bg-white shadow-sm hover:shadow-md transition-shadow cursor-pointer text-left ${statusBorder(candidate.status)}`}
    >
      {/* Thumbnail */}
      <div className="aspect-[4/3] w-full overflow-hidden bg-gray-100">
        {imgSrc && !failed ? (
          <img
            src={imgSrc}
            alt={candidate.title}
            loading="lazy"
            onError={() => setFailed(true)}
            className="h-full w-full object-cover group-hover:scale-105 transition-transform duration-200"
          />
        ) : (
          <div className="flex h-full items-center justify-center text-gray-400 text-xs">
            No preview
          </div>
        )}
      </div>

      {/* Info */}
      <div className="flex flex-col gap-1 p-2">
        <p className="text-xs font-medium text-gray-900 line-clamp-2 leading-tight">
          {candidate.title}
        </p>
        <div className="flex items-center gap-1.5">
          {candidate.relevance_score != null && (
            <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold ${scoreBadgeColor(candidate.relevance_score)}`}>
              {candidate.relevance_score.toFixed(2)}
            </span>
          )}
          {candidate.license && (
            <span className="text-[10px] text-gray-400 truncate">
              {candidate.license}
            </span>
          )}
        </div>
      </div>

      {/* Status indicator dot */}
      {candidate.status !== 'PENDING' && (
        <div className={`absolute top-1.5 right-1.5 h-2.5 w-2.5 rounded-full ${
          candidate.status === 'APPROVED' ? 'bg-green-500' : 'bg-red-400'
        }`} />
      )}
    </button>
  );
}