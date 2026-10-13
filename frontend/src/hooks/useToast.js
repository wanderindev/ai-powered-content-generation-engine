import { useState, useCallback } from 'react';

let nextId = 0;

// Toast queue state + show/dismiss controls. Lives in its own module (not
// alongside ToastContainer) so the component file only exports components —
// keeps react-refresh/only-export-components happy. Pair with <ToastContainer>.
export function useToast() {
  const [toasts, setToasts] = useState([]);

  const dismiss = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const show = useCallback(({ type = 'info', title, message, duration }) => {
    const id = ++nextId;
    setToasts((prev) => [...prev, { id, type, title, message, duration }]);
    return id;
  }, []);

  return { toasts, show, dismiss };
}
