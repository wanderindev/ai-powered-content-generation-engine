import { StatusCell } from './SharedCellRenderers';
import StatusDropdown from './StatusDropdown';

export default function InlineStatusCell({ data, colDef }) {
  const onStatusChange = colDef.cellRendererParams?.onStatusChange;

  return (
    <div className="flex items-center gap-1">
      <StatusCell value={data.status} />
      {onStatusChange && (
        <StatusDropdown
          currentStatus={data.status}
          onStatusChange={(newStatus) => onStatusChange(data, newStatus)}
        />
      )}
    </div>
  );
}
