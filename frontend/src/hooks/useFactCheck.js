import { useState, useEffect, useCallback } from 'react';
import { adminService } from '../services/admin';

// Per-page fact-check state for the admin grids (#378): the per-row verdict
// counts (from the summary endpoint), the run-fact-check action (a background
// job since #384 — kicked off, then polled to completion), and the claims-modal
// record. Shared by AdminSuggestions (SUGGESTION/
// suggestions), AdminResearch (RESEARCH/research), AdminArticles (ARTICLE/
// articles).

// Status derivation shared by the badge cell and the approve-warning check.
// CORRECTED (#387) is a terminal green state — an INACCURATE claim whose
// correction was applied to the content — so it never counts as flagged.
// DISPUTED is tracked apart from flagged: only INACCURATE claims are
// auto-correctable ("Fix inaccuracies" drains them to CORRECTED), while
// disputed ones need editorial judgment — separating them lets the red
// count reach zero instead of sticking on claims with no fix action.
export function factCheckState(counts) {
  if (!counts || Object.keys(counts).length === 0) return { kind: 'none' };
  const flagged = counts.INACCURATE || 0;
  const disputed = counts.DISPUTED || 0;
  const open = (counts.PENDING || 0) + (counts.NEEDS_DEEP_RESEARCH || 0);
  const corrected = counts.CORRECTED || 0;
  if (flagged > 0) return { kind: 'flagged', flagged, disputed, open, corrected };
  if (open > 0) return { kind: 'open', disputed, open, corrected };
  if (disputed > 0) return { kind: 'disputed', disputed, corrected };
  if (corrected > 0) return { kind: 'corrected', corrected };
  return { kind: 'passed' };
}

// Human warning for the approve-anyway dialog; null when nothing to warn about.
export function factCheckWarning(counts, entityLabel) {
  const state = factCheckState(counts);
  if (state.kind === 'none') {
    return `This ${entityLabel} has not been fact-checked.`;
  }
  if (state.kind === 'flagged') {
    const disputed = state.disputed ? `, ${state.disputed} disputed` : '';
    const open = state.open ? ` and ${state.open} unresolved` : '';
    return `Fact-check flagged ${state.flagged} claim(s) as inaccurate${disputed}${open}.`;
  }
  if (state.kind === 'open') {
    const disputed = state.disputed ? ` and ${state.disputed} disputed claim(s)` : '';
    return `Fact-check has ${state.open} unresolved claim(s) (pending or awaiting deep research)${disputed}.`;
  }
  if (state.kind === 'disputed') {
    return `Fact-check found ${state.disputed} disputed claim(s) that need editorial review.`;
  }
  return null;
}

const POLL_INTERVAL_MS = 5000;
// 45 minutes — far beyond what extract-only or grounding jobs need (#389), but
// harmless headroom kept from the web-search era (that tier was removed in #751).
const MAX_POLLS = 540;

export function useFactCheck({ token, subjectType, entityType, showToast, dismissToast, titleOf }) {
  const [summary, setSummary] = useState({}); // subject_id -> counts
  const [running, setRunning] = useState(() => new Set());
  const [modalRecord, setModalRecord] = useState(null);

  useEffect(() => {
    adminService
      .getFactCheckSummary(token)
      .then((items) => {
        const map = {};
        items
          .filter((item) => item.subject_type === subjectType)
          .forEach((item) => {
            map[item.subject_id] = item.counts;
          });
        setSummary(map);
      })
      // Non-fatal: the grid still works, badges just show "Not checked".
      .catch((err) => console.error('Failed to load fact-check summary:', err));
  }, [token, subjectType]);

  const getCounts = useCallback((record) => summary[record.id] || null, [summary]);
  const isRunning = useCallback((record) => running.has(record.id), [running]);
  // Refresh one subject's counts in place (after applying corrections, #387).
  const updateCounts = useCallback((subjectId, counts) => {
    setSummary((prev) => ({ ...prev, [subjectId]: counts }));
  }, []);

  const run = useCallback(
    async (record) => {
      setRunning((prev) => new Set(prev).add(record.id));
      const toastId = showToast({
        type: 'info',
        title: 'Fact-check running',
        message: `Checking "${titleOf(record)}" in the background — usually done in a couple of minutes.`,
        duration: 0,
      });
      try {
        // The endpoint returns 202 + a background job (#384); poll it.
        const job = await adminService.runFactCheck(token, entityType, record.id);
        let status = job;
        for (let i = 0; i < MAX_POLLS; i++) {
          if (status.status === 'SUCCEEDED' || status.status === 'FAILED') break;
          await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
          status = await adminService.getCrawlJob(token, job.id);
        }
        if (status.status === 'SUCCEEDED') {
          const counts = status.stats?.counts || {};
          setSummary((prev) => ({ ...prev, [record.id]: counts }));
          const parts = [
            (counts.INACCURATE || 0) > 0 && `${counts.INACCURATE} inaccurate`,
            (counts.DISPUTED || 0) > 0 && `${counts.DISPUTED} disputed`,
          ].filter(Boolean);
          showToast({
            type: parts.length > 0 ? 'error' : 'success',
            title: parts.length > 0 ? `Fact-check found ${parts.join(', ')}` : 'Fact-check passed',
            message: `${status.stats?.total_claims ?? 0} claims checked. Click the status badge for details.`,
          });
        } else if (status.status === 'FAILED') {
          showToast({
            type: 'error',
            title: 'Fact-check failed',
            message: status.error || 'The background job failed.',
          });
        } else {
          showToast({
            type: 'error',
            title: 'Fact-check still running',
            message: 'Gave up waiting for the job — reload the page later to see the results.',
          });
        }
      } catch (err) {
        showToast({ type: 'error', title: 'Fact-check failed', message: err.message });
      } finally {
        dismissToast(toastId);
        setRunning((prev) => {
          const next = new Set(prev);
          next.delete(record.id);
          return next;
        });
      }
    },
    [token, entityType, showToast, dismissToast, titleOf],
  );

  return { getCounts, isRunning, run, updateCounts, modalRecord, setModalRecord };
}
