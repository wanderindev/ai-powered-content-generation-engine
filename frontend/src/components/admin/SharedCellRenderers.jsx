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
