import { useState, useEffect, useRef } from 'react';
import { Dialog, DialogPanel, DialogTitle, Transition, TransitionChild } from '@headlessui/react';
import { adminService } from '../../services/admin';

// Claim-level drill-in for one subject's fact-check results (#378), plus the
// "Fix inaccuracies" preview→apply corrections flow (#387).

const VERDICT_STYLES = {
  VERIFIED: 'bg-green-100 text-green-700',
  INACCURATE: 'bg-red-100 text-red-700',
  DISPUTED: 'bg-orange-100 text-orange-700',
  NEEDS_DEEP_RESEARCH: 'bg-amber-100 text-amber-700',
  PENDING: 'bg-gray-100 text-gray-500',
  UNVERIFIABLE: 'bg-gray-100 text-gray-500',
  CORRECTED: 'bg-green-100 text-green-700',
};

// `fact_check_claims.correction` is not only a correction: a VERIFIED row keeps
// what the source says there (#746, and deep research always did), and a
// deferred row keeps the note it was downgraded with. Label it by verdict so a
// correct claim never reads "Correction: …" (#749).
function noteLabel(verdict) {
  if (verdict === 'INACCURATE' || verdict === 'CORRECTED') return 'Correction: ';
  if (verdict === 'DISPUTED') return 'Positions: ';
  if (verdict === 'VERIFIED') return 'Source: ';
  return 'Note: ';
}

function Citation({ citation }) {
  if (!citation) return null;
  // Citations are comma/semicolon-separated; render URL tokens as links.
  const parts = citation.split(/[;,]\s*/).filter(Boolean);
  return (
    <p className="mt-1 text-xs text-gray-500">
      Sources:{' '}
      {parts.map((part, i) => (
        <span key={i}>
          {i > 0 && ', '}
          {part.startsWith('http') ? (
            <a
              href={part}
              target="_blank"
              rel="noreferrer"
              className="text-indigo-600 underline hover:text-indigo-800"
            >
              {part}
            </a>
          ) : (
            part
          )}
        </span>
      ))}
    </p>
  );
}

// The corrections preview (#387): per claim, the old snippet (red) and its
// replacement (green), per language for articles; non-applicable edits show
// why the claim can't be corrected yet.
function CorrectionsPreview({ preview, claimTextById }) {
  const byClaim = new Map();
  preview.edits.forEach((edit) => {
    if (!byClaim.has(edit.claim_id)) byClaim.set(edit.claim_id, []);
    byClaim.get(edit.claim_id).push(edit);
  });
  const showLanguage = preview.edits.some((e) => e.language !== 'en');
  return (
    <ul className="mt-3 space-y-4">
      {[...byClaim.entries()].map(([claimId, edits]) => (
        <li key={claimId} className="rounded border border-gray-200 p-3">
          <p className="text-sm text-gray-900">{claimTextById[claimId] || `Claim ${claimId}`}</p>
          {edits.map((edit, i) => (
            <div key={i} className="mt-2">
              {showLanguage && (
                <span className="text-xs font-medium uppercase text-gray-400">
                  {edit.language}
                </span>
              )}
              {edit.status === 'ok' ? (
                <div className="mt-1 space-y-1 text-xs">
                  <p className="rounded bg-red-50 px-2 py-1 text-red-800 line-through">
                    {edit.find}
                  </p>
                  <p className="rounded bg-green-50 px-2 py-1 text-green-800">{edit.replace}</p>
                </div>
              ) : edit.status === 'skipped' ? (
                <p className="mt-1 rounded bg-green-50 px-2 py-1 text-xs text-green-800">
                  Already correct — will be marked resolved on apply: {edit.reason}
                </p>
              ) : (
                <p className="mt-1 rounded bg-amber-50 px-2 py-1 text-xs text-amber-800">
                  Not correctable ({edit.status.replaceAll('_', ' ')}): {edit.reason}
                </p>
              )}
            </div>
          ))}
        </li>
      ))}
    </ul>
  );
}

