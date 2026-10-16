import { useState, useEffect, useMemo, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { useAdminAuth } from '../../contexts/AdminAuthContext';
import { adminService } from '../../services/admin';
import { ToastContainer } from '../../components/admin/Toast';
import { useToast } from '../../hooks/useToast';

// Deliberately a plain grouped list rather than an AG Grid: the interesting
// structure here is the per-subject grouping, and row grouping is an AG Grid
// Enterprise feature (the repo ships AllCommunityModule only).

const statusColors = {
  PENDING: 'text-gray-600 bg-gray-50 ring-gray-500/10',
  READY: 'text-blue-700 bg-blue-50 ring-blue-600/20',
  LEASED: 'text-indigo-700 bg-indigo-50 ring-indigo-600/20',
  WAITING: 'text-amber-700 bg-amber-50 ring-amber-600/20',
  DONE: 'text-green-700 bg-green-50 ring-green-600/20',
  FAILED: 'text-red-700 bg-red-50 ring-red-600/20',
  SKIPPED: 'text-orange-700 bg-orange-50 ring-orange-600/20',
};

const EXPIRED_COLORS = 'text-red-700 bg-red-50 ring-red-600/20';

// Where "Open" sends the operator. There are no per-id admin editor routes, so
// each subject links to its existing grid page.
const SUBJECT_ROUTES = {
  ARTICLE: '/admin/articles',
  RESEARCH: '/admin/research',
  INSTAGRAM_POST: '/admin/instagram',
};

const SUBJECT_LABELS = {
  ARTICLE: 'Article',
  RESEARCH: 'Research',
  INSTAGRAM_POST: 'Instagram post',
};

// A LEASED row whose lease has run out is the "stuck" case the issue cares
// about: the driver died mid-task and nothing will report it until a sweep.
function isStuck(task) {
  return (
    task.status === 'LEASED'
    && !!task.lease_expires_at
    && Date.parse(task.lease_expires_at) < Date.now()
  );
}

function Pill({ children, className, title }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset ${className}`}
    >
      {children}
    </span>
  );
}

function StatusPill({ task }) {
  const stuck = isStuck(task);
  const colors = stuck ? EXPIRED_COLORS : (statusColors[task.status] || statusColors.PENDING);
  const pulse = task.status === 'LEASED' && !stuck ? ' animate-pulse' : '';
  let title;
  if (stuck) title = 'lease expired';
  else if (task.status === 'WAITING' && task.next_check_at) {
    title = `Rechecked after ${new Date(task.next_check_at).toLocaleString()}`;
  }
  return (
    <Pill className={colors + pulse} title={title}>
      {task.status}
    </Pill>
  );
}

function TaskRow({ task, onRetry, onSkip }) {
  const canSkip = ['PENDING', 'READY', 'FAILED', 'WAITING'].includes(task.status);
  const waiting = task.status === 'WAITING';
  return (
    <tr className="border-t border-gray-100 align-top">
      <td className="px-3 py-2 text-sm text-gray-400 tabular-nums">{task.position}</td>
      <td className="px-3 py-2 text-sm font-medium text-gray-900">{task.kind}</td>
      <td className="px-3 py-2"><StatusPill task={task} /></td>
      <td className="px-3 py-2 text-sm text-gray-500 tabular-nums">
        {task.attempts > 0 ? task.attempts : ''}
      </td>
      <td className="px-3 py-2 text-sm text-gray-500">
        {task.status === 'LEASED' && task.leased_by ? (
          <span title={task.lease_expires_at ? `Lease expires ${new Date(task.lease_expires_at).toLocaleString()}` : undefined}>
            {task.leased_by}
          </span>
        ) : ''}
      </td>
      {waiting ? (
        <td className="px-3 py-2 text-sm text-amber-700 max-w-md truncate" title={task.waiting_for || undefined}>
          {task.waiting_for ? `Waiting for: ${task.waiting_for}` : ''}
        </td>
      ) : (
        <td className="px-3 py-2 text-sm text-red-600 max-w-md truncate" title={task.error || undefined}>
          {task.error || ''}
        </td>
      )}
      <td className="px-3 py-2">
        <div className="flex items-center justify-end gap-2">
          {(task.status === 'FAILED' || waiting) && (
            <button
              onClick={() => onRetry(task)}
              title={waiting ? 'Re-check on the next driver call instead of waiting for the recheck time' : undefined}
              className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50"
            >
              {waiting ? 'Check now' : 'Retry'}
            </button>
          )}
          {canSkip && (
            <button
              onClick={() => onSkip(task)}
              className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50"
            >
              Skip
            </button>
          )}
          <Link
            to={SUBJECT_ROUTES[task.subject_type] || '/admin/dashboard'}
            className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50"
          >
            Open
          </Link>
        </div>
      </td>
    </tr>
  );
}

export default function AdminAgentTasks() {
  const { token } = useAdminAuth();
  const [tasks, setTasks] = useState([]);
  const [selectedGoal, setSelectedGoal] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [planning, setPlanning] = useState(false);
  const { toasts, show: showToast, dismiss: dismissToast } = useToast();

  const refresh = useCallback(
    () => adminService.getAgentTasks(token).then(setTasks),
    [token],
  );

  useEffect(() => {
    refresh()
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [refresh]);

  // Read-mostly page: a 30s tick is enough to watch a Saturday run and is the
  // tightest interval the issue allows.
  useEffect(() => {
    const id = setInterval(() => {
      refresh().catch(() => {
        // A transient poll failure shouldn't nag; the manual Refresh reports.
      });
    }, 30000);
    return () => clearInterval(id);
  }, [refresh]);

  // The API already orders goal_key desc, position asc — so this is newest-first.
  const goalKeys = useMemo(() => [...new Set(tasks.map((t) => t.goal_key))], [tasks]);
  const activeGoal = selectedGoal && goalKeys.includes(selectedGoal) ? selectedGoal : goalKeys[0];
  const goalTasks = useMemo(
    () => tasks.filter((t) => t.goal_key === activeGoal),
    [tasks, activeGoal],
  );

  const summary = useMemo(() => {
    const research = goalTasks.find((t) => t.subject_type === 'RESEARCH');
    return {
      total: goalTasks.length,
      done: goalTasks.filter((t) => t.status === 'DONE').length,
      failed: goalTasks.filter((t) => t.status === 'FAILED').length,
      waiting: goalTasks.filter((t) => t.status === 'WAITING').length,
      stuck: goalTasks.filter(isStuck).length,
      researchId: research ? research.subject_id : null,
    };
  }, [goalTasks]);

  // Consecutive runs of the same subject, in position order — the graph reads
  // as "everything for research N, then everything for article M".
  const groups = useMemo(() => {
    const out = [];
    for (const task of goalTasks) {
      const key = `${task.subject_type}:${task.subject_id}`;
      const last = out[out.length - 1];
      if (last && last.key === key) last.tasks.push(task);
      else out.push({ key, subject_type: task.subject_type, subject_id: task.subject_id, tasks: [task] });
    }
    return out;
  }, [goalTasks]);

  const handlePlanWeek = useCallback(async () => {
    setPlanning(true);
    try {
      const res = await adminService.planWeek(token);
      if (res.at_capacity) {
        // #559: enough series unfinished — nothing was planned, so there is
        // no new goal to select.
        showToast({
          type: 'info',
          title: 'At capacity',
          message: res.detail || `${res.in_flight_goals.length} series already in flight`,
        });
        await refresh();
        return;
      }
      showToast({
        type: 'success',
        title: 'Week planned',
        message: `Planned ${res.goal_key}: ${res.created} task(s) created`,
      });
      setSelectedGoal(res.goal_key);
      await refresh();
    } catch (err) {
      showToast({ type: 'error', title: 'Plan week failed', message: err.message || 'Unexpected error' });
    } finally {
      setPlanning(false);
    }
  }, [token, refresh, showToast]);

  const handleRefresh = useCallback(async () => {
    try {
      await refresh();
    } catch (err) {
      showToast({ type: 'error', title: 'Failed to refresh', message: err.message || 'Unexpected error' });
    }
  }, [refresh, showToast]);

  const handleRetry = useCallback(async (task) => {
    try {
      await adminService.agentTaskAction(token, task.id, 'retry');
      showToast({ type: 'success', title: 'Task requeued', message: `${task.kind} is READY again` });
      await refresh();
    } catch (err) {
      showToast({ type: 'error', title: 'Retry failed', message: err.message || 'Unexpected error' });
    }
  }, [token, refresh, showToast]);

  const handleSkip = useCallback(async (task) => {
    if (!window.confirm(`Skip "${task.kind}"? Its dependents will be unblocked and it will never run.`)) return;
    try {
      const res = await adminService.agentTaskAction(token, task.id, 'skip');
      const promoted = res.promoted?.length || 0;
      showToast({
        type: 'success',
        title: 'Task skipped',
        message: promoted > 0 ? `${promoted} task(s) promoted to READY` : undefined,
      });
      await refresh();
    } catch (err) {
      showToast({ type: 'error', title: 'Skip failed', message: err.message || 'Unexpected error' });
    }
  }, [token, refresh, showToast]);

  if (loading) return <div className="text-gray-500">Loading agent tasks...</div>;
  if (error) return <div className="text-red-600">Error: {error}</div>;

  const planButton = (
    <button
      onClick={handlePlanWeek}
      disabled={planning}
      className="rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
    >
      {planning ? 'Planning...' : 'Plan This Week'}
    </button>
  );

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Agent Tasks</h1>
          <p className="mt-2 text-sm text-gray-700">
            {tasks.length} tasks · {goalKeys.length} goal(s)
          </p>
        </div>
        <div className="flex items-center gap-3">
          {goalKeys.length > 0 && (
            <select
              value={activeGoal || ''}
              onChange={(e) => setSelectedGoal(e.target.value)}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 focus:border-indigo-500 focus:ring-indigo-500"
            >
              {goalKeys.map((key) => (
                <option key={key} value={key}>{key}</option>
              ))}
            </select>
          )}
          <button
            onClick={handleRefresh}
            className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
          >
            Refresh
          </button>
          {planButton}
        </div>
      </div>

      {tasks.length === 0 ? (
        <div className="mt-16 text-center">
          <p className="text-sm text-gray-500">
            No agent tasks yet. Plan this week to create the first goal.
          </p>
        </div>
      ) : (
        <>
          <div className="mt-6 rounded-lg bg-white p-4 shadow-xs ring-1 ring-gray-200">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
              <span className="font-mono text-sm font-semibold text-gray-900">{activeGoal}</span>
              <span className="text-sm text-gray-500">
                {summary.researchId ? `Research #${summary.researchId}` : '—'}
              </span>
              <Pill className="text-gray-700 bg-gray-50 ring-gray-500/10">
                {summary.done}/{summary.total} done
              </Pill>
              <Pill
                className={summary.failed > 0 ? statusColors.FAILED : 'text-gray-600 bg-gray-50 ring-gray-500/10'}
              >
                {summary.failed} failed
              </Pill>
              <Pill
                className={summary.waiting > 0 ? statusColors.WAITING : 'text-gray-600 bg-gray-50 ring-gray-500/10'}
                title="Tasks waiting on operator work (deep research, Grammarly)"
              >
                {summary.waiting} waiting
              </Pill>
              <Pill
                className={summary.stuck > 0 ? statusColors.FAILED : 'text-gray-600 bg-gray-50 ring-gray-500/10'}
                title="LEASED tasks whose lease has expired"
              >
                {summary.stuck} stuck
              </Pill>
            </div>
          </div>

          <div className="mt-6 space-y-6">
            {groups.map((group) => (
              <div key={group.key} className="overflow-hidden rounded-lg bg-white shadow-xs ring-1 ring-gray-200">
                <div className="flex items-center justify-between bg-gray-50 px-4 py-2">
                  <h2 className="text-sm font-semibold text-gray-900">
                    {SUBJECT_LABELS[group.subject_type] || group.subject_type} #{group.subject_id}
                  </h2>
                  <span className="text-xs text-gray-500">{group.tasks.length} task(s)</span>
                </div>
                <div className="overflow-x-auto">
                  <table className="min-w-full">
                    <thead>
                      <tr className="text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                        <th className="px-3 py-2">#</th>
                        <th className="px-3 py-2">Kind</th>
                        <th className="px-3 py-2">Status</th>
                        <th className="px-3 py-2">Attempts</th>
                        <th className="px-3 py-2">Leased by</th>
                        <th className="px-3 py-2">Error / waiting for</th>
                        <th className="px-3 py-2 text-right">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {group.tasks.map((task) => (
                        <TaskRow key={task.id} task={task} onRetry={handleRetry} onSkip={handleSkip} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
