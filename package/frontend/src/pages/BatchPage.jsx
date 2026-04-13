import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import {
  ArrowLeft, Play, RotateCcw, Download, FileText,
  CheckCircle, AlertCircle, Clock, Loader2, List, ChevronDown, ChevronUp
} from 'lucide-react';
import { batchAPI } from '../api';

// ─── Status badge helper ─────────────────────────────────
const statusConfig = {
  pending:        { label: '待处理', color: 'bg-gray-100 text-gray-600' },
  running:        { label: '执行中', color: 'bg-blue-100 text-blue-600' },
  done:           { label: '完成',   color: 'bg-green-100 text-green-700' },
  completed:      { label: '完成',   color: 'bg-green-100 text-green-700' },
  failed:         { label: '失败',   color: 'bg-red-100 text-red-600' },
  partial_failed: { label: '部分失败', color: 'bg-orange-100 text-orange-600' },
  retrying:       { label: '重试中', color: 'bg-yellow-100 text-yellow-700' },
  cancelled:      { label: '已取消', color: 'bg-gray-200 text-gray-500' },
};

const StatusBadge = ({ status }) => {
  const cfg = statusConfig[status] || { label: status, color: 'bg-gray-100 text-gray-600' };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${cfg.color}`}>
      {cfg.label}
    </span>
  );
};

// ─── Progress bar ────────────────────────────────────────
const ProgressBar = ({ progress }) => (
  <div className="w-full bg-gray-200 rounded-full h-2.5">
    <div
      className="bg-blue-500 h-2.5 rounded-full transition-all"
      style={{ width: `${Math.min(progress, 100)}%` }}
    />
  </div>
);

// ─── Main page ───────────────────────────────────────────
const BatchPage = () => {
  const navigate = useNavigate();

  // ── state ──
  const [view, setView] = useState('list');           // list | create | detail
  const [batches, setBatches] = useState([]);
  const [loading, setLoading] = useState(false);

  // create form
  const [name, setName] = useState('');
  const [rawText, setRawText] = useState('');
  const [taskTypes, setTaskTypes] = useState(['polish']);
  const [skipThreshold, setSkipThreshold] = useState('');

  // detail
  const [currentBatch, setCurrentBatch] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [taskTotal, setTaskTotal] = useState(0);
  const [taskPage, setTaskPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState('');
  const [expandedTask, setExpandedTask] = useState(null);

  // ── load batches ──
  const loadBatches = useCallback(async () => {
    setLoading(true);
    try {
      const res = await batchAPI.listBatches();
      setBatches(res.data);
    } catch (err) {
      toast.error('加载批次列表失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadBatches(); }, [loadBatches]);

  // ── create batch ──
  const handleCreate = async () => {
    if (!name.trim()) { toast.error('请输入批次名称'); return; }
    if (!rawText.trim()) { toast.error('请输入正文'); return; }
    if (taskTypes.length === 0) { toast.error('请至少选择一种任务类型'); return; }

    setLoading(true);
    try {
      const payload = {
        name: name.trim(),
        raw_text: rawText,
        task_types: taskTypes,
      };
      if (skipThreshold !== '') {
        payload.skip_short_threshold = parseInt(skipThreshold, 10);
      }
      const res = await batchAPI.createBatch(payload);
      toast.success(`批次已创建：${res.data.total_segments} 段，${res.data.total_tasks} 个任务`);
      setView('list');
      setName('');
      setRawText('');
      loadBatches();
    } catch (err) {
      toast.error(err.response?.data?.detail || '创建失败');
    } finally {
      setLoading(false);
    }
  };

  // ── open detail ──
  const openDetail = async (batchId) => {
    setLoading(true);
    try {
      const bRes = await batchAPI.getBatch(batchId);
      setCurrentBatch(bRes.data);
      setTaskPage(1);
      setStatusFilter('');
      await loadTasks(batchId, 1, '');
      setView('detail');
    } catch (err) {
      toast.error('加载批次详情失败');
    } finally {
      setLoading(false);
    }
  };

  // ── load tasks ──
  const loadTasks = async (batchId, page = 1, status = '') => {
    try {
      const params = { page, page_size: 50 };
      if (status) params.status = status;
      const res = await batchAPI.listTasks(batchId, params);
      setTasks(res.data.tasks);
      setTaskTotal(res.data.total);
      setTaskPage(page);
    } catch (err) {
      toast.error('加载任务列表失败');
    }
  };

  // ── start batch ──
  const handleStart = async () => {
    if (!currentBatch) return;
    try {
      await batchAPI.startBatch(currentBatch.batch_id);
      toast.success('批次已启动');
      // refresh after short delay
      setTimeout(() => openDetail(currentBatch.batch_id), 1500);
    } catch (err) {
      toast.error(err.response?.data?.detail || '启动失败');
    }
  };

  // ── retry failed ──
  const handleRetryFailed = async () => {
    if (!currentBatch) return;
    try {
      const res = await batchAPI.retryFailed(currentBatch.batch_id);
      toast.success(res.data.message);
      setTimeout(() => openDetail(currentBatch.batch_id), 1500);
    } catch (err) {
      toast.error(err.response?.data?.detail || '重试失败');
    }
  };

  // ── retry single task ──
  const handleRetryTask = async (taskId) => {
    try {
      await batchAPI.retryTask(taskId);
      toast.success('任务已重新排队');
      setTimeout(() => openDetail(currentBatch.batch_id), 1500);
    } catch (err) {
      toast.error(err.response?.data?.detail || '重试失败');
    }
  };

  // ── export ──
  const handleExport = async (fmt) => {
    if (!currentBatch) return;
    try {
      const res = await batchAPI.exportBatch(currentBatch.batch_id, fmt);
      const data = res.data;
      const blob = new Blob(
        [typeof data.content === 'string' ? data.content : JSON.stringify(data.content, null, 2)],
        { type: fmt === 'csv' ? 'text/csv;charset=utf-8;' : 'application/json' },
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = data.filename;
      a.click();
      URL.revokeObjectURL(url);
      toast.success('导出成功');
    } catch (err) {
      toast.error('导出失败');
    }
  };

  // ── auto-refresh detail when running ──
  useEffect(() => {
    if (view !== 'detail' || !currentBatch) return;
    if (!['running', 'pending'].includes(currentBatch.status)) return;
    const iv = setInterval(() => {
      openDetail(currentBatch.batch_id);
    }, 5000);
    return () => clearInterval(iv);
  }, [view, currentBatch?.batch_id, currentBatch?.status]);

  // ── toggle task type ──
  const toggleType = (type) => {
    setTaskTypes(prev =>
      prev.includes(type) ? prev.filter(t => t !== type) : [...prev, type],
    );
  };

  // ────────────────────────────────────────────────────────
  // RENDER
  // ────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-4 py-3 flex items-center gap-3">
          <button onClick={() => {
            if (view === 'detail') { setView('list'); loadBatches(); }
            else if (view === 'create') { setView('list'); }
            else navigate('/workspace');
          }} className="p-1.5 hover:bg-gray-100 rounded-lg">
            <ArrowLeft className="w-5 h-5 text-gray-600" />
          </button>
          <FileText className="w-5 h-5 text-blue-500" />
          <h1 className="text-lg font-semibold text-gray-800">
            {view === 'list' && '批处理任务'}
            {view === 'create' && '新建批次'}
            {view === 'detail' && (currentBatch ? currentBatch.name : '批次详情')}
          </h1>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-4 py-6">

        {/* ──── LIST VIEW ──── */}
        {view === 'list' && (
          <>
            <div className="flex justify-between items-center mb-4">
              <p className="text-sm text-gray-500">管理正文分段批处理任务</p>
              <button
                onClick={() => setView('create')}
                className="flex items-center gap-1.5 px-4 py-2 bg-blue-500 text-white rounded-lg text-sm font-medium hover:bg-blue-600 transition"
              >
                <FileText className="w-4 h-4" /> 新建批次
              </button>
            </div>

            {loading ? (
              <div className="flex justify-center py-12">
                <Loader2 className="w-6 h-6 animate-spin text-blue-500" />
              </div>
            ) : batches.length === 0 ? (
              <div className="text-center py-16 text-gray-400">
                <List className="w-10 h-10 mx-auto mb-3 opacity-50" />
                <p>暂无批次，点击上方按钮创建</p>
              </div>
            ) : (
              <div className="space-y-3">
                {batches.map(b => (
                  <div
                    key={b.batch_id}
                    onClick={() => openDetail(b.batch_id)}
                    className="bg-white rounded-xl p-4 border border-gray-100 hover:border-blue-200 cursor-pointer transition shadow-sm"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-gray-800">{b.name}</span>
                        <span className="text-xs text-gray-400">{b.batch_id}</span>
                      </div>
                      <StatusBadge status={b.status} />
                    </div>
                    <ProgressBar progress={b.progress} />
                    <div className="flex items-center gap-4 mt-2 text-xs text-gray-500">
                      <span>{b.total_segments} 段</span>
                      <span>{b.completed_tasks}/{b.total_tasks} 任务完成</span>
                      {b.failed_tasks > 0 && (
                        <span className="text-red-500">{b.failed_tasks} 失败</span>
                      )}
                      <span className="ml-auto">{new Date(b.created_at).toLocaleString()}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {/* ──── CREATE VIEW ──── */}
        {view === 'create' && (
          <div className="bg-white rounded-xl p-6 border border-gray-100 shadow-sm space-y-5">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">批次名称</label>
              <input
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="例如：毕业论文正文"
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-200 focus:border-blue-400 outline-none"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">正文内容</label>
              <textarea
                value={rawText}
                onChange={e => setRawText(e.target.value)}
                rows={12}
                placeholder="从 Word 中复制正文粘贴于此，系统将按段落自动切分…"
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-200 focus:border-blue-400 outline-none resize-y"
              />
              <p className="text-xs text-gray-400 mt-1">
                将按空行/换行符切分段落，短段落将被跳过
              </p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">任务类型</label>
              <div className="flex gap-3">
                {['polish', 'enhance'].map(type => (
                  <label key={type} className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={taskTypes.includes(type)}
                      onChange={() => toggleType(type)}
                      className="rounded text-blue-500 focus:ring-blue-300"
                    />
                    <span className="text-sm">{type === 'polish' ? '润色' : '增强'}</span>
                  </label>
                ))}
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">跳过短段阈值（可选）</label>
              <input
                value={skipThreshold}
                onChange={e => setSkipThreshold(e.target.value)}
                type="number"
                min={0}
                placeholder="默认使用系统配置"
                className="w-40 border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-200 focus:border-blue-400 outline-none"
              />
            </div>

            <div className="flex justify-end gap-3 pt-2">
              <button onClick={() => setView('list')}
                className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg transition">
                取消
              </button>
              <button
                onClick={handleCreate}
                disabled={loading}
                className="flex items-center gap-1.5 px-5 py-2 bg-blue-500 text-white rounded-lg text-sm font-medium hover:bg-blue-600 disabled:opacity-50 transition"
              >
                {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileText className="w-4 h-4" />}
                创建批次
              </button>
            </div>
          </div>
        )}

        {/* ──── DETAIL VIEW ──── */}
        {view === 'detail' && currentBatch && (
          <div className="space-y-5">
            {/* Summary card */}
            <div className="bg-white rounded-xl p-5 border border-gray-100 shadow-sm">
              <div className="flex items-center justify-between mb-3">
                <div>
                  <span className="text-xs text-gray-400 mr-2">{currentBatch.batch_id}</span>
                  <StatusBadge status={currentBatch.status} />
                </div>
                <div className="flex gap-2">
                  {['pending', 'partial_failed', 'failed'].includes(currentBatch.status) && (
                    <button onClick={handleStart}
                      className="flex items-center gap-1 px-3 py-1.5 bg-blue-500 text-white rounded-lg text-xs font-medium hover:bg-blue-600 transition">
                      <Play className="w-3.5 h-3.5" /> 启动
                    </button>
                  )}
                  {currentBatch.failed_tasks > 0 && (
                    <button onClick={handleRetryFailed}
                      className="flex items-center gap-1 px-3 py-1.5 bg-orange-500 text-white rounded-lg text-xs font-medium hover:bg-orange-600 transition">
                      <RotateCcw className="w-3.5 h-3.5" /> 重试全部失败
                    </button>
                  )}
                  <button onClick={() => handleExport('json')}
                    className="flex items-center gap-1 px-3 py-1.5 border border-gray-200 rounded-lg text-xs text-gray-600 hover:bg-gray-50 transition">
                    <Download className="w-3.5 h-3.5" /> JSON
                  </button>
                  <button onClick={() => handleExport('csv')}
                    className="flex items-center gap-1 px-3 py-1.5 border border-gray-200 rounded-lg text-xs text-gray-600 hover:bg-gray-50 transition">
                    <Download className="w-3.5 h-3.5" /> CSV
                  </button>
                </div>
              </div>

              <ProgressBar progress={currentBatch.progress} />

              <div className="flex items-center gap-6 mt-3 text-sm text-gray-600">
                <span className="flex items-center gap-1"><FileText className="w-4 h-4" /> {currentBatch.total_segments} 段</span>
                <span className="flex items-center gap-1"><CheckCircle className="w-4 h-4 text-green-500" /> {currentBatch.completed_tasks} 完成</span>
                <span className="flex items-center gap-1"><AlertCircle className="w-4 h-4 text-red-500" /> {currentBatch.failed_tasks} 失败</span>
                <span className="flex items-center gap-1"><Clock className="w-4 h-4 text-blue-500" /> {currentBatch.total_tasks - currentBatch.completed_tasks - currentBatch.failed_tasks} 进行中/待处理</span>
              </div>
            </div>

            {/* Task filter */}
            <div className="flex items-center gap-2">
              <span className="text-sm text-gray-500">筛选：</span>
              {['', 'pending', 'running', 'done', 'failed', 'retrying'].map(s => (
                <button
                  key={s}
                  onClick={() => { setStatusFilter(s); loadTasks(currentBatch.batch_id, 1, s); }}
                  className={`px-3 py-1 rounded-full text-xs font-medium transition ${
                    statusFilter === s ? 'bg-blue-500 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
                >
                  {s === '' ? '全部' : (statusConfig[s]?.label || s)}
                </button>
              ))}
              <span className="ml-auto text-xs text-gray-400">共 {taskTotal} 条</span>
            </div>

            {/* Task table */}
            <div className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-gray-500 text-xs">
                  <tr>
                    <th className="px-4 py-2 text-left">#</th>
                    <th className="px-4 py-2 text-left">段落 ID</th>
                    <th className="px-4 py-2 text-left">原文预览</th>
                    <th className="px-4 py-2 text-left">类型</th>
                    <th className="px-4 py-2 text-left">状态</th>
                    <th className="px-4 py-2 text-left">重试</th>
                    <th className="px-4 py-2 text-left">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {tasks.map(task => (
                    <React.Fragment key={task.id}>
                      <tr
                        className="border-t border-gray-50 hover:bg-gray-50 cursor-pointer"
                        onClick={() => setExpandedTask(expandedTask === task.id ? null : task.id)}
                      >
                        <td className="px-4 py-2 text-gray-500">{task.source_index}</td>
                        <td className="px-4 py-2 font-mono text-xs text-gray-600">{task.segment_display_id}</td>
                        <td className="px-4 py-2 text-gray-600 max-w-[200px] truncate">{task.source_preview}</td>
                        <td className="px-4 py-2">{task.task_type === 'polish' ? '润色' : '增强'}</td>
                        <td className="px-4 py-2"><StatusBadge status={task.status} /></td>
                        <td className="px-4 py-2 text-gray-500">{task.retry_count}/{task.max_retries}</td>
                        <td className="px-4 py-2">
                          <div className="flex items-center gap-2">
                            {task.status === 'failed' && (
                              <button
                                onClick={(e) => { e.stopPropagation(); handleRetryTask(task.id); }}
                                className="text-xs text-orange-500 hover:text-orange-600 font-medium"
                              >
                                重试
                              </button>
                            )}
                            {expandedTask === task.id ? (
                              <ChevronUp className="w-4 h-4 text-gray-400" />
                            ) : (
                              <ChevronDown className="w-4 h-4 text-gray-400" />
                            )}
                          </div>
                        </td>
                      </tr>
                      {expandedTask === task.id && (
                        <tr className="bg-gray-50">
                          <td colSpan={7} className="px-4 py-3">
                            <div className="space-y-2 text-xs">
                              {task.error_message && (
                                <div className="p-2 bg-red-50 rounded text-red-600">
                                  <strong>错误：</strong>{task.error_message}
                                </div>
                              )}
                              {task.result_text && (
                                <div className="p-2 bg-green-50 rounded text-green-800">
                                  <strong>结果：</strong>
                                  <p className="mt-1 whitespace-pre-wrap">{task.result_text}</p>
                                </div>
                              )}
                              {!task.error_message && !task.result_text && (
                                <p className="text-gray-400">暂无结果</p>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  ))}
                  {tasks.length === 0 && (
                    <tr>
                      <td colSpan={7} className="px-4 py-8 text-center text-gray-400">无匹配任务</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {taskTotal > 50 && (
              <div className="flex justify-center gap-2">
                {Array.from({ length: Math.ceil(taskTotal / 50) }, (_, i) => i + 1).map(p => (
                  <button
                    key={p}
                    onClick={() => loadTasks(currentBatch.batch_id, p, statusFilter)}
                    className={`px-3 py-1 rounded text-xs ${p === taskPage ? 'bg-blue-500 text-white' : 'bg-gray-100 text-gray-600'}`}
                  >
                    {p}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
};

export default BatchPage;
