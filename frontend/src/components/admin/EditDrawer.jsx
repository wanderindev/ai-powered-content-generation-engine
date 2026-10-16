import { Dialog, DialogBackdrop, DialogPanel, TransitionChild } from '@headlessui/react';
import { XMarkIcon } from '@heroicons/react/24/outline';
import { useState, useEffect, useRef, useCallback } from 'react';
import RichTextEditor from './RichTextEditor';
import MediaPickerModal from './MediaPickerModal';
import GuideBlocksEditor from './GuideBlocksEditor';
import SlideDeckEditor from './SlideDeckEditor';
import RegenerateRangeModal from './RegenerateRangeModal';
import { cacheBustUrl } from '../../utils/cacheBust';
import TagInput from './TagInput';
import { adminService } from '../../services/admin';

function useResizableDrawer(defaultWidth, minWidth = 400) {
  const [width, setWidth] = useState(defaultWidth);
  const dragging = useRef(false);

  const onMouseDown = useCallback((e) => {
    e.preventDefault();
    dragging.current = true;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const onMouseMove = (e) => {
      if (!dragging.current) return;
      const newWidth = Math.max(minWidth, window.innerWidth - e.clientX);
      setWidth(newWidth);
    };

    const onMouseUp = () => {
      dragging.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };

    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
  }, [minWidth]);

  return { width, onMouseDown };
}

function ResearchEditor({ record, token, onClose, requestCloseRef }) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [contentHtml, setContentHtml] = useState('');
  const [confirmClose, setConfirmClose] = useState(false);
  const loadedRef = useRef(false);
  const savedSnapshot = useRef('');

  const isDirty = useCallback(() => {
    return contentHtml !== savedSnapshot.current;
  }, [contentHtml]);

  const markClean = useCallback(() => {
    savedSnapshot.current = contentHtml;
  }, [contentHtml]);

  useEffect(() => {
    if (!record?.id || !token || loadedRef.current) return;
    loadedRef.current = true;

    adminService.getResearchDetail(token, record.id)
      .then((data) => {
        const loaded = data.content_html || '';
        setContentHtml(loaded);
        savedSnapshot.current = loaded;
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [record?.id, token]);

  const requestClose = useCallback(() => {
    if (isDirty()) {
      setConfirmClose(true);
    } else {
      onClose();
    }
  }, [isDirty, onClose]);

  useEffect(() => {
    if (requestCloseRef) requestCloseRef.current = requestClose;
  }, [requestCloseRef, requestClose]);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await adminService.updateResearch(token, record.id, contentHtml);
      markClean();
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-gray-500">Loading research content...</div>
      </div>
    );
  }

  if (error && !contentHtml) {
    return (
      <div className="flex-1 px-4 py-6">
        <p className="text-red-600">{error}</p>
      </div>
    );
  }

  return (
    <>
      <div className="flex-1 flex flex-col px-4 py-2 min-h-0">
        {error && <p className="text-red-600 text-sm mb-2">{error}</p>}

        {confirmClose && (
          <div className="mb-3 rounded-md border border-amber-300 bg-amber-50 p-3">
            <p className="text-sm text-amber-800">You have unsaved research edits. Discard them?</p>
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                onClick={() => setConfirmClose(false)}
                className="rounded-md bg-white px-2.5 py-1.5 text-xs font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={onClose}
                className="rounded-md bg-red-600 px-2.5 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-red-500"
              >
                Discard & Close
              </button>
            </div>
          </div>
        )}

        <div className="flex-1 min-h-0">
          <RichTextEditor content={contentHtml} onChange={setContentHtml} />
        </div>
      </div>
      <div className="border-t border-gray-200 px-4 py-3 flex justify-end gap-3">
        <button
          type="button"
          onClick={requestClose}
          className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50"
        >
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>
    </>
  );
}

function stateSnapshot(fields, tags) {
  return JSON.stringify({ fields, tagIds: tags.map((t) => t.id).sort() });
}

