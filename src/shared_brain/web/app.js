const $ = (id) => document.getElementById(id);
const pages = {
  overview: ["第二大脑总观", "查看会话总结、知识与正文 token 的积累。"],
  settings: ["工作区与夜间维护", "选择 Vault，配置模型连接与每天的维护时间。"],
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
  const result = await window.pywebview.api[method](...args);
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
  $("ready-label").textContent = state.ready ? "Vault 已就绪" : (vaultPath ? "等待 Agent 配置" : "等待选择仓库");
  $("ready-dot").classList.toggle("ready", state.ready);
  document.querySelectorAll("[data-copy-vault], [data-copy-agent]").forEach((button) => {
    button.disabled = !vaultPath || button.dataset.busy === "true";
  });
  $("key-status").textContent = state.key_configured ? "密钥已配置" : "密钥未配置";
  $("key-status").classList.toggle("configured", state.key_configured);
  if (fillForms) {
    $("vault-path").value = settings.vault_path || "";
    $("base-url").value = settings.base_url;
    $("model").value = settings.model;
    $("maintenance-enabled").checked = settings.maintenance_enabled;
    $("maintenance-time").value = settings.maintenance_time;
  }
  activatePage(fillForms && !state.ready ? "settings" : activePage);
  if (activePage === "overview") await refreshOverview();
  if (state.background_error) toast(state.background_error, true);
  return state;
}

function activatePage(page) {
  activePage = page;
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.page === page));
  document.querySelectorAll(".page").forEach((item) => item.classList.toggle("active", item.id === page));
  [$("page-title").textContent, $("page-description").textContent] = page === "settings" && !vaultReady
    ? ["首次设置", "选择仓库路径，再把内置提示词交给 Agent 完成配置。"] : pages[page];
}

function svgElement(tag, attributes) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  return element;
}

function renderMetrics(metrics) {
  $("growth-cards").replaceChildren();
  const end = Date.now();
  const duration = { "24h": 24, "7d": 7 * 24, "30d": 30 * 24 }[overviewPeriod] * 3600000;
  const start = end - duration;
  metrics.forEach((metric, index) => {
    const card = document.createElement("article");
    card.className = "stat-card";
    card.style.setProperty("--chart-color", ["#345de3", "#2e917d", "#9670c8"][index]);
    const label = document.createElement("span");
    label.textContent = metric.label;
    const total = document.createElement("strong");
    total.id = "count-" + metric.key;
    total.textContent = Number(metric.current).toLocaleString("zh-CN");
    const unit = document.createElement("small");
    unit.textContent = `当前 · ${metric.unit}`;
    card.append(label, total, unit);
    const points = metric.points;
    if (!points.length) {
      const empty = document.createElement("p");
      empty.className = "chart-empty";
      empty.textContent = "暂无历史记录";
      card.append(empty);
    } else {
      const times = points.map((point) => new Date(point.at).getTime());
      const values = points.map((point) => point.value);
      const low = Math.min(...values), high = Math.max(...values);
      const last = times[times.length - 1];
      const coordinates = points.map((point, i) => [
        52 + (times[i] - start) / duration * 254,
        low === high ? 47 : 84 - (point.value - low) / (high - low) * 72,
      ]);
      const chart = svgElement("svg", { viewBox: "0 0 320 100", class: "growth-chart", role: "img", "aria-label": `${metric.label}历史，${points.length}条记录` });
      if (points.length > 1) chart.append(svgElement("polyline", { points: coordinates.map((point) => point.join(",")).join(" "), fill: "none", stroke: "currentColor", "stroke-width": 2.5, "stroke-linejoin": "round" }));
      const [x, y] = coordinates[coordinates.length - 1];
      const dot = svgElement("circle", { cx: x, cy: y, r: 3.5, fill: "currentColor" });
      const title = svgElement("title", {});
      title.textContent = `${new Date(last).toLocaleString("zh-CN")} · ${values[values.length - 1].toLocaleString("zh-CN")} ${metric.unit}`;
      dot.append(title);
      chart.append(dot);
      for (const [value, position] of high === low ? [[high, 50]] : [[high, 15], [low, 87]]) {
        const axisLabel = svgElement("text", { x: 0, y: position, class: "chart-tick" });
        axisLabel.textContent = Number(value).toLocaleString("zh-CN", { notation: "compact" });
        chart.append(axisLabel);
      }
      const axis = document.createElement("div");
      axis.className = "chart-axis";
      for (const at of [start, end]) {
        const time = document.createElement("time");
        time.dateTime = new Date(at).toISOString();
        time.textContent = new Date(at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
        axis.append(time);
      }
      card.append(chart, axis);
    }
    $("growth-cards").append(card);
  });
}

async function refreshOverview() {
  const result = await call("overview", overviewPeriod);
  if (result.period === overviewPeriod) renderMetrics(result.metrics);
}

document.querySelectorAll("[data-page]").forEach((button) => {
  button.addEventListener("click", () => action(null, async () => {
    const page = button.dataset.page;
    activatePage(page);
    if (page === "overview") await refreshOverview();

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
}));

$("initialize-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(event.submitter, async () => {
    const result = await call("initialize", $("vault-path").value.trim());
    await refresh(true);
    toast(result.message);
  });
});

document.querySelectorAll("[data-copy-vault]").forEach((button) => {
  button.addEventListener("click", () => action(button, async () => {
    await navigator.clipboard.writeText(vaultPath);
    toast("Vault 路径已复制");
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

window.addEventListener("pywebviewready", () => {
  action(null, () => refresh(true));
  setInterval(() => action(null, () => refresh()), 30000);
});
