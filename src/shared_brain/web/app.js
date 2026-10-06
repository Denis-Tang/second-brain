const $ = (id) => document.getElementById(id);
const pages = {
  projects: "项目配置",
  overview: "总观",
  settings: "设置",
};
let toastTimer;
let activePage = "overview";
let overviewPeriod = "24h";
let vaultReady = false;
let vaultPath = "";
let updatePrompt = "";
let updateArchive = "";
let updatePoll = 0;
let updatePolling = false;

function toast(message, error = false) {
  $("toast").textContent = message;
  $("toast").className = error ? "error" : "";
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, error ? 7000 : 4000);
}

async function call(method, ...args) {
  const result = await window.desktop[method](...args);
  if (result.error) throw new Error(result.error);
  return result;
}

async function action(button, work) {
  if (button) { button.disabled = true; button.dataset.busy = "true"; }
  try { await work(); }
  catch (error) { toast(error.message || String(error), true); }
  finally { if (button) { button.disabled = false; delete button.dataset.busy; } }
}

async function refresh(fillForms = false) {
  const state = await call("status");
  const settings = state.settings;
  const budget = state.budget;
  $("budget-status").textContent = budget ? `本月已用/预留 ¥${budget.used_or_reserved_cny.toFixed(4)} / ¥${budget.monthly_limit_cny} · 剩余 ¥${budget.remaining_cny.toFixed(4)} · 今日 ${budget.today_calls}/3 批` : "";
  vaultReady = state.ready;
  vaultPath = settings.vault_path || "";
  $("ready-label").textContent = state.ready ? "已就绪" : (vaultPath ? "待保存" : "未设置");
  if (state.version) {
    $("app-version").textContent = `v${state.version}`;
  }
  $("ready-dot").classList.toggle("ready", state.ready);
  $("key-status").textContent = state.key_configured ? "密钥已配置" : "密钥未配置";
  $("key-status").classList.toggle("configured", state.key_configured);
  if (fillForms) {
    $("vault-path").value = settings.vault_path || "";
    $("base-url").value = settings.base_url;
    $("model").value = settings.model;
    $("maintenance-enabled").checked = settings.maintenance_enabled;
    $("maintenance-time").value = settings.maintenance_time;
    $("global-prompt").value = state.global_prompt || "";
  }
  updatePromptAvailability();
  activatePage(fillForms && !state.ready ? "settings" : activePage);
  if (activePage === "overview") await refreshOverview();
  if (activePage === "projects") await refreshProjects();
  if (state.background_error) toast(state.background_error, true);
  return state;
}

function updatePromptAvailability() {
  const ready = vaultReady && $("vault-path").value.trim() === vaultPath;
  document.querySelectorAll("[data-copy-vault], [data-copy-agent], [data-copy-unbind]").forEach((button) => {
    button.disabled = !ready || button.dataset.busy === "true";
  });
  $("global-prompt").disabled = !ready;
  $("save-global-prompt").disabled = !ready;
  $("prompt-vault").textContent = ready ? `当前知识库：${vaultPath}` : "请先保存知识库设置。";
}

function activatePage(page) {
  activePage = page;
  $("new-project").hidden = page !== "projects";
  $("open-setup-guide").hidden = page !== "settings";
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.page === page));
  document.querySelectorAll(".page").forEach((item) => item.classList.toggle("active", item.id === page));
  $("page-title").textContent = page === "settings" && !vaultReady ? "首次设置" : pages[page];
}

let selectedMetric = "sessions";
let overviewMetrics = [];

function renderMetrics(metrics) {
  overviewMetrics = metrics;
  $("growth-cards").replaceChildren();
  for (const metric of metrics) {
    const card = document.createElement("button");
    card.className = "stat";
    card.classList.toggle("active", metric.key === selectedMetric);
    card.setAttribute("aria-pressed", metric.key === selectedMetric);
    const label = document.createElement("small");
    label.textContent = metric.key === "estimated_tokens" ? "知识库 token" : metric.label;
    const total = document.createElement("strong");
    total.textContent = Number(metric.current).toLocaleString("zh-CN");
    const change = document.createElement("em");
    const delta = metric.current - (metric.points[0]?.value ?? metric.current);
    change.textContent = metric.points.length ? `${delta >= 0 ? "增加" : "减少"} ${Math.abs(delta).toLocaleString("zh-CN")} ${metric.unit}` : "暂无记录";
    card.append(label, total, change);
    card.onclick = () => { selectedMetric = metric.key; renderMetrics(overviewMetrics); };
    $("growth-cards").append(card);
  }
  renderHistory(metrics.find(metric => metric.key === selectedMetric));
}

function renderHistory(metric) {
  const plot = $("chart");
  plot.classList.remove("tracking");
  plot.onpointermove = plot.onpointerleave = null;
  plot.replaceChildren();
  $("chart-title").textContent = metric.label;
  $("axis-start").textContent = $("axis-end").textContent = "";
  if (!metric.points.length) {
    const empty = document.createElement("p");empty.className = "chart-empty";empty.textContent = "暂无历史记录";plot.append(empty);return;
  }
  const points = metric.points, values = points.map(p => p.value), times = points.map(p => new Date(p.at).getTime());
  const start = times[0], end = times[times.length-1], low = Math.min(...values), high = Math.max(...values);
  const coords = points.map((p,i) => [end === start ? 350 : 42 + (times[i]-start)/(end-start)*624, high === low ? 90 : 156-(p.value-low)/(high-low)*132]);
  const stamp = time => new Date(time).toLocaleString("zh-CN", {month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"});
  $("axis-start").textContent = stamp(start);$("axis-end").textContent = stamp(end);
  plot.innerHTML = `<svg viewBox="0 0 700 180" preserveAspectRatio="none" role="img" aria-label="历史变化"><defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#729ae5" stop-opacity=".23"/><stop offset="100%" stop-color="#729ae5" stop-opacity="0"/></linearGradient></defs>${[24,90,156].map(y=>`<line x1="42" x2="666" y1="${y}" y2="${y}" stroke="#edf1f7"/>`).join('')}<path d="M${coords[0][0]} 166 L${coords.map(p=>p.join(' ')).join(' L')} L${coords[coords.length-1][0]} 166 Z" fill="url(#fill)"/><polyline points="${coords.map(p=>p.join(',')).join(' ')}" fill="none" stroke="#497cdf" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/><line id="cross" y1="12" y2="166" stroke="#a6bde6" stroke-dasharray="4" visibility="hidden"/><circle id="point" r="5" fill="#497cdf" stroke="white" stroke-width="2" visibility="hidden"/></svg><div id="chart-tip" class="chart-tip" hidden><span></span><strong></strong></div>`;
  const cross=$("cross"),point=$("point"),tip=$("chart-tip");let selected=null;
  plot.onpointermove=e=>{
    const r=plot.getBoundingClientRect(),cursor=(e.clientX-r.left)/r.width*700;
    let i=0;for(let j=1;j<coords.length;j++)if(Math.abs(coords[j][0]-cursor)<Math.abs(coords[i][0]-cursor))i=j;
    if(i===selected)return;
    const p=coords[i];cross.style.transform=`translateX(${p[0]}px)`;cross.setAttribute('visibility','visible');
    point.style.transform=`translate(${p[0]}px,${p[1]}px)`;point.setAttribute('visibility','visible');
    tip.firstElementChild.textContent=stamp(times[i]);tip.lastElementChild.textContent=`${values[i].toLocaleString()} ${metric.unit}`;tip.hidden=false;
    const x=p[0]/700*r.width,y=p[1]/180*r.height,w=tip.offsetWidth,h=tip.offsetHeight;
    const left=Math.max(8,Math.min(r.width-w-8,x+w+20<=r.width?x+12:x-w-12)),top=Math.max(8,Math.min(r.height-h-8,y-h/2));
    tip.style.transform=`translate(${left}px,${top}px)`;
    if(selected===null){void tip.offsetWidth;plot.classList.add('tracking')}selected=i;
  };
  plot.onpointerleave=()=>{cross.setAttribute('visibility','hidden');point.setAttribute('visibility','hidden');tip.hidden=true;selected=null;plot.classList.remove('tracking')};
}

function heatTip(e,item){
  $("tooltip").textContent=`${item.date} · ${item.value === null ? "未记录" : "新增 "+item.value.toLocaleString()+" token"}`;
  $("tooltip").hidden=false;const r=$("tooltip").getBoundingClientRect();
  $("tooltip").style.left=Math.max(8,Math.min(innerWidth-r.width-8,e.clientX+12))+'px';
  $("tooltip").style.top=Math.max(8,e.clientY-r.height-12)+'px';
}

function renderHeatmap(activity){
  $("heat-grid").replaceChildren();$("tooltip").hidden=true;
  const colors=['#ebedf0','#a7f3d0','#6ee7b7','#34d399','#10b981'];
  const values=new Map(activity.days.map(d=>[d.day,d.tokens]));
  const localDay=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  const today=new Date(),start=new Date(today.getFullYear(),today.getMonth(),today.getDate()-today.getDay()-51*7,12),end=localDay(today);
  const days=Array.from({length:364},(_,i)=>{const d=new Date(start);d.setDate(start.getDate()+i);const date=localDay(d);return {date,value:activity.since && date>=activity.since && date<=end ? values.get(date)||0 : null}});
  const max=Math.max(1,...days.map(d=>d.value||0)),halves=[];
  for(let half=0;half<2;half++){const section=document.createElement('div');section.innerHTML='<div class="month-row"></div><div class="heat-weeks"></div>';$("heat-grid").append(section);halves.push(section)}
  days.forEach((d,i)=>{
    const half=Math.floor(i/182),week=Math.floor(i%182/7),section=halves[half],level=d.value?Math.max(1,Math.ceil(d.value/max*4)):0;
    if(i%182===0||(d.date.slice(8)==='01'&&week<24)){const month=document.createElement('span');month.textContent=Number(d.date.slice(5,7))+'月';month.style.gridColumn=week+1;section.firstElementChild.append(month)}
    const cell=document.createElement('button');cell.className='heat-cell';cell.style.background=colors[level];cell.classList.toggle('unrecorded',d.value===null);
    cell.setAttribute('aria-label',`${d.date} ${d.value===null?'未记录':d.value+' token'}`);cell.onpointermove=e=>heatTip(e,d);cell.onpointerleave=()=>$("tooltip").hidden=true;section.lastElementChild.append(cell);

  });
  terrainDays=days.map(d=>({...d,height:3+(d.value||0)/max*48,color:colors[d.value?Math.max(1,Math.ceil(d.value/max*4)):0]}));
  transform();
  $("heat-period").textContent="过去 52 周";
  $("growth-note").textContent=activity.since?`新增 token 从 ${activity.since} 开始记录，斜纹日期尚无记录。`:"选择知识库后，开始记录每天新增的内容。";
}

async function refreshOverview() {
  const result = await call("overview", overviewPeriod);
  if (result.period === overviewPeriod) { renderMetrics(result.metrics); renderHeatmap(result.activity); }
}

document.querySelectorAll("[data-page]").forEach((button) => {
  button.addEventListener("click", () => action(null, async () => {
    const page = button.dataset.page;
    activatePage(page);
    if (page === "overview") await refreshOverview();
    if (page === "projects") await refreshProjects();

  }));
});

document.querySelectorAll("[data-period]").forEach((button) => {
  button.addEventListener("click", () => action(button, async () => {
    overviewPeriod = button.dataset.period;
    document.querySelectorAll("[data-period]").forEach((item) => item.setAttribute("aria-pressed", item === button));
    await refreshOverview();
  }));
});

$("choose-vault").addEventListener("click", (event) => action(event.currentTarget, async () => {
  const result = await call("choose_vault");
  if (result.path) $("vault-path").value = result.path;
  updatePromptAvailability();
}));

$("open-setup-guide").addEventListener("click", () => $("setup-guide").showModal());
$("close-setup-guide").addEventListener("click", () => $("setup-guide").close());

$("vault-path").addEventListener("input", updatePromptAvailability);

document.querySelectorAll("[data-copy-vault]").forEach((button) => {
  button.addEventListener("click", () => action(button, async () => {
    await navigator.clipboard.writeText(vaultPath);
    toast("知识库地址已复制");
  }));
});

$("clear-key").addEventListener("change", () => {
  $("api-key").disabled = $("clear-key").checked;
  if ($("clear-key").checked) $("api-key").value = "";
});

$("settings-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(event.submitter, async () => {
    const values = {
      vault_path: $("vault-path").value.trim(),
      base_url: $("base-url").value.trim(),
      model: $("model").value.trim(),
      maintenance_enabled: $("maintenance-enabled").checked,
      maintenance_time: $("maintenance-time").value,
    };
    const result = await call("configure", values, $("clear-key").checked ? "" : ($("api-key").value || null));
    $("api-key").value = "";
    $("api-key").disabled = false;
    $("clear-key").checked = false;
    await refresh(true);
    toast(result.message);
  });
});

$("test-button").addEventListener("click", (event) => action(event.currentTarget, async () => {
  toast((await call("test_connection")).message);
}));

document.querySelectorAll("[data-copy-agent]").forEach((button) => {
  button.addEventListener("click", () => action(button, async () => {
    const result = await call("agent_prompt");
    await navigator.clipboard.writeText(result.text);
    toast("Agent 接入提示词已复制");
  }));
});

document.querySelectorAll("[data-copy-unbind]").forEach((button) => {
  button.addEventListener("click", () => action(button, async () => {
    const result = await call("unbind_prompt");
    await navigator.clipboard.writeText(result.text);
    toast("解绑提示词已复制，请发送给要解绑的 Agent");
  }));
});

function renderUpdateStaged(result) {
  const staged = $("update-staged");
  staged.textContent = result.local_archive
    ? `已下载并校验：${result.local_archive}（本程序没有替换任何文件）` : "";
  staged.hidden = !result.local_archive;
  if (result.local_archive) updateArchive = result.local_archive;
  $("update-download").hidden = !result.newer || !!result.local_archive;
  $("update-apply").hidden = !(result.newer && !!result.local_archive);
  $("update-folder").hidden = !updateArchive;
}

function renderUpdateProgress(state) {
  const running = state.stage === "downloading";
  $("update-progress").hidden = !state.stage || state.stage === "idle";
  $("update-bar-fill").style.width = `${state.percent || 0}%`;
  $("update-progress-text").textContent = state.message || "";
  $("update-download").textContent = running ? "取消下载" : "下载并校验";
}

