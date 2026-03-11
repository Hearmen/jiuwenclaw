/**
 * CronPanel 组件
 *
 * 定时任务面板，使用 cron 表达式管理定时任务
 */

import { useState, useEffect, useCallback } from 'react';
import { webRequest } from '../../services/webClient';


interface CronJob {
  id: string;
  name: string;
  enabled: boolean;
  cron_expr: string;
  timezone: string;
  wake_offset_seconds: number;
  description?: string;
  targets: string;
  created_at: string;
  updated_at: string;
}

// 更新任务的类型，继承自CronJob并将部分字段设置为可选
interface UpdateCronJob {
  id: string;
  name: string;
  enabled: boolean;
  cron_expr: string;
  timezone: string;
  wake_offset_seconds: number;
  description?: string;
  targets: string;
  created_at?: string;
  updated_at?: string;
}

export default function CronPanel() {
  const [cronJobs, setCronJobs] = useState<CronJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [newJob, setNewJob] = useState({
    name: '',
    enabled: true,
    cron_expr: '',
    timezone: 'Asia/Shanghai',
    wake_offset_seconds: 0,
    description: '',
    targets: 'web'
  });
  const [isCreating, setIsCreating] = useState(false);
  const [editingJobId, setEditingJobId] = useState<string | null>(null);
  const [editJob, setEditJob] = useState<UpdateCronJob | null>(null);

  // 时区选项
  const timezoneOptions = [
    { value: 'Asia/Shanghai', label: 'Asia/Shanghai' },
    { value: 'Asia/Bangkok', label: 'Asia/Bangkok' },
    { value: 'Asia/Tokyo', label: 'Asia/Tokyo' },
    { value: 'Asia/Seoul', label: 'Asia/Seoul' },
    { value: 'Asia/Singapore', label: 'Asia/Singapore' },
    { value: 'Europe/London', label: 'Europe/London' },
    { value: 'Europe/Paris', label: 'Europe/Paris' },
    { value: 'America/New_York', label: 'America/New_York' },
    { value: 'America/Los_Angeles', label: 'America/Los_Angeles' },
    { value: 'America/Chicago', label: 'America/Chicago' }
  ];

  // 目标选项
  const targetOptions = [
    { value: 'web', label: '网页 (web)' },
    { value: 'feishu', label: '飞书 (feishu)' },
    { value: 'xiaoyi', label: '小艺 (xiaoyi)' }
  ];

  // 加载任务列表
  const loadJobs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await webRequest<{ jobs: CronJob[] }>('cron.job.list');
      setCronJobs(payload.jobs || []);
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : '加载定时任务失败';
      setError(message);
      // 加载失败时使用空数组
      setCronJobs([]);
    } finally {
      setLoading(false);
    }
  }, []);

  // 初始化加载
  useEffect(() => {
    void loadJobs();
  }, [loadJobs]);

  // 成功消息自动消失
  useEffect(() => {
    if (!success) return;
    const timer = window.setTimeout(() => {
      setSuccess(null);
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [success]);

  // 创建任务
  const handleCreateJob = async () => {
    // 检查必填字段
    if (!newJob.name) {
      setError('任务名称不能为空');
      return;
    }
    if (!newJob.cron_expr) {
      setError('Cron 表达式不能为空');
      return;
    }
    if (!newJob.timezone) {
      setError('时区不能为空');
      return;
    }
    if (!newJob.targets) {
      setError('推送频道不能为空');
      return;
    }
    if (!newJob.description) {
      setError('描述不能为空');
      return;
    }

    try {
      await webRequest<{ job: CronJob }>('cron.job.create', newJob);
      setSuccess('任务创建成功');
      setIsCreating(false);
      setNewJob({
        name: '',
        enabled: true,
        cron_expr: '',
        timezone: 'Asia/Shanghai',
        wake_offset_seconds: 0,
        description: '',
        targets: 'web'
      });
      await loadJobs();
    } catch (createError) {
      const message = createError instanceof Error ? createError.message : '创建任务失败';
      setError(message);
    }
  };

  // 切换任务状态
  const handleToggleJob = async (id: string, enabled: boolean) => {
    try {
      await webRequest<{ job: CronJob }>('cron.job.toggle', {
        id,
        enabled: !enabled
      });
      setSuccess('任务状态更新成功');
      await loadJobs();
    } catch (toggleError) {
      const message = toggleError instanceof Error ? toggleError.message : '更新任务状态失败';
      setError(message);
    }
  };

  // 删除任务
  const handleDeleteJob = async (id: string) => {
    if (!window.confirm('确定要删除这个任务吗？')) return;

    try {
      await webRequest<{ deleted: boolean }>('cron.job.delete', { id });
      setSuccess('任务删除成功');
      await loadJobs();
    } catch (deleteError) {
      const message = deleteError instanceof Error ? deleteError.message : '删除任务失败';
      setError(message);
    }
  };



  // 准备更新任务
  const handleUpdateJob = async (id: string) => {
    try {
      const payload = await webRequest<{ job: CronJob }>('cron.job.get', { id });
      setEditJob(payload.job as UpdateCronJob);
      setEditingJobId(id);
    } catch (viewError) {
      const message = viewError instanceof Error ? viewError.message : '获取任务详情失败';
      setError(message);
    }
  };

  // 执行更新任务
  const handleSubmitUpdate = async () => {
    if (!editJob) return;

    // 检查必填字段
    if (!editJob.name) {
      setError('任务名称不能为空');
      return;
    }
    if (!editJob.cron_expr) {
      setError('Cron 表达式不能为空');
      return;
    }
    if (!editJob.timezone) {
      setError('时区不能为空');
      return;
    }
    if (!editJob.targets) {
      setError('推送频道不能为空');
      return;
    }
    if (!editJob.description) {
      setError('描述不能为空');
      return;
    }

    try {
      // 准备更新数据，将除id外的参数用patch包起来
      const updateData: Record<string, unknown> = {
        id: editJob.id,
        patch: {
          name: editJob.name,
          enabled: editJob.enabled,
          cron_expr: editJob.cron_expr,
          timezone: editJob.timezone,
          wake_offset_seconds: editJob.wake_offset_seconds,
          description: editJob.description,
          targets: editJob.targets
        }
      };
      
      await webRequest<{ job: CronJob }>('cron.job.update', updateData);
      setSuccess('任务更新成功');
      setEditingJobId(null);
      setEditJob(null);
      await loadJobs();
    } catch (updateError) {
      const message = updateError instanceof Error ? updateError.message : '更新任务失败';
      setError(message);
    }
  };

  return (
    <div className="flex-1 min-h-0 relative">
      {success && (
        <div className="pointer-events-none absolute top-3 left-1/2 -translate-x-1/2 z-20">
          <div className="bg-ok text-white px-4 py-2 rounded-lg shadow-lg animate-rise text-sm">
            {success}
          </div>
        </div>
      )}
      
      <div className="card w-full h-full flex flex-col">
        <div className="flex items-center justify-between gap-4 mb-4">
          <div>
            <h2 className="text-lg font-semibold">定时任务管理</h2>
            <p className="text-sm text-text-muted mt-1">使用 cron 表达式管理定时任务，支持添加、启用和删除操作</p>
          </div>
          <button
            onClick={() => setIsCreating(!isCreating)}
            className="btn primary !px-4 !py-2"
          >
            {isCreating ? '取消创建' : '创建任务'}
          </button>
        </div>

        <div className="flex-1 min-h-0">
          {error && (
            <div className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-danger mb-4">
              {error}
            </div>
          )}

          {loading ? (
            <div className="rounded-lg border border-border bg-secondary/30 px-3 py-4 flex items-center justify-center">
              正在加载任务列表...
            </div>
          ) : (
            <div className="overflow-auto rounded-lg border border-border">
              <table className="w-full border-collapse">
                <thead>
                  <tr className="border-b border-border">
                    <th className="px-4 py-3 text-left text-sm font-medium text-text-muted w-[120px]">任务名称</th>
                    <th className="px-4 py-3 text-left text-sm font-medium text-text-muted w-[400px]">Cron 表达式</th>
                    <th className="px-4 py-3 text-left text-sm font-medium text-text-muted">状态</th>
                    <th className="px-4 py-3 text-left text-sm font-medium text-text-muted">描述</th>
                    <th className="px-4 py-3 text-left text-sm font-medium text-text-muted w-[120px]">唤醒偏移秒数</th>
                    <th className="px-4 py-3 text-left text-sm font-medium text-text-muted">时区</th>
                    <th className="px-4 py-3 text-left text-sm font-medium text-text-muted">推送频道</th>
                    <th className="px-4 py-3 text-right text-sm font-medium text-text-muted">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {/* 创建任务行 */}
                  {isCreating && (
                    <tr className="border-b border-border bg-secondary/10">
                      <td className="px-4 py-3">
                        <input
                          type="text"
                          value={newJob.name}
                          onChange={(e) => setNewJob({ ...newJob, name: e.target.value })}
                          className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] text-text outline-none focus:border-accent"
                          placeholder="输入任务名称"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <input
                          type="text"
                          value={newJob.cron_expr}
                          onChange={(e) => setNewJob({ ...newJob, cron_expr: e.target.value })}
                          className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] text-text outline-none focus:border-accent"
                          placeholder="5段式：分 时 日 月 周。例：每周一11时58分='58 11 * * 1'"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-between">
                          <span className="text-sm">{newJob.enabled ? '启用' : '禁用'}</span>
                          <div 
                            className="relative inline-block w-10 h-6 align-middle select-none rounded-full cursor-pointer"
                            onClick={() => setNewJob({ ...newJob, enabled: !newJob.enabled })}
                            style={{ backgroundColor: newJob.enabled ? '#10b981' : '#d1d5db' }}
                          >
                            <div 
                              className="absolute left-1 top-1 h-4 w-4 rounded-full bg-white transition-transform"
                              style={{ transform: newJob.enabled ? 'translateX(16px)' : 'none' }}
                            ></div>
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <input
                          type="text"
                          value={newJob.description}
                          onChange={(e) => setNewJob({ ...newJob, description: e.target.value })}
                          className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] text-text outline-none focus:border-accent"
                          placeholder="输入描述"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <input
                          type="number"
                          value={newJob.wake_offset_seconds}
                          onChange={(e) => setNewJob({ ...newJob, wake_offset_seconds: parseInt(e.target.value) || 0 })}
                          className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] text-text outline-none focus:border-accent"
                          placeholder="唤醒偏移秒数"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <select
                          value={newJob.timezone}
                          onChange={(e) => setNewJob({ ...newJob, timezone: e.target.value })}
                          className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] text-text outline-none focus:border-accent"
                        >
                          {timezoneOptions.map(option => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="px-4 py-3">
                        <select
                          value={newJob.targets}
                          onChange={(e) => setNewJob({ ...newJob, targets: e.target.value })}
                          className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] text-text outline-none focus:border-accent"
                        >
                          {targetOptions.map(option => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className="flex items-center justify-end gap-2">
                          <button
                            onClick={() => {
                              setIsCreating(false);
                              setNewJob({
                                name: '',
                                enabled: true,
                                cron_expr: '',
                                timezone: 'Asia/Shanghai',
                                wake_offset_seconds: 0,
                                description: '',
                                targets: 'web'
                              });
                            }}
                            className="px-2 py-1 text-sm border border-gray-300 bg-white text-gray-600 rounded hover:bg-gray-50"
                          >
                            取消
                          </button>
                          <button
                            onClick={handleCreateJob}
                            className="px-2 py-1 text-sm bg-blue-500 text-white rounded hover:bg-blue-600"
                          >
                            创建
                          </button>
                        </div>
                      </td>
                    </tr>
                  )}

                  {/* 编辑任务行 */}
                  {editingJobId && editJob && (
                    <tr className="border-b border-border bg-secondary/10">
                      <td className="px-4 py-3">
                        <input
                          type="text"
                          value={editJob.name}
                          onChange={(e) => setEditJob({ ...editJob, name: e.target.value })}
                          className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] text-text outline-none focus:border-accent"
                          placeholder="输入任务名称"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <input
                          type="text"
                          value={editJob.cron_expr}
                          onChange={(e) => setEditJob({ ...editJob, cron_expr: e.target.value })}
                          className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] text-text outline-none focus:border-accent"
                          placeholder="例：58 11 * * 1"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-between">
                          <span className="text-sm">{editJob.enabled ? '启用' : '禁用'}</span>
                          <div 
                            className="relative inline-block w-10 h-6 align-middle select-none rounded-full cursor-pointer"
                            onClick={() => setEditJob({ ...editJob, enabled: !editJob.enabled })}
                            style={{ backgroundColor: editJob.enabled ? '#10b981' : '#d1d5db' }}
                          >
                            <div 
                              className="absolute left-1 top-1 h-4 w-4 rounded-full bg-white transition-transform"
                              style={{ transform: editJob.enabled ? 'translateX(16px)' : 'none' }}
                            ></div>
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <input
                          type="text"
                          value={editJob.description || ''}
                          onChange={(e) => setEditJob({ ...editJob, description: e.target.value })}
                          className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] text-text outline-none focus:border-accent"
                          placeholder="输入描述"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <input
                          type="number"
                          value={editJob.wake_offset_seconds}
                          onChange={(e) => setEditJob({ ...editJob, wake_offset_seconds: parseInt(e.target.value) || 0 })}
                          className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] text-text outline-none focus:border-accent"
                          placeholder="唤醒偏移秒数"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <select
                          value={editJob.timezone}
                          onChange={(e) => setEditJob({ ...editJob, timezone: e.target.value })}
                          className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] text-text outline-none focus:border-accent"
                        >
                          {timezoneOptions.map(option => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="px-4 py-3">
                        <select
                          value={editJob.targets}
                          onChange={(e) => setEditJob({ ...editJob, targets: e.target.value })}
                          className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] text-text outline-none focus:border-accent"
                        >
                          {targetOptions.map(option => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className="flex items-center justify-end gap-2">
                          <button
                            onClick={() => {
                              setEditingJobId(null);
                              setEditJob(null);
                            }}
                            className="px-2 py-1 text-sm border border-gray-300 bg-white text-gray-600 rounded hover:bg-gray-50"
                          >
                            取消
                          </button>
                          <button
                            onClick={handleSubmitUpdate}
                            className="px-2 py-1 text-sm bg-blue-500 text-white rounded hover:bg-blue-600"
                          >
                            更新
                          </button>
                        </div>
                      </td>
                    </tr>
                  )}

                  {/* 任务列表 */}
                  {cronJobs.length === 0 ? (
                    <tr>
                      <td colSpan={8} className="px-4 py-8 text-center text-text-muted">
                        暂无定时任务
                      </td>
                    </tr>
                  ) : (
                    cronJobs.map(job => (
                      <tr key={job.id} className="border-b border-border hover:bg-secondary/10">
                        <td className="px-4 py-3 text-sm">
                          <div className="max-w-[100px] overflow-hidden text-ellipsis whitespace-nowrap" title={job.name}>
                            {job.name}
                          </div>
                        </td>
                        <td className="px-4 py-3 text-sm mono">{job.cron_expr}</td>
                        <td className="px-4 py-3">
                          <span className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${job.enabled ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-700'}`}>
                            {job.enabled ? '启用' : '禁用'}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-sm text-text-muted">
                          <div className="max-w-[200px] overflow-hidden text-ellipsis whitespace-nowrap" title={job.description || '-'}>
                            {job.description || '-'}
                          </div>
                        </td>
                        <td className="px-4 py-3 text-sm text-text-muted">
                          {job.wake_offset_seconds}
                        </td>
                        <td className="px-4 py-3 text-sm text-text-muted">
                          {job.timezone}
                        </td>
                        <td className="px-4 py-3 text-sm text-text-muted">
                          {job.targets || '-'}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <div className="flex items-center justify-end gap-2">
                            <button
                              onClick={() => handleToggleJob(job.id, job.enabled)}
                              className={`px-2 py-1 text-sm rounded hover:bg-opacity-90 ${job.enabled ? 'bg-red-500 text-white hover:bg-red-600' : 'bg-blue-500 text-white'}`}
                            >
                              {job.enabled ? '停用' : '启动'}
                            </button>
                            <button
                              onClick={() => handleUpdateJob(job.id)}
                              className="px-2 py-1 text-sm bg-blue-500 text-white rounded hover:bg-blue-600"
                            >
                              更新
                            </button>
                            <button
                              onClick={() => handleDeleteJob(job.id)}
                              className="px-2 py-1 text-sm bg-blue-500 text-white rounded hover:bg-blue-600"
                            >
                              删除
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>


    </div>
  );
}