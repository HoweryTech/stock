const state = {
  snapshot: null,
  research: new Map(),
  actions: new Map(),
  events: [],
  filter: "all",
  search: "",
  selectedCode: null,
};

const labels = {
  risk_review: "风险复核",
  no_add_watch: "禁止补仓",
  observe: "观察",
  data_stale: "数据失效",
};

const actionLabels = {
  exit_risk_review: "优先核验退出风险",
  risk_reduction_review: "优先评估降仓",
  fundamental_review: "优先复核基本面",
  hold_no_add: "持有观察，禁止补仓",
  t_watch_only: "做T观察，不执行",
  data_insufficient: "数据不足，暂不决策",
};

const money = value => value == null ? "--" : `¥${Number(value).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const pct = value => value == null ? "--" : `${Number(value).toFixed(2)}%`;
const num = (value, digits = 2) => value == null ? "--" : Number(value).toFixed(digits);
const tone = value => Number(value) > 0 ? "positive" : Number(value) < 0 ? "negative" : "";
const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));

function adviceFor(item) {
  const action = state.actions.get(item.code);
  if (item.state === "data_stale") return "行情过期，暂停判断";
  if (action) return actionLabels[action.action] || action.action_label || "人工复核";
  if (item.state === "risk_review") return "优先处理风险，不新增仓位";
  if (item.state === "no_add_watch") return "等待趋势恢复，禁止补仓";
  return "继续观察，不执行交易";
}

function filteredItems() {
  const items = state.snapshot?.items || [];
  return items.filter(item => {
    const matchesFilter = state.filter === "all" || item.state === state.filter;
    const query = state.search.trim().toLowerCase();
    const matchesSearch = !query || item.code.includes(query) || String(item.name).toLowerCase().includes(query);
    return matchesFilter && matchesSearch;
  });
}

function renderSummary() {
  const items = state.snapshot?.items || [];
  const marketValue = items.reduce((sum, item) => sum + Number(item.position.market_value || 0), 0);
  const pnl = items.reduce((sum, item) => sum + Number(item.position.unrealized_pnl || 0), 0);
  const risk = items.filter(item => item.state === "risk_review").length;
  const noAdd = items.filter(item => item.state === "no_add_watch").length;
  const maxLag = Math.max(0, ...items.map(item => Number(item.quote.quote_lag_seconds || 0)));
  const blocks = [
    ["账户总资产", money(state.snapshot?.total_assets), "持仓基准"],
    ["持仓市值", money(marketValue), pct(marketValue / Number(state.snapshot?.total_assets || 1) * 100)],
    ["浮动盈亏", money(pnl), "按最新快照估算"],
    ["风险复核", `${risk} 只`, `${noAdd} 只禁止补仓`],
    ["最大行情延迟", `${maxLag.toFixed(1)} 秒`, "超过60秒自动失效"],
  ];
  document.querySelector("#summaryBand").innerHTML = blocks.map(([label, value, sub]) => `
    <div class="summary-item">
      <div class="summary-label">${label}</div>
      <div class="summary-value ${label === "浮动盈亏" ? tone(pnl) : ""}">${value}</div>
      <div class="summary-sub">${sub}</div>
    </div>`).join("");
}

function tableRow(item) {
  const lag = item.quote.quote_lag_seconds;
  return `<tr data-code="${item.code}" tabindex="0">
    <td><div class="stock-name">${escapeHtml(item.name)}</div><div class="stock-code">${item.code}</div></td>
    <td class="number"><div>${num(item.quote.latest_price)}</div><div class="secondary ${tone(item.quote.change_pct)}">${pct(item.quote.change_pct)}</div></td>
    <td class="number"><div class="${tone(item.position.unrealized_pnl)}">${money(item.position.unrealized_pnl)}</div><div class="secondary ${tone(item.position.return_pct)}">${pct(item.position.return_pct)}</div></td>
    <td class="number"><div>${pct(item.position.live_position_pct)}</div><div class="secondary">${Number(item.position.shares).toFixed(0)}股</div></td>
    <td><span class="state-badge state-${item.state}">${labels[item.state] || item.state}</span></td>
    <td class="advice">${escapeHtml(adviceFor(item))}</td>
    <td class="number">${lag == null ? "--" : `${Number(lag).toFixed(1)}s`}</td>
  </tr>`;
}

function mobileCard(item) {
  return `<article class="position-card" data-code="${item.code}" tabindex="0">
    <div class="card-top">
      <div><div class="stock-name">${escapeHtml(item.name)}</div><div class="stock-code">${item.code}</div></div>
      <div class="number"><strong>${num(item.quote.latest_price)}</strong><div class="secondary ${tone(item.quote.change_pct)}">${pct(item.quote.change_pct)}</div></div>
    </div>
    <div class="card-row"><span>持仓盈亏</span><span class="${tone(item.position.unrealized_pnl)}">${money(item.position.unrealized_pnl)} · ${pct(item.position.return_pct)}</span></div>
    <div class="card-row"><span class="state-badge state-${item.state}">${labels[item.state] || item.state}</span><span>仓位 ${pct(item.position.live_position_pct)}</span></div>
    <div class="card-advice">${escapeHtml(adviceFor(item))}</div>
  </article>`;
}

function bindPositionOpeners() {
  document.querySelectorAll("[data-code]").forEach(element => {
    element.addEventListener("click", () => openDetail(element.dataset.code));
    element.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") openDetail(element.dataset.code);
    });
  });
}

function renderPositions() {
  const items = filteredItems();
  document.querySelector("#positionsBody").innerHTML = items.map(tableRow).join("");
  document.querySelector("#mobileList").innerHTML = items.map(mobileCard).join("");
  document.querySelector("#emptyState").hidden = items.length > 0;
  bindPositionOpeners();
}

function detailSection(title, body) {
  return `<section class="detail-section"><h3>${title}</h3>${body}</section>`;
}

function openDetail(code) {
  const item = state.snapshot?.items.find(entry => entry.code === code);
  if (!item) return;
  state.selectedCode = code;
  const research = state.research.get(code);
  const action = state.actions.get(code);
  document.querySelector("#detailCode").textContent = code;
  document.querySelector("#detailName").textContent = item.name;
  const metrics = [
    ["现价", money(item.quote.latest_price)], ["当日涨跌", pct(item.quote.change_pct)],
    ["成本价", money(item.position.entry_price)], ["持仓收益", pct(item.position.return_pct)],
    ["持仓市值", money(item.position.market_value)], ["浮动盈亏", money(item.position.unrealized_pnl)],
    ["5日均线", money(item.technicals.ma5)], ["20日均线", money(item.technicals.ma20)],
  ];
  let html = detailSection("实时状态", `<div class="metric-grid">${metrics.map(([key, value]) => `<dl class="metric"><dt>${key}</dt><dd>${value}</dd></dl>`).join("")}</div>`);
  html += detailSection("当前建议", `<p><span class="state-badge state-${item.state}">${labels[item.state] || item.state}</span></p><p>${escapeHtml(adviceFor(item))}</p>${action?.reasons?.length ? `<ul class="reason-list">${action.reasons.map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>` : ""}`);
  html += detailSection("盘中信号", item.signals.length ? `<ul class="signal-list">${item.signals.map(signal => `<li>${escapeHtml(signal.message)}</li>`).join("")}</ul>` : "<p>当前没有新增盘中风险信号。</p>");
  if (research) {
    const fin = research.latest_financials || {};
    const quote = research.quote_profile || {};
    const financialMetrics = [
      ["行业", quote.industry || "--"], ["PE(TTM)", num(quote.pe_ttm)],
      ["PB", num(quote.pb)], ["报告期", String(fin.report_date || "--").slice(0, 10)],
      ["营收同比", pct(fin.revenue_yoy_pct)], ["归母净利同比", pct(fin.parent_net_profit_yoy_pct)],
      ["ROE", pct(fin.roe_weighted_pct)], ["资产负债率", pct(fin.debt_ratio_pct)],
    ];
    html += detailSection("基本面快照", `<div class="metric-grid">${financialMetrics.map(([key, value]) => `<dl class="metric"><dt>${key}</dt><dd>${value}</dd></dl>`).join("")}</div>`);
    const flags = research.financial_review?.flags || [];
    const notices = research.risk_review?.matched_announcements || [];
    html += detailSection("待复核事项", `${flags.length ? `<ul class="reason-list">${flags.map(flag => `<li>${escapeHtml(flag.message)}</li>`).join("")}</ul>` : "<p>财务阈值未触发风险旗标。</p>"}${notices.length ? `<ul class="reason-list">${notices.map(notice => `<li>${escapeHtml(notice.title)}</li>`).join("")}</ul>` : ""}`);
  }
  document.querySelector("#detailContent").innerHTML = html;
  document.querySelector("#detailPanel").classList.add("open");
  document.querySelector("#detailPanel").setAttribute("aria-hidden", "false");
  document.querySelector("#scrim").hidden = false;
}

function closeDetail() {
  state.selectedCode = null;
  document.querySelector("#detailPanel").classList.remove("open");
  document.querySelector("#detailPanel").setAttribute("aria-hidden", "true");
  document.querySelector("#scrim").hidden = true;
}

function renderEvents() {
  const container = document.querySelector("#eventList");
  if (!state.events.length) {
    container.innerHTML = '<div class="empty-state">暂无状态变化事件</div>';
    return;
  }
  container.innerHTML = state.events.map(event => {
    const tags = Object.entries(event.signature || {}).map(([code, info]) => `<span class="event-tag">${code} · ${labels[info.state] || info.state}</span>`).join("");
    return `<article class="event-item"><div class="event-time">${escapeHtml(event.generated_at)}</div><div class="event-changes">${tags}</div></article>`;
  }).join("");
}

function updateHeader(status) {
  const generatedAt = state.snapshot?.generated_at;
  document.querySelector("#updatedAt").textContent = generatedAt ? `数据更新时间 ${generatedAt.replace("T", " ")}` : "等待第一轮数据";
  const dot = document.querySelector("#statusDot");
  dot.className = `status-dot ${status.running ? "online" : "offline"}`;
  document.querySelector("#monitorStatus").textContent = status.running ? "监控运行中" : "监控未运行";
  const maxLag = Math.max(0, ...(state.snapshot?.items || []).map(item => Number(item.quote.quote_lag_seconds || 0)));
  document.querySelector("#latencyText").textContent = `最大延迟 ${maxLag.toFixed(1)}秒`;
}

async function loadData() {
  try {
    const [snapshot, research, actions, status, events] = await Promise.all([
      fetch("/api/snapshot", { cache: "no-store" }).then(response => response.json()),
      fetch("/api/research", { cache: "no-store" }).then(response => response.json()),
      fetch("/api/action-draft", { cache: "no-store" }).then(response => response.json()),
      fetch("/api/status", { cache: "no-store" }).then(response => response.json()),
      fetch("/api/events?limit=20", { cache: "no-store" }).then(response => response.json()),
    ]);
    state.snapshot = snapshot;
    state.research = new Map((research.items || []).map(item => [item.code, item]));
    state.actions = new Map((actions.items || []).map(item => [item.stock_code, item]));
    state.events = events.events || [];
    updateHeader(status);
    renderSummary();
    renderPositions();
    renderEvents();
    if (state.selectedCode) openDetail(state.selectedCode);
  } catch (error) {
    document.querySelector("#monitorStatus").textContent = "数据连接失败";
    document.querySelector("#statusDot").className = "status-dot offline";
  }
}

document.querySelectorAll(".tab").forEach(button => button.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach(item => item.classList.toggle("active", item === button));
  document.querySelectorAll(".view").forEach(view => view.classList.remove("active"));
  document.querySelector(`#${button.dataset.view}View`).classList.add("active");
  document.querySelector("#positionsToolbar").style.display = button.dataset.view === "positions" ? "flex" : "none";
}));

document.querySelectorAll(".filter").forEach(button => button.addEventListener("click", () => {
  document.querySelectorAll(".filter").forEach(item => item.classList.toggle("active", item === button));
  state.filter = button.dataset.filter;
  renderPositions();
}));

document.querySelector("#searchInput").addEventListener("input", event => {
  state.search = event.target.value;
  renderPositions();
});
document.querySelector("#closeDetail").addEventListener("click", closeDetail);
document.querySelector("#scrim").addEventListener("click", closeDetail);
document.addEventListener("keydown", event => { if (event.key === "Escape") closeDetail(); });

loadData();
setInterval(loadData, 5000);
