'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pretty = value => esc(JSON.stringify(value, null, 2));
const number = value => value == null ? '—' : Number(value).toLocaleString('zh-CN', {maximumFractionDigits: 6});
const date = value => value ? new Date(value).toLocaleString('zh-CN', {hour12:false}) : '—';
const short = value => esc(String(value || '').slice(0, 8));
const roles = {admin:'管理员', operator:'测试员', viewer:'查看者'};
const states = {queued:'排队中',running:'执行中',cancel_requested:'取消处理中',cancelled:'已取消',succeeded:'已完成',partial:'部分完成',failed:'失败',interrupted:'异常中断',completed:'完成',unsupported:'不支持',skipped_after_abort:'已跳过',timeout:'超时',budget_exhausted:'资源达上限'};
const protocols = {'capability.mirror_fidelity.v1':['镜像电路保真度','MIR','门级保真度与深度容量'], 'kernel.hamiltonian.v1':['哈密顿模拟','HAM','演化与分布误差'], 'kernel.boson_fock.v1':['玻色采样','FCK','Fock 输入与采样分布'], 'application.mis.v1':['组合优化 · MIS','MIS','QAOA 场景算法'], 'application.vqe_toy.v1':['模型 VQE','VQE','双比特能量估计'], 'application.material_dynamics.v1':['材料动力学 · Ising 链','MDY','关联与结构因子轨迹']};
const active = ['queued','running','cancel_requested'];
const badge = state => `<span class="pill ${esc(state)}">${esc(states[state] || state)}</span>`;
const ptitle = key => protocols[key]?.[0] || key;
const layerLabel = key => ({L0:'基础能力', L1:'内核'})[catalog?.protocols?.find(p => p.id === key)?.layer] || '场景算法';
const mdef = key => catalog?.metrics?.[key];
const mlabel = key => mdef(key)?.[0] || key;
const dirText = key => catalog?.directions?.[mdef(key)?.[2]] || '';
const dirBadge = key => dirText(key) ? `<span class="pill neutral">${esc(dirText(key))}</span>` : '';
const primaryOf = benchmark => catalog?.protocols?.find(p => p.id === benchmark)?.primary;
const stats = values => {
  const v = values.filter(x => typeof x === 'number' && Number.isFinite(x));
  if (!v.length) return {n: 0, mean: null, median: null, sd: null};
  const sorted = [...v].sort((a, b) => a - b), mid = Math.floor(sorted.length / 2);
  const mean = v.reduce((a, b) => a + b, 0) / v.length;
  return {n: v.length, mean, median: sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2,
          sd: v.length > 1 ? Math.sqrt(v.reduce((a, b) => a + (b - mean) ** 2, 0) / (v.length - 1)) : null};
};
const CHART_COLORS = ['#0c786f', '#ac7525', '#227b9e', '#b84848', '#5b6bbf'];
function lineChart(series, {xLabel = '', height = 190} = {}) {
  const W = 760, H = height, padL = 70, padR = 16, padT = 14, padB = 34;
  const points = series.flatMap(s => s.points);
  if (points.length < 2) return '';
  const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  let xmin = Math.min(...xs), xmax = Math.max(...xs), ymin = Math.min(...ys), ymax = Math.max(...ys);
  if (xmin === xmax) { xmin -= 0.5; xmax += 0.5; }
  if (ymin === ymax) { ymin -= 0.5; ymax += 0.5; }
  const px = x => padL + (W - padL - padR) * (x - xmin) / (xmax - xmin);
  const py = y => H - padB - (H - padB - padT) * (y - ymin) / (ymax - ymin);
  const ticks = [0, 0.5, 1].map(f => ymin + f * (ymax - ymin));
  const fmtTick = v => Number(v.toFixed(4)).toString();
  const grid = ticks.map(v => `<line x1="${padL}" y1="${py(v).toFixed(1)}" x2="${W - padR}" y2="${py(v).toFixed(1)}" stroke="#e4e9ed"></line><text x="${padL - 8}" y="${(py(v) + 4).toFixed(1)}" text-anchor="end" font-size="10" fill="#74818d">${fmtTick(v)}</text>`).join('');
  const xticks = [[xmin, 'start'], [xmax, 'end']].map(([v, anchor]) => `<text x="${px(v).toFixed(1)}" y="${H - 10}" text-anchor="${anchor}" font-size="10" fill="#74818d">${fmtTick(v)}</text>`).join('');
  const lines = series.filter(s => s.points.length > 1).map((s, i) => `<polyline fill="none" stroke="${CHART_COLORS[i % CHART_COLORS.length]}" stroke-width="2" points="${s.points.map(p => px(p[0]).toFixed(1) + ',' + py(p[1]).toFixed(1)).join(' ')}"></polyline>`).join('');
  const legend = series.map((s, i) => `<span><i style="background:${CHART_COLORS[i % CHART_COLORS.length]}"></i>${esc(s.name)}</span>`).join('');
  return `<div class="chart-svg"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(series.map(s => s.name).join('、'))}">${grid}${xticks}${lines}${xLabel ? `<text x="${W - padR}" y="${padT}" text-anchor="end" font-size="10" fill="#74818d">${esc(xLabel)}</text>` : ''}</svg><div class="chart-legend">${legend}</div></div>`;
}
const empty = (title, text) => `<div class="empty"><div class="empty-icon">◇</div><h3>${esc(title)}</h3><p>${esc(text)}</p></div>`;
const button = (action, text, extra = '', cls = '') => `<button type="button" class="${cls}" data-action="${action}" ${extra}>${text}</button>`;
let user, catalog, cases = [], devices = [], tasks = [], overview = {}, route = '', toastTimer, polling = false, generation = 0;
let currentTask, editorId, taskKey, planSignature, taskFormSignature, selectedResults = new Set();
let filterText = '', filterStatus = '', showArchived = false;
const canEdit = () => user?.role !== 'viewer';

