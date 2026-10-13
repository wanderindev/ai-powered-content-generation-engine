import { factCheckState } from '../../hooks/useFactCheck';

// Fact-check status badge for the admin grids (#378). Reads the per-subject
// verdict counts (from GET /admin/dashboard/fact-check/summary) and renders
// one of: not-checked / N flagged / N open / N disputed / N corrected / Passed.
// Flagged counts INACCURATE only (the auto-correctable claims); DISPUTED gets
// its own orange chip next to the main badge so contested claims stay visible
// without blocking the red count from draining. Clicking a non-empty badge
// opens the claims drill-in modal.

const BADGES = {
  none: { label: 'Not checked', className: 'bg-gray-100 text-gray-500' },
  flagged: { className: 'bg-red-100 text-red-700' },
  open: { className: 'bg-amber-100 text-amber-700' },
  disputed: { className: 'bg-orange-100 text-orange-700' },
  corrected: { className: 'bg-green-100 text-green-700' },
  passed: { label: 'Passed', className: 'bg-green-100 text-green-700' },
};

const PILL = 'inline-block cursor-pointer rounded-full px-2 py-0.5 text-xs hover:ring-1 hover:ring-gray-300';

export default function FactCheckStatusCell({ data, colDef }) {
  const { getCounts, onOpen } = colDef.cellRendererParams || {};
  const counts = getCounts ? getCounts(data) : null;
  const state = factCheckState(counts);
  const badge = BADGES[state.kind];
  const label =
    state.kind === 'flagged'
      ? `${state.flagged} flagged`
      : state.kind === 'open'
        ? `${state.open} open`
        : state.kind === 'disputed'
          ? `${state.disputed} disputed`
          : state.kind === 'corrected'
            ? `${state.corrected} corrected`
            : badge.label;

  if (state.kind === 'none') {
    return (
      <span className={`inline-block rounded-full px-2 py-0.5 text-xs ${badge.className}`}>
        {label}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1">
      <button
        onClick={() => onOpen?.(data)}
        className={`${PILL} ${badge.className}`}
        title="View fact-check claims"
      >
        {label}
      </button>
      {state.kind !== 'disputed' && state.disputed > 0 && (
        <button
          onClick={() => onOpen?.(data)}
          className={`${PILL} ${BADGES.disputed.className}`}
          title="View fact-check claims"
        >
          {state.disputed} disputed
        </button>
      )}
    </span>
  );
}
