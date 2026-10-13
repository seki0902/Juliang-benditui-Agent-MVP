'use strict';

const $ = id => document.getElementById(id);
const labels = {
  success: '成功',
  partial: '部分完成',
  failed: '失败',
  running: '运行中',
  interrupted: '已中断',
  recovered: '重试后恢复',
  unknown: '待核实',
  disabled: '未启用',
  unavailable: '不可用',
  skipped_duplicate: '已完成，跳过重复',
  skipped_no_recommendations: '无建议，已跳过',
};

const stateLabel = status => labels[status] || (status?.startsWith('skipped') ? '已跳过' : status || '未知');
const el = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
};
const badge = status => el('span', stateLabel(status), `badge ${status}`);
const stamp = value => value
  ? new Date(value).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    })
  : '—';
const duration = run => {
  if (!run.started_at) return '';
  const seconds = Math.max(0, Math.round(((run.finished_at ? new Date(run.finished_at) : new Date()) - new Date(run.started_at)) / 1000));
  return seconds > 60 ? `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒` : `${seconds} 秒`;
};

let data = null;
let selected = null;
let signature = '';
let busy = false;
let detailVersion = 0;
let triggerState = 'idle';
let triggerStartedAt = 0;

async function get(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    cache: 'no-store',
    signal: options.signal || AbortSignal.timeout(12000),
  });
  let result = {};
  try {
    result = await response.json();
  } catch (_) {
    result = {};
  }
  if (!response.ok) throw Error(result.message || result.error || `请求失败 (${response.status})`);
  if (result.runs) {
    for (const item of result.runs) {
      if (item.source === 'repair_validation') item.source = '修复验证';
    }
  }
  return result;
}

function hasRunningRun() {
  return Boolean(data?.runs?.some(run => run.status === 'running'));
}

function setRunFeedback(message, type = '') {
  const node = $('run-feedback');
  node.textContent = message;
  node.className = `run-feedback${type ? ` ${type}` : ''}`;
}

function updateRunControl() {
  const button = $('run-agent');
  const label = $('run-agent-label');
  if (!button || !label) return;
  if (triggerState === 'starting') {
    button.disabled = true;
    label.textContent = '正在启动…';
    return;
  }
  if (triggerState === 'running' || hasRunningRun()) {
    triggerState = 'running';
    button.disabled = true;
    label.textContent = 'Agent 运行中';
    return;
  }
  button.disabled = false;
  label.textContent = '立即运行 Agent';
}

function drawRuns() {
  const filter = $('filter').value;
  const runs = data.runs.filter(run => filter === 'all' || run.status === filter || (filter === 'failed' && run.status === 'interrupted'));
  $('runs').replaceChildren();
  if (!runs.length) $('runs').append(el('p', '没有符合条件的运行记录', 'muted'));
  for (const run of runs) {
    const button = el('button', undefined, `run${run.id === selected ? ' selected' : ''}`);
    button.setAttribute('aria-pressed', String(run.id === selected));
    const top = el('div', undefined, 'run-top');
    top.append(el('strong', stamp(run.started_at)), badge(run.status));
    button.append(top, el('small', `统计日 ${run.data_date || '未知'} · ${run.source === 'scheduled' ? '自动运行' : run.source === 'manual' ? '手动运行' : run.source}`));
    button.append(el('small', run.legacy ? '历史记录 · 步骤信息可能不完整' : duration(run)));
    button.onclick = () => {
      selected = run.id;
      signature = '';
      drawRuns();
      drawDetail();
    };
    $('runs').append(button);
  }
}