function stopUpdatePolling() {
  if (updatePoll) { clearInterval(updatePoll); updatePoll = 0; }
}

function startUpdatePolling() {
  stopUpdatePolling();
  updatePoll = setInterval(() => action(null, pollUpdateDownload), 500);
}

async function pollUpdateDownload() {
  if (updatePolling) return;
  updatePolling = true;
  try {
    const state = await call("update_download_state");
    renderUpdateProgress(state);
    if (state.stage === "done") {
      stopUpdatePolling();
      updateArchive = state.path || updateArchive;
      const refreshed = await call("check_update");
      updatePrompt = refreshed.prompt || updatePrompt;
      renderUpdateStaged(refreshed);
      $("update-copy").hidden = !updatePrompt;
      toast(state.message);
    } else if (state.stage === "failed") {
      stopUpdatePolling();
      toast(state.message || "下载失败", true);
    } else if (state.stage === "idle") {
      stopUpdatePolling();
    }
  } finally {
    updatePolling = false;
  }
}

async function resumeUpdateProgress() {
  const state = await call("update_download_state");
  renderUpdateProgress(state);
  if (state.stage === "downloading") startUpdatePolling();
}

function showUpdateDialog(result) {
  updatePrompt = result.newer ? (result.prompt || "") : "";
  $("update-title").textContent = result.newer ? `发现新版本 v${result.latest}` : "检查更新";
  const summary = [result.message];
  if (result.published_at) summary.push(`发布于 ${String(result.published_at).slice(0, 10)}`);
  if (result.cached && result.available) summary.push("（最近一次检查的结果）");
  $("update-summary").textContent = summary.filter(Boolean).join(" · ");
  const notes = $("update-notes");
  notes.textContent = result.notes || "";
  notes.hidden = !result.notes;
  const asset = result.asset || {};
  const size = asset.size ? ` · 约 ${(asset.size / 1048576).toFixed(1)} MB` : "";
  const digest = asset.sha256 ? ` · SHA256 ${String(asset.sha256).slice(0, 12)}…` : "";
  $("update-asset").textContent = result.newer
    ? `更新时保持程序目录路径不变，MCP 与 Hook 配置无需修改${size}${digest}` : "";
  updateArchive = "";
  renderUpdateStaged(result);
  renderUpdateProgress({stage: "idle"});
  $("update-copy").hidden = !updatePrompt;
  $("update-dialog").showModal();
  action(null, resumeUpdateProgress);
}

$("app-version").addEventListener("click", (event) => action(event.currentTarget, async () => {
  showUpdateDialog(await call("check_update"));
}));

$("update-close").addEventListener("click", () => {
  stopUpdatePolling();
  $("update-dialog").close();
});

$("update-download").addEventListener("click", (event) => action(event.currentTarget, async () => {
  if ($("update-download").textContent === "取消下载") {
    renderUpdateProgress(await call("cancel_download"));
    return;
  }
  const state = await call("download_update");
  renderUpdateProgress(state);
  if (!state.started) {
    if (state.message) toast(state.message, true);
    return;
  }
  startUpdatePolling();
}));

$("update-folder").addEventListener("click", (event) => action(event.currentTarget, async () => {
  await call("open_download_folder", updateArchive || "");
}));

$("update-apply").addEventListener("click", (event) => action(event.currentTarget, async () => {
  const state = await call("apply_update");
  if (!state.started) {
    toast(state.message || "无法开始更新", true);
    return;
  }
  event.currentTarget.textContent = "正在更新…";
  $("update-progress").hidden = false;
  $("update-progress-text").textContent = state.message || "正在退出并替换，程序稍后会自动重启…";
  toast(state.message || "正在退出并替换，程序稍后会自动重启…");
}));

$("update-copy").addEventListener("click", (event) => action(event.currentTarget, async () => {
  await navigator.clipboard.writeText(updatePrompt);
  toast("更新提示词已复制，请发送给要执行更新的 Agent");
}));

$("update-page").addEventListener("click", (event) => action(event.currentTarget, async () => {
  const result = await call("open_release_page");
  if (!result.opened) toast("没能自动打开浏览器，请手动访问 GitHub Releases 页面", true);
}));

window.addEventListener("desktopready", () => {
  action(null, () => refresh(true));
  setInterval(() => action(null, () => refresh()), 30000);
});

$("global-prompt-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(event.submitter, async () => {
    const result = await call("save_global_prompt", $("global-prompt").value);
    $("global-prompt").value = result.global_prompt;
    toast(result.message);
  });
});

let editingProject = "";
let projectPaths = [];