function formatTimestamp(ms) {
  const s = Math.floor((ms || 0) / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

// Narration voice check (#601): the last run's pitch QA. High pitch is
// necessary but not sufficient for "not Diego", so flagged paragraphs are
// worded as ones to listen to, not as wrong. Timestamps seek the player.
function NarrationVoiceCheck({ qa, onSeek }) {
  const retried = qa.retried || [];
  const unresolved = qa.unresolved || [];
  const fixed = retried.filter((r) => r.resolved).length;
  const alarm = qa.status === 'NEEDS_REVIEW' && retried.length === 0 && unresolved.length > 0;

  if (unresolved.length === 0) {
    return (
      <p className="mt-2 text-xs text-gray-500">
        Voice check:{' '}
        {fixed > 0
          ? `${fixed} paragraph${fixed === 1 ? '' : 's'} re-taken (high pitch).`
          : qa.measured
            ? `all ${qa.measured} measured paragraphs within ${qa.threshold_pct}% of the voice's pitch.`
            : 'could not measure any paragraph.'}
      </p>
    );
  }

  const pct = qa.measured ? Math.round((qa.suspects / qa.measured) * 100) : 0;
  return (
    <div className="mt-2 rounded-md bg-amber-50 p-2 text-xs text-amber-900">
      <p className="font-medium">
        {alarm
          ? `⚠ ${qa.suspects} of ${qa.measured} paragraphs high-pitched (${pct}%), not auto-fixed.`
          : `⚠ ${unresolved.length} paragraph${unresolved.length === 1 ? '' : 's'} still high-pitched after re-takes${fixed > 0 ? ` (${fixed} re-taken fine)` : ''}.`}{' '}
        Listen to these — high pitch can still be Diego:
      </p>
      <ul className="mt-1 max-h-40 overflow-y-auto">
        {unresolved.map((u) => (
          <li key={u.position} className="truncate">
            <button
              type="button"
              onClick={() => onSeek(u.start_ms)}
              className="font-mono text-indigo-700 hover:underline"
              title="Play from here"
            >
              {formatTimestamp(u.start_ms)}
            </button>{' '}
            #{u.position} (+{u.pct}%) — “{u.text}”
          </li>
        ))}
      </ul>
    </div>
  );
}

// Cross-article consistency findings (#631, shadow mode): claims of this
// article the judge found contradicting another article's checked facts or
// the verified-claims KB. Records only — no verdict or publish gate reads
// them. Dismiss = not a real contradiction; Mark resolved = fixed in one of
// the articles. Every value renders as plain text.
function ConsistencyFindings({ articleId, token }) {
  const [findings, setFindings] = useState(null);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);

  useEffect(() => {
    let cancelled = false;
    // Async IIFE so no setState runs in the effect's synchronous body
    // (react-hooks/set-state-in-effect).
    (async () => {
      try {
        const data = await adminService.getConsistencyFindings(token, articleId);
        if (!cancelled) setFindings(data.findings || []);
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    })();
    return () => { cancelled = true; };
  }, [articleId, token]);

  const close = async (finding, resolution) => {
    setBusyId(finding.id);
    setError(null);
    try {
      await adminService.resolveConsistencyFinding(token, articleId, finding.id, resolution);
      setFindings((prev) => (prev || []).filter((f) => f.id !== finding.id));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  };

  if (error && findings === null) {
    return <p className="mt-4 text-xs text-gray-400">Consistency check unavailable.</p>;
  }
  if (!findings || findings.length === 0) return null;

  return (
    <div className="mt-4 mb-2 rounded-md border border-rose-200 bg-rose-50 p-3">
      <p className="text-sm font-medium text-rose-900">
        Possible contradictions with other articles ({findings.length})
      </p>
      <p className="text-xs text-rose-800">
        Shadow mode: found after the last fact-check, never blocks publishing.
      </p>
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
      <ul className="mt-2 space-y-2">
        {findings.map((f) => (
          <li key={f.id} className="rounded-md bg-white p-2 text-xs text-gray-700 ring-1 ring-rose-100">
            <p><span className="font-semibold">This article:</span> {f.claim_text}</p>
            <p className="mt-1">
              <span className="font-semibold">
                {f.other_source_type === 'VERIFIED_CLAIM' ? 'Verified-claims KB' : 'Other article'}
                {f.other_article_id && (
                  <>
                    {' ('}
                    {f.other_article_slug ? (
                      <a
                        href={`/en/articles/${f.other_article_slug}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-indigo-700 hover:underline"
                      >
                        {f.other_article_title || `#${f.other_article_id}`}
                      </a>
                    ) : (
                      f.other_article_title || `#${f.other_article_id}`
                    )}
                    {')'}
                  </>
                )}
                :
              </span>{' '}
              {f.other_text}
            </p>
            <p className="mt-1 text-gray-500">
              {f.verdict}: {f.explanation}
            </p>
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                disabled={busyId === f.id}
                onClick={() => close(f, 'DISMISSED')}
                className="rounded-md bg-white px-2 py-1 text-xs font-semibold text-gray-700 ring-1 ring-gray-300 hover:bg-gray-100 disabled:opacity-50"
              >
                Dismiss
              </button>
              <button
                type="button"
                disabled={busyId === f.id}
                onClick={() => close(f, 'RESOLVED')}
                className="rounded-md bg-rose-600 px-2 py-1 text-xs font-semibold text-white hover:bg-rose-500 disabled:opacity-50"
              >
                Mark resolved
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ArticleEditor({ record, token, onClose, requestCloseRef }) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [lang, setLang] = useState('en');
  const [confirmAction, setConfirmAction] = useState(null); // 'lang:en'|'lang:es'|'close'
  const [hasSpanish, setHasSpanish] = useState(false);
  const [tags, setTags] = useState([]);
  const [showImagePicker, setShowImagePicker] = useState(false);
  const [audioUrlEs, setAudioUrlEs] = useState(null);
  const [audioBusy, setAudioBusy] = useState(false);
  const [audioProgress, setAudioProgress] = useState(null);
  const [audioError, setAudioError] = useState(null);
  const [audioChoice, setAudioChoice] = useState(null); // segments diff, pending scope choice
  const [audioQa, setAudioQa] = useState(null); // voice check of the last run (#601)
  const audioRef = useRef(null);
  const editorRef = useRef(null);
  const [fields, setFields] = useState({
    en: { title: '', excerpt: '', contentHtml: '' },
    es: { title: '', excerpt: '', contentHtml: '' },
  });
  const loadedRef = useRef(false);
  const savedSnapshot = useRef('');
  const stabilizingRef = useRef(false);

  const isDirty = useCallback(() => {
    if (stabilizingRef.current) return false;
    return stateSnapshot(fields, tags) !== savedSnapshot.current;
  }, [fields, tags]);

  const markClean = useCallback(() => {
    savedSnapshot.current = stateSnapshot(fields, tags);
  }, [fields, tags]);

  // While stabilizing, keep updating the snapshot so TipTap normalization is captured
  useEffect(() => {
    if (stabilizingRef.current) {
      savedSnapshot.current = stateSnapshot(fields, tags);
    }
  }, [fields, tags]);

  useEffect(() => {
    if (!record?.id || !token || loadedRef.current) return;
    loadedRef.current = true;
    stabilizingRef.current = true;

    adminService.getArticleDetail(token, record.id)
      .then((data) => {
        const spanishAvailable = data.title_es != null;
        setHasSpanish(spanishAvailable);
        setAudioUrlEs(data.audio_url_es || null);
        setAudioQa(data.audio_qa_es || null);
        const loadedTags = data.tags || [];
        const loadedFields = {
          en: {
            title: data.title || '',
            excerpt: data.excerpt || '',
            contentHtml: data.content_html || '',
          },
          es: {
            title: data.title_es || '',
            excerpt: data.excerpt_es || '',
            contentHtml: data.content_html_es || '',
          },
        };
        setTags(loadedTags);
        setFields(loadedFields);
        savedSnapshot.current = stateSnapshot(loadedFields, loadedTags);
        // Give TipTap time to normalize HTML before locking the snapshot
        setTimeout(() => { stabilizingRef.current = false; }, 500);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [record?.id, token]);

  const updateField = (field, value) => {
    setFields((prev) => ({
      ...prev,
      [lang]: { ...prev[lang], [field]: value },
    }));
  };

  const requestClose = useCallback(() => {
    if (isDirty()) {
      setConfirmAction('close');
    } else {
      onClose();
    }
  }, [isDirty, onClose]);

  useEffect(() => {
    if (requestCloseRef) requestCloseRef.current = requestClose;
  }, [requestCloseRef, requestClose]);

  const handleLangSwitch = (newLang) => {
    if (newLang === lang) return;
    if (isDirty()) {
      setConfirmAction(`lang:${newLang}`);
    } else {
      setLang(newLang);
    }
  };

  const confirmDiscard = () => {
    const action = confirmAction;
    setConfirmAction(null);
    if (action === 'close') {
      onClose();
    } else if (action?.startsWith('lang:')) {
      // Revert fields & tags to saved state, then switch language
      const saved = JSON.parse(savedSnapshot.current);
      setFields(saved.fields);
      setTags((prev) => {
        // Restore tags with their full objects by matching IDs from saved snapshot
        const savedIds = new Set(saved.tagIds);
        const kept = prev.filter((t) => savedIds.has(t.id));
        return kept;
      });
      setLang(action.slice(5));
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await adminService.updateArticle(token, record.id, {
        title: fields.en.title,
        excerpt: fields.en.excerpt || null,
        content_html: fields.en.contentHtml,
        title_es: hasSpanish ? (fields.es.title || null) : null,
        excerpt_es: hasSpanish ? (fields.es.excerpt || null) : null,
        content_html_es: hasSpanish ? (fields.es.contentHtml || null) : null,
        tag_ids: tags.map((t) => t.id),
      });
      markClean();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const runAudioGeneration = async (mode) => {
    setAudioChoice(null);
    setAudioBusy(true);
    setAudioError(null);
    setAudioProgress(null);
    try {
      const result = await adminService.generateArticleAudio(token, record.id, {
        mode,
        onProgress: (job) => setAudioProgress(job.current_unit),
      });
      // Only ever replace the URL with a real one (#594): a response that
      // lacks it (e.g. a stale bundle reading a job object) must not wipe the
      // player, or the next click would skip the chooser as a "first" run.
      if (result?.audio_url_es) setAudioUrlEs(result.audio_url_es);
      // The job stats carry only a summary of the voice check; the segment
      // list with timestamps is on the article (audio_qa_es).
      try {
        const detail = await adminService.getArticleDetail(token, record.id);
        setAudioQa(detail.audio_qa_es || null);
      } catch {
        // Non-fatal: the narration itself succeeded.
      }
    } catch (err) {
      setAudioError(err.message);
    } finally {
      setAudioBusy(false);
      setAudioProgress(null);
    }
  };

  const handleGenerateAudio = async () => {
    setAudioError(null);
    setAudioChoice(null);
    if (isDirty()) {
      setAudioError('Save your changes first — audio is generated from the saved article.');
      return;
    }
    // Always ask the server before spending (#594): local `audioUrlEs` can be
    // wiped by an unexpected response, and a full run is a paid ElevenLabs
    // narration of every segment. Diff the saved text against the stored
    // per-segment audio; only when the server confirms there is no narration
    // at all (no audio, no manifest) go straight to a full run — otherwise the
    // user picks the scope.
    let status;
    try {
      status = await adminService.getArticleAudioSegments(token, record.id);
    } catch (err) {
      setAudioError(err.message);
      return;
    }
    if (!status.has_audio && !status.has_manifest) {
      runAudioGeneration('all');
      return;
    }
    setAudioChoice(status);
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-gray-500">Loading article...</div>
      </div>
    );
  }

  if (error && !fields.en.title) {
    return (
      <div className="flex-1 px-4 py-6">
        <p className="text-red-600">{error}</p>
      </div>
    );
  }

  const current = fields[lang];

  return (
    <>
      <div className="flex-1 flex flex-col px-4 py-2 min-h-0 overflow-y-auto">
        {error && <p className="text-red-600 text-sm mb-2">{error}</p>}

        {/* Unsaved changes confirm dialog */}
        {confirmAction && (
          <div className="mb-3 rounded-md border border-amber-300 bg-amber-50 p-3">
            <p className="text-sm text-amber-800">You have unsaved changes that will be lost.</p>
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                onClick={() => setConfirmAction(null)}
                className="rounded-md bg-white px-2.5 py-1.5 text-xs font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={confirmDiscard}
                className="rounded-md bg-red-600 px-2.5 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-red-500"
              >
                {confirmAction === 'close' ? 'Discard & Close' : 'Discard & Switch'}
              </button>
            </div>
          </div>
        )}

        {/* Language toggle */}
        <div className="flex gap-1 mb-4 rounded-lg bg-gray-100 p-1 w-fit">
          <button
            type="button"
            onClick={() => handleLangSwitch('en')}
            className={`rounded-md px-3 py-1 text-sm font-medium transition-colors ${
              lang === 'en'
                ? 'bg-white text-indigo-600 shadow-sm'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            EN
          </button>
          <button
            type="button"
            onClick={() => hasSpanish && handleLangSwitch('es')}
            disabled={!hasSpanish}
            className={`rounded-md px-3 py-1 text-sm font-medium transition-colors ${
              lang === 'es'
                ? 'bg-white text-indigo-600 shadow-sm'
                : hasSpanish
                  ? 'text-gray-600 hover:text-gray-900'
                  : 'text-gray-300 cursor-not-allowed'
            }`}
          >
            ES
          </button>
        </div>

        {/* Title */}
        <div className="mb-3">
          <label className="block text-sm font-medium text-gray-700 mb-1">Title</label>
          <input
            type="text"
            value={current.title}
            onChange={(e) => updateField('title', e.target.value)}
            className="w-full rounded-md border-0 px-3 py-1.5 text-sm text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 placeholder:text-gray-400 focus:ring-2 focus:ring-inset focus:ring-indigo-600"
          />
        </div>

        {/* Excerpt */}
        <div className="mb-3">
          <label className="block text-sm font-medium text-gray-700 mb-1">Excerpt</label>
          <textarea
            rows={3}
            value={current.excerpt}
            onChange={(e) => updateField('excerpt', e.target.value)}
            className="w-full rounded-md border-0 px-3 py-1.5 text-sm text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 placeholder:text-gray-400 focus:ring-2 focus:ring-inset focus:ring-indigo-600"
          />
        </div>

        {/* Tags (shared, not language-specific) */}
        <div className="mb-3">
          <label className="block text-sm font-medium text-gray-700 mb-1">Tags</label>
          <TagInput tags={tags} onChange={setTags} token={token} />
        </div>

        {/* Content */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Content</label>
          <div style={{ height: 'calc(100vh - 320px)', minHeight: '400px' }}>
            <RichTextEditor
              ref={editorRef}
              content={current.contentHtml}
              onChange={(val) => updateField('contentHtml', val)}
              onImageButtonClick={() => setShowImagePicker(true)}
            />
          </div>
        </div>

        {/* Cross-article consistency findings (#631); claims are English */}
        {lang === 'en' && record?.id && (
          <ConsistencyFindings articleId={record.id} token={token} />
        )}

        {/* Narration audio (Spanish only) */}
        {lang === 'es' && hasSpanish && (
          <div className="mt-4 mb-2 rounded-md border border-gray-200 p-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-gray-700">Narration audio (Diego)</p>
                <p className="text-xs text-gray-500">
                  ElevenLabs reads the saved Spanish content. Save your edits first, then
                  {audioUrlEs ? ' regenerate' : ' generate'} — it takes a few minutes.
                </p>
              </div>
              <button
                type="button"
                onClick={handleGenerateAudio}
                disabled={audioBusy}
                className="shrink-0 rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50"
              >
                {audioBusy
                  ? `Generating…${audioProgress ? ` ${audioProgress}` : ''}`
                  : audioUrlEs ? 'Regenerate audio' : 'Generate audio'}
              </button>
            </div>
            {audioError && <p className="mt-2 text-xs text-red-600">{audioError}</p>}
            {audioChoice && !audioBusy && (
              <div className="mt-3 rounded-md bg-gray-50 p-3">
                {audioChoice.changed_positions.length === 0 ? (
                  <p className="text-xs text-gray-600">
                    No text changes since the last generation — the stored audio already
                    matches the article.
                  </p>
                ) : (
                  <>
                    <p className="text-xs font-medium text-gray-700">
                      Changed since the last generation ({audioChoice.changed_positions.length} of{' '}
                      {audioChoice.segments.length} segments):
                    </p>
                    <ul className="mt-1 max-h-32 overflow-y-auto text-xs text-gray-600">
                      {audioChoice.segments.filter((s) => s.changed).map((s) => (
                        <li key={s.position} className="truncate">
                          #{s.position}{s.is_heading ? ' (heading)' : ''} — “{s.preview}”
                        </li>
                      ))}
                    </ul>
                  </>
                )}
                <div className="mt-2 flex gap-2">
                  {audioChoice.changed_positions.length > 0 && (
                    <button
                      type="button"
                      onClick={() => runAudioGeneration('changed')}
                      className="rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500"
                    >
                      Only changed ({audioChoice.changed_positions.length})
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => runAudioGeneration('all')}
                    className="rounded-md bg-white px-3 py-1.5 text-xs font-semibold text-gray-700 ring-1 ring-gray-300 hover:bg-gray-100"
                  >
                    Whole article ({audioChoice.segments.length})
                  </button>
                  <button
                    type="button"
                    onClick={() => setAudioChoice(null)}
                    className="rounded-md px-3 py-1.5 text-xs font-semibold text-gray-500 hover:text-gray-700"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
            {audioUrlEs && !audioBusy && (
              <audio ref={audioRef} controls preload="none" src={audioUrlEs} className="mt-3 w-full" />
            )}
            {audioUrlEs && !audioBusy && audioQa && (
              <NarrationVoiceCheck
                qa={audioQa}
                onSeek={(ms) => {
                  const el = audioRef.current;
                  if (!el) return;
                  el.currentTime = ms / 1000;
                  el.play().catch(() => {});
                }}
              />
            )}
          </div>
        )}
      </div>

      <MediaPickerModal
        open={showImagePicker}
        onClose={() => setShowImagePicker(false)}
        token={token}
        onSelect={({ src, alt }) => {
          editorRef.current?.insertImage(src, alt);
        }}
      />

      {/* Footer */}
      <div className="border-t border-gray-200 px-4 py-3 flex justify-end gap-3">
        <button
          type="button"
          onClick={requestClose}
          className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50"
        >
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>
    </>
  );
}

function suggestionSnapshot(fields) {
  return JSON.stringify(fields);
}

function SuggestionEditor({ record, token, onClose, requestCloseRef }) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [confirmClose, setConfirmClose] = useState(false);
  const [fields, setFields] = useState({
    title: '',
    title_es: '',
    main_topic: '',
    sub_topics: [],
    point_of_view: '',
  });
  const loadedRef = useRef(false);
  const savedSnapshot = useRef('');

  const isDirty = useCallback(() => {
    return suggestionSnapshot(fields) !== savedSnapshot.current;
  }, [fields]);

  const markClean = useCallback(() => {
    savedSnapshot.current = suggestionSnapshot(fields);
  }, [fields]);

  useEffect(() => {
    if (!record?.id || !token || loadedRef.current) return;
    loadedRef.current = true;

    adminService.getSuggestionDetail(token, record.id)
      .then((data) => {
        const loaded = {
          title: data.title || '',
          title_es: data.title_es || '',
          main_topic: data.main_topic || '',
          sub_topics: data.sub_topics || [],
          point_of_view: data.point_of_view || '',
        };
        setFields(loaded);
        savedSnapshot.current = suggestionSnapshot(loaded);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [record?.id, token]);

  const updateField = (field, value) => {
    setFields((prev) => ({ ...prev, [field]: value }));
  };

  const updateSubtopic = (index, value) => {
    setFields((prev) => ({
      ...prev,
      sub_topics: prev.sub_topics.map((s, i) => (i === index ? value : s)),
    }));
  };

  const removeSubtopic = (index) => {
    setFields((prev) => ({
      ...prev,
      sub_topics: prev.sub_topics.filter((_, i) => i !== index),
    }));
  };

  const addSubtopic = () => {
    setFields((prev) => ({
      ...prev,
      sub_topics: [...prev.sub_topics, ''],
    }));
  };

  const requestClose = useCallback(() => {
    if (isDirty()) {
      setConfirmClose(true);
    } else {
      onClose();
    }
  }, [isDirty, onClose]);

  useEffect(() => {
    if (requestCloseRef) requestCloseRef.current = requestClose;
  }, [requestCloseRef, requestClose]);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await adminService.updateSuggestion(token, record.id, {
        title: fields.title,
        title_es: fields.title_es || null,
        main_topic: fields.main_topic || null,
        sub_topics: fields.sub_topics,
        point_of_view: fields.point_of_view,
      });
      markClean();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-gray-500">Loading suggestion...</div>
      </div>
    );
  }

  if (error && !fields.title) {
    return (
      <div className="flex-1 px-4 py-6">
        <p className="text-red-600">{error}</p>
      </div>
    );
  }

  const inputClass = 'w-full rounded-md border-0 px-3 py-1.5 text-sm text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 placeholder:text-gray-400 focus:ring-2 focus:ring-inset focus:ring-indigo-600';

  return (
    <>
      <div className="flex-1 flex flex-col px-4 py-2 min-h-0 overflow-y-auto">
        {error && <p className="text-red-600 text-sm mb-2">{error}</p>}

        {confirmClose && (
          <div className="mb-3 rounded-md border border-amber-300 bg-amber-50 p-3">
            <p className="text-sm text-amber-800">You have unsaved changes that will be lost.</p>
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                onClick={() => setConfirmClose(false)}
                className="rounded-md bg-white px-2.5 py-1.5 text-xs font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={onClose}
                className="rounded-md bg-red-600 px-2.5 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-red-500"
              >
                Discard & Close
              </button>
            </div>
          </div>
        )}

        {/* Title EN */}
        <div className="mb-3">
          <label className="block text-sm font-medium text-gray-700 mb-1">Title (EN)</label>
          <input
            type="text"
            value={fields.title}
            onChange={(e) => updateField('title', e.target.value)}
            className={inputClass}
          />
        </div>

        {/* Title ES */}
        <div className="mb-3">
          <label className="block text-sm font-medium text-gray-700 mb-1">Title (ES)</label>
          <input
            type="text"
            value={fields.title_es}
            onChange={(e) => updateField('title_es', e.target.value)}
            className={inputClass}
          />
        </div>

        {/* Main Topic */}
        <div className="mb-3">
          <label className="block text-sm font-medium text-gray-700 mb-1">Main Topic</label>
          <textarea
            rows={3}
            value={fields.main_topic}
            onChange={(e) => updateField('main_topic', e.target.value)}
            className={inputClass}
          />
        </div>

        {/* Subtopics */}
        <div className="mb-3">
          <label className="block text-sm font-medium text-gray-700 mb-1">Subtopics</label>
          <div className="space-y-2">
            {fields.sub_topics.map((topic, i) => (
              <div key={i} className="flex gap-2">
                <textarea
                  rows={3}
                  value={topic}
                  onChange={(e) => updateSubtopic(i, e.target.value)}
                  className={`${inputClass} flex-1`}
                />
                <button
                  type="button"
                  onClick={() => removeSubtopic(i)}
                  className="self-start mt-1 text-red-500 hover:text-red-700 text-lg font-bold px-1"
                  title="Remove subtopic"
                >
                  &times;
                </button>
              </div>
            ))}
          </div>
          <button
            type="button"
            onClick={addSubtopic}
            className="mt-2 text-sm text-indigo-600 hover:text-indigo-500 font-medium"
          >
            + Add subtopic
          </button>
        </div>

        {/* Point of View */}
        <div className="mb-3">
          <label className="block text-sm font-medium text-gray-700 mb-1">Point of View</label>
          <textarea
            rows={3}
            value={fields.point_of_view}
            onChange={(e) => updateField('point_of_view', e.target.value)}
            className={inputClass}
          />
        </div>
      </div>

      {/* Footer */}
      <div className="border-t border-gray-200 px-4 py-3 flex justify-end gap-3">
        <button
          type="button"
          onClick={requestClose}
          className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50"
        >
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>
    </>
  );
}

function OutlineEditor({ record, token, onClose }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [fields, setFields] = useState({ title: '', outline: '' });
  const loadedRef = useRef(false);

  useEffect(() => {
    if (!record?.id || !token || loadedRef.current) return;
    loadedRef.current = true;

    adminService.getArticleDetail(token, record.id)
      .then((data) => {
        setFields({
          title: data.title || '',
          outline: data.outline || '',
        });
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [record?.id, token]);

  // Outline is intentionally read-only: the backend's `update_article`
  // endpoint accepts only title/excerpt/content_html/tags (no `outline` field),
  // and there is no other endpoint that updates outline content. Until a real
  // save path exists, the editor displays the generated outline and lets
  // operators review/copy it without offering a Save button that cannot work.
  // The previous `handleSave` here was dead (no button wired to it) and would
  // have posted `content_html: ''` + `tag_ids: []` to `updateArticle`, which
  // would have blanked the article body and removed all tags if ever wired up.

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-gray-500">Loading outline...</div>
      </div>
    );
  }

  if (error && !fields.title) {
    return (
      <div className="flex-1 px-4 py-6">
        <p className="text-red-600">{error}</p>
      </div>
    );
  }

  const inputClass = 'w-full rounded-md border-0 px-3 py-1.5 text-sm text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 placeholder:text-gray-400 focus:ring-2 focus:ring-inset focus:ring-indigo-600';

  return (
    <>
      <div className="flex-1 flex flex-col px-4 py-2 min-h-0 overflow-y-auto">
        {error && <p className="text-red-600 text-sm mb-2">{error}</p>}

        {/* Title (read-only) */}
        <div className="mb-3">
          <label className="block text-sm font-medium text-gray-700 mb-1">Title</label>
          <input
            type="text"
            value={fields.title}
            readOnly
            className={`${inputClass} bg-gray-50`}
          />
        </div>

        {/* Outline (read-only — no backend save path exists yet) */}
        <div className="flex-1 flex flex-col min-h-0">
          <label className="block text-sm font-medium text-gray-700 mb-1">Outline (read-only)</label>
          <textarea
            value={fields.outline}
            readOnly
            className={`${inputClass} flex-1 font-mono text-xs leading-relaxed bg-gray-50`}
            style={{ minHeight: '400px' }}
          />
        </div>
      </div>

      {/* Footer */}
      <div className="border-t border-gray-200 px-4 py-3 flex justify-end gap-3">
        <button
          type="button"
          onClick={onClose}
          className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
        >
          Close
        </button>
      </div>
    </>
  );
}

function EduSuggestionEditor({ record, token, onClose, requestCloseRef }) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [confirmClose, setConfirmClose] = useState(false);
  const [fields, setFields] = useState({
    title: '',
    description: '',
    grade_bands: [],
    sub_topics: [],
  });
  const loadedRef = useRef(false);
  const savedSnapshot = useRef('');

  const snapshot = (f) => JSON.stringify(f);

  const isDirty = useCallback(() => {
    return snapshot(fields) !== savedSnapshot.current;
  }, [fields]);

  const markClean = useCallback(() => {
    savedSnapshot.current = snapshot(fields);
  }, [fields]);

  useEffect(() => {
    if (!record?.id || !token || loadedRef.current) return;
    loadedRef.current = true;

    adminService.getEduSuggestionDetail(token, record.id)
      .then((data) => {
        const loaded = {
          title: data.title || '',
          description: data.description || '',
          grade_bands: data.grade_bands || [],
          sub_topics: data.sub_topics || [],
        };
        setFields(loaded);
        savedSnapshot.current = snapshot(loaded);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [record?.id, token]);

  const updateField = (field, value) => {
    setFields((prev) => ({ ...prev, [field]: value }));
  };

  const updateSubtopic = (index, value) => {
    setFields((prev) => ({
      ...prev,
      sub_topics: prev.sub_topics.map((s, i) => (i === index ? value : s)),
    }));
  };

  const removeSubtopic = (index) => {
    setFields((prev) => ({
      ...prev,
      sub_topics: prev.sub_topics.filter((_, i) => i !== index),
    }));
  };

  const addSubtopic = () => {
    setFields((prev) => ({
      ...prev,
      sub_topics: [...prev.sub_topics, ''],
    }));
  };

  const toggleGradeBand = (band) => {
    setFields((prev) => ({
      ...prev,
      grade_bands: prev.grade_bands.includes(band)
        ? prev.grade_bands.filter((b) => b !== band)
        : [...prev.grade_bands, band],
    }));
  };

  const requestClose = useCallback(() => {
    if (isDirty()) {
      setConfirmClose(true);
    } else {
      onClose();
    }
  }, [isDirty, onClose]);

  useEffect(() => {
    if (requestCloseRef) requestCloseRef.current = requestClose;
  }, [requestCloseRef, requestClose]);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await adminService.updateEduSuggestion(token, record.id, {
        title: fields.title,
        description: fields.description || null,
        grade_bands: fields.grade_bands,
        sub_topics: fields.sub_topics,
      });
      markClean();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-gray-500">Loading edu suggestion...</div>
      </div>
    );
  }

  if (error && !fields.title) {
    return (
      <div className="flex-1 px-4 py-6">
        <p className="text-red-600">{error}</p>
      </div>
    );
  }

  const inputClass = 'w-full rounded-md border-0 px-3 py-1.5 text-sm text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 placeholder:text-gray-400 focus:ring-2 focus:ring-inset focus:ring-indigo-600';
  const allGradeBands = ['3rd-6th', '4th-8th', '7th-8th', '9th-12th'];

  return (
    <>
      <div className="flex-1 flex flex-col px-4 py-2 min-h-0 overflow-y-auto">
        {error && <p className="text-red-600 text-sm mb-2">{error}</p>}

        {confirmClose && (
          <div className="mb-3 rounded-md border border-amber-300 bg-amber-50 p-3">
            <p className="text-sm text-amber-800">You have unsaved changes that will be lost.</p>
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                onClick={() => setConfirmClose(false)}
                className="rounded-md bg-white px-2.5 py-1.5 text-xs font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={onClose}
                className="rounded-md bg-red-600 px-2.5 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-red-500"
              >
                Discard & Close
              </button>
            </div>
          </div>
        )}

        {/* Title */}
        <div className="mb-3">
          <label className="block text-sm font-medium text-gray-700 mb-1">Title</label>
          <input
            type="text"
            value={fields.title}
            onChange={(e) => updateField('title', e.target.value)}
            className={inputClass}
          />
        </div>

        {/* Description */}
        <div className="mb-3">
          <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
          <textarea
            rows={4}
            value={fields.description}
            onChange={(e) => updateField('description', e.target.value)}
            className={inputClass}
          />
        </div>

        {/* Grade Bands */}
        <div className="mb-3">
          <label className="block text-sm font-medium text-gray-700 mb-1">Grade Bands</label>
          <div className="flex flex-wrap gap-2">
            {allGradeBands.map((band) => (
              <button
                key={band}
                type="button"
                onClick={() => toggleGradeBand(band)}
                className={`rounded-full px-3 py-1 text-xs font-medium border transition-colors ${
                  fields.grade_bands.includes(band)
                    ? 'bg-indigo-100 border-indigo-300 text-indigo-700'
                    : 'bg-gray-50 border-gray-200 text-gray-500 hover:bg-gray-100'
                }`}
              >
                {band}
              </button>
            ))}
          </div>
        </div>

        {/* Subtopics */}
        <div className="mb-3">
          <label className="block text-sm font-medium text-gray-700 mb-1">Subtopics</label>
          <div className="space-y-2">
            {fields.sub_topics.map((topic, i) => (
              <div key={i} className="flex gap-2">
                <textarea
                  rows={3}
                  value={topic}
                  onChange={(e) => updateSubtopic(i, e.target.value)}
                  className={`${inputClass} flex-1`}
                />
                <button
                  type="button"
                  onClick={() => removeSubtopic(i)}
                  className="self-start mt-1 text-red-500 hover:text-red-700 text-lg font-bold px-1"
                  title="Remove subtopic"
                >
                  &times;
                </button>
              </div>
            ))}
          </div>
          <button
            type="button"
            onClick={addSubtopic}
            className="mt-2 text-sm text-indigo-600 hover:text-indigo-500 font-medium"
          >
            + Add subtopic
          </button>
        </div>
      </div>

      {/* Footer */}
      <div className="border-t border-gray-200 px-4 py-3 flex justify-end gap-3">
        <button
          type="button"
          onClick={requestClose}
          className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50"
        >
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>
    </>
  );
}

function EduResearchEditor({ record, token, onClose }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [contentHtml, setContentHtml] = useState('');
  const loadedRef = useRef(false);

  useEffect(() => {
    if (!record?.id || !token || loadedRef.current) return;
    loadedRef.current = true;

    adminService.getEduResearchDetail(token, record.id)
      .then((data) => {
        setContentHtml(data.content_html);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [record?.id, token]);

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-gray-500">Loading edu research content...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex-1 px-4 py-6">
        <p className="text-red-600">{error}</p>
      </div>
    );
  }

  return (
    <>
      <div className="flex-1 px-4 py-2 min-h-0 overflow-y-auto">
        <div
          className="article-content"
          dangerouslySetInnerHTML={{ __html: contentHtml }}
        />
      </div>
      <div className="border-t border-gray-200 px-4 py-3 flex justify-end gap-3">
        <button
          type="button"
          onClick={onClose}
          className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
        >
          Close
        </button>
      </div>
    </>
  );
}

function eduMaterialSnapshot(title, blocksEn, blocksEs, slides) {
  return JSON.stringify({ title, blocksEn, blocksEs, slides });
}

function EduMaterialEditor({ record, token, onClose, requestCloseRef }) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(null); // 'en' | 'es' | null
  const [busy, setBusy] = useState(null); // slide pipeline action in flight
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [confirmClose, setConfirmClose] = useState(false);
  const [regenModal, setRegenModal] = useState(null); // { key, ...modal props } | null
  const [data, setData] = useState(null);
  // Re-stamped on every data load / re-sync so the artifact links below always
  // bypass the browser's cached copy of a file that was regenerated in place.
  const [loadedAt, setLoadedAt] = useState(() => Date.now());
  useEffect(() => {
    setLoadedAt(Date.now());
  }, [data]);
  const [lang, setLang] = useState('en');
  const [title, setTitle] = useState('');
  const [blocksEn, setBlocksEn] = useState(null);
  const [blocksEs, setBlocksEs] = useState(null);
  const [slides, setSlides] = useState(null);
  const loadedRef = useRef(false);
  const savedSnapshot = useRef('');

  const syncFromDetail = useCallback((d) => {
    setData(d);
    setTitle(d.title || '');
    setBlocksEn(d.content_blocks || null);
    setBlocksEs(d.content_blocks_es || null);
    setSlides(d.slides || null);
    savedSnapshot.current = eduMaterialSnapshot(
      d.title || '', d.content_blocks || null, d.content_blocks_es || null, d.slides || null,
    );
  }, []);

  useEffect(() => {
    if (!record?.id || !token || loadedRef.current) return;
    loadedRef.current = true;

    adminService.getEduMaterialDetail(token, record.id)
      .then(syncFromDetail)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [record?.id, token, syncFromDetail]);

  // Editable only for structured-block guides; non-guide materials and legacy
  // markdown-only guides keep the read-only view below.
  const editable = data?.material_type === 'guide' && !!blocksEn;
  // Slide decks get the per-slide editor + the pipeline buttons.
  const slidesEditable = data?.material_type === 'slides' && Array.isArray(slides) && slides.length > 0;
  const hasSpanishBlocks = !!blocksEs;

  const isDirty = useCallback(
    () =>
      (editable || slidesEditable) &&
      eduMaterialSnapshot(title, blocksEn, blocksEs, slides) !== savedSnapshot.current,
    [editable, slidesEditable, title, blocksEn, blocksEs, slides],
  );

  const requestClose = useCallback(() => {
    if (isDirty()) {
      setConfirmClose(true);
    } else {
      onClose();
    }
  }, [isDirty, onClose]);

  useEffect(() => {
    if (requestCloseRef) requestCloseRef.current = requestClose;
  }, [requestCloseRef, requestClose]);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      let d;
      if (slidesEditable) {
        d = await adminService.updateEduSlides(token, record.id, { title, slides });
      } else {
        const payload = { title, content_blocks: blocksEn };
        if (hasSpanishBlocks) payload.content_blocks_es = blocksEs;
        d = await adminService.updateEduMaterialContent(token, record.id, payload);
      }
      // Re-sync from the server's normalized copy so what's on screen is
      // exactly what got stored.
      syncFromDetail(d);
      setNotice('Saved.');
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleRegenerate = async (pdfLang) => {
    setRegenerating(pdfLang);
    setError(null);
    setNotice(null);
    try {
      const res = await adminService.generateEduGuidePdf(token, record.id, pdfLang);
      setData((d) => ({
        ...d,
        file_url: res.file_url || d.file_url,
        pdf_urls: res.file_url ? { ...(d.pdf_urls || { en: null, es: null }), [pdfLang]: res.file_url } : d.pdf_urls,
      }));
      setNotice(`${pdfLang.toUpperCase()} PDF regenerated.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setRegenerating(null);
    }
  };

  // One slide-pipeline action at a time; each re-syncs the deck afterwards so
  // artifact chips (frame/audio) and narration fields reflect the server state.
  const runDeckAction = async (key, action, describe) => {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      const res = await action();
      const d = await adminService.getEduMaterialDetail(token, record.id);
      syncFromDetail(d);
      setNotice(describe(res));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
    }
  };

  // Render, audio and video run as background jobs (#346); show the job's
  // per-slide progress in the notice line while the drawer waits on it.
  const progressNotice = (label) => (job) =>
    setNotice(
      job.units_total
        ? `${label}… ${job.units_done}/${job.units_total}`
        : `${label}… started`,
    );

  const deckHasNarration = slidesEditable && slides.some((s) => (s.narration || '').trim());
  const deckFullyRendered = slidesEditable && slides.every((s) => s.image_url && s.audio_url);

  const handleApprove = () =>
    runDeckAction(
      'approve',
      () => adminService.updateStatus(token, 'edu/materials', record.id, 'APPROVED'),
      () => 'Deck approved — you can now generate the narration.',
    );

  // Each pipeline step accepts a 1-based `positions` subset (null = whole deck).
  const handleNarration = (positions = null) =>
    runDeckAction(
      'narration',
      () =>
        adminService.generateEduNarration(token, record.id, {
          // A whole-deck rewrite over existing narration needs force; a subset
          // is inherently an intentional edit, so it skips the guard.
          force: positions ? false : deckHasNarration,
          positions,
        }),
      (res) =>
        positions
          ? `Narration rewritten for ${res.slides_narrated} slide(s), ${res.total_words} words.`
          : `Narration written: ${res.slides_narrated} slides, ${res.total_words} words (~${Math.round(res.total_words / 150)} min).`,
    );

  const handleAudio = (positions = null) =>
    runDeckAction(
      'audio',
      () =>
        adminService.generateEduSlideAudio(token, record.id, {
          positions,
          onProgress: progressNotice('Synthesizing audio'),
        }),
      (res) => `Audio synthesized for ${res.slides_narrated} slide(s) (${Math.round((res.total_duration_ms || 0) / 60000)} min).`,
    );

  const handleRender = (positions = null) =>
    runDeckAction(
      'render',
      () =>
        adminService.renderEduSlides(token, record.id, {
          positions,
          onProgress: progressNotice('Rendering frames'),
        }),
      (res) =>
        res.images_missing > 0
          ? `Rendered ${res.slides_rendered} frame(s) — ${res.images_missing} slide(s) still have no picked photo.`
          : `Rendered ${res.slides_rendered} frame(s).`,
    );

  const handleContent = (positions) =>
    runDeckAction(
      'content',
      () => adminService.regenerateEduSlides(token, record.id, positions),
      (res) =>
        `Regenerated content for ${res.slides_regenerated} slide(s): ${res.positions.join(', ')}. Re-render those frames.`,
    );

  // A slide is a candidate for a step when its artifact for that step is stale
  // (matches the server's "content changed → frame/audio cleared" rule). Returns
  // the modal's `changed` shape, or null when nothing is stale (hides the option).
  const changedSet = (predicate, label) => {
    const positions = (slides || [])
      .filter(predicate)
      .map((s) => s.position)
      .sort((a, b) => a - b);
    return positions.length ? { positions, label } : null;
  };

  const REGEN_CONFIG = {
    narration: () => ({
      title: deckHasNarration ? 'Regenerate narration' : 'Generate narration',
      actionLabel: deckHasNarration ? 'Regenerate' : 'Generate',
      description: deckHasNarration
        ? "Rewriting narration clears the affected slides' audio — re-synthesize them after."
        : null,
      changed: changedSet((s) => !(s.narration || '').trim(), 'Only slides without narration'),
      allowAll: true,
    }),
    audio: () => ({
      title: 'Generate audio',
      actionLabel: 'Generate',
      changed: changedSet(
        (s) => (s.narration || '').trim() && !s.audio_url,
        'Only slides missing audio',
      ),
      allowAll: true,
    }),
    render: () => ({
      title: 'Render slides',
      actionLabel: 'Render',
      changed: changedSet((s) => !s.image_url, 'Only slides missing a frame'),
      allowAll: true,
    }),
    content: () => ({
      title: 'Regenerate slide content',
      actionLabel: 'Regenerate',
      description:
        'Rewrites the on-screen content of the chosen slides in place (keeps their photo, narration, and audio). Re-render the frames afterward.',
      changed: null, // content has no "stale" signal — pick the slides explicitly
      allowAll: false, // whole-deck content regen is the separate generate-slides flow
    }),
  };

  const openRegen = (key) => setRegenModal({ key, ...REGEN_CONFIG[key]() });

  const runRegen = (positions) => {
    const key = regenModal?.key;
    setRegenModal(null);
    if (key === 'narration') return handleNarration(positions);
    if (key === 'audio') return handleAudio(positions);
    if (key === 'render') return handleRender(positions);
    if (key === 'content') return handleContent(positions);
    return undefined;
  };

  const handleVideo = () =>
    runDeckAction(
      'video',
      () =>
        adminService.assembleEduVideo(token, record.id, {
          onProgress: progressNotice('Assembling video'),
        }),
      // The detail re-sync picks up video_url, so the "Open video" link above
      // appears as soon as this notice does.
      (res) => `Video assembled (${Math.round((res.duration_ms || 0) / 60000)} min) — use "Open video" above.`,
    );

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-gray-500">Loading edu material...</div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="flex-1 px-4 py-6">
        <p className="text-red-600">{error}</p>
      </div>
    );
  }

  const contentHtml = lang === 'es' ? (data?.content_es_html || '') : (data?.content_html || '');
  const currentBlocks = lang === 'es' ? blocksEs : blocksEn;
  const setCurrentBlocks = lang === 'es' ? setBlocksEs : setBlocksEn;

  const langTabs = (
    <div className="flex gap-1 mb-4 rounded-lg bg-gray-100 p-1 w-fit">
      {['en', 'es'].map((l) => {
        const disabled = l === 'es' && editable && !hasSpanishBlocks;
        return (
          <button
            key={l}
            type="button"
            disabled={disabled}
            title={disabled ? 'No Spanish translation yet — run Translate first' : undefined}
            onClick={() => setLang(l)}
            className={`rounded-md px-3 py-1 text-sm font-medium transition-colors ${
              lang === l
                ? 'bg-white text-indigo-600 shadow-sm'
                : disabled
                  ? 'text-gray-300 cursor-not-allowed'
                  : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            {l.toUpperCase()}
          </button>
        );
      })}
    </div>
  );

  return (
    <>
      <div className="flex-1 px-4 py-2 min-h-0 overflow-y-auto">
        {error && <p className="text-red-600 text-sm mb-2">{error}</p>}
        {notice && <p className="text-green-700 text-sm mb-2">{notice}</p>}

        {confirmClose && (
          <div className="mb-3 rounded-md border border-amber-300 bg-amber-50 p-3">
            <p className="text-sm text-amber-800">You have unsaved edits. Discard them?</p>
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                onClick={() => setConfirmClose(false)}
                className="rounded-md bg-white px-2.5 py-1.5 text-xs font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={onClose}
                className="rounded-md bg-red-600 px-2.5 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-red-500"
              >
                Discard & Close
              </button>
            </div>
          </div>
        )}

        {slidesEditable && data?.status === 'APPROVED' && isDirty() && (
          <div className="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2">
            <p className="text-xs text-amber-700">
              Edits to an approved deck don't reset its approval — save, then re-run the
              affected steps (narration / audio / render).
            </p>
          </div>
        )}

        <div className="mb-4 space-y-1 text-sm text-gray-600">
          <p><span className="font-medium text-gray-700">Grade Band:</span> {data?.grade_band}</p>
          <p><span className="font-medium text-gray-700">Type:</span> {data?.material_type}</p>
          {slidesEditable && (
            <p><span className="font-medium text-gray-700">Status:</span> {data?.status}</p>
          )}
          {data?.material_type === 'guide' && data?.pdf_urls ? (
            <p className="flex gap-4">
              {['es', 'en'].map((l) =>
                data.pdf_urls[l] ? (
                  <a
                    key={l}
                    href={cacheBustUrl(data.pdf_urls[l], loadedAt)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-medium text-indigo-600 hover:text-indigo-500"
                  >
                    Open PDF ({l.toUpperCase()})
                  </a>
                ) : (
                  <span key={l} className="text-gray-400">
                    No {l.toUpperCase()} PDF yet
                  </span>
                ),
              )}
            </p>
          ) : data?.file_url && (
            <p>
              <a
                href={cacheBustUrl(data.file_url, loadedAt)}
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-indigo-600 hover:text-indigo-500"
              >
                {data?.material_type === 'video'
                  ? 'Open video'
                  : data?.material_type === 'guide'
                    ? 'Open current PDF'
                    : 'Open file'}
              </a>
            </p>
          )}
          {data?.video_url && (
            <p>
              <a
                href={cacheBustUrl(data.video_url, loadedAt)}
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-indigo-600 hover:text-indigo-500"
              >
                Open video
                {data?.video_duration_ms
                  ? ` (${Math.round(data.video_duration_ms / 60000)} min)`
                  : ''}
              </a>
            </p>
          )}
        </div>

        {editable ? (
          <>
            <div className="mb-4">
              <span className="block text-xs font-medium text-gray-500 mb-1">Guide title</span>
              <input
                className="block w-full rounded-md border-0 py-1.5 text-sm text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 focus:ring-2 focus:ring-inset focus:ring-indigo-600"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </div>
            {langTabs}
            {currentBlocks ? (
              <GuideBlocksEditor
                blocks={currentBlocks}
                onChange={setCurrentBlocks}
                token={token}
              />
            ) : (
              <p className="text-sm text-gray-500 italic">
                No {lang.toUpperCase()} content yet.
              </p>
            )}
          </>
        ) : slidesEditable ? (
          <>
            <div className="mb-4">
              <span className="block text-xs font-medium text-gray-500 mb-1">Deck title</span>
              <input
                className="block w-full rounded-md border-0 py-1.5 text-sm text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 focus:ring-2 focus:ring-inset focus:ring-indigo-600"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </div>
            <SlideDeckEditor slides={slides} onChange={setSlides} token={token} />
          </>
        ) : (
          <>
            {data?.file_url && data.thumbnail_url && (
              <img src={data.thumbnail_url} alt="Thumbnail" className="max-w-xs rounded border mb-4" />
            )}
            {(data?.content_html || data?.content_es_html) && (
              <>
                {!!data?.has_spanish && langTabs}
                <div
                  className="article-content"
                  dangerouslySetInnerHTML={{ __html: contentHtml }}
                />
              </>
            )}
          </>
        )}
      </div>
      <div className="border-t border-gray-200 px-4 py-3 flex flex-wrap items-center gap-2">
        {editable && (
          <div className="flex gap-2 mr-auto">
            {['en', ...(hasSpanishBlocks ? ['es'] : [])].map((l) => (
              <button
                key={l}
                type="button"
                onClick={() => handleRegenerate(l)}
                disabled={!!regenerating || saving || isDirty()}
                title={isDirty() ? 'Save your edits first' : undefined}
                className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50 disabled:opacity-50"
              >
                {regenerating === l ? 'Rendering…' : `Regenerate PDF (${l.toUpperCase()})`}
              </button>
            ))}
          </div>
        )}
        {slidesEditable && (
          <div className="flex flex-wrap gap-2 mr-auto">
            {[
              {
                key: 'approve',
                label: busy === 'approve' ? 'Approving…' : 'Approve',
                onClick: handleApprove,
                enabled: data?.status !== 'APPROVED',
                hint:
                  data?.status === 'APPROVED'
                    ? 'Deck is already approved'
                    : 'Approve the deck content to unlock narration',
              },
              {
                key: 'content',
                label: busy === 'content' ? 'Rewriting…' : 'Regenerate content',
                onClick: () => openRegen('content'),
                enabled: data?.status !== 'APPROVED',
                hint:
                  data?.status === 'APPROVED'
                    ? 'Approved decks are locked — content is final'
                    : null,
              },
              {
                key: 'narration',
                label:
                  busy === 'narration'
                    ? 'Writing script…'
                    : deckHasNarration
                      ? 'Regenerate narration'
                      : 'Generate narration',
                onClick: () => openRegen('narration'),
                enabled: data?.status === 'APPROVED',
                hint: 'Approve the deck first',
              },
              {
                key: 'audio',
                label: busy === 'audio' ? 'Synthesizing…' : 'Generate audio',
                onClick: () => openRegen('audio'),
                enabled: deckHasNarration,
                hint: 'Narrate at least one slide first',
              },
              {
                key: 'render',
                label: busy === 'render' ? 'Rendering…' : 'Render slides',
                onClick: () => openRegen('render'),
                enabled: true,
                hint: null,
              },
              {
                key: 'video',
                label: busy === 'video' ? 'Assembling…' : 'Assemble video',
                onClick: handleVideo,
                enabled: deckFullyRendered,
                hint: 'Every slide needs a rendered frame and audio first',
              },
            ].map((b) => {
              const disabled = !!busy || saving || isDirty() || !b.enabled;
              return (
                <button
                  key={b.key}
                  type="button"
                  onClick={b.onClick}
                  disabled={disabled}
                  title={
                    isDirty() ? 'Save your edits first' : !b.enabled ? b.hint || undefined : undefined
                  }
                  className="rounded-md bg-white px-2.5 py-2 text-xs font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50 disabled:opacity-50"
                >
                  {b.label}
                </button>
              );
            })}
          </div>
        )}
        <button
          type="button"
          onClick={editable || slidesEditable ? requestClose : onClose}
          className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
        >
          Close
        </button>
        {(editable || slidesEditable) && (
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || !!regenerating || !!busy}
            className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50"
          >
            {saving ? 'Saving...' : 'Save'}
          </button>
        )}
      </div>

      {regenModal && (
        <RegenerateRangeModal
          open
          onClose={() => setRegenModal(null)}
          onConfirm={runRegen}
          title={regenModal.title}
          actionLabel={regenModal.actionLabel}
          description={regenModal.description}
          slideCount={slides?.length || 0}
          changed={regenModal.changed}
          allowAll={regenModal.allowAll}
          busy={!!busy}
        />
      )}
    </>
  );
}

function PlaceholderEditor({ record }) {
  return (
    <div className="flex-1 px-4 py-6">
      {record ? (
        <div className="space-y-4">
          <div>
            <dt className="text-sm font-medium text-gray-500">ID</dt>
            <dd className="mt-1 text-sm text-gray-900">{record.id}</dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Title</dt>
            <dd className="mt-1 text-sm text-gray-900">
              {record.title || record.suggestion_title || '\u2014'}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Status</dt>
            <dd className="mt-1 text-sm text-gray-900">{record.status}</dd>
          </div>
          <p className="text-sm text-gray-500 italic">
            Full editing form coming soon.
          </p>
        </div>
      ) : (
        <p className="text-sm text-gray-500">No record selected.</p>
      )}
    </div>
  );
}

const IG_VARIANTS = [
  { value: 'AZUL', label: 'Azul (navy card)' },
  { value: 'CLARO', label: 'Claro (warm gray card)' },
];

const IG_CAPTION_MAX = 2200; // Instagram's hard caption limit

function InstagramPostEditor({ record, token, onClose, requestCloseRef }) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [rendering, setRendering] = useState(false);
  const [error, setError] = useState(null);
  const [detail, setDetail] = useState(null);
  const [fields, setFields] = useState(null);
  const [confirmClose, setConfirmClose] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const loadedRef = useRef(false);
  const savedSnapshot = useRef('');

  const isSabias = detail?.post_type === 'SABIAS_QUE';
  const isPublished = !!detail?.published_at;

  const isDirty = useCallback(
    () => fields !== null && JSON.stringify(fields) !== savedSnapshot.current,
    [fields],
  );

  useEffect(() => {
    if (!record?.id || !token || loadedRef.current) return;
    loadedRef.current = true;
    adminService.getInstagramPost(token, record.id)
      .then((data) => {
        setDetail(data);
        const f = {
          title: data.title || '',
          fact: data.fact || '',
          polaroid_caption: data.polaroid_caption || '',
          kicker: data.kicker || '',
          meta_text: data.meta_text || '',
          caption: data.caption || '',
          template_variant: data.template_variant || 'AZUL',
          image_query: data.image_query || '',
          media_id: data.media_id || null,
          media_path: data.media_path || null,
        };
        setFields(f);
        savedSnapshot.current = JSON.stringify(f);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [record?.id, token]);

  const requestClose = useCallback(() => {
    if (isDirty()) {
      setConfirmClose(true);
    } else {
      onClose();
    }
  }, [isDirty, onClose]);

  useEffect(() => {
    if (requestCloseRef) requestCloseRef.current = requestClose;
  }, [requestCloseRef, requestClose]);

  const patch = (updates) => setFields((prev) => ({ ...prev, ...updates }));

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const payload = {
        title: fields.title,
        fact: isSabias ? fields.fact : null,
        polaroid_caption: isSabias ? fields.polaroid_caption : null,
        kicker: !isSabias ? fields.kicker : null,
        meta_text: !isSabias ? fields.meta_text : null,
        caption: fields.caption,
        template_variant: isSabias ? fields.template_variant : null,
        image_query: fields.image_query,
      };
      if (fields.media_path) {
        payload.media_id = fields.media_id;
        payload.media_path = fields.media_path;
      } else {
        payload.clear_media = true;
      }
      const data = await adminService.updateInstagramPost(token, record.id, payload);
      setDetail(data);
      // The server sanitizes the fact — reflect what was actually stored.
      const f = { ...fields, fact: data.fact || '' };
      setFields(f);
      savedSnapshot.current = JSON.stringify(f);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleRender = async () => {
    setRendering(true);
    setError(null);
    try {
      const res = await adminService.renderInstagramPost(token, record.id);
      setDetail((prev) => ({ ...prev, image_url: res.image_url }));
      if (res.photo_missing) {
        setError('Rendered without a photo — the card used the no-polaroid fallback.');
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setRendering(false);
    }
  };

  if (loading || !fields) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-gray-500">{error || 'Loading post...'}</div>
      </div>
    );
  }

  const inputCls =
    'mt-1 w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none disabled:bg-gray-50 disabled:text-gray-500';
  const captionLen = fields.caption.length;

  return (
    <>
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {error && <p className="rounded bg-red-50 p-2 text-sm text-red-700">{error}</p>}

        {confirmClose && (
          <div className="rounded-md border border-amber-300 bg-amber-50 p-3">
            <p className="text-sm text-amber-800">You have unsaved edits. Discard them?</p>
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                onClick={() => setConfirmClose(false)}
                className="rounded-md bg-white px-2.5 py-1.5 text-xs font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={onClose}
                className="rounded-md bg-red-600 px-2.5 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-red-500"
              >
                Discard & Close
              </button>
            </div>
          </div>
        )}

        {isPublished && (
          <div className="rounded-md bg-green-50 p-3 text-sm text-green-800">
            Published on Instagram
            {detail.published_at && ` (${new Date(detail.published_at).toLocaleString()})`}
            {detail.ig_permalink && (
              <>
                {' — '}
                <a className="underline" href={detail.ig_permalink} target="_blank" rel="noreferrer">
                  view post
                </a>
              </>
            )}
            . Content is read-only.
          </div>
        )}

        {detail.publish_error && !isPublished && (
          <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">
            Last publish attempt failed: {detail.publish_error}. Re-schedule the post (calendar
            icon on the grid) to retry.
          </div>
        )}

        <div>
          <label className="block text-sm font-medium text-gray-700">Internal title</label>
          <input
            type="text"
            value={fields.title}
            onChange={(e) => patch({ title: e.target.value })}
            disabled={isPublished}
            className={inputCls}
          />
        </div>

        {isSabias ? (
          <>
            <div>
              <label className="block text-sm font-medium text-gray-700">
                Fact <span className="font-normal text-gray-400">(continues "¿Sabías que…"; use &lt;b&gt;…&lt;/b&gt; for emphasis)</span>
              </label>
              <textarea
                rows={4}
                value={fields.fact}
                onChange={(e) => patch({ fact: e.target.value })}
                disabled={isPublished}
                className={inputCls}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium text-gray-700">Polaroid caption</label>
                <input
                  type="text"
                  maxLength={120}
                  value={fields.polaroid_caption}
                  onChange={(e) => patch({ polaroid_caption: e.target.value })}
                  disabled={isPublished}
                  className={inputCls}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700">Card style</label>
                <select
                  value={fields.template_variant}
                  onChange={(e) => patch({ template_variant: e.target.value })}
                  disabled={isPublished}
                  className={inputCls}
                >
                  {IG_VARIANTS.map((v) => (
                    <option key={v.value} value={v.value}>{v.label}</option>
                  ))}
                </select>
              </div>
            </div>
          </>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700">Kicker</label>
              <input
                type="text"
                maxLength={120}
                value={fields.kicker}
                onChange={(e) => patch({ kicker: e.target.value })}
                disabled={isPublished}
                className={inputCls}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700">Meta line</label>
              <input
                type="text"
                maxLength={200}
                value={fields.meta_text}
                onChange={(e) => patch({ meta_text: e.target.value })}
                disabled={isPublished}
                className={inputCls}
              />
            </div>
          </div>
        )}

        <div>
          <label className="block text-sm font-medium text-gray-700">
            Caption{' '}
            <span className={`font-normal ${captionLen > IG_CAPTION_MAX ? 'text-red-600' : 'text-gray-400'}`}>
              {captionLen} / {IG_CAPTION_MAX}
            </span>
          </label>
          <textarea
            rows={7}
            value={fields.caption}
            onChange={(e) => patch({ caption: e.target.value })}
            disabled={isPublished}
            className={inputCls}
          />
        </div>

        {/* Photo pick (edu-material flow: image_query is only the search hint) */}
        <div className="rounded-md border border-dashed border-gray-300 bg-gray-50 p-3">
          <p className="text-xs text-gray-500 mb-2">
            <span className="font-medium text-gray-600">
              {isSabias ? 'Polaroid photo' : 'Background photo'} — wanted:
            </span>{' '}
            {fields.image_query || '—'}
          </p>
          {fields.media_path ? (
            <div className="flex items-center gap-3">
              <img
                src={fields.media_path}
                alt=""
                className="h-16 w-24 rounded object-cover ring-1 ring-gray-200"
              />
              {!isPublished && (
                <>
                  <button
                    type="button"
                    onClick={() => setPickerOpen(true)}
                    className="rounded bg-white px-2.5 py-1.5 text-xs font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
                  >
                    Change…
                  </button>
                  <button
                    type="button"
                    onClick={() => patch({ media_id: null, media_path: null })}
                    className="inline-flex items-center gap-1 rounded px-2 py-1.5 text-xs text-gray-500 hover:bg-gray-200"
                  >
                    <XMarkIcon className="h-3.5 w-3.5" /> Clear
                  </button>
                </>
              )}
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setPickerOpen(true)}
              disabled={isPublished}
              className="rounded bg-indigo-50 px-3 py-1.5 text-xs font-semibold text-indigo-700 hover:bg-indigo-100 disabled:opacity-50"
            >
              Choose image…
            </button>
          )}
        </div>

        {/* Rendered artifact */}
        <div className="rounded-md border border-gray-200 p-3">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium text-gray-700">Rendered image (1080×1350)</p>
            {!isPublished && (
              <button
                type="button"
                onClick={handleRender}
                disabled={rendering || isDirty()}
                title={isDirty() ? 'Save your edits first' : 'Render the branded image'}
                className="rounded bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
              >
                {rendering ? 'Rendering…' : detail.image_url ? 'Re-render' : 'Render image'}
              </button>
            )}
          </div>
          {isDirty() && !isPublished && (
            <p className="mt-1 text-xs text-amber-600">Unsaved edits — save before rendering.</p>
          )}
          {detail.image_url ? (
            <a href={detail.image_url} target="_blank" rel="noreferrer">
              <img
                src={detail.image_url}
                alt="Rendered post"
                className="mt-3 w-64 rounded-lg ring-1 ring-gray-200"
                style={{ aspectRatio: '4 / 5', objectFit: 'cover' }}
              />
            </a>
          ) : (
            <p className="mt-2 text-xs text-gray-400">Not rendered yet.</p>
          )}
        </div>
      </div>

      <div className="border-t border-gray-200 px-4 py-3 flex justify-end gap-3">
        <button
          type="button"
          onClick={requestClose}
          className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
        >
          {isPublished ? 'Close' : 'Cancel'}
        </button>
        {!isPublished && (
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || captionLen > IG_CAPTION_MAX}
            className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50"
          >
            {saving ? 'Saving...' : 'Save'}
          </button>
        )}
      </div>

      <MediaPickerModal
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        token={token}
        excludeFeatureImages={false}
        initialSearch={fields.image_query || ''}
        onSelect={({ id, filePath, src }) => {
          patch({
            media_id: id,
            media_path: /^https?:\/\//.test(filePath || '') ? filePath : src,
          });
        }}
      />
    </>
  );
}

function ResizeHandle({ onMouseDown }) {
  return (
    <div
      onMouseDown={onMouseDown}
      className="absolute inset-y-0 left-0 w-2 cursor-col-resize group z-10 flex items-center"
    >
      <div className="h-8 w-1 rounded-full bg-gray-300 group-hover:bg-indigo-400 transition-colors" />
    </div>
  );
}

export default function EditDrawer({ open, onClose, record, recordType, token }) {
  const isResizable = recordType === 'Research' || recordType === 'Article' || recordType === 'Suggestion' || recordType === 'Outline' || recordType === 'EduSuggestion' || recordType === 'EduResearch' || recordType === 'EduMaterial' || recordType === 'InstagramPost';
  const { width, onMouseDown } = useResizableDrawer(896, 448);
  const articleRequestCloseRef = useRef(null);
  const suggestionRequestCloseRef = useRef(null);
  const outlineRequestCloseRef = useRef(null);
  const eduSuggestionRequestCloseRef = useRef(null);
  const researchRequestCloseRef = useRef(null);
  const eduMaterialRequestCloseRef = useRef(null);
  const instagramRequestCloseRef = useRef(null);

  const handleClose = useCallback(() => {
    if (recordType === 'Article' && articleRequestCloseRef.current) {
      articleRequestCloseRef.current();
    } else if (recordType === 'Suggestion' && suggestionRequestCloseRef.current) {
      suggestionRequestCloseRef.current();
    } else if (recordType === 'Outline' && outlineRequestCloseRef.current) {
      outlineRequestCloseRef.current();
    } else if (recordType === 'EduSuggestion' && eduSuggestionRequestCloseRef.current) {
      eduSuggestionRequestCloseRef.current();
    } else if (recordType === 'Research' && researchRequestCloseRef.current) {
      researchRequestCloseRef.current();
    } else if (recordType === 'EduMaterial' && eduMaterialRequestCloseRef.current) {
      eduMaterialRequestCloseRef.current();
    } else if (recordType === 'InstagramPost' && instagramRequestCloseRef.current) {
      instagramRequestCloseRef.current();
    } else {
      onClose();
    }
  }, [recordType, onClose]);

  const renderEditor = () => {
    if (recordType === 'Research') {
      return <ResearchEditor record={record} token={token} onClose={onClose} requestCloseRef={researchRequestCloseRef} />;
    }
    if (recordType === 'Article') {
      return <ArticleEditor record={record} token={token} onClose={onClose} requestCloseRef={articleRequestCloseRef} />;
    }
    if (recordType === 'Suggestion') {
      return <SuggestionEditor record={record} token={token} onClose={onClose} requestCloseRef={suggestionRequestCloseRef} />;
    }
    if (recordType === 'Outline') {
      return <OutlineEditor record={record} token={token} onClose={onClose} requestCloseRef={outlineRequestCloseRef} />;
    }
    if (recordType === 'EduSuggestion') {
      return <EduSuggestionEditor record={record} token={token} onClose={onClose} requestCloseRef={eduSuggestionRequestCloseRef} />;
    }
    if (recordType === 'EduResearch') {
      return <EduResearchEditor record={record} token={token} onClose={onClose} />;
    }
    if (recordType === 'EduMaterial') {
      return <EduMaterialEditor record={record} token={token} onClose={onClose} requestCloseRef={eduMaterialRequestCloseRef} />;
    }
    if (recordType === 'InstagramPost') {
      return <InstagramPostEditor record={record} token={token} onClose={onClose} requestCloseRef={instagramRequestCloseRef} />;
    }
    return <PlaceholderEditor record={record} recordType={recordType} />;
  };

  return (
    <Dialog open={open} onClose={handleClose} className="relative z-50">
      <DialogBackdrop
        transition
        className="fixed inset-0 bg-gray-900/80 transition-opacity duration-300 ease-linear data-closed:opacity-0"
      />
      <div className="fixed inset-0 overflow-hidden">
        <div className="absolute inset-0 overflow-hidden">
          <div className="pointer-events-none fixed inset-y-0 right-0 flex max-w-full pl-10">
            <DialogPanel
              transition
              style={isResizable ? { width: `${width}px` } : undefined}
              className={`pointer-events-auto ${isResizable ? '' : 'w-screen max-w-md'} transform transition duration-300 ease-in-out data-closed:translate-x-full`}
            >
              <TransitionChild>
                <div className="relative flex h-full flex-col bg-white shadow-xl">
                  {isResizable && <ResizeHandle onMouseDown={onMouseDown} />}

                  <div className="flex items-center justify-between border-b border-gray-200 px-4 py-4">
                    <h2 className="text-lg font-semibold text-gray-900">
                      Edit {recordType}
                    </h2>
                    <button
                      onClick={handleClose}
                      className="rounded-md p-1 text-gray-400 hover:text-gray-500"
                    >
                      <XMarkIcon className="h-6 w-6" />
                    </button>
                  </div>

                  {renderEditor()}
                </div>
              </TransitionChild>
            </DialogPanel>
          </div>
        </div>
      </div>
    </Dialog>
  );
}
