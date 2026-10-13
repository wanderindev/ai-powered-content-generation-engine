import PublicMediaLibrary from '../../components/public/PublicMediaLibrary';
import EducatorAccessGate from '../../components/public/EducatorAccessGate';

// Media Library section of the Educational Materials shell (#309/#310).
// PublicMediaLibrary renders its own intro heading, search, grid, and modal.
// The educator email gate stays on this section only (its real, downloadable
// content) — the shell chrome and "coming soon" placeholders are open.
export default function MediaLibrarySection() {
  return (
    <EducatorAccessGate>
      <PublicMediaLibrary />
    </EducatorAccessGate>
  );
}