function projectButton(label, work) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "button secondary";
  button.textContent = label;
  button.addEventListener("click", () => action(button, work));
  return button;
}

async function copyProjectText(text) {
  await navigator.clipboard.writeText(text);
  toast("已复制");
}

function renderProjectPaths() {
  $("project-paths").replaceChildren();
  projectPaths.forEach((path, index) => {
    const row = document.createElement("div");
    row.className = "project-path-row";
    const label = document.createElement("span");
    label.textContent = path;
    row.append(label, projectButton("复制路径", () => copyProjectText(path)), projectButton("移除", () => {
      projectPaths.splice(index, 1);
      renderProjectPaths();
    }));
    $("project-paths").append(row);
  });
}

function editProject(project) {
  editingProject = project?.project_id || "";
  projectPaths = [...(project?.paths || [])];
  $("project-name").value = project?.name || "";
  $("project-form-title").textContent = project ? "编辑项目" : "新增项目";
  $("save-project").textContent = project ? "保存修改" : "创建项目";
  renderProjectPaths();
}

async function refreshProjects() {
  const result = vaultReady ? await call("projects") : { projects: [] };
  $("project-count").textContent = `全部项目（${result.projects.length}）`;
  $("new-project").disabled = !vaultReady;
  $("project-list").replaceChildren();
  if (!result.projects.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = vaultReady ? "暂无项目" : "请先在设置中保存知识库路径。";
    $("project-list").append(empty);
  }
  for (const project of result.projects) {
    const card = document.createElement("article");
    card.className = "card project-card";
    const heading = document.createElement("div");
    heading.className = "project-heading";
    const title = document.createElement("h2");
    title.textContent = project.name;
    const actions = document.createElement("div");
    actions.className = "project-actions";
    const menu = document.createElement("details");
    menu.className = "project-menu";
    const summary = document.createElement("summary");
    summary.textContent = "···";
    summary.ariaLabel = "更多操作";
    const menuItems = document.createElement("div");
    menuItems.className = "project-menu-items";
    menuItems.append(projectButton("复制名称", () => {
      menu.open = false;
      summary.focus();
      return copyProjectText(project.name);
    }), projectButton("删除项目", () => {
      menu.open = false;
      summary.focus();
      $("delete-project-dialog").dataset.projectId = project.project_id;
      $("delete-project-question").textContent = `确定删除“${project.name}”的立项信息吗？`;
      $("delete-project-dialog").showModal();
    }));
    menuItems.lastChild.className = "button destructive";
    menu.append(summary, menuItems);
    actions.append(projectButton("编辑", () => {
      editProject(project);
      $("project-editor").showModal();
    }), menu);
    heading.append(title, actions);
    const paths = document.createElement("div");
    paths.className = "project-field";
    const pathsLabel = document.createElement("h3");
    pathsLabel.textContent = "工作目录";
    paths.append(pathsLabel);
    if (!project.paths.length) {
      const note = document.createElement("p");
      note.className = "hint";
      note.textContent = "未关联";
      paths.append(note);
    }
    for (const path of project.paths) {
      const row = document.createElement("div");
      row.className = "project-path-row";
      const label = document.createElement("span");
      label.textContent = path;
      row.append(label, projectButton("复制路径", () => copyProjectText(path)));
      paths.append(row);
    }
    const directory = document.createElement("div");
    directory.className = "project-field project-directory";
    const directoryLabel = document.createElement("h3");
    directoryLabel.textContent = "项目资料";
    const directoryPath = document.createElement("p");
    directoryPath.textContent = project.directory;
    directory.append(directoryLabel, directoryPath);
    card.append(heading, paths, directory);
    $("project-list").append(card);
  }
}

$("add-project-path").addEventListener("click", (event) => action(event.currentTarget, async () => {
  const result = await call("choose_vault");
  if (result.path && !projectPaths.includes(result.path)) projectPaths.push(result.path);
  renderProjectPaths();
}));

