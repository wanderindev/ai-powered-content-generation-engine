const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

/**
 * fetch() wrapper that sends the admin magic-link token in the Authorization
 * header instead of the URL query string (#57). Keeps the token out of access
 * logs, browser history, Referer headers, and Sentry breadcrumbs. Merges into
 * any caller-supplied headers (e.g. Content-Type) and leaves FormData uploads
 * without a Content-Type so the browser sets the multipart boundary.
 */
async function authFetch(token, url, options = {}) {
  const { headers, ...rest } = options;
  return fetch(url, {
    ...rest,
    headers: { ...(headers || {}), Authorization: `Bearer ${token}` },
  });
}

// The long edu pipeline steps (guide generation, render-slides, generate-audio,
// assemble-video, #346) and article narration (#547) run as background jobs:
// the POST returns 202 + a crawl_jobs row, and the step's result arrives as the job's `stats` — the same
// body the endpoint returned when it was synchronous, so callers see no shape
// change. `onProgress(job)` fires on every poll with `units_done` /
// `units_total` / `current_unit`. Re-issuing an identical request while the job
// runs (e.g. after a reload) joins it instead of starting a second one.
// The error `detail` of a failed response, or null. A 422 carries a list of
// validation errors, flattened to "field: message".
async function errorDetail(res) {
  const detail = await res.json().then((d) => d.detail).catch(() => null);
  if (Array.isArray(detail)) {
    return detail.map((e) => `${(e.loc || []).slice(-1)[0] || 'field'}: ${e.msg}`).join('; ');
  }
  return detail || null;
}

const EDU_JOB_POLL_MS = 3000;
const EDU_JOB_MAX_WAIT_MS = 60 * 60 * 1000; // the server's STALE_AFTER
const EDU_JOB_MAX_POLL_ERRORS = 5; // tolerate a few network blips mid-job

async function runBackgroundJob(token, url, { body, onProgress, errorMessage }) {
  const options = { method: 'POST' };
  if (body !== undefined) {
    options.headers = { 'Content-Type': 'application/json' };
    options.body = JSON.stringify(body);
  }
  const res = await authFetch(token, url, options);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || errorMessage);
  }
  let job = await res.json();
  const deadline = Date.now() + EDU_JOB_MAX_WAIT_MS;
  let pollErrors = 0;
  while (job.status !== 'SUCCEEDED' && job.status !== 'FAILED') {
    if (Date.now() > deadline) {
      throw new Error(`Job ${job.id} is still running — reload later to see its result`);
    }
    onProgress?.(job);
    await new Promise((resolve) => setTimeout(resolve, EDU_JOB_POLL_MS));
    try {
      job = await adminService.getCrawlJob(token, job.id);
      pollErrors = 0;
    } catch (err) {
      pollErrors += 1;
      if (pollErrors >= EDU_JOB_MAX_POLL_ERRORS) throw err;
    }
  }
  if (job.status === 'FAILED') {
    throw new Error(job.error ? `${errorMessage}: ${job.error}` : errorMessage);
  }
  return job.stats;
}