async function drawDetail() {
  const run = data.runs.find(item => item.id === selected);
  if (!run) return;
  const nextSignature = JSON.stringify(run);
  if (signature === nextSignature) return;
  signature = nextSignature;
  const version = ++detailVersion;

  $('detail-date').textContent = `统计日 ${run.data_date || '未知'}`;
  $('detail-status').replaceWith(Object.assign(badge(run.status), { id: 'detail-status' }));
  $('detail-meta').textContent = `开始 ${stamp(run.started_at)}${run.finished_at ? ` · 结束 ${stamp(run.finished_at)}` : ''}${run.legacy ? ' · 历史记录' : ` · ${duration(run)}`}`;
  $('failure').hidden = !run.error;
  $('failure').textContent = run.error || '';
  $('steps').replaceChildren();
  for (const step of run.steps || []) {
    const row = el('div', undefined, `stage ${step.status}`);
    const head = el('div', undefined, 'stage-head');
    head.append(el('span', step.name), badge(step.status));
    row.append(head, el('p', [step.started_at ? `${stamp(step.started_at)} · ${duration(step)}` : '', step.error || step.reason || ''].filter(Boolean).join(' / ')));
    $('steps').append(row);
  }

  $('result').hidden = true;
  $('empty-result').hidden = false;
  $('empty-result').textContent = run.status === 'running'
    ? '任务正在执行，步骤会自动更新。'
    : run.status === 'failed' || run.status === 'interrupted'
      ? '该次运行未完成日报流程。失败记录会保留，补跑不会覆盖这次记录。'
      : '正在读取日报与回执…';
  if (!['success', 'partial'].includes(run.status) || !run.data_date) {
    if (run.status === 'skipped_duplicate') $('empty-result').textContent = '这一天已有成功记录，本次没有重复执行。请选择此前成功的运行查看结果。';
    return;
  }

  try {
    const report = await get(`/api/report/${run.data_date}`);
    if (version !== detailVersion) return;
    $('result').hidden = false;
    $('empty-result').hidden = true;
    $('report-date').textContent = `${run.data_date} · 当前保存版本`;
    const metrics = report.metrics || {};
    $('metrics').replaceChildren();
    for (const [name, key, prefix] of [['消耗', 'spend', '¥'], ['平台留资', 'leads', ''], ['留资成本', 'lead_cost', '¥'], ['点击', 'clicks', '']]) {
      const value = metrics[key];
      const block = el('div', undefined, 'metric');
      block.append(el('small', name), el('strong', value == null ? '—' : prefix + Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })));
      $('metrics').append(block);
    }
    const receipts = report.receipts || {};
    $('receipts').replaceChildren();
    for (const [key, name] of [['platform_sync', '石墨平台数据'], ['recommendation_sync', '石墨运营建议'], ['push', '日报推送']]) {
      const receipt = receipts[key] || {};
      const row = el('div', undefined, 'receipt');
      const text = el('div', name);
      if (receipt.message_id) text.append(el('small', `发送服务已接收 · 回执 ${receipt.message_id}`));
      if (receipt.error || receipt.reason) text.append(el('small', receipt.error || receipt.reason));
      row.append(text, badge(receipt.status || 'unknown'));
      $('receipts').append(row);
    }
    $('artifacts').replaceChildren();
    const names = {
      'daily_report.md': '完整日报 ↗',
      'run_manifest.json': '数据来源 ↗',
      'recommendations.json': '运营建议 ↗',
      'clawbot_push.json': '推送回执 ↗',
    };
    for (const file of report.artifacts || []) {
      if (!names[file]) continue;
      const link = el('a', names[file]);
      link.href = `/artifacts/${run.data_date}/${file}`;
      link.target = '_blank';
      link.rel = 'noopener';
      $('artifacts').append(link);
    }
    $('report').textContent = report.report || '未找到完整日报。';
  } catch (error) {
    if (version === detailVersion) {
      $('empty-result').textContent = `读取日报失败：${error.message}`;
      signature = '';
    }
  }
}

