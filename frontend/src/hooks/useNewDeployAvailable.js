import { useEffect, useState } from 'react';

// The hash this bundle was built with. docker/frontend/Dockerfile.prod stamps
// VITE_BUILD_HASH and writes the SAME value to dist/version.json, so a tab
// whose bundle hash differs from the served file is running a stale bundle
// against a backend that may have changed its API contract (#595).
const BUNDLE_HASH = import.meta.env.VITE_BUILD_HASH || '';

const POLL_INTERVAL_MS = 60_000;

/**
 * Poll /version.json (every 60s and whenever the tab regains focus) and return
 * true once the served build hash differs from this bundle's own.
 *
 * Inert when the bundle has no hash — `npm run dev`, CI and any build outside
 * the prod Dockerfile — so dev never shows the banner. Fetch, parse and 404
 * failures are ignored (the SPA fallback answers a missing file with
 * index.html, which simply fails to parse).
 */
export function useNewDeployAvailable() {
  const [stale, setStale] = useState(false);

  useEffect(() => {
    if (!BUNDLE_HASH || stale) return undefined;
    let cancelled = false;

    // async: setState lands after the await, outside the effect's sync body
    // (react-hooks/set-state-in-effect).
    const check = async () => {
      try {
        // no-store + a query-string buster: nginx serves a root-level .json
        // without a Cache-Control header, so never let the browser reuse one.
        const res = await fetch(`/version.json?t=${Date.now()}`, { cache: 'no-store' });
        if (!res.ok) return;
        const { buildHash } = await res.json();
        if (!cancelled && buildHash && String(buildHash) !== BUNDLE_HASH) {
          setStale(true);
        }
      } catch {
        // Offline, mid-deploy, or not JSON — try again on the next tick.
      }
    };

    check();
    const interval = setInterval(check, POLL_INTERVAL_MS);
    const onVisible = () => {
      if (document.visibilityState === 'visible') check();
    };
    window.addEventListener('focus', check);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      cancelled = true;
      clearInterval(interval);
      window.removeEventListener('focus', check);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [stale]);

  return stale;
}