async function api(path, options = {}) {
  const headers = {...(options.headers || {})};
  if (options.method && options.method !== 'GET') Object.assign(headers, {'Content-Type':'application/json','X-QTest-Request':'1'});
  const response = await fetch('/api' + path, {...options, headers});
  let data;
  try { data = await response.json(); } catch { throw new Error('服务器响应异常，请稍后重试'); }
  if (!response.ok) {
    if (response.status === 401) showLogin();
    throw new Error(typeof data.detail === 'string' ? data.detail : '请求未通过校验');
  }
  return data;
}
function notify(text) {
  $('#toast').textContent = text; $('#toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden = true, 4500);
}
function showLogin() {
  user = null; $('#workspace').hidden = true; $('#login').hidden = false;
  if ($('#modal').open) $('#modal').close();
}
async function enter() {
  const me = await api('/me'); user = me.user;
  $('#login').hidden = true; $('#workspace').hidden = false;
  $('#username').textContent = user.username; $('#avatar').textContent = user.username.slice(0,1).toUpperCase();
  $('#role').textContent = roles[user.role]; $('#version').textContent = 'v' + me.version;
  catalog = await api('/catalog'); await navigate();
}
function heading(title, subtitle, actions = '') {
  return `<div class="page-heading"><div><h1>${title}</h1><p>${subtitle}</p></div><div class="actions">${actions}</div></div>`;
}
function updateHeartbeat() {
  const online = overview.worker?.online;
  $('#worker-dot').classList.toggle('offline', !online);
  $('#worker-label').textContent = online ? '执行器在线' : '执行器离线';
}
function workerNotice() {
  return overview.worker?.online ? '' : '<div class="notice warning">执行器当前离线。你仍可管理用例和提交任务，任务会等待服务器上的 Worker 启动。</div>';
}
async function refreshData() {
  [overview, cases, devices, tasks] = await Promise.all([api('/overview'), api('/cases'), api('/devices'), api('/tasks')]);
  updateHeartbeat();
}
async function navigate() {
  if (!user) return;
  const ticket = ++generation;
  route = location.hash.slice(1) || 'overview';
  filterText = ''; filterStatus = ''; selectedResults.clear();
  const page = route.split('/')[0];
  $$('[data-nav]').forEach(a => a.classList.toggle('active', a.dataset.nav === page));
  $('#breadcrumb').textContent = {overview:'总览',cases:'用例库',tasks:'测试任务',results:'结果与对比',settings:'系统管理'}[page] || '总览';
  try {
    await refreshData();
    if (ticket !== generation) return;
    if (route.startsWith('tasks/')) { await renderDetail(route.split('/')[1], ticket); return; }
    if (route === 'settings') { await renderSettings(ticket); return; }
    renderPage(page);
  } catch (error) { if (user) $('#main').innerHTML = heading('暂时无法读取', esc(error.message), button('refresh','重新加载')); }
}
function renderPage(page) {
  if (page === 'cases') renderCases(); else if (page === 'tasks') renderTasks(); else if (page === 'results') renderResults(); else renderOverview();
}
function taskRows(rows, selectable = false) {
  if (!rows.length) return empty('暂无任务', '选择用例并创建一次测试，实验进度与证据将在这里记录。');
  return `<div class="table-wrap"><table><thead><tr>${selectable?'<th>选择</th>':''}<th>任务名称</th><th>设备 / 类型</th><th>状态</th><th>创建人</th><th>创建时间</th><th></th></tr></thead><tbody>${rows.map(t => `<tr>${selectable ? `<td>${t.kind==='test' && ['succeeded','partial','cancelled','failed'].includes(t.status) ? `<input type="checkbox" aria-label="选择 ${esc(t.name)}" data-compare-id="${t.id}" ${selectedResults.has(t.id)?'checked':''}>` : ''}</td>` : ''}<td><a class="table-link" href="#tasks/${t.id}">${esc(t.name)}</a><small>${short(t.id)}</small></td><td>${esc(t.kind==='comparison'?'综合对比':devices.find(d=>d.id===t.device_id)?.name || t.device_id)}<small>${t.kind==='comparison'?'条件分组分析':esc(devices.find(d=>d.id===t.device_id)?.descriptor.kind === 'qpu'?'真实 QPU':'模拟器')}</small></td><td>${badge(t.status)}</td><td>${esc(t.username)}</td><td class="muted">${date(t.created_at)}</td><td><a href="#tasks/${t.id}">查看 →</a></td></tr>`).join('')}</tbody></table></div>`;
}
function deviceCards() {
  return devices.map(d=>`<div class="device"><div class="device-top"><h3>${esc(d.name)}</h3><span class="pill ${d.blocked?'partial':'good'}">${d.blocked?'待核实':d.enabled?'已注册':'已停用'}</span></div><p>${esc(d.descriptor.device_id)} · ${d.descriptor.kind==='qpu'?'真实 QPU':'理想模拟器'}</p><div class="tags">${d.descriptor.capabilities.map(c=>`<span class="badge-outline">${esc({'gate.sample.v1':'量子门','analog.evolve.v1':'模拟演化','photon.fock.v1':'光子 Fock'}[c]||c)}</span>`).join('')}</div>${d.blocked?`<p class="warn-text">${esc(d.blocked)}</p>${user.role==='admin'?button('resolve','核实并解除阻塞',`data-id="${d.id}"`,'small-button'):''}`:''}</div>`).join('');
}
function renderOverview() {
  const counts = overview.status_counts || {}, total = Object.values(counts).reduce((a,b)=>a+b,0);
  $('#main').innerHTML = heading('测试总览','从用例到结果，掌握实验室的每一次测试。',canEdit()?button('new-task','＋ 新建测试任务','','primary'):'') + workerNotice() +
    `<div class="stats">${[['累计任务',total,'全部测试与综合对比','▤'],['执行与排队',(counts.running||0)+(counts.cancel_requested||0)+(counts.queued||0),`${counts.running||0} 个执行中 · ${counts.queued||0} 个待执行`,'▷'],['完成任务',counts.succeeded||0,`${counts.partial||0} 个部分完成`,'✓'],['可用测试用例',overview.cases,'覆盖内核与场景算法','▦']].map(([name,n,note,icon])=>`<div class="stat"><span>${name}</span><i class="stat-icon">${icon}</i><strong>${number(n)}</strong><small>${note}</small></div>`).join('')}</div>` +
    `<section class="panel"><div class="panel-head"><div><h2>最近任务</h2><p>跟踪执行进度，查看独立验收的测试结果</p></div><a href="#tasks" class="small">全部任务 →</a></div>${taskRows(tasks.slice(0,5))}</section>` +
    `<div class="two-cols"><section class="panel"><div class="panel-head"><h2>测试能力</h2><span class="pill neutral">6 项已实现协议</span></div><div class="panel-body protocol-list">${Object.entries(protocols).map(([key,p])=>`<div class="protocol-row"><span class="protocol-number">${p[1]}</span><div><strong>${p[0]}</strong><small>${p[2]}</small></div><span class="pill neutral">${layerLabel(key)}</span></div>`).join('')}</div></section><section class="panel"><div class="panel-head"><h2>设备与连接</h2><span class="muted small">${devices.length} 台已注册</span></div><div class="panel-body">${deviceCards()}</div></section></div><div class="notice">当前内置设备是小规模理想模拟器，适合验证测试流程。真实设备通过管理员注册的厂商 Adapter 接入，模拟器与 QPU 的结果分组比较。</div>`;
}
function caseCards() {
  const rows = cases.filter(c=>(showArchived || !c.archived) && (c.name+c.data.id+c.data.benchmark).toLowerCase().includes(filterText.toLowerCase()));
  return rows.length ? `<div class="case-grid">${rows.map(c=>`<article class="case-card ${c.archived?'archived':''}"><div class="case-card-top"><span class="pill neutral">${esc(ptitle(c.data.benchmark))}</span><span class="mono muted">v${c.version}${c.archived?' · 已归档':''}</span></div><h2>${esc(c.name)}</h2><span class="mono muted">${esc(c.data.id)}</span><p class="description">${esc(c.description || '暂无说明')}</p><div class="case-facts"><div><small>每次测量 SHOTS</small><b>${number(c.data.shots)}</b></div><div><small>重复实验</small><b>${c.data.repetitions} 次</b></div><div><small>测试层级</small><b>${esc(layerLabel(c.data.benchmark))}</b></div></div><div class="case-card-bottom"><span>${date(c.updated_at).split(' ')[0]}</span><div class="actions">${button('edit-case',canEdit()?'编辑':'查看',`data-id="${c.id}"`)}${canEdit()?button('copy-case','复制',`data-id="${c.id}"`)+button('archive-case',c.archived?'恢复':'归档',`data-id="${c.id}"`):''}</div></div></article>`).join('')}</div>` : empty('没有匹配的用例','尝试调整搜索条件，或从协议模板新建用例。');
}
function renderCases() {
  $('#main').innerHTML = heading('用例库','版本化管理实验参数，任务提交时自动冻结所选版本。',canEdit()?button('new-case','＋ 新建用例','','primary'):'') + `<div class="toolbar"><input id="case-search" type="search" placeholder="搜索用例名称、ID 或协议" aria-label="搜索用例" value="${esc(filterText)}"><label class="check-label"><input id="show-archived" type="checkbox" ${showArchived?'checked':''}>显示已归档用例</label></div><div id="case-list">${caseCards()}</div>`;
}
function filteredTasks(resultsOnly=false) {
  return tasks.filter(t=>(!resultsOnly || !active.includes(t.status)) && (!filterStatus || t.status===filterStatus) && (t.name+t.id+t.username).toLowerCase().includes(filterText.toLowerCase()));
}
function taskToolbar() {
  return `<div class="toolbar"><input id="task-search" type="search" placeholder="搜索名称、任务 ID 或创建人" aria-label="搜索任务" value="${esc(filterText)}"><div class="actions"><span class="small muted">最近 200 个任务</span><select id="status-filter" aria-label="筛选状态"><option value="">全部状态</option>${['queued','running','succeeded','partial','cancelled','failed','interrupted'].map(s=>`<option value="${s}" ${filterStatus===s?'selected':''}>${states[s]}</option>`).join('')}</select>${button('refresh','↻ 刷新')}</div></div>`;
}
function renderTasks() {
  $('#main').innerHTML = heading('测试任务','提交测试、跟踪作业状态，保留每一步执行记录。',canEdit()?button('new-task','＋ 新建测试任务','','primary'):'') + workerNotice() + taskToolbar() + `<section class="panel" id="task-list">${taskRows(filteredTasks())}</section>`;
}
function renderResults() {
  $('#main').innerHTML = heading('结果与对比','查看测试证据，按相同实验条件生成综合对比。') + '<div class="notice">综合对比会独立重算指标并检查可比条件。不同协议、随机种子、资源上限或后处理配置会被分组；缺失结果不按零分处理。</div>' +
    (canEdit()?`<div class="selection-bar"><span id="selection-count">已选择 ${selectedResults.size} 个任务</span>${button('new-comparison','生成综合对比','','primary')}<span>选择 2–20 个有已验证报告的测试任务</span></div>`:'') + taskToolbar() + `<section class="panel" id="task-list">${taskRows(filteredTasks(true),canEdit())}</section>`;
}
function showModal(title, content) {
  $('#modal-title').textContent=title; $('#modal-body').innerHTML=content;
  if (!$('#modal').open) $('#modal').showModal();
}
function formActions(save, cancel=true) { return `<p class="form-error" role="alert"></p><div class="form-actions">${cancel?button('close-modal','取消'):''}<button class="primary" type="submit">${save}</button></div>`; }
function caseEditor(id=null, copy=false) {
  editorId = copy ? null : id;
  const row = cases.find(c=>c.id===id), template = structuredClone(row?.data || catalog.protocols[0].template);
  if (!row || copy) template.id = template.id + '-' + crypto.randomUUID().slice(0,5);
  showModal(row && !copy ? (canEdit()?'编辑用例':'查看用例') : copy?'复制用例':'新建测试用例', `<form id="case-form"><div class="form-grid"><label>用例名称<input name="name" value="${esc(row?row.name+(copy?' · 副本':''):'新建 '+ptitle(template.benchmark))}" required maxlength="200"></label><label>测试协议<select name="benchmark" id="protocol-select">${catalog.protocols.map(p=>`<option value="${p.id}" ${p.id===template.benchmark?'selected':''}>${esc(ptitle(p.id))}</option>`).join('')}</select></label></div><label>用例说明<textarea name="description" rows="2" maxlength="4000">${esc(row?.description || '')}</textarea></label><div class="form-grid three"><label>用例 ID<input name="case_id" value="${esc(template.id)}" required pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,79}" maxlength="80"></label><label>每次测量 shots<input name="shots" type="number" min="1" max="1000000" value="${template.shots}" required></label><label>重复实验次数<input name="repetitions" type="number" min="1" max="100" value="${template.repetitions}" required></label></div><label>协议参数（JSON）<textarea name="parameters" class="code" spellcheck="false" required>${pretty(template.parameters)}</textarea></label><p class="form-hint">参数必须满足所选协议的约束。切换协议会载入对应模板。模型 VQE 是双比特示例，不能作为真实分子计算成绩。</p>${row&&!copy?`<p class="form-hint">当前版本 v${row.version}，保存生成下一版本；已有任务保留原有快照。</p>`:''}${canEdit()?formActions('校验并保存'):''}</form>`);
  if (!canEdit()) $$('input,textarea,select',$('#case-form')).forEach(el=>el.disabled=true);
}
async function saveCase(form) {
  const f = new FormData(form), existing = cases.find(c=>c.id===editorId);
  const data = {name:f.get('name'),description:f.get('description'), data:{id:f.get('case_id'),benchmark:f.get('benchmark'),shots:Number(f.get('shots')),repetitions:Number(f.get('repetitions')),parameters:JSON.parse(f.get('parameters'))},archived:existing?.archived || false};
  if (existing) data.version=existing.version;
  await api('/cases'+(editorId?'/'+editorId:''), {method:editorId?'PUT':'POST', body:JSON.stringify(data)});
  $('#modal').close(); notify('用例已校验并保存'); await navigate();
}
function newTask() {
  taskKey=crypto.randomUUID(); planSignature=null; taskFormSignature=null;
  const limits=catalog.default_limits;
  showModal('新建测试任务', `<form id="task-form"><div class="form-grid"><label>任务名称<input name="name" value="量子测试 ${new Date().toLocaleDateString('zh-CN')}" required maxlength="200"></label><label>目标设备<select name="device_id">${devices.filter(d=>d.enabled&&!d.blocked).map(d=>`<option value="${d.id}">${esc(d.name)} · ${d.descriptor.kind==='qpu'?'QPU':'模拟器'}</option>`).join('')}</select></label></div><label>选择测试用例</label><div class="check-list">${cases.filter(c=>!c.archived).map(c=>`<label><input type="checkbox" name="cases" value="${c.id}"><span>${esc(c.name)}<small>${esc(c.data.id)} · v${c.version} · ${number(c.data.shots)} shots × ${c.data.repetitions}</small></span><span class="pill neutral">${esc(ptitle(c.data.benchmark))}</span></label>`).join('')}</div><h3 class="form-section">执行设置</h3><div class="form-grid"><label>随机种子<input name="seed" type="number" min="0" max="2147483647" value="${catalog.default_seed}" required></label><label>任务备注（可选）<input name="note" maxlength="200" placeholder="例如：复测 2026-11 第一批"></label></div><div class="form-grid three"><label>最大作业数<input name="max_jobs" type="number" min="1" max="10000" value="${limits.max_jobs}" required></label><label>最大 shots<input name="max_shots" type="number" min="1" max="1000000000" value="${limits.max_shots}" required></label><label>执行时限（秒）<input name="wall_seconds" type="number" min="1" max="604800" value="${limits.wall_seconds}" required></label></div><label>厂商状态轮询间隔（秒）<input name="poll_seconds" type="number" min="0.01" max="60" step="0.01" value="${limits.poll_seconds}" required></label><p class="form-hint">执行时限从 Worker 开始运行计算，包含厂商排队时间。本地示例默认 120 秒，真实设备需按排队情况调整。</p><div id="plan-result"></div><p class="form-error" role="alert"></p><div class="form-actions">${button('close-modal','取消')}${button('preflight','检查执行计划')}<button type="submit" class="primary" id="submit-task" disabled>提交测试</button></div></form>`);
}
function readTaskForm() {
  const f = new FormData($('#task-form'));
  return {name:f.get('name'),device_id:f.get('device_id'),cases:f.getAll('cases').map(id=>({id,version:cases.find(c=>c.id===id).version})),seed:Number(f.get('seed')),limits:Object.fromEntries(['max_jobs','max_shots','wall_seconds','poll_seconds'].map(k=>[k,Number(f.get(k))]))};
}
async function preflight() {
  const form=$('#task-form'); if(!form.reportValidity()) return;
  const input=readTaskForm(), signature=JSON.stringify(input), result=await api('/plan',{method:'POST',body:signature});
  if (JSON.stringify(readTaskForm())!==signature) return;
  planSignature=signature;
  const unsupported=result.cases.filter(c=>!c.supported);
  $('#plan-result').innerHTML=`<div class="plan-box"><strong>预检通过</strong> · ${result.jobs} 次作业 · ${number(result.requested_shots)} shots<p>${unsupported.length ? '将跳过不支持用例：'+unsupported.map(c=>esc(c.id)).join('、') : '所选用例均匹配设备声明的能力。'}</p><small>此预检检查协议能力与资源上限；具体拓扑和硬件可执行性仍由 Adapter 验证。</small></div>`;
  $('#submit-task').disabled=false;
}
async function submitTask() {
  const body=JSON.stringify(readTaskForm());
  if (body!==planSignature) throw new Error('参数已改变，请重新检查执行计划');
  if (taskFormSignature && taskFormSignature!==body) taskKey=crypto.randomUUID();
  taskFormSignature=body;
  const result=await api('/tasks',{method:'POST',headers:{'Idempotency-Key':taskKey},body});
  $('#modal').close(); notify('任务已提交'); location.hash='tasks/'+result.id;
}
async function renderDetail(id, ticket=generation) {
  const t=await api('/tasks/'+encodeURIComponent(id));
  if(ticket!==generation) return;
  currentTask=t;
  const progress=t.progress, total=progress?.total || (t.input.suite?.cases.reduce((n,c)=>n+c.repetitions,0) || 0), ended=progress?.ended||0;
  const cancel=canEdit() && (user.role==='admin'||user.id===t.created_by) && active.includes(t.status);
  let output=`<a class="back" href="#tasks">← 返回任务列表</a>${heading(esc(t.name),`<span class="mono">${esc(t.id)}</span> · ${t.kind==='test'?'测试执行':'综合对比'}`,badge(t.status)+(cancel?button('cancel-task',t.status==='cancel_requested'?'已申请取消':'取消任务',`data-id="${t.id}" ${t.status==='cancel_requested'?'disabled':''}`,'danger'):''))}`;
  if (t.message) output+=`<div class="notice ${['failed','interrupted','cancelled'].includes(t.status)?'warning':''}">${esc(t.message)}</div>`;
  if(t.status==='queued') output+=workerNotice();
  if(t.kind==='test') {
    output+=`<div class="two-cols"><section class="panel"><div class="panel-head"><h2>实验进度</h2><span class="small muted">${active.includes(t.status)?'每 2 秒刷新':'任务已结束'}</span></div><div class="panel-body"><div class="progress-header"><strong>${ended}<small> / ${total} 次</small></strong><small>已结束重复实验 · 成功 ${progress?.completed||0} 次</small></div><progress value="${ended}" max="${Math.max(total,1)}" aria-label="实验进度"></progress><div class="resource-row"><div><span>已提交作业</span><strong>${number(progress?.resources.reserved_jobs||0)} <small>/ ${t.input.plan.jobs}</small></strong></div><div><span>已预留 shots</span><strong>${number(progress?.resources.reserved_shots||0)}</strong></div><div><span>执行设备</span><strong class="small">${esc(t.input.device.descriptor.device_id)}</strong></div></div></div></section><section class="panel"><div class="panel-head"><h2>执行信息</h2></div><div class="panel-body"><dl class="kv"><dt>设备类型</dt><dd>${t.input.device.descriptor.kind==='qpu'?'真实 QPU':'理想模拟器'}</dd><dt>随机种子</dt><dd>${t.input.suite.seed}</dd><dt>创建时间</dt><dd>${date(t.created_at)}</dd><dt>开始时间</dt><dd>${date(t.started_at)}</dd></dl></div></section></div>`;
    if(progress?.latest_job) output+=`<section class="panel"><div class="panel-head"><h2>最近一次厂商作业</h2><span class="pill neutral">${esc(progress.latest_job.provider_state || progress.latest_job.state)}</span></div><div class="panel-body"><dl class="kv"><dt>作业 ID</dt><dd class="mono">${esc(progress.latest_job.job_id||'尚未返回')}</dd><dt>请求 ID</dt><dd class="mono">${esc(progress.latest_job.request_id)}</dd>${progress.latest_job.cancellation?`<dt>取消信息</dt><dd>${esc(progress.latest_job.cancellation)}</dd>`:''}</dl></div></section>`;
    output+=`<section class="panel"><div class="panel-head"><h2>重复实验明细</h2><span class="small muted">未完成实验不计分</span></div><div class="table-wrap"><table><thead><tr><th>用例</th><th>协议</th><th>重复编号</th><th>状态</th><th>主指标</th><th>测量作业数</th></tr></thead><tbody>${(progress?.cases||[]).map(c=>{const primary=primaryOf(c.benchmark),value=primary&&c.status==='completed'&&c.metrics?c.metrics[primary]:null;return `<tr><td>${esc(c.case_id)}</td><td>${esc(ptitle(c.benchmark))}</td><td>${c.repetition+1}</td><td>${badge(c.status)}</td><td>${primary?(c.status==='completed'?(value==null?'—':`<strong class="mono">${number(value)}</strong><small>${esc(mlabel(primary))}</small>`):'<span class="muted small">未完成</span>'):'—'}</td><td>${c.records.length}</td></tr>`;}).join('')}</tbody></table></div>${!progress?.cases.length?empty('等待开始实验','Worker 领取任务后展示逐次实验状态。'):''}</section>`;
  }
  output+=`<section class="panel"><div class="panel-head"><h2>执行记录</h2></div><div class="panel-body"><ul class="timeline">${t.events.map(e=>`<li>${esc(e.message)}<small>${date(e.at)}</small></li>`).join('')}</ul></div></section>`;
  if(t.artifacts.length) output+=`<section class="panel"><div class="panel-head"><h2>结果文件</h2><span class="pill good">独立验收</span></div><div class="panel-body"><div class="actions">${t.artifacts.map(name=>`<a class="badge-outline" href="/api/tasks/${t.id}/artifacts/${encodeURIComponent(name)}" download>${esc(name)} ↓</a>`).join('')}</div></div></section><div id="result-view"></div>`;
  output+=`<details><summary>查看冻结的任务输入</summary><pre>${pretty(t.input)}</pre></details>`;
  $('#main').innerHTML=output;
  if (t.artifacts.length) {
    try { const result=await api('/tasks/'+id+'/result'); if(ticket===generation&&$('#result-view')) $('#result-view').innerHTML=t.kind==='test'?renderMetrics(result):renderComparison(result); }
    catch(error) { if($('#result-view')) $('#result-view').textContent=error.message; }
  }
}
function metricSummaryTable(completed, primary) {
  const names = [...new Set(completed.flatMap(c => Object.entries(c.metrics || {}).filter(([, v]) => typeof v === 'number' || v === null).map(([k]) => k)))];
  if (!names.length) return '';
  const rows = names.map(key => {
    const def = mdef(key), values = completed.map(c => c.metrics?.[key]), s = stats(values);
    return `<tr class="${key === primary ? 'primary-metric' : ''}"><td class="metric-name">${esc(mlabel(key))}<small>${esc(def?.[1] || '未定义单位')}${dirText(key) ? ' · ' + esc(dirText(key)) : ''}</small></td>${values.map(v => `<td class="mono">${v == null ? '<span class="muted">缺失</span>' : number(v)}</td>`).join('')}<td class="mono"><strong>${number(s.mean)}</strong></td><td class="mono">${number(s.median)}</td><td class="mono">${s.sd == null ? '<span class="muted">—</span>' : number(s.sd)}</td></tr>`;
  }).join('');
  return `<div class="table-wrap"><table><thead><tr><th>指标 / 单位</th>${completed.map(c => `<th>第 ${c.repetition + 1} 次</th>`).join('')}<th>均值</th><th>中位数</th><th>标准差</th></tr></thead><tbody>${rows}</tbody></table></div><p class="result-note">统计仅基于已完成的重复，n=1 不提供标准差；主指标行已高亮，未完成重复不填零。指标解释与方向以指标定义为准，原始能量等依任务解释的指标不能仅按高低排序。</p>`;
}
function trajectoryCharts(metrics, repetition) {
  const blocks = [];
  if (Array.isArray(metrics?.trajectory) && metrics.trajectory.length) {
    const rows = metrics.trajectory;
    const xKey = typeof rows[0]?.depth === 'number' ? 'depth' : 'time';
    const byDepth = xKey === 'depth';
    const keys = [...new Set(rows.flatMap(r => Object.keys(r)))].filter(k => k !== xKey && k !== 'acceptance' && !k.endsWith('_se') && rows.some(r => typeof r[k] === 'number'));
    const series = keys.map(k => ({name: mlabel(k), points: rows.filter(r => typeof r[k] === 'number').map(r => [r[xKey], r[k]])}));
    if (series.length) blocks.push(`<div class="chart-block"><h4>${byDepth ? '深度扫描轨迹' : '演化轨迹'} · 第 ${repetition + 1} 次重复（各${byDepth ? '深度' : '时刻'}不视为独立重复${byDepth ? '；理想成功概率为 1' : ''}）</h4>${lineChart(series, {xLabel: byDepth ? '线路深度（层）' : '演化时间'})}</div>`);
  }
  for (const [key, title] of [['training_objective', '训练目标轨迹'], ['training_energy', '训练能量轨迹']]) {
    const values = metrics?.[key];
    if (Array.isArray(values) && values.length) {
      const points = values.map((v, i) => [i, v]);
      blocks.push(`<div class="chart-block"><h4>${title} · 第 ${repetition + 1} 次重复</h4>${lineChart([{name: title, points}], {xLabel: '迭代'})}<p class="result-note">训练中的带噪估计不是最终成绩；独立最终评估见上方指标表。</p></div>`);
    }
  }
  return blocks.join('');
}
function renderMetrics(result) {
  const s = result.summary, grouped = [];
  for (const c of s.cases) {
    let g = grouped.find(x => x.case_id === c.case_id);
    if (!g) { g = {case_id: c.case_id, benchmark: c.benchmark, rows: []}; grouped.push(g); }
    g.rows.push(c);
  }
  const overview = `<section class="panel"><div class="panel-head"><div><h2>验收结果概览</h2><p>完成用例经独立重放验收；设备身份由提供方声明，不构成真机认证</p></div></div><div class="panel-body"><div class="metric-grid four">${[['完成作业', result.resources.completed_jobs],['已回收尝试 shots', result.resources.observed_attempted_shots],['已回收接受 shots', result.resources.observed_accepted_shots],['无完整结果作业', result.resources.incomplete_jobs]].map(([k, v]) => `<div class="metric-box"><span>${k}</span><strong>${number(v)}</strong></div>`).join('')}</div></div></section>`;
  const sections = grouped.map(g => {
    const completed = g.rows.filter(c => c.status === 'completed'), primary = primaryOf(g.benchmark);
    const hero = primary && completed.length ? `<div class="metric-hero"><span>主指标均值 · ${esc(mlabel(primary))}</span><strong>${number(stats(completed.map(c => c.metrics?.[primary])).mean)}</strong>${dirBadge(primary)}</div>` : '';
    const charts = completed.flatMap(c => trajectoryCharts(c.metrics, c.repetition)).join('');
    return `<section class="panel"><div class="panel-head"><div><h2>${esc(g.case_id)} <span class="pill neutral">${esc(ptitle(g.benchmark))}</span></h2><p>${completed.length} / ${g.rows.length} 次重复完成${g.rows.some(c => c.status !== 'completed') ? '；未完成重复不产生分数' : ''}</p></div>${hero}</div><div class="panel-body">${completed.length ? metricSummaryTable(completed, primary) + charts : '<div class="notice">此用例没有完成的重复实验，不生成质量分数。</div>'}</div></section>`;
  }).join('');
  return overview + sections + `<details><summary>查看完整指标数据（JSON）</summary><pre>${pretty(s.cases)}</pre></details>`;
}
function renderComparison(result) {
  return `<div class="notice">共 ${result.sources.length} 份独立运行，${result.groups.length} 个可比组。组内均值仅用于描述观测结果，不代表统计显著优势。</div>${result.groups.map(g => {
    const primary = primaryOf(g.benchmark);
    const metrics = [...new Set(g.entries.flatMap(e => Object.keys(e.metrics)))];
    return `<section class="panel comparison-group"><h3>${esc(g.case_id)} <span class="pill neutral">${esc(ptitle(g.benchmark))}</span></h3><div class="table-wrap"><table><thead><tr><th>设备 / 运行</th><th>完成 / 计划</th><th>指标 / 单位</th><th>有效 n</th><th>均值</th><th>中位数</th><th>标准差</th><th>最小 / 最大</th></tr></thead><tbody>${g.entries.flatMap(e => metrics.filter(key => e.metrics[key]).map(key => {
      const m = e.metrics[key], def = mdef(key), best = g.observed_leaders?.[key]?.observed_best_run_ids?.includes(e.run_id);
      return `<tr class="${key === primary ? 'primary-metric' : ''}"><td>${esc(e.device_id)}<small>${short(e.run_id)}</small></td><td>${e.completed} / ${e.planned}</td><td class="metric-name">${esc(mlabel(key))}<small>${esc(def?.[1] || '')}${dirText(key) ? ' · ' + esc(dirText(key)) : ''}</small></td><td>${m.n}${m.missing ? `<small>缺失 ${m.missing}</small>` : ''}</td><td class="mono"><strong>${number(m.mean)}</strong>${best ? ' <span class="pill good">观测最佳</span>' : ''}</td><td class="mono">${number(m.median)}</td><td class="mono">${m.sd == null ? '—' : number(m.sd)}</td><td class="mono">${number(m.minimum)} / ${number(m.maximum)}</td></tr>`;
    })).join('')}</tbody></table></div></section>`;
  }).join('')}`;
}
async function renderSettings(ticket=generation) {
  let html=heading('系统管理','管理实验室成员、设备状态与操作记录。')+`<section class="panel"><div class="panel-head"><h2>已注册设备</h2><span class="small muted">SDK 与配置文件在服务器维护</span></div><div class="panel-body">${deviceCards()}<p class="result-note">设备能力来自注册时的描述，执行前再次校验。新增真实设备由服务器管理员安装对应 Adapter 并运行 device-add。</p></div></section>`;
  if(user.role==='admin') {
    const [users,logs]=await Promise.all([api('/users'),api('/audit')]);
    if(ticket!==generation) return;
    window.qtestUsers=users;
    html+=`<section class="panel"><div class="panel-head"><h2>工作区成员</h2>${button('new-user','＋ 新建账号','','primary small-button')}</div><div class="table-wrap"><table><thead><tr><th>用户名</th><th>角色</th><th>状态</th><th>创建时间</th><th></th></tr></thead><tbody>${users.map(u=>`<tr><td>${esc(u.username)}</td><td>${roles[u.role]}</td><td><span class="pill ${u.enabled?'good':'neutral'}">${u.enabled?'启用':'停用'}</span></td><td>${date(u.created_at)}</td><td>${button('edit-user','管理',`data-id="${u.id}"`,'small-button')}</td></tr>`).join('')}</tbody></table></div></section><section class="panel"><div class="panel-head"><h2>操作审计</h2><span class="small muted">最近 200 条</span></div><div class="table-wrap"><table><thead><tr><th>时间</th><th>操作者</th><th>操作</th><th>对象</th><th>说明</th></tr></thead><tbody>${logs.map(l=>`<tr><td>${date(l.at)}</td><td>${esc(l.username)}</td><td>${esc(l.action)}</td><td class="mono">${short(l.target)}</td><td>${esc(l.detail)}</td></tr>`).join('')}</tbody></table></div></section>`;
  } else html+='<div class="notice">当前账号可以查看共享工作区。账号管理和设备状态核实由管理员负责。</div>';
  $('#main').innerHTML=html;
}
function userEditor(id) {
  const row=window.qtestUsers?.find(u=>u.id===id);
  showModal(row?'管理账号':'新建账号',`<form id="user-form" data-id="${row?.id||''}"><label>用户名<input name="username" value="${esc(row?.username||'')}" ${row?'disabled':''} pattern="[A-Za-z0-9_.-]{3,64}" required autocomplete="off"></label><label>角色<select name="role">${Object.entries(roles).map(([k,v])=>`<option value="${k}" ${k===(row?.role||'operator')?'selected':''}>${v}</option>`).join('')}</select></label><label>${row?'重设密码（留空则保持原密码）':'初始密码'}<input type="password" name="password" minlength="12" maxlength="200" ${row?'':'required'} autocomplete="new-password"></label>${row?`<label class="check-label"><input name="enabled" type="checkbox" ${row.enabled?'checked':''}>启用此账号</label><p class="small muted">保存会撤销该账号的已有登录会话。</p>`:''}${formActions(row?'保存账号':'创建账号')}</form>`);
}
async function handleAction(el) {
  const id=el.dataset.id;
  switch(el.dataset.action) {
    case 'close-modal': $('#modal').close(); break;
    case 'refresh': await navigate(); break;
    case 'new-case': caseEditor(); break;
    case 'edit-case': caseEditor(id); break;
    case 'copy-case': caseEditor(id,true); break;
    case 'archive-case': {
      const c=cases.find(c=>c.id===id); await api('/cases/'+id,{method:'PUT',body:JSON.stringify({...c,archived:!c.archived})}); await navigate(); notify(c.archived?'用例已恢复':'用例已归档，已有任务不受影响'); break;
    }
    case 'new-task': newTask(); break;
    case 'preflight': await preflight(); break;
    case 'cancel-task': await api('/tasks/'+id+'/cancel',{method:'POST',body:'{}'}); await renderDetail(id); notify('取消请求已记录'); break;
    case 'new-comparison':
      if(selectedResults.size<2||selectedResults.size>20) throw new Error('请选择 2–20 个测试任务');
      taskKey=crypto.randomUUID(); showModal('生成综合对比',`<form id="comparison-form"><label>对比报告名称<input name="name" value="设备测试综合对比" required maxlength="200"></label><p class="muted small">将读取所选 ${selectedResults.size} 个任务的原始证据，重新验收后按可比条件分组。计算由 Worker 执行。</p>${formActions('提交对比任务')}</form>`); break;
    case 'new-user': userEditor(); break;
    case 'edit-user': userEditor(id); break;
    case 'resolve': showModal('核实设备执行状态',`<form id="resolve-form" data-id="${id}"><p class="result-note">请先在厂商控制台确认该设备关联的未知作业已结束或已取消。解除阻塞后，排队任务会继续执行。</p><label>核实记录<textarea name="note" required rows="4" maxlength="1000" placeholder="记录核查的作业、终态与处理情况"></textarea></label>${formActions('记录并解除阻塞')}</form>`); break;
  }
}
document.addEventListener('click', async event=>{
  const el=event.target.closest('[data-action]'); if(!el) return;
  el.disabled=true;
  try { const err=$('.form-error',$('#modal')); if(err) err.textContent=''; await handleAction(el); }
  catch(error) { const err=$('.form-error',$('#modal')); if($('#modal').open&&err) err.textContent=error.message; else notify(error.message); }
  finally { if(el.isConnected) el.disabled=false; }
});
document.addEventListener('submit', async event=>{
  event.preventDefault(); const form=event.target, submit=$('button[type=submit]',form); if(submit?.disabled) return;
  if(submit) submit.disabled=true;
  const errorEl=$('.form-error',form)||$('#login-error'); errorEl.textContent='';
  try {
    const f=new FormData(form);
    if(form.id==='login-form') { await api('/login',{method:'POST',body:JSON.stringify({username:f.get('username'),password:f.get('password')})}); form.reset(); await enter(); }
    if(form.id==='case-form') await saveCase(form);
    if(form.id==='task-form') await submitTask();
    if(form.id==='comparison-form') { const t=await api('/comparisons',{method:'POST',headers:{'Idempotency-Key':taskKey},body:JSON.stringify({name:f.get('name'),task_ids:[...selectedResults]})}); $('#modal').close(); location.hash='tasks/'+t.id; }
    if(form.id==='user-form') { const id=form.dataset.id; const data={username:f.get('username'),role:f.get('role'),password:f.get('password'),enabled:f.has('enabled')}; await api('/users'+(id?'/'+id:''),{method:id?'PUT':'POST',body:JSON.stringify(data)}); $('#modal').close(); notify('账号已保存'); await navigate(); }
    if(form.id==='resolve-form') { await api('/devices/'+form.dataset.id+'/resolve',{method:'POST',body:JSON.stringify({note:f.get('note')})}); $('#modal').close(); notify('设备阻塞已解除'); await navigate(); }
  } catch(error) { errorEl.textContent=error.message; }
  finally { if(submit?.isConnected) submit.disabled=false; }
});
document.addEventListener('input', event=>{
  const el=event.target;
  if(el.id==='case-search') { filterText=el.value; $('#case-list').innerHTML=caseCards(); }
  if(el.id==='task-search') { filterText=el.value; $('#task-list').innerHTML=taskRows(filteredTasks(route==='results'),route==='results'&&canEdit()); }
  if(el.closest('#task-form') && $('#submit-task')) { planSignature=null; $('#submit-task').disabled=true; $('#plan-result').innerHTML=''; }
});
document.addEventListener('change',event=>{
  const el=event.target;
  if(el.id==='show-archived') { showArchived=el.checked; $('#case-list').innerHTML=caseCards(); }
  if(el.id==='status-filter') { filterStatus=el.value; $('#task-list').innerHTML=taskRows(filteredTasks(route==='results'),route==='results'&&canEdit()); }
  if(el.dataset.compareId) { if(el.checked) selectedResults.add(el.dataset.compareId); else selectedResults.delete(el.dataset.compareId); $('#selection-count').textContent='已选择 '+selectedResults.size+' 个任务'; }
  if(el.id==='protocol-select') { const t=catalog.protocols.find(p=>p.id===el.value).template; $('[name=parameters]',$('#case-form')).value=JSON.stringify(t.parameters,null,2); }
});
$('#logout').addEventListener('click',async()=>{try{await api('/logout',{method:'POST',body:'{}'});showLogin();}catch(error){notify(error.message);}});
$('#mobile-logout').addEventListener('click',()=>$('#logout').click());
window.addEventListener('hashchange',navigate);
setInterval(async()=>{
  if(!user || polling || document.hidden || $('#modal').open) return;
  if(!['overview','tasks'].includes(route) && !route.startsWith('tasks/')) return;
  polling=true; const ticket=generation;
  try {
    overview=await api('/overview'); if(ticket!==generation) return; updateHeartbeat();
    if(route.startsWith('tasks/') && active.includes(currentTask?.status)) await renderDetail(route.split('/')[1],ticket);
    else if(route==='tasks'||route==='overview') { tasks=await api('/tasks'); if(ticket!==generation) return; if(route==='tasks') $('#task-list').innerHTML=taskRows(filteredTasks()); else renderOverview(); }
  } catch(error) { if(user) { $('#worker-label').textContent='连接暂时中断'; $('#worker-dot').classList.add('offline'); } }
  finally { polling=false; }
},2000);
enter().catch(()=>showLogin());
