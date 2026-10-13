import { useEffect } from 'react';

/**
 * Install a window.beforeunload listener while `when` is true.
 *
 * When the user attempts to close the tab, refresh, or navigate away (e.g.,
 * via the browser Back button) while `when === true`, the browser shows its
 * default "Leave site? Changes you made may not be saved." confirmation.
 *
 * Modern browsers ignore custom messages — setting `returnValue` to any
 * non-empty value is enough to trigger the dialog. We use `''` because the
 * empty-string assignment is the canonical, lint-clean idiom.
 *
 * @param {boolean} when - When truthy, the listener is installed. When falsy,
 *                         no listener is registered and the browser will not
 *                         prompt before navigation.
 */
export function useBeforeUnload(when) {
  useEffect(() => {
    if (!when) return;
    const handler = (e) => {
      e.preventDefault();
      e.returnValue = '';
      return '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [when]);
}
