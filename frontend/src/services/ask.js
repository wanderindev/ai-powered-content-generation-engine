/**
 * Ask Panama in Context API service (#636, epic #619).
 *
 * Public and unauthenticated. Same VITE_API_URL fallback as the other public
 * services (production) — see the fallback note in CLAUDE.md before running
 * a bare `npm run dev` without frontend/.env.
 */

const API_BASE = import.meta.env.VITE_API_URL || 'https://api.panamaincontext.com';

// One status request per page load: the entry points on the blog list and on
// every article share it instead of re-asking on each navigation.
let statusPromise = null;

/**
 * Whether the feature is switched on (GET /api/v1/ask/status). Never throws:
 * any failure reads as disabled, so the entry points stay hidden.
 * @returns {Promise<{enabled: boolean, max_question_chars: number|null}>}
 */
export function getAskStatus() {
  if (!statusPromise) {
    statusPromise = fetch(`${API_BASE}/api/v1/ask/status`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => ({
        enabled: data?.enabled === true,
        max_question_chars: Number.isInteger(data?.max_question_chars)
          ? data.max_question_chars
          : null,
      }))
      .catch(() => {
        statusPromise = null; // let a later page view retry
        return { enabled: false, max_question_chars: null };
      });
  }
  return statusPromise;
}

/**
 * Ask a question. Resolves to a discriminated result instead of throwing, so
 * the page can map each backend outcome to its own copy:
 *   { kind: 'answered' | 'not_covered' | 'refused', answer, citations, language }
 *   { kind: 'invalid' }       422 — blank or too long
 *   { kind: 'rate_limited' }  429 — per-IP / global limit
 *   { kind: 'budget' }        429 — today's spending cap
 *   { kind: 'unavailable' }   503 — kill switch off
 *   { kind: 'error' }         502, network failure, anything else
 * `answer` is untrusted model output: render it as plain text only.
 * @param {string} question
 * @param {'en'|'es'} lang
 */
export async function askQuestion(question, lang) {
  let response;
  try {
    response = await fetch(`${API_BASE}/api/v1/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, lang }),
    });
  } catch {
    return { kind: 'error' };
  }

  if (response.ok) {
    try {
      const data = await response.json();
      if (!['answered', 'not_covered', 'refused'].includes(data?.status)) {
        return { kind: 'error' };
      }
      return {
        kind: data.status,
        answer: typeof data.answer === 'string' ? data.answer : '',
        citations: Array.isArray(data.citations) ? data.citations : [],
        language: data.language,
      };
    } catch {
      return { kind: 'error' };
    }
  }

  if (response.status === 422) return { kind: 'invalid' };
  if (response.status === 503) return { kind: 'unavailable' };
  if (response.status === 429) {
    // The backend's two 429s differ only in `detail`
    // (app/api/ask.py `_ERRORS`): the budget one mentions today's limit.
    let detail = '';
    try {
      detail = (await response.json())?.detail || '';
    } catch {
      // fall through to the generic rate-limit copy
    }
    return { kind: /today's limit/i.test(detail) ? 'budget' : 'rate_limited' };
  }
  return { kind: 'error' };
}