export const adminService = {
  async getDashboardStats(token) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/stats`);
    if (!res.ok) throw new Error('Failed to fetch dashboard stats');
    return res.json();
  },

  async getSuggestions(token) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/suggestions`);
    if (!res.ok) throw new Error('Failed to fetch suggestions');
    return res.json();
  },

  async getResearch(token) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/research`);
    if (!res.ok) throw new Error('Failed to fetch research');
    return res.json();
  },

  async getArticles(token) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/articles`);
    if (!res.ok) throw new Error('Failed to fetch articles');
    return res.json();
  },

  async getSuggestionDetail(token, id) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/suggestions/${id}`);
    if (!res.ok) throw new Error('Failed to fetch suggestion detail');
    return res.json();
  },

  async updateSuggestion(token, id, data) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/suggestions/${id}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to update suggestion');
    }
    return res.json();
  },

  async getResearchDetail(token, id) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/research/${id}`);
    if (!res.ok) throw new Error('Failed to fetch research detail');
    return res.json();
  },

  async updateResearch(token, id, contentHtml) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/research/${id}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content_html: contentHtml }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to update research');
    }
    return res.json();
  },

  async getArticleDetail(token, id) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/articles/${id}`);
    if (!res.ok) throw new Error('Failed to fetch article detail');
    return res.json();
  },

  async generateArticleAudio(token, id, { mode = 'all', onProgress } = {}) {
    // Several minutes server-side: ElevenLabs narrates the Spanish article, so
    // it runs as a background job (#547) and resolves with the job's stats.
    // mode 'changed' re-synthesizes only edited paragraphs and reuses the rest.
    return runBackgroundJob(token,
      `${API_BASE}/api/v1/admin/dashboard/articles/${id}/generate-audio`,
      { body: { mode }, onProgress, errorMessage: 'Audio generation failed' },
    );
  },

  async getArticleAudioSegments(token, id) {
    // Diff of the saved Spanish text vs the stored narration manifest.
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/articles/${id}/audio-segments`,
    );
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || 'Failed to check audio segments');
    }
    return res.json();
  },

  async updateArticle(token, id, data) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/articles/${id}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to update article');
    }
    return res.json();
  },

  async searchTags(token, query = '') {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/tags?q=${encodeURIComponent(query)}`);
    if (!res.ok) throw new Error('Failed to search tags');
    return res.json();
  },

  async createTag(token, name) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/tags`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to create tag');
    }
    return res.json();
  },

  async getSuggestionsForResearch(token) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/suggestions/available-for-research`);
    if (!res.ok) throw new Error('Failed to fetch suggestions for research');
    return res.json();
  },

  async generateResearchPrompt(token, suggestionId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/research/generate-prompt`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ suggestion_id: suggestionId }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to generate research prompt');
    }
    return res.json();
  },

  async uploadResearch(token, file, suggestionId) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('suggestion_id', String(suggestionId));
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/research/upload`,
      {
        method: 'POST',
        body: formData,
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      const detail = err.detail;
      if (typeof detail === 'object' && detail.message) {
        const checks = detail.checks || {};
        const parts = [detail.message];
        if (!checks.word_count) parts.push(`Word count: ${detail.word_count} (minimum 4,000)`);
        if (!checks.references) parts.push('Missing References/Sources section');
        if (checks.subtopic_coverage && !checks.subtopic_coverage.passed) {
          parts.push(`Sub-topic coverage: found ${checks.subtopic_coverage.found} of ${checks.subtopic_coverage.total} required`);
        }
        throw new Error(parts.join('. '));
      }
      throw new Error(typeof detail === 'string' ? detail : 'Failed to upload research');
    }
    return res.json();
  },

  async downloadResearch(token, id) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/research/${id}/download`);
    if (!res.ok) throw new Error('Failed to download research');
    const filename = res.headers.get('Content-Disposition')?.match(/filename="(.+)"/)?.[1] || 'research.md';
    const blob = await res.blob();
    return { blob, filename };
  },

  async generateResearchSummary(token, id) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/research/${id}/generate-summary`, {
      method: 'POST',
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to generate summary');
    }
    return res.json();
  },

  async downloadResearchSummary(token, id) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/research/${id}/download-summary`);
    if (!res.ok) throw new Error('Failed to download summary');
    const filename = res.headers.get('Content-Disposition')?.match(/filename="(.+)"/)?.[1] || 'summary.md';
    const blob = await res.blob();
    return { blob, filename };
  },

  async downloadArticle(token, id, lang = 'en') {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/articles/${id}/download?lang=${lang}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to download article');
    }
    const filename = res.headers.get('Content-Disposition')?.match(/filename="(.+)"/)?.[1] || 'article.md';
    const blob = await res.blob();
    return { blob, filename };
  },

  async getCategoriesForGeneration(token) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/suggestions/available-for-generation`);
    if (!res.ok) throw new Error('Failed to fetch categories for generation');
    return res.json();
  },

  async generateSuggestions(token, categoryId, numSuggestions = 10) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/suggestions/generate`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category_id: categoryId, num_suggestions: numSuggestions }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to generate suggestions');
    }
    return res.json();
  },

  // --- Article Generation Pipeline ---

  async getResearchForOutlines(token) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/research/available-for-outlines`);
    if (!res.ok) throw new Error('Failed to fetch research for outlines');
    return res.json();
  },

  async generateOutlines(token, researchId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/articles/generate-outlines`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ research_id: researchId }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to generate outlines');
    }
    return res.json();
  },

  async getArticlesForWriting(token) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/articles/available-for-writing`);
    if (!res.ok) throw new Error('Failed to fetch articles for writing');
    return res.json();
  },

  async writeArticle(token, articleId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/articles/${articleId}/write`,
      { method: 'POST' },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to write article');
    }
    return res.json();
  },

  async generateTags(token, articleId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/articles/${articleId}/generate-tags`,
      { method: 'POST' },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to generate tags');
    }
    return res.json();
  },

  async updateOutlineStatus(token, articleId, newStatus) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/articles/${articleId}/outline-status`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to update outline status');
    }
    return res.json();
  },

  // --- Feature Image ---

  // One call: Sonnet writes the watercolor prompt, Gemini paints it, the
  // backend stores it under a fresh URL (#419, #580). Always regenerates.
  // Errors: 503 (no Gemini key), Gemini's own status, 400 (no content).
  async generateFeatureImage(token, articleId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/articles/${articleId}/generate-image`,
      { method: 'POST' },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Failed to generate feature image (HTTP ${res.status})`);
    }
    return res.json();
  },

  // --- Translate, Series Sections, Publish ---

  async translateArticle(token, articleId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/articles/${articleId}/translate`,
      { method: 'POST' },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to translate article');
    }
    return res.json();
  },

  // Grammar check (#409/#410) — lives on the /admin router, NOT /admin/dashboard.
  // Synchronous: returns the applied/skipped FIND→REPLACE fixes in seconds.
  async grammarCheckArticle(token, articleId, language) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/articles/${articleId}/grammar-check?language=${language}`,
      { method: 'POST' },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Grammar check failed');
    }
    return res.json();
  },

  // Grammarly meaning-check review diffs (#518): paragraphs Sonnet could not
  // vouch for after a grammar-apply, written but flagged for a human.
  async getGrammarReviewDiffs(token, articleId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/articles/${articleId}/grammar-review`,
    );
    if (!res.ok) throw new Error('Failed to load grammar review diffs');
    return res.json();
  },

  async resolveGrammarReviewDiff(token, articleId, diffId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/articles/${articleId}/grammar-review/${diffId}/resolve`,
      { method: 'POST' },
    );
    if (!res.ok) throw new Error('Failed to resolve grammar review diff');
    return res.json();
  },

  // Cross-article consistency findings (#631, shadow mode): claims that
  // contradict another article's checked facts or the verified-claims KB.
  // Records only — nothing here changes a verdict or gates publishing.
  async getConsistencyFindings(token, articleId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/articles/${articleId}/consistency-findings`,
    );
    if (!res.ok) throw new Error('Failed to load consistency findings');
    return res.json();
  },

  // resolution: 'DISMISSED' (not a real contradiction) or 'RESOLVED' (fixed).
  async resolveConsistencyFinding(token, articleId, findingId, resolution) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/articles/${articleId}/consistency-findings/${findingId}/resolve`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resolution }),
      },
    );
    if (!res.ok) throw new Error('Failed to update consistency finding');
    return res.json();
  },

  // --- Weekly-series agent tasks (#420) ---
  // The router accepts the admin magic-link token as well as the driver's
  // service token, so authFetch is all the admin UI needs.

  async getAgentTasks(token, goalKey) {
    const qs = goalKey ? `?goal_key=${encodeURIComponent(goalKey)}` : '';
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/agent-tasks${qs}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to fetch agent tasks');
    }
    return res.json();
  },

  async planWeek(token) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/agent-tasks/plan-week`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to plan the week');
    }
    return res.json();
  },

  // --- MCP approval requests (#642) ---
  // Admin-only (plain admin token gate): an MCP agent can only *request* a
  // publish / unpublish / approve; these run or decline it.

  async getMcpApprovals(token, status) {
    const qs = status ? `?status=${encodeURIComponent(status)}` : '';
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/mcp/approvals${qs}`);
    if (!res.ok) {
      throw new Error((await errorDetail(res)) || 'Failed to fetch approval requests');
    }
    return res.json();
  },

  // decision is 'approve' (executes; the result is EXECUTED or FAILED) or 'reject'.
  async decideMcpApproval(token, requestId, decision) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/mcp/approvals/${requestId}/${decision}`,
      { method: 'POST' },
    );
    if (!res.ok) {
      throw new Error((await errorDetail(res)) || `Failed to ${decision} request`);
    }
    return res.json();
  },

  // action is 'retry' (FAILED only) or 'skip' (PENDING/READY/FAILED).
  async agentTaskAction(token, taskId, action) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/agent-tasks/${taskId}/action`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Failed to ${action} task`);
    }
    return res.json();
  },

  async updateEduMaterialContent(token, materialId, payload) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/materials/${materialId}/content`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to save guide content');
    }
    return res.json();
  },

  async generateEduGuidePdf(token, materialId, language = 'es') {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/materials/${materialId}/generate-pdf?language=${language}`,
      { method: 'POST' },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to generate guide PDF');
    }
    return res.json();
  },

  async updateEduSlides(token, materialId, payload) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/materials/${materialId}/slides`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to save slides');
    }
    return res.json();
  },

  // `positions` (1-based) narrows the rewrite to a subset of slides; omit it
  // (or pass null) to (re)narrate the whole deck.
  async generateEduNarration(token, materialId, { force = false, positions = null } = {}) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/materials/${materialId}/generate-narration`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force, ...(positions ? { positions } : {}) }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to generate narration');
    }
    return res.json();
  },

  // `positions` (1-based) re-renders only a subset; omit it for the whole deck.
  // Background job (#346) — resolves with the render summary when it finishes.
  async renderEduSlides(token, materialId, { positions = null, onProgress } = {}) {
    return runBackgroundJob(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/materials/${materialId}/render-slides`,
      { body: positions ? { positions } : {}, onProgress, errorMessage: 'Failed to render slides' },
    );
  },

  // `positions` (1-based) re-synths only a subset; omit it for the whole deck.
  // Background job (#346) — resolves with the audio summary when it finishes.
  async generateEduSlideAudio(token, materialId, { positions = null, onProgress } = {}) {
    return runBackgroundJob(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/materials/${materialId}/generate-audio`,
      {
        body: positions ? { positions } : {},
        onProgress,
        errorMessage: 'Failed to generate slide audio',
      },
    );
  },

  // Re-roll the CONTENT of a subset of an existing deck's slides in place
  // (`positions` is required, 1-based). Keeps the deck's slide count.
  async regenerateEduSlides(token, materialId, positions) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/materials/${materialId}/regenerate-slides`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ positions }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to regenerate slides');
    }
    return res.json();
  },

  // Background job (#346) — a full deck takes ~15-20 minutes of ffmpeg.
  async assembleEduVideo(token, materialId, { onProgress } = {}) {
    return runBackgroundJob(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/materials/${materialId}/assemble-video`,
      { onProgress, errorMessage: 'Failed to assemble video' },
    );
  },

  async translateEduGuide(token, materialId, { force = false, formality } = {}) {
    const params = new URLSearchParams();
    if (force) params.set('force', 'true');
    if (formality) params.set('formality', formality);
    const qs = params.toString();
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/materials/${materialId}/translate${qs ? `?${qs}` : ''}`,
      { method: 'POST' },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to translate guide');
    }
    return res.json();
  },

  async generateSeriesSections(token, articleId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/articles/${articleId}/series-sections`,
      { method: 'POST' },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to generate series sections');
    }
    return res.json();
  },

  // --- Fact-check (#378) ---
  // entityType: 'suggestions' | 'research' | 'articles'. Returns 202 + a
  // background job (#384) — poll it with getCrawlJob(token, job.id).
  async runFactCheck(token, entityType, entityId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/${entityType}/${entityId}/fact-check`,
      { method: 'POST' },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Fact-check failed');
    }
    return res.json();
  },

  async getFactCheckSummary(token) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/fact-check/summary`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to load fact-check summary');
    }
    return res.json();
  },

  // subjectType: 'SUGGESTION' | 'RESEARCH' | 'ARTICLE'.
  async getFactCheckClaims(token, subjectType, subjectId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/fact-check/${subjectType}/${subjectId}`,
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to load fact-check claims');
    }
    return res.json();
  },

  // --- Fact-check corrections (#387) ---
  // Generates proposed FIND/REPLACE edits for the subject's INACCURATE claims.
  async previewFactCheckCorrections(token, subjectType, subjectId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/fact-check/${subjectType}/${subjectId}/corrections/preview`,
      { method: 'POST' },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to preview corrections');
    }
    return res.json();
  },

  // Applies previewed edits verbatim; fully-applied claims flip to CORRECTED.
  async applyFactCheckCorrections(token, subjectType, subjectId, edits) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/fact-check/${subjectType}/${subjectId}/corrections/apply`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ edits }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to apply corrections');
    }
    return res.json();
  },

  async publishArticle(token, articleId, publishedAt) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/articles/${articleId}/publish`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ published_at: publishedAt }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to publish article');
    }
    return res.json();
  },

  async unpublishArticle(token, articleId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/articles/${articleId}/unpublish`,
      { method: 'PATCH' },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to unpublish article');
    }
    return res.json();
  },

  // --- Settings: Admin Emails ---

  async getAdminEmails(token) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/settings/admin-emails`);
    if (!res.ok) throw new Error('Failed to fetch admin emails');
    return res.json();
  },

  async addAdminEmail(token, data) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/settings/admin-emails`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to add admin email');
    }
    return res.json();
  },

  async deleteAdminEmail(token, id) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/settings/admin-emails/${id}`,
      { method: 'DELETE' },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to delete admin email');
    }
    return res.json();
  },

  // --- Settings: Pricing ---

  async getPricingConfig(token) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/settings/pricing`);
    if (!res.ok) throw new Error('Failed to fetch pricing config');
    return res.json();
  },

  async updatePricingConfig(token, items) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/settings/pricing`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(items),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to update pricing config');
    }
    return res.json();
  },

  // --- Media Library ---

  async getMediaCandidates(token, { status, minScore, search, page = 1, pageSize = 50 } = {}) {
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    if (minScore != null) params.set('min_score', minScore);
    if (search) params.set('search', search);
    params.set('page', page);
    params.set('page_size', pageSize);
    const res = await authFetch(token, `${API_BASE}/api/v1/media-library/candidates?${params}`);
    if (!res.ok) throw new Error('Failed to fetch media candidates');
    return res.json();
  },

  async getMediaCandidate(token, id) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/media-library/candidates/${id}`,
    );
    if (!res.ok) throw new Error('Failed to fetch media candidate');
    return res.json();
  },

  async approveCandidate(token, id, notes) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/media-library/candidates/${id}/approve`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notes }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to approve candidate');
    }
    return res.json();
  },

  async rejectCandidate(token, id, notes) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/media-library/candidates/${id}/reject`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notes }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to reject candidate');
    }
    return res.json();
  },

  async getMediaList(token, { search, source, excludeFeatureImages, page = 1, pageSize = 50 } = {}) {
    const params = new URLSearchParams();
    if (search) params.set('search', search);
    if (source) params.set('source', source);
    if (excludeFeatureImages) params.set('exclude_feature_images', 'true');
    params.set('page', page);
    params.set('page_size', pageSize);
    const res = await authFetch(token, `${API_BASE}/api/v1/media-library/media?${params}`);
    if (!res.ok) throw new Error('Failed to fetch media');
    return res.json();
  },

  // Import an external image URL into the media library (re-hosted on Spaces
  // with a thumbnail); returns the same item shape as getMediaList items.
  // metadata: { title, caption, attribution, source_page_url, license,
  // license_url, keywords, alt_text } — all optional (#589).
  async importMediaFromUrl(token, url, metadata = {}) {
    const res = await authFetch(token, `${API_BASE}/api/v1/media-library/media/import-url`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, ...metadata }),
    });
    if (!res.ok) {
      throw new Error((await errorDetail(res)) || 'Failed to import image');
    }
    return res.json();
  },

  // Partial metadata update of a media row (#589): send only changed fields;
  // null clears one. Returns the updated item.
  async updateMedia(token, id, changes) {
    const res = await authFetch(token, `${API_BASE}/api/v1/media-library/media/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(changes),
    });
    if (!res.ok) {
      throw new Error((await errorDetail(res)) || 'Failed to update image');
    }
    return res.json();
  },

  async assignMediaToArticle(token, articleId, mediaId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/articles/${articleId}/assign-media`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ media_id: mediaId }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to assign media');
    }
    return res.json();
  },

  async getMediaStats(token) {
    const res = await authFetch(token, `${API_BASE}/api/v1/media-library/stats`);
    if (!res.ok) throw new Error('Failed to fetch media stats');
    return res.json();
  },

  // --- Media Library: crawl/score jobs (#277) ---

  async startCrawlJob(token, kind, body = {}) {
    // kind: 'crawl' (Wikimedia) | 'crawl-loc' (LOC) | 'score'
    const res = await authFetch(token, `${API_BASE}/api/v1/media-library/${kind}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to start job');
    }
    return res.json();
  },

  async getCrawlJob(token, id) {
    const res = await authFetch(token, `${API_BASE}/api/v1/media-library/jobs/${id}`);
    if (!res.ok) throw new Error('Failed to fetch job status');
    return res.json();
  },

  async getCrawlJobs(token, { jobType, limit } = {}) {
    const params = new URLSearchParams();
    if (jobType) params.set('job_type', jobType);
    if (limit) params.set('limit', limit);
    const res = await authFetch(token, `${API_BASE}/api/v1/media-library/jobs?${params}`);
    if (!res.ok) throw new Error('Failed to fetch jobs');
    return res.json();
  },

  // --- Educational Content ---

  async getEduSuggestions(token) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/edu/suggestions`);
    if (!res.ok) throw new Error('Failed to fetch edu suggestions');
    return res.json();
  },

  async getEduSuggestionDetail(token, id) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/edu/suggestions/${id}`);
    if (!res.ok) throw new Error('Failed to fetch edu suggestion detail');
    return res.json();
  },

  async updateEduSuggestionStatus(token, id, newStatus) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/suggestions/${id}/status`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to update edu suggestion status');
    }
    return res.json();
  },

  async updateEduSuggestion(token, id, data) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/suggestions/${id}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to update edu suggestion');
    }
    return res.json();
  },

  async getEduResearch(token) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/edu/research`);
    if (!res.ok) throw new Error('Failed to fetch edu research');
    return res.json();
  },

  async getEduSuggestionsForResearch(token) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/edu/suggestions/available-for-research`);
    if (!res.ok) throw new Error('Failed to fetch edu suggestions for research');
    return res.json();
  },

  async generateEduResearchPrompt(token, suggestionId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/research/generate-prompt`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ suggestion_id: suggestionId }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to generate edu research prompt');
    }
    return res.json();
  },

  async uploadEduResearch(token, file, suggestionId) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('suggestion_id', String(suggestionId));
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/research/upload`,
      {
        method: 'POST',
        body: formData,
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      const detail = err.detail;
      if (typeof detail === 'object' && detail.message) {
        const checks = detail.checks || {};
        const parts = [detail.message];
        if (!checks.word_count) parts.push(`Word count: ${detail.word_count} (minimum 4,000)`);
        if (!checks.references) parts.push('Missing References/Sources section');
        if (checks.subtopic_coverage && !checks.subtopic_coverage.passed) {
          parts.push(`Sub-topic coverage: found ${checks.subtopic_coverage.found} of ${checks.subtopic_coverage.total} required`);
        }
        throw new Error(parts.join('. '));
      }
      throw new Error(typeof detail === 'string' ? detail : 'Failed to upload edu research');
    }
    return res.json();
  },

  async getEduResearchDetail(token, id) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/edu/research/${id}/detail`);
    if (!res.ok) throw new Error('Failed to fetch edu research detail');
    return res.json();
  },

  async downloadEduResearch(token, id) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/edu/research/${id}/download`);
    if (!res.ok) throw new Error('Failed to download edu research');
    const filename = res.headers.get('Content-Disposition')?.match(/filename="(.+)"/)?.[1] || 'edu_research.md';
    const blob = await res.blob();
    return { blob, filename };
  },

  async getEduMaterials(token) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/edu/materials`);
    if (!res.ok) throw new Error('Failed to fetch edu materials');
    return res.json();
  },

  async getEduResearchForMaterials(token) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/edu/research/available-for-materials`);
    if (!res.ok) throw new Error('Failed to fetch research for materials');
    return res.json();
  },

  // Background job (#346) — one study guide per grade band, several minutes.
  async generateEduMaterials(token, researchId, { onProgress } = {}) {
    return runBackgroundJob(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/materials/generate`,
      {
        body: { research_id: researchId },
        onProgress,
        errorMessage: 'Failed to generate edu materials',
      },
    );
  },

  async getEduGuidesForSlides(token) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/edu/materials/guides-for-slides`);
    if (!res.ok) throw new Error('Failed to fetch guides for slides');
    return res.json();
  },

  async generateEduSlides(token, guideMaterialId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/materials/generate-slides`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ guide_material_id: guideMaterialId }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to generate slides');
    }
    return res.json();
  },

  async getEduMaterialDetail(token, id) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/edu/materials/${id}/detail`);
    if (!res.ok) throw new Error('Failed to fetch edu material detail');
    return res.json();
  },

  async downloadEduMaterial(token, id) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/edu/materials/${id}/download`);
    if (!res.ok) throw new Error('Failed to download edu material');
    const filename = res.headers.get('Content-Disposition')?.match(/filename="(.+)"/)?.[1] || 'edu_material.md';
    const blob = await res.blob();
    return { blob, filename };
  },

  async getBlogResearchForUpload(token) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/edu/blog-research-for-upload`);
    if (!res.ok) throw new Error('Failed to fetch blog research for upload');
    return res.json();
  },

  async uploadMaterial(token, { materialType, blogResearchId, file }) {
    const formData = new FormData();
    formData.append('material_type', materialType);
    formData.append('blog_research_id', String(blogResearchId));
    formData.append('file', file);

    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/materials/upload`,
      { method: 'POST', body: formData },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to upload material');
    }
    return res.json();
  },

  async updateStatus(token, entityType, entityId, newStatus) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/${entityType}/${entityId}/status`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to update status');
    }
    return res.json();
  },

  // Publish an APPROVED edu material to the public delivery surface (#326/#327).
  // Omit publishedAt to publish now; pass an ISO string to schedule.
  async publishEduMaterial(token, materialId, publishedAt = null) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/materials/${materialId}/publish`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ published_at: publishedAt }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to publish material');
    }
    return res.json();
  },

  async unpublishEduMaterial(token, materialId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/materials/${materialId}/unpublish`,
      { method: 'PATCH' },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to unpublish material');
    }
    return res.json();
  },

  // --- Instagram posts (social pipeline) ---

  async getInstagramPosts(token) {
    const res = await authFetch(token, `${API_BASE}/api/v1/admin/dashboard/instagram/posts`);
    if (!res.ok) throw new Error('Failed to fetch Instagram posts');
    return res.json();
  },

  async getInstagramPost(token, postId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/instagram/posts/${postId}`);
    if (!res.ok) throw new Error('Failed to fetch Instagram post');
    return res.json();
  },

  async updateInstagramPost(token, postId, data) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/instagram/posts/${postId}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to update Instagram post');
    }
    return res.json();
  },

  async deleteInstagramPost(token, postId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/instagram/posts/${postId}`,
      { method: 'DELETE' },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to delete Instagram post');
    }
    return res.json();
  },

  // Research docs eligible for sabías-que generation (approved blog research).
  async generateSabiasQuePosts(token, researchId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/instagram/generate/sabias-que`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ research_id: researchId }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to generate posts');
    }
    return res.json();
  },

  async generateArticlePromo(token, articleId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/instagram/generate/article-promo`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ article_id: articleId }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to generate promo post');
    }
    return res.json();
  },

  async getPromotableArticles(token) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/instagram/promotable-articles`);
    if (!res.ok) throw new Error('Failed to fetch promotable articles');
    return res.json();
  },

  // Renders the branded 1080x1350 JPEG server-side (Playwright) — can take
  // ~5-15s on the droplet, same as edu slide renders.
  async renderInstagramPost(token, postId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/instagram/posts/${postId}/render`,
      { method: 'POST' },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to render post image');
    }
    return res.json();
  },

  async scheduleInstagramPost(token, postId, scheduledAt) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/instagram/posts/${postId}/schedule`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scheduled_at: scheduledAt }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to schedule post');
    }
    return res.json();
  },

  async unscheduleInstagramPost(token, postId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/instagram/posts/${postId}/unschedule`,
      { method: 'PATCH' },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to unschedule post');
    }
    return res.json();
  },

  // { configured, publishing_enabled } — publishing is off by default (#576).
  async getInstagramPublishingStatus(token) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/instagram/publishing-status`);
    if (!res.ok) throw new Error('Failed to fetch Instagram publishing status');
    return res.json();
  },

  // Never posts: checks the account + token and, with a post, creates (but
  // never publishes) a media container for its rendered JPEG.
  async verifyInstagram(token, postId = null) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/instagram/verify`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(postId ? { post_id: postId } : {}),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to verify Instagram');
    }
    return res.json();
  },

  async publishInstagramPostNow(token, postId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/instagram/posts/${postId}/publish-now`,
      { method: 'POST' },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to publish post');
    }
    return res.json();
  },

  // One month of publications (blog articles + Instagram posts) for the
  // editorial calendar. month is 1-based.
  async getEditorialCalendar(token, year, month) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/editorial-calendar?year=${year}&month=${month}`);
    if (!res.ok) throw new Error('Failed to fetch editorial calendar');
    return res.json();
  },
};
