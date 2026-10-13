import { useState, useEffect, useRef, useCallback } from 'react';
import { adminService } from '../../services/admin';
import { ArrowPathIcon, ChevronDownIcon, ChevronRightIcon } from '@heroicons/react/24/outline';

const POLL_INTERVAL_MS = 3000;

const JOBS = [
  { kind: 'crawl', jobType: 'WIKIMEDIA', label: 'Crawl Wikimedia' },
  { kind: 'crawl-loc', jobType: 'LOC', label: 'Crawl LOC' },
  { kind: 'score', jobType: 'SCORE', label: 'Score candidates' },
];

const ACTIVE_STATUSES = ['PENDING', 'RUNNING'];

function isActive(job) {
  return job && ACTIVE_STATUSES.includes(job.status);
}

function StatsSummary({ job }) {
  const s = job.stats || {};
  // The stats dict shape differs per job type; show whatever's present.
  const parts = [];
  if (s.categories_crawled != null) parts.push(`${s.categories_crawled} categories`);
  if (s.queries_executed != null) parts.push(`${s.queries_executed} queries`);
  if (s.images_new != null) parts.push(`${s.images_new} new`);
  if (s.images_skipped != null) parts.push(`${s.images_skipped} skipped`);
  if (s.candidates_scored != null) parts.push(`${s.candidates_scored} scored`);
  if (s.batches_processed != null) parts.push(`${s.batches_processed} batches`);
  const errorCount = Array.isArray(s.errors) ? s.errors.length : 0;
  return (
    <span>
      {parts.join(' · ') || 'Done'}
      {errorCount > 0 && <span className="text-amber-600"> · {errorCount} error(s)</span>}
    </span>
  );
}

export default function CrawlPanel({ token, onComplete }) {
  const [open, setOpen] = useState(false);
  const [job, setJob] = useState(null); // the job currently being tracked
  const [error, setError] = useState(null);
  const [starting, setStarting] = useState(false);
  const pollRef = useRef(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const poll = useCallback(
    async (id) => {
      try {
        const updated = await adminService.getCrawlJob(token, id);
        setJob(updated);
        if (!isActive(updated)) {
          stopPolling();
          if (updated.status === 'SUCCEEDED' && onComplete) onComplete();
        }
      } catch (err) {
        setError(err.message);
        stopPolling();
      }
    },
    [token, stopPolling, onComplete],
  );

  const startPolling = useCallback(
    (id) => {
      stopPolling();
      pollRef.current = setInterval(() => poll(id), POLL_INTERVAL_MS);
    },
    [poll, stopPolling],
  );

  // On mount, resume tracking if a job is already in flight (survives reloads /
  // a different worker handling the poll).
  useEffect(() => {
    let cancelled = false;
    adminService
      .getCrawlJobs(token, { limit: 10 })
      .then((data) => {
        if (cancelled) return;
        const running = (data.items || []).find(isActive);
        if (running) {
          setJob(running);
          setOpen(true);
          startPolling(running.id);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [token, startPolling, stopPolling]);

  const handleStart = async (kind) => {
    setError(null);
    setStarting(true);
    try {
      const started = await adminService.startCrawlJob(token, kind);
      setJob(started);
      startPolling(started.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setStarting(false);
    }
  };

  const busy = starting || isActive(job);
  const progressPct =
    job && job.units_total > 0 ? Math.round((job.units_done / job.units_total) * 100) : null;

  return (
    <div className="mb-5 rounded-lg border border-gray-200 bg-white">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-4 py-3 text-left"
      >
        <span className="flex items-center gap-2 text-sm font-semibold text-gray-900">
          {open ? (
            <ChevronDownIcon className="h-4 w-4 text-gray-400" />
          ) : (
            <ChevronRightIcon className="h-4 w-4 text-gray-400" />
          )}
          Crawl &amp; Scoring
        </span>
        {isActive(job) && (
          <span className="flex items-center gap-1.5 text-xs text-indigo-600">
            <ArrowPathIcon className="h-3.5 w-3.5 animate-spin" />
            {job.job_type} running
          </span>
        )}
      </button>

      {open && (
        <div className="border-t border-gray-100 px-4 py-4">
          <div className="flex flex-wrap gap-2">
            {JOBS.map(({ kind, jobType, label }) => (
              <button
                key={kind}
                onClick={() => handleStart(kind)}
                disabled={busy}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {label}
                {isActive(job) && job.job_type === jobType && ' …'}
              </button>
            ))}
          </div>

          {error && <p className="mt-3 text-sm text-red-700">{error}</p>}

          {job && (
            <div className="mt-4 text-sm">
              <div className="flex items-center gap-2 text-gray-700">
                <span className="font-medium">{job.job_type}</span>
                <span
                  className={
                    job.status === 'SUCCEEDED'
                      ? 'text-green-600'
                      : job.status === 'FAILED'
                        ? 'text-red-600'
                        : 'text-indigo-600'
                  }
                >
                  {job.status}
                </span>
              </div>

              {isActive(job) && (
                <div className="mt-2">
                  <div className="flex items-center justify-between text-xs text-gray-500">
                    <span className="truncate">{job.current_unit || 'Starting…'}</span>
                    <span>
                      {job.units_done}
                      {job.units_total ? ` / ${job.units_total}` : ''}
                    </span>
                  </div>
                  {progressPct != null && (
                    <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-gray-100">
                      <div
                        className="h-full bg-indigo-500 transition-all"
                        style={{ width: `${progressPct}%` }}
                      />
                    </div>
                  )}
                </div>
              )}

              {job.status === 'SUCCEEDED' && (
                <p className="mt-2 text-gray-600">
                  <StatsSummary job={job} />
                </p>
              )}
              {job.status === 'FAILED' && (
                <p className="mt-2 text-red-600">{job.error || 'Job failed'}</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
