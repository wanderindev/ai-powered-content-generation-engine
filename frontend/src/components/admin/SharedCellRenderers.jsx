export const BooleanCell = ({ value }) => (
  <span className={value ? 'text-green-600' : 'text-gray-300'}>{value ? 'Yes' : 'No'}</span>
);

const ARTICLES_STATUS_STYLES = {
  ALL: 'text-green-700 bg-green-50',
  SOME: 'text-yellow-700 bg-yellow-50',
  NONE: 'text-gray-400',
};

export const ArticlesStatusCell = ({ value }) => {
  const label = value === 'ALL' ? 'Yes' : value === 'SOME' ? 'Some' : 'No';
  const style = ARTICLES_STATUS_STYLES[value] || ARTICLES_STATUS_STYLES.NONE;
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ${style}`}>
      {label}
    </span>
  );
};

export const StatusCell = ({ value }) => {
  const colors = {
    PENDING: 'text-yellow-700 bg-yellow-50 ring-yellow-600/20',
    APPROVED: 'text-green-700 bg-green-50 ring-green-600/20',
    REJECTED: 'text-red-700 bg-red-50 ring-red-600/20',
  };
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset ${colors[value] || 'text-gray-600 bg-gray-50 ring-gray-500/10'}`}>
      {value}
    </span>
  );
};

// "Possible duplicate" pill for the suggestions grid (#630). Advisory only: the
// backend flags a suggestion whose topic is close to an existing article or
// suggestion. An article match calls onOpenArticle(id) and a suggestion match
// onOpenSuggestion(id), so the page opens that record's admin drawer. (Never a
// public link: the match is computed with internal visibility, so it can be a
// scheduled or unpublished article, #659.) No handler -> a plain pill.
export const DuplicateCell = ({ data, onOpenSuggestion, onOpenArticle }) => {
  if (!data) return null;
  const { duplicate_article_id: articleId, duplicate_suggestion_id: suggestionId } = data;
  if (!articleId && !suggestionId) return null;
  const score = data.duplicate_similarity != null ? `${Math.round(data.duplicate_similarity * 100)}%` : '';
  const title = articleId
    ? data.duplicate_article_title || `Article #${articleId}`
    : data.duplicate_suggestion_title || `Suggestion #${suggestionId}`;
  const kind = articleId ? 'article' : 'suggestion';
  const label = `Possible duplicate ${score}`.trim();
  const tooltip = `Possible duplicate of ${kind}: ${title}${score ? ` (similarity ${score})` : ''}`;
  const className =
    'inline-flex max-w-full items-center truncate rounded px-1.5 py-0.5 text-xs font-medium text-amber-800 bg-amber-50 ring-1 ring-inset ring-amber-600/20 hover:bg-amber-100';
  if (articleId && onOpenArticle) {
    return (
      <button type="button" title={tooltip} className={className} onClick={() => onOpenArticle(articleId)}>
        {label}: {title}
      </button>
    );
  }
  if (suggestionId && onOpenSuggestion) {
    return (
      <button type="button" title={tooltip} className={className} onClick={() => onOpenSuggestion(suggestionId)}>
        {label}: {title}
      </button>
    );
  }
  return (
    <span title={tooltip} className={className}>
      {label}: {title}
    </span>
  );
};