async function refresh() {
  if (busy) return;
  busy = true;
  $('refresh').disabled = true;
  try {
    data = await get('/api/status');
    $('error').hidden = true;
    $('connection').textContent = '本地服务已连接';
    $('updated').textContent = `更新于 ${stamp(data.server_time)}`;
    const latest = data.runs[0];
    $('run-count').textContent = `${data.runs.length} 次`;
    $('latest-date').textContent = latest ? stamp(latest.started_at) : '';
    $('latest-status').textContent = latest ? stateLabel(latest.status) : '暂无运行记录';
    $('latest-summary').textContent = latest
      ? latest.error || (latest.status === 'partial'
        ? '日报流程已有产出，部分步骤未完成，请查看详情。'
        : latest.status === 'running'
          ? '正在执行，进度自动更新。'
          : latest.status === 'success'
            ? '本次记录的流程已完成。'
            : '查看执行详情了解结果。')
      : '首次任务运行后会在这里显示。';
    const expected = data.runs.find(run => run.data_date === data.expected_date && ['success', 'partial'].includes(run.status));
    $('freshness').textContent = expected ? `昨日（${data.expected_date}）日报已有产出` : `昨日（${data.expected_date}）日报尚未完成`;
    const schedule = data.schedule || {};
    $('next-run').textContent = schedule.status === 'available' ? stamp(schedule.next_run) : '待核实';
    $('schedule-info').textContent = schedule.status === 'available'
      ? `每日 09:00 · ${schedule.state === 'Ready' ? '已就绪' : schedule.state === 'Running' ? '执行中' : schedule.state}`
      : schedule.message || '正在读取任务';
    $('schedule-detail').textContent = schedule.status === 'available'
      ? `上次退出码 ${schedule.last_result} · ${schedule.wake ? '允许唤醒' : '不自动唤醒'} · 需 Windows 用户登录`
      : '';
    const counts = { success: 0, partial: 0, failed: 0 };
    for (const run of data.runs) if (run.status in counts) counts[run.status]++;
    $('success-count').textContent = `${counts.success} / ${data.runs.length}`;
    $('history-count').textContent = `${counts.success} 次成功 · ${counts.partial} 次部分完成 · ${counts.failed} 次失败`;
    $('spark').replaceChildren(...data.runs.slice(0, 16).reverse().map(run => {
      const node = el('span', undefined, run.status);
      node.title = `${stamp(run.started_at)} ${stateLabel(run.status)}`;
      return node;
    }));
    if (!selected || !data.runs.some(run => run.id === selected)) selected = latest?.id;

    if (hasRunningRun()) {
      triggerState = 'running';
    } else if (triggerState === 'running') {
      triggerState = 'idle';
      setRunFeedback('上一次运行已结束，请查看运行记录。', 'success');
    } else if (triggerState === 'starting' && Date.now() - triggerStartedAt > 30000) {
      triggerState = 'idle';
      setRunFeedback('暂时没有看到新的运行记录，请刷新后重试。', 'error');
    }
    updateRunControl();
    drawRuns();
    await drawDetail();
  } catch (error) {
    $('error').hidden = false;
    $('error').textContent = `无法连接本地服务：${error.message}。已显示的数据可能不是最新。`;
    $('connection').textContent = '连接中断';
  } finally {
    busy = false;
    $('refresh').disabled = false;
    updateRunControl();
  }
}

async function triggerAgent() {
  if (triggerState !== 'idle' || hasRunningRun()) return;
  triggerState = 'starting';
  triggerStartedAt = Date.now();
  updateRunControl();
  setRunFeedback('正在启动 Agent…');
  try {
    const result = await get('/api/run', { method: 'POST', headers: { Accept: 'application/json' } });
    setRunFeedback(result.message || 'Agent 已启动，运行记录即将出现。', 'success');
    signature = '';
    await refresh();
  } catch (error) {
    triggerState = 'idle';
    setRunFeedback(`启动失败：${error.message}`, 'error');
    updateRunControl();
  }
}

$('run-agent').onclick = triggerAgent;
$('refresh').onclick = () => {
  signature = '';
  refresh();
};
$('filter').onchange = drawRuns;
refresh();
setInterval(refresh, 5000);
