import MediaCard from './MediaCard';

export default function MediaGrid({ candidates, onCardClick }) {
  if (!candidates.length) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-gray-400">
        <p className="text-lg font-medium">No images found</p>
        <p className="text-sm mt-1">Try adjusting the filters</p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
      {candidates.map((c) => (
        <MediaCard key={c.id} candidate={c} onClick={onCardClick} />
      ))}
    </div>
  );
}