$("new-project").addEventListener("click", () => {
  editProject(null);
  $("project-editor").showModal();
});
$("cancel-project").addEventListener("click", () => $("project-editor").close());
$("cancel-delete-project").addEventListener("click", () => $("delete-project-dialog").close());
$("confirm-delete-project").addEventListener("click", (event) => action(event.currentTarget, async () => {
  const result = await call("delete_project", $("delete-project-dialog").dataset.projectId);
  $("delete-project-dialog").close();
  await refreshProjects();
  toast(result.message);
}));
$("project-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(event.submitter, async () => {
    const result = await call("configure_project", $("project-name").value.trim(), projectPaths, editingProject);
    $("project-editor").close();
    await refreshProjects();
    toast(result.message);
  });
});

let terrainDays=[],terrainFaces=[],terrainVisible=false;
const terrain=$('terrain'),terrainContext=terrain.getContext('2d');
function drawTerrain(){
  const rect=terrain.getBoundingClientRect(),ratio=Math.min(devicePixelRatio,2);
  const width=Math.round(rect.width*ratio),height=Math.round(rect.height*ratio);
  if(terrain.width!==width||terrain.height!==height){terrain.width=width;terrain.height=height}
  terrainContext.setTransform(ratio,0,0,ratio,0,0);
  terrainContext.clearRect(0,0,rect.width,rect.height);
  const a=shownAngle*Math.PI/180,t=shownTilt*Math.PI/180;
  const ca=Math.cos(a),sa=Math.sin(a),ct=Math.cos(t),st=Math.sin(t);
  const zoom=Math.min(1,(rect.width-24)/420)*shownScale;
  const project=([x,y,z])=>{const rx=x*ca-y*sa,ry=x*sa+y*ca;return [rect.width/2+rx*zoom,rect.height/2+(ry*ct-z*st)*zoom,ry*st+z*ct]};
  terrainFaces=[];
  // Select the two sides facing the camera, plus the top, for every solid column.
  const sides=[[[0,1,5,4],-ca*st],[[1,2,6,5],sa*st],[[2,3,7,6],ca*st],[[3,0,4,7],-sa*st]];
  terrainDays.forEach((day,i)=>{
    const x=Math.floor(i%182/7)*14-181,y=(Math.floor(i/182)*8+i%7)*14-103,h=day.height;
    const points=[[x,y,0],[x+11,y,0],[x+11,y+11,0],[x,y+11,0],[x,y,h],[x+11,y,h],[x+11,y+11,h],[x,y+11,h]].map(project);
    for(const [indices,light] of [[[4,5,6,7],1],...sides.filter(s=>s[1]>0)]){
      const vertices=indices.map(j=>points[j]);
      terrainFaces.push({vertices,depth:vertices.reduce((sum,p)=>sum+p[2],0)/4,day,shade:indices[0]===4?0:.16+(1-light)*.12});
    }
  });
  terrainFaces.sort((a,b)=>a.depth-b.depth);
  for(const face of terrainFaces){
    terrainContext.beginPath();face.vertices.forEach((p,i)=>i?terrainContext.lineTo(p[0],p[1]):terrainContext.moveTo(p[0],p[1]));terrainContext.closePath();
    terrainContext.fillStyle=face.day.color;terrainContext.fill();
    if(face.shade){terrainContext.fillStyle=`rgba(20,55,40,${face.shade})`;terrainContext.fill()}
    if(face.day.value===null){terrainContext.save();terrainContext.clip();terrainContext.strokeStyle='#80958d66';terrainContext.lineWidth=1;
      const xs=face.vertices.map(p=>p[0]),ys=face.vertices.map(p=>p[1]),left=Math.min(...xs),right=Math.max(...xs),top=Math.min(...ys),bottom=Math.max(...ys);
      for(let x=left-(bottom-top);x<right;x+=5){terrainContext.beginPath();terrainContext.moveTo(x,top);terrainContext.lineTo(x+bottom-top,bottom);terrainContext.stroke()}terrainContext.restore()}
  }
}
new ResizeObserver(()=>transform()).observe($('heat3d'));
new IntersectionObserver(entries=>{terrainVisible=entries[0].isIntersecting;transform()}).observe($('heat3d'));
document.addEventListener('visibilitychange',()=>transform());
let angle=-18,tilt=58,scale=1,auto=false,drag=null;
let shownAngle=angle,shownTilt=tilt,shownScale=scale,speed=0,pending=0,last=0;