export default function FactCheckModal({
  open,
  onClose,
  token,
  subjectType,
  subjectId,
  title,
  onCountsChange,
}) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  // Corrections flow (#387): null → previewing (spinner) → preview shown →
  // applying → applied summary. Preview is stateless; apply echoes it back.
  const [preview, setPreview] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applyResult, setApplyResult] = useState(null);
  // "Fix inaccuracies" sits below the claim list but its preview renders above
  // it, so on a long list the panel (and its Apply button) lands off-screen and
  // the click reads as a no-op. Pull it into view when it arrives.
  const previewRef = useRef(null);

  useEffect(() => {
    if (!open || subjectId == null) return;
    setPreview(null);
    setApplyResult(null);
    (async () => {
      setLoading(true);
      setError(null);
      setDetail(null);
      try {
        setDetail(await adminService.getFactCheckClaims(token, subjectType, subjectId));
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    })();
  }, [open, token, subjectType, subjectId]);

  useEffect(() => {
    if (preview) previewRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [preview]);

  const correctable = detail
    ? detail.claims.filter((c) => c.verdict === 'INACCURATE' && c.correction)
    : [];
  const claimTextById = Object.fromEntries((detail?.claims || []).map((c) => [c.id, c.claim_text]));
  const applicableEdits = preview ? preview.edits.filter((e) => e.status === 'ok') : [];
  // "Skipped" = the model says the content never asserts the inaccuracy;
  // applying echoes these back so the claim resolves without a content edit.
  const skippedEdits = preview ? preview.edits.filter((e) => e.status === 'skipped') : [];

  const handlePreview = async () => {
    setPreviewing(true);
    setError(null);
    setApplyResult(null);
    try {
      setPreview(await adminService.previewFactCheckCorrections(token, subjectType, subjectId));
    } catch (err) {
      setError(err.message);
    } finally {
      setPreviewing(false);
    }
  };

  const handleApply = async () => {
    setApplying(true);
    setError(null);
    try {
      const result = await adminService.applyFactCheckCorrections(
        token,
        subjectType,
        subjectId,
        [...applicableEdits, ...skippedEdits].map(({ claim_id, language, find, replace, status }) => ({
          claim_id,
          language,
          find,
          replace,
          status,
        })),
      );
      setApplyResult(result);
      setPreview(null);
      onCountsChange?.(subjectId, result.counts);
      // Refresh the claim list so CORRECTED rows render green.
      setDetail(await adminService.getFactCheckClaims(token, subjectType, subjectId));
    } catch (err) {
      setError(err.message);
    } finally {
      setApplying(false);
    }
  };

  return (
    <Transition appear show={open}>
      <Dialog as="div" className="relative z-50" onClose={onClose}>
        <TransitionChild
          enter="ease-out duration-150"
          enterFrom="opacity-0"
          enterTo="opacity-100"
          leave="ease-in duration-100"
          leaveFrom="opacity-100"
          leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-black/40" />
        </TransitionChild>

        <div className="fixed inset-0 flex items-center justify-center p-4">
          <TransitionChild
            enter="ease-out duration-150"
            enterFrom="opacity-0 scale-95"
            enterTo="opacity-100 scale-100"
            leave="ease-in duration-100"
            leaveFrom="opacity-100 scale-100"
            leaveTo="opacity-0 scale-95"
          >
            <DialogPanel className="max-h-[80vh] w-full max-w-2xl overflow-y-auto rounded-lg bg-white p-6 shadow-xl">
              <DialogTitle className="text-lg font-semibold text-gray-900">
                Fact-check claims{title ? ` — ${title}` : ''}
              </DialogTitle>

              {loading && <p className="mt-4 text-sm text-gray-500">Loading claims...</p>}
              {error && (
                <div className="mt-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {error}
                </div>
              )}

              {applyResult && (
                <div className="mt-4 rounded border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">
                  {applyResult.corrected_claim_ids.length} claim(s) corrected in the content.
                  {(applyResult.already_correct_claim_ids?.length ?? 0) > 0 && (
                    <span>
                      {' '}
                      {applyResult.already_correct_claim_ids.length} claim(s) resolved as already
                      correct.
                    </span>
                  )}
                  {applyResult.failed.length > 0 && (
                    <span className="text-amber-800">
                      {' '}
                      {applyResult.failed.length} edit(s) did not apply:{' '}
                      {applyResult.failed.map((f) => f.reason).join('; ')}.
                    </span>
                  )}
                </div>
              )}

              {detail && detail.claims.length === 0 && (
                <p className="mt-4 text-sm text-gray-500">
                  No fact-check claims recorded for this item yet.
                </p>
              )}

              {detail && detail.claims.length > 0 && (
                <>
                  <p className="mt-1 text-sm text-gray-600">
                    {detail.total_claims} claims —{' '}
                    {Object.entries(detail.counts)
                      .map(([verdict, count]) => `${count} ${verdict.toLowerCase().replaceAll('_', ' ')}`)
                      .join(', ')}
                  </p>

                  {preview && (
                    <div
                      ref={previewRef}
                      className="mt-4 rounded-lg border border-indigo-200 bg-indigo-50/40 p-3"
                    >
                      <p className="text-sm font-medium text-gray-900">
                        Proposed corrections — review, then apply
                      </p>
                      {/* Sticky so Apply stays reachable no matter how many
                          diffs the preview holds (19 for one research doc). */}
                      <div className="sticky top-0 z-10 -mx-3 flex items-center justify-between gap-2 border-b border-indigo-200 bg-indigo-50 px-3 py-2">
                        <span className="text-xs text-gray-600">
                          Nothing is written until you apply.
                        </span>
                        <span className="flex gap-2">
                          <button
                            onClick={() => setPreview(null)}
                            disabled={applying}
                            className="rounded border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
                          >
                            Cancel
                          </button>
                          <button
                            onClick={handleApply}
                            disabled={applying || (applicableEdits.length === 0 && skippedEdits.length === 0)}
                            className="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700 disabled:opacity-50"
                          >
                            {applying
                              ? 'Applying...'
                              : `Apply ${applicableEdits.length} edit(s)` +
                                (skippedEdits.length > 0
                                  ? ` + resolve ${skippedEdits.length}`
                                  : '')}
                          </button>
                        </span>
                      </div>
                      <CorrectionsPreview preview={preview} claimTextById={claimTextById} />
                    </div>
                  )}

                  <ul className="mt-4 divide-y divide-gray-100">
                    {detail.claims.map((claim) => (
                      <li key={claim.id} className="py-3">
                        <div className="flex items-start gap-2">
                          <span
                            className={`mt-0.5 shrink-0 rounded-full px-2 py-0.5 text-xs ${
                              VERDICT_STYLES[claim.verdict] || 'bg-gray-100 text-gray-500'
                            }`}
                          >
                            {claim.verdict.replaceAll('_', ' ')}
                          </span>
                          <div className="min-w-0">
                            <p className="text-sm text-gray-900">{claim.claim_text}</p>
                            {claim.correction && (
                              <p className="mt-1 text-xs text-gray-700">
                                <span className="font-medium">
                                  {noteLabel(claim.verdict)}
                                </span>
                                {claim.correction}
                              </p>
                            )}
                            <Citation citation={claim.citation} />
                          </div>
                        </div>
                      </li>
                    ))}
                  </ul>
                </>
              )}

              <div className="mt-6 flex justify-between">
                <div>
                  {correctable.length > 0 && !preview && (
                    <>
                      <button
                        onClick={handlePreview}
                        disabled={previewing}
                        className="rounded bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-700 disabled:opacity-50"
                        title="Generate targeted edits that apply the corrections to the content"
                      >
                        {previewing ? 'Generating edits...' : 'Fix inaccuracies'}
                      </button>
                      {previewing && (
                        <p className="mt-2 text-xs text-gray-500">
                          Drafting edits for {correctable.length} claim(s) — the proposal will open
                          above for review; nothing is written yet.
                        </p>
                      )}
                    </>
                  )}
                </div>
                <button
                  onClick={onClose}
                  className="rounded border border-gray-300 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50"
                >
                  Close
                </button>
              </div>
            </DialogPanel>
          </TransitionChild>
        </div>
      </Dialog>
    </Transition>
  );
}
