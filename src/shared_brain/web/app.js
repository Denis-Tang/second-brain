const $ = (id) => document.getElementById(id);
const pages = {
  projects: ["项目配置", "让每个项目都有自己的记录。"],
  overview: ["总观", "看看知识库最近的积累。"],
  settings: ["设置", "选好知识库，就可以开始。"],
};
let toastTimer;
let activePage = "overview";
let overviewPeriod = "24h";
let vaultReady = false;
let vaultPath = "";

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
  $("ready-label").textContent = state.ready ? "知识库已就绪" : (vaultPath ? "等待保存设置" : "等待选择仓库");
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
  document.querySelectorAll("[data-copy-vault], [data-copy-agent]").forEach((button) => {
    button.disabled = !ready || button.dataset.busy === "true";
  });
  $("global-prompt").disabled = !ready;
  $("save-global-prompt").disabled = !ready;
  $("prompt-vault").textContent = ready ? `当前知识库：${vaultPath}` : "请先保存知识库设置。";
}

function activatePage(page) {
  activePage = page;
  $("open-setup-guide").hidden = page !== "settings";
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.page === page));
  document.querySelectorAll(".page").forEach((item) => item.classList.toggle("active", item.id === page));
  [$("page-title").textContent, $("page-description").textContent] = page === "settings" && !vaultReady
    ? ["首次设置", "选择文件夹并保存，再复制提示词接入 Agent。"] : pages[page];
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
    const empty = document.createElement("p");empty.className = "chart-empty";empty.textContent = "开始积累后，这里会显示变化。";plot.append(empty);return;
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
  $("heat-grid").replaceChildren();$("terrain").replaceChildren();$("tooltip").hidden=true;
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
    const bar=document.createElement('div');bar.className='column';bar.style.cssText=`grid-column:${week+1};grid-row:${half*8+i%7+1};--height:${3+(d.value||0)/max*48}px;--color:${colors[level]}`;
    bar.innerHTML='<span class="top"></span><span class="front"></span><span class="side"></span>';bar.classList.toggle('unrecorded',d.value===null);
    bar.onpointermove=e=>heatTip(e,d);bar.onpointerleave=()=>$("tooltip").hidden=true;$("terrain").append(bar);
  });
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
  $("project-form-title").textContent = project ? `编辑项目：${project.name}` : "新增项目";
  $("project-form-description").textContent = project ? "修改名称或关联的工作文件夹。" : "为项目选择一个或多个工作文件夹。";
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
    empty.textContent = vaultReady ? "还没有项目，点击“新增项目”开始。" : "请先在设置中保存知识库路径。";
    $("project-list").append(empty);
  }
  for (const project of result.projects) {
    const card = document.createElement("article");
    card.className = "card";
    const title = document.createElement("h2");
    title.textContent = project.name;
    const directory = document.createElement("p");
    directory.className = "hint project-directory";
    directory.textContent = `项目资料：${project.directory}`;
    const actions = document.createElement("div");
    actions.className = "action-row";
    actions.append(projectButton("编辑", () => {
      editProject(project);
      $("project-editor").showModal();
    }), projectButton("复制名称", () => copyProjectText(project.name)),
    projectButton("删除项目", () => {
      $("delete-project-dialog").dataset.projectId = project.project_id;
      $("delete-project-question").textContent = `确定删除“${project.name}”的立项信息吗？`;
      $("delete-project-dialog").showModal();
    }));
    actions.lastChild.title = "删除立项和路径绑定，保留历史资料与工作目录";
    card.append(title, directory, actions);
    if (!project.paths.length) {
      const note = document.createElement("p");
      note.className = "hint";
      note.textContent = "尚未关联工作文件夹。";
      card.append(note);
    }
    for (const path of project.paths) {
      const row = document.createElement("div");
      row.className = "project-path-row";
      const label = document.createElement("span");
      label.textContent = path;
      row.append(label, projectButton("复制路径", () => copyProjectText(path)));
      card.append(row);
    }
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

let angle=-18,tilt=58,scale=1,auto=false,drag=null;
let shownAngle=angle,shownTilt=tilt,shownScale=scale,speed=0,pending=0,last=0;

function transform(){if(!pending){last=performance.now();pending=requestAnimationFrame(frame)}}
function frame(at){
  const dt=Math.min(at-last,64),mix=window.matchMedia('(prefers-reduced-motion:reduce)').matches?1:1-Math.exp(-dt/55);
  last=at;
  const targetSpeed=auto&&!drag&&!$('heat3d').hidden ? .008 : 0;
  speed+=(targetSpeed-speed)*(window.matchMedia('(prefers-reduced-motion:reduce)').matches?1:1-Math.exp(-dt/180));
  if(!drag&&!$('heat3d').hidden)angle+=speed*dt;
  shownAngle+=(angle-shownAngle)*mix;shownTilt+=(tilt-shownTilt)*mix;shownScale+=(scale-shownScale)*mix;
  const moving=Math.abs(angle-shownAngle)>.01||Math.abs(tilt-shownTilt)>.01||Math.abs(scale-shownScale)>.0001||Math.abs(speed)>.00001;
  if(!moving){shownAngle=angle;shownTilt=tilt;shownScale=scale;speed=0}
  $('terrain').style.transform=`scale(${shownScale}) rotateX(${shownTilt}deg) rotateZ(${shownAngle}deg)`;
  pending=moving||targetSpeed?requestAnimationFrame(frame):0;
}
for(const view of ['2d','3d'])$('view-'+view).onclick=()=>{
  $('heat2d').hidden=view!=='2d';$('heat3d').hidden=view!=='3d';$('rotate-controls').hidden=view!=='3d';
  $('view-2d').classList.toggle('active',view==='2d');$('view-3d').classList.toggle('active',view==='3d');$('tooltip').hidden=true;transform();
};
$('heat3d').onpointerdown=e=>{drag={x:e.clientX,y:e.clientY,angle,tilt};$('heat3d').setPointerCapture(e.pointerId);transform()};
$('heat3d').onpointermove=e=>{if(drag){angle=drag.angle+(e.clientX-drag.x)*.4;tilt=Math.max(15,Math.min(80,drag.tilt-(e.clientY-drag.y)*.3));transform();$('tooltip').hidden=true}};
$('heat3d').onpointerup=$('heat3d').onpointercancel=()=>{drag=null;transform()};
$('heat3d').onwheel=e=>{e.preventDefault();scale=Math.max(.5,Math.min(1.7,scale-e.deltaY*.001));transform()};
$('rotate').onclick=()=>{auto=!auto;$('rotate').textContent=auto?'停止旋转':'自动旋转';transform()};
$('reset').onclick=()=>{angle=-18;tilt=58;scale=1;auto=false;speed=0;$('rotate').textContent='自动旋转';transform()};