function transform(){if(!pending){last=performance.now();pending=requestAnimationFrame(frame)}}
function frame(at){
  const dt=Math.min(at-last,64),mix=window.matchMedia('(prefers-reduced-motion:reduce)').matches?1:1-Math.exp(-dt/55);
  last=at;
  const targetSpeed=auto&&!drag&&terrainVisible&&!document.hidden&&!$('heat3d').hidden ? .008 : 0;
  speed+=(targetSpeed-speed)*(window.matchMedia('(prefers-reduced-motion:reduce)').matches?1:1-Math.exp(-dt/180));
  if(!drag&&!$('heat3d').hidden)angle+=speed*dt;
  shownAngle+=(angle-shownAngle)*mix;shownTilt+=(tilt-shownTilt)*mix;shownScale+=(scale-shownScale)*mix;
  const moving=Math.abs(angle-shownAngle)>.01||Math.abs(tilt-shownTilt)>.01||Math.abs(scale-shownScale)>.0001||Math.abs(speed)>.00001;
  if(!moving){shownAngle=angle;shownTilt=tilt;shownScale=scale;speed=0}
  if(!$('heat3d').hidden&&terrainVisible&&!document.hidden)drawTerrain();
  pending=moving||targetSpeed?requestAnimationFrame(frame):0;
}
for(const view of ['2d','3d'])$('view-'+view).onclick=()=>{
  $('heat2d').hidden=view!=='2d';$('heat3d').hidden=view!=='3d';$('rotate-controls').hidden=view!=='3d';
  $('view-2d').classList.toggle('active',view==='2d');$('view-3d').classList.toggle('active',view==='3d');$('tooltip').hidden=true;transform();
};
$('heat3d').onpointerdown=e=>{drag={x:e.clientX,y:e.clientY,angle,tilt};$('heat3d').setPointerCapture(e.pointerId);transform()};
$('heat3d').onpointermove=e=>{if(drag){angle=drag.angle+(e.clientX-drag.x)*.4;tilt=Math.max(15,Math.min(80,drag.tilt-(e.clientY-drag.y)*.3));transform();$('tooltip').hidden=true}else{
  const rect=terrain.getBoundingClientRect(),x=e.clientX-rect.left,y=e.clientY-rect.top;
  const face=[...terrainFaces].reverse().find(f=>{let inside=false;const p=f.vertices;for(let i=0,j=p.length-1;i<p.length;j=i++){if((p[i][1]>y)!==(p[j][1]>y)&&x<(p[j][0]-p[i][0])*(y-p[i][1])/(p[j][1]-p[i][1])+p[i][0])inside=!inside}return inside});
  if(face)heatTip(e,face.day);else $('tooltip').hidden=true;
}};
$('heat3d').onpointerleave=()=>$('tooltip').hidden=true;
$('heat3d').onpointerup=$('heat3d').onpointercancel=()=>{drag=null;transform()};
$('heat3d').onwheel=e=>{e.preventDefault();scale=Math.max(.5,Math.min(1.7,scale-e.deltaY*.001));transform()};
$('rotate').onclick=()=>{auto=!auto;$('rotate').textContent=auto?'停止旋转':'自动旋转';transform()};
$('reset').onclick=()=>{angle=-18;tilt=58;scale=1;auto=false;speed=0;$('rotate').textContent='自动旋转';transform()};
