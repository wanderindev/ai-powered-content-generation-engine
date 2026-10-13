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

  async generateImagePrompt(token, articleId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/articles/${articleId}/image-prompt`,
      { method: 'POST' },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to generate image prompt');
    }
    return res.json();
  },

  async uploadFeatureImage(token, articleId, file) {
    const formData = new FormData();
    formData.append('file', file);
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/articles/${articleId}/upload-image`,
      {
        method: 'POST',
        body: formData,
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to upload feature image');
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
  async renderEduSlides(token, materialId, { positions = null } = {}) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/materials/${materialId}/render-slides`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(positions ? { positions } : {}),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to render slides');
    }
    return res.json();
  },

  // `positions` (1-based) re-synths only a subset; omit it for the whole deck.
  async generateEduSlideAudio(token, materialId, { positions = null } = {}) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/materials/${materialId}/generate-audio`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(positions ? { positions } : {}),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to generate slide audio');
    }
    return res.json();
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

  async assembleEduVideo(token, materialId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/materials/${materialId}/assemble-video`,
      { method: 'POST' },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to assemble video');
    }
    return res.json();
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
  async importMediaFromUrl(token, url, title) {
    const res = await authFetch(token, `${API_BASE}/api/v1/media-library/media/import-url`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, title: title || null }),
    });
    if (!res.ok) {
      const detail = await res.json().then((d) => d.detail).catch(() => null);
      throw new Error(detail || 'Failed to import image');
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

  async generateEduMaterials(token, researchId) {
    const res = await authFetch(token,
      `${API_BASE}/api/v1/admin/dashboard/edu/materials/generate`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ research_id: researchId }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to generate edu materials');
    }
    return res.json();
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
