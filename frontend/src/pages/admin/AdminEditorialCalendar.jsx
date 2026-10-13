import { useState, useEffect, useMemo, useCallback } from 'react';
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  NewspaperIcon,
  CameraIcon,
} from '@heroicons/react/24/outline';
import { useAdminAuth } from '../../contexts/AdminAuthContext';
import { adminService } from '../../services/admin';
import EditDrawer from '../../components/admin/EditDrawer';
import { ToastContainer } from '../../components/admin/Toast';
import { useToast } from '../../hooks/useToast';

// ─── Helpers (mirrors AdminCalendar's hand-rolled month grid) ────────────────

function getDaysInMonth(year, month) {
  return new Date(year, month + 1, 0).getDate();
}

function getFirstDayOfMonth(year, month) {
  return new Date(year, month, 1).getDay();
}

function formatDate(year, month, day) {
  return `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

// Local calendar day for an ISO timestamp (pills land on the operator's day).
function localDay(iso) {
  const d = new Date(iso);
  return formatDate(d.getFullYear(), d.getMonth(), d.getDate());
}

// Pill colors: orange = queued for a future publish, green = live, red = a
// failed Instagram publish.
const PILL_STYLES = {
  live: 'bg-green-100 border-green-300 text-green-800',
  future: 'bg-orange-100 border-orange-300 text-orange-800',
  failed: 'bg-red-100 border-red-300 text-red-800',
};

function pillStyle(kind) {
  return PILL_STYLES[kind] || 'bg-gray-100 border-gray-300 text-gray-700';
}

function ItemPill({ pill, onClick }) {
  return (
    <button
      onClick={onClick}
      title={pill.titleAttr || pill.title}
      className={`w-full rounded border px-1.5 py-1 text-left text-xs leading-tight ${pillStyle(pill.kind)} hover:opacity-80 transition-opacity`}
    >
      <div className="flex items-center gap-1">
        <pill.icon className="h-3 w-3 shrink-0" />
        <span className="font-medium truncate">{pill.title}</span>
      </div>
      {pill.time && <div className="text-[10px] opacity-75">{pill.time}</div>}
    </button>
  );
}

// ─── Calendar grid ───────────────────────────────────────────────────────────

function CalendarGrid({ year, month, itemMap, onItemClick }) {
  const daysInMonth = getDaysInMonth(year, month);
  const firstDay = getFirstDayOfMonth(year, month);
  const today = new Date();
  const todayStr = formatDate(today.getFullYear(), today.getMonth(), today.getDate());

  const weeks = [];
  let currentWeek = new Array(firstDay).fill(null);
  for (let day = 1; day <= daysInMonth; day++) {
    currentWeek.push(day);
    if (currentWeek.length === 7) {
      weeks.push(currentWeek);
      currentWeek = [];
    }
  }
  if (currentWeek.length > 0) {
    while (currentWeek.length < 7) currentWeek.push(null);
    weeks.push(currentWeek);
  }

  return (
    <div className="border rounded-lg overflow-hidden">
      <div className="grid grid-cols-7 bg-gray-50 border-b">
        {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((d) => (
          <div key={d} className="px-2 py-2 text-xs font-medium text-gray-500 text-center">
            {d}
          </div>
        ))}
      </div>

      {weeks.map((week, wi) => (
        <div key={wi} className="grid grid-cols-7 border-b last:border-b-0">
          {week.map((day, di) => {
            if (!day) {
              return <div key={di} className="min-h-[100px] bg-gray-50 border-r last:border-r-0" />;
            }
            const dateStr = formatDate(year, month, day);
            const isToday = dateStr === todayStr;
            const isPast = new Date(dateStr) < new Date(todayStr);
            const items = itemMap.get(dateStr) || [];

            return (
              <div
                key={di}
                className={`min-h-[100px] border-r last:border-r-0 ${isPast ? 'bg-gray-50/50' : 'bg-white'}`}
              >
                <div className="px-2 pt-1">
                  <span className={`text-xs font-medium ${isToday ? 'bg-indigo-600 text-white rounded-full w-6 h-6 flex items-center justify-center' : isPast ? 'text-gray-400' : 'text-gray-700'}`}>
                    {day}
                  </span>
                </div>
                <div className="px-1 pb-1 space-y-0.5 mt-0.5">
                  {items.map((item) => (
                    <ItemPill key={`${item.recordType}-${item.id}`} pill={item.pill} onClick={() => onItemClick(item)} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

// ─── Page ────────────────────────────────────────────────────────────────────

export default function AdminEditorialCalendar() {
  const { token } = useAdminAuth();
  const [currentDate, setCurrentDate] = useState(() => {
    const now = new Date();
    return { year: now.getFullYear(), month: now.getMonth() };
  });
  const [data, setData] = useState({ articles: [], instagram_posts: [] });
  const [drawer, setDrawer] = useState(null); // { record, recordType }
  const { toasts, show: showToast, dismiss: dismissToast } = useToast();

  const { year, month } = currentDate;
  const monthLabel = new Date(year, month).toLocaleString('default', { month: 'long', year: 'numeric' });

  const fetchCalendar = useCallback(() => {
    adminService.getEditorialCalendar(token, year, month + 1)
      .then(setData)
      .catch((err) => {
        console.error('Failed to load editorial calendar:', err);
        showToast({ type: 'error', title: 'Failed to load calendar', message: err.message || 'Unexpected error' });
      });
  }, [token, year, month, showToast]);

  useEffect(() => { (async () => { fetchCalendar(); })(); }, [fetchCalendar]);

  // date -> ordered pills (articles first, then IG posts, by time)
  const itemMap = useMemo(() => {
    const map = new Map();
    const push = (day, item) => {
      if (!map.has(day)) map.set(day, []);
      map.get(day).push(item);
    };
    const timeLabel = (iso) =>
      new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

    for (const a of data.articles) {
      push(localDay(a.published_at), {
        id: a.id,
        recordType: 'Article',
        when: a.published_at,
        pill: {
          icon: NewspaperIcon,
          title: a.title,
          time: timeLabel(a.published_at),
          kind: a.is_live ? 'live' : 'future',
          titleAttr: a.is_live ? `${a.title} — live on the blog` : `${a.title} — scheduled`,
        },
      });
    }
    for (const p of data.instagram_posts) {
      const kind = p.state === 'published' ? 'live' : p.state === 'failed' ? 'failed' : 'future';
      push(localDay(p.when), {
        id: p.id,
        recordType: 'InstagramPost',
        when: p.when,
        pill: {
          icon: CameraIcon,
          title: p.title,
          time: timeLabel(p.when),
          kind,
          titleAttr: `${p.title} — Instagram ${p.state}`,
        },
      });
    }
    for (const items of map.values()) {
      items.sort((x, y) => new Date(x.when) - new Date(y.when));
    }
    return map;
  }, [data]);

  const navigate = (delta) => {
    setCurrentDate((prev) => {
      let m = prev.month + delta;
      let y = prev.year;
      if (m < 0) { m = 11; y--; }
      if (m > 11) { m = 0; y++; }
      return { year: y, month: m };
    });
  };

  const goToday = () => {
    const now = new Date();
    setCurrentDate({ year: now.getFullYear(), month: now.getMonth() });
  };

  const total = data.articles.length + data.instagram_posts.length;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Editorial Calendar</h1>
          <p className="mt-1 text-sm text-gray-500">
            {total} publication{total !== 1 ? 's' : ''} this month — blog articles and Instagram posts
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={goToday}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
          >
            Today
          </button>
          <button
            onClick={() => navigate(-1)}
            className="rounded-md border border-gray-300 p-1.5 hover:bg-gray-50"
          >
            <ChevronLeftIcon className="h-4 w-4" />
          </button>
          <span className="text-sm font-semibold text-gray-900 min-w-[140px] text-center">
            {monthLabel}
          </span>
          <button
            onClick={() => navigate(1)}
            className="rounded-md border border-gray-300 p-1.5 hover:bg-gray-50"
          >
            <ChevronRightIcon className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Legend */}
      <div className="mb-4 flex items-center gap-4 text-xs text-gray-600">
        <span className="flex items-center gap-1.5">
          <span className="h-3 w-3 rounded border bg-green-100 border-green-300" /> Live
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-3 w-3 rounded border bg-orange-100 border-orange-300" /> Scheduled
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-3 w-3 rounded border bg-red-100 border-red-300" /> Failed
        </span>
        <span className="flex items-center gap-1.5">
          <NewspaperIcon className="h-3.5 w-3.5" /> Blog article
        </span>
        <span className="flex items-center gap-1.5">
          <CameraIcon className="h-3.5 w-3.5" /> Instagram post
        </span>
      </div>

      <CalendarGrid
        year={year}
        month={month}
        itemMap={itemMap}
        onItemClick={(item) => setDrawer({ record: { id: item.id }, recordType: item.recordType })}
      />

      <EditDrawer
        open={drawer !== null}
        onClose={() => {
          setDrawer(null);
          fetchCalendar();
        }}
        record={drawer?.record || null}
        recordType={drawer?.recordType || 'Article'}
        token={token}
      />

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
