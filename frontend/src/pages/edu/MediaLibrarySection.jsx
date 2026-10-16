import PublicMediaLibrary from '../../components/public/PublicMediaLibrary';

// Media Library section of the Educational Materials shell (#309/#310).
// PublicMediaLibrary renders its own intro heading, search, grid, and modal.
// Open to everyone — the educator email gate that used to wrap this section
// was removed, matching the no-login Presentations section.
export default function MediaLibrarySection() {
  return <PublicMediaLibrary />;
}
