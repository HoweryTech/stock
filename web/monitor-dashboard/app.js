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
const compactMoney = value => {
  if (value == null) return "--";
  const amount = Number(value);
  if (Math.abs(amount) >= 100000000) return `${(amount / 100000000).toFixed(2)}亿`;
  if (Math.abs(amount) >= 10000) return `${(amount / 10000).toFixed(1)}万`;
  return `${amount.toFixed(0)}元`;
};
const tone = value => Number(value) > 0 ? "positive" : Number(value) < 0 ? "negative" : "";
const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));

function adviceFor(item) {
  const action = currentActionFor(item);
  if (item.state === "data_stale") return "行情过期，暂停判断";
  if (item.reduction_plan?.status === "granularity_review") return "仓位略超限，100股减仓幅度过大，暂不机械降仓";
  if (action) return actionLabels[action.action] || action.action_label || "人工复核";
  if (item.state === "risk_review") return "优先处理风险，不新增仓位";
  if (item.state === "no_add_watch") return "等待趋势恢复，禁止补仓";
  return "继续观察，不执行交易";
}

function currentActionFor(item) {
  const action = state.actions.get(item.code);
  if (!action) return null;
  const liveSignals = new Set((item.signals || []).map(signal => signal.code));
  if (action.action === "exit_risk_review" && !liveSignals.has("limit_down_or_near")) return null;
  if (action.action === "risk_reduction_review" && item.reduction_plan?.status === "granularity_review") return null;
  return action;
}

function isReverseTCandidate(item) {
  return item.reverse_t_plan?.status === "candidate";
}

function isReverseTWatch(item) {
  return ["candidate", "watch"].includes(item.reverse_t_plan?.status);
}

function filteredItems() {
  const items = state.snapshot?.items || [];
  return items.filter(item => {
    const matchesFilter = state.filter === "all" || (state.filter === "reverse_t" ? isReverseTWatch(item) : item.state === state.filter);
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
  const reverseT = items.filter(isReverseTWatch).length;
  const blocks = [
    ["账户总资产", money(state.snapshot?.total_assets), "持仓基准"],
    ["持仓市值", money(marketValue), pct(marketValue / Number(state.snapshot?.total_assets || 1) * 100)],
    ["浮动盈亏", money(pnl), "按最新快照估算"],
    ["风险复核", `${risk} 只`, `${noAdd} 只禁止补仓`],
    ["反T可观察", `${reverseT} 只`, "其中形态触发后才是候选"],
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
  const reverseTag = isReverseTCandidate(item) ? '<div class="advice-tag">反T候选 · 先卖100股</div>' : "";
  return `<tr data-code="${item.code}" tabindex="0">
    <td><div class="stock-name">${escapeHtml(item.name)}</div><div class="stock-code">${item.code}</div></td>
    <td class="number"><div>${num(item.quote.latest_price)}</div><div class="secondary ${tone(item.quote.change_pct)}">${pct(item.quote.change_pct)}</div></td>
    <td class="number"><div class="${tone(item.position.unrealized_pnl)}">${money(item.position.unrealized_pnl)}</div><div class="secondary ${tone(item.position.return_pct)}">${pct(item.position.return_pct)}</div></td>
    <td class="number"><div>${pct(item.position.live_position_pct)}</div><div class="secondary">${Number(item.position.shares).toFixed(0)}股</div></td>
    <td><span class="state-badge state-${item.state}">${labels[item.state] || item.state}</span></td>
    <td class="advice">${escapeHtml(adviceFor(item))}${reverseTag}</td>
    <td class="number"><div class="${tone(item.capital_flow?.main_net_inflow)}">${compactMoney(item.capital_flow?.main_net_inflow)}</div><div class="secondary ${tone(item.capital_flow?.main_net_inflow_ratio_pct)}">${pct(item.capital_flow?.main_net_inflow_ratio_pct)}</div></td>
    <td class="number">${lag == null ? "--" : `${Number(lag).toFixed(1)}s`}</td>
  </tr>`;
}

function mobileCard(item) {
  const reverseTag = isReverseTCandidate(item) ? '<div class="advice-tag">反T候选 · 先卖100股</div>' : "";
  return `<article class="position-card" data-code="${item.code}" tabindex="0">
    <div class="card-top">
      <div><div class="stock-name">${escapeHtml(item.name)}</div><div class="stock-code">${item.code}</div></div>
      <div class="number"><strong>${num(item.quote.latest_price)}</strong><div class="secondary ${tone(item.quote.change_pct)}">${pct(item.quote.change_pct)}</div></div>
    </div>
    <div class="card-row"><span>持仓盈亏</span><span class="${tone(item.position.unrealized_pnl)}">${money(item.position.unrealized_pnl)} · ${pct(item.position.return_pct)}</span></div>
    <div class="card-row"><span class="state-badge state-${item.state}">${labels[item.state] || item.state}</span><span>仓位 ${pct(item.position.live_position_pct)}</span></div>
    <div class="card-row"><span>主力净额</span><span class="${tone(item.capital_flow?.main_net_inflow)}">${compactMoney(item.capital_flow?.main_net_inflow)} · ${pct(item.capital_flow?.main_net_inflow_ratio_pct)}</span></div>
    <div class="card-advice">${escapeHtml(adviceFor(item))}${reverseTag}</div>
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
  const action = currentActionFor(item);
  document.querySelector("#detailCode").textContent = code;
  document.querySelector("#detailName").textContent = item.name;
  const metrics = [
    ["现价", money(item.quote.latest_price)], ["当日涨跌", pct(item.quote.change_pct)],
    ["成本价", money(item.position.entry_price)], ["持仓收益", pct(item.position.return_pct)],
    ["持仓市值", money(item.position.market_value)], ["浮动盈亏", money(item.position.unrealized_pnl)],
    ["5日均线", money(item.technicals.ma5)], ["20日均线", money(item.technicals.ma20)],
  ];
  let html = detailSection("实时状态", `<div class="metric-grid">${metrics.map(([key, value]) => `<dl class="metric"><dt>${key}</dt><dd>${value}</dd></dl>`).join("")}</div>`);
  const decision = item.action_decision;
  if (decision) {
    html += detailSection("系统结论", `<p><strong>${escapeHtml(decision.headline)}</strong></p><p>${escapeHtml(decision.what_to_do_now)}</p><h4>什么时候再做</h4><ol class="reason-list">${decision.execute_when.map(condition => `<li>${escapeHtml(condition)}</li>`).join("")}</ol><h4>操作后的效果</h4><ul class="reason-list">${decision.expected_effects.map(effect => `<li>${escapeHtml(effect)}</li>`).join("")}</ul><p class="secondary">${escapeHtml(decision.prediction_note)}</p>`);
  }
  const multi = item.technicals?.multi_timeframe || {};
  const multiMetrics = [
    ["周线方向", multi.alignment === "bullish" ? "周月共振向上" : multi.alignment === "bearish" ? "周月共同偏弱" : multi.alignment === "mixed" ? "周期分歧" : "历史不足"],
    ["4周均价", money(multi.weekly_ma4)], ["12周均价", money(multi.weekly_ma12)],
    ["4周收益", pct(multi.weekly_return_4_pct)], ["3月均价", money(multi.monthly_ma3)],
    ["6月均价", money(multi.monthly_ma6)], ["3月收益", pct(multi.monthly_return_3_pct)],
  ];
  html += detailSection("日线 / 周线 / 月线", `<div class="metric-grid">${multiMetrics.map(([key, value]) => `<dl class="metric"><dt>${key}</dt><dd>${value}</dd></dl>`).join("")}</div>`);
  html += detailSection("当前建议", `<p><span class="state-badge state-${item.state}">${labels[item.state] || item.state}</span></p><p>${escapeHtml(adviceFor(item))}</p>${action?.reasons?.length ? `<ul class="reason-list">${action.reasons.map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>` : ""}`);
  html += detailSection("盘中信号", item.signals.length ? `<ul class="signal-list">${item.signals.map(signal => `<li>${escapeHtml(signal.message)}</li>`).join("")}</ul>` : "<p>当前没有新增盘中风险信号。</p>");
  const flow = item.capital_flow || {};
  const flowMetrics = [
    ["主力净额", compactMoney(flow.main_net_inflow)], ["主力净占比", pct(flow.main_net_inflow_ratio_pct)],
    ["超大单净额", compactMoney(flow.super_large_net_inflow)], ["大单净额", compactMoney(flow.large_net_inflow)],
    ["中单净额", compactMoney(flow.medium_net_inflow)], ["小单净额", compactMoney(flow.small_net_inflow)],
  ];
  html += detailSection("主力资金流", `<div class="metric-grid">${flowMetrics.map(([key, value]) => `<dl class="metric"><dt>${key}</dt><dd>${value}</dd></dl>`).join("")}</div><p class="secondary">${escapeHtml(flow.interpretation || "")}</p>`);
  const reversePlan = item.reverse_t_plan;
  if (reversePlan) {
    const reverseStatus = reversePlan.status === "candidate" ? "反T候选" : reversePlan.status === "watch" ? "等待形态" : reversePlan.status === "fee_blocked" ? "手续费阻断" : "仅供观察，不可执行";
    const zone = reversePlan.sell_zone ? `${num(reversePlan.sell_zone[0])}–${num(reversePlan.sell_zone[1])}元` : "--";
    const planMetrics = [
      ["状态", reverseStatus], ["试做数量", `${reversePlan.trade_shares || 100}股`],
      ["卖出观察区", zone], ["参考回补上限", money(reversePlan.buyback_max_price)],
      ["实际所需价差", pct(reversePlan.required_gap_pct)], ["占当前持仓", pct(reversePlan.trade_ratio_pct)],
      ["未回补后果", reversePlan.failure_as_reduction_acceptable ? "计入计划降仓" : "形成计划外减仓"],
      ["主力确认", reversePlan.main_flow_confirmation === "wait_for_weakening" ? "净流入偏强，等待转弱" : "未见强净流入阻断"],
    ];
    if (reversePlan.cost_estimate) {
      planMetrics.push(["预计总费用", money(reversePlan.cost_estimate.total_fees)]);
      planMetrics.push(["预计净收益", money(reversePlan.cost_estimate.net_profit)]);
    }
    planMetrics.push(["费用参数", reversePlan.cost_model_verified ? "已按交割单核验" : "保守假设，尚未核验"]);
    if (reversePlan.high_position_ratio_warning) planMetrics.push(["仓位风险", "单次涉及半仓，高风险"]);
    const blockers = reversePlan.blockers || [];
    html += detailSection("反T降低成本", `<div class="metric-grid">${planMetrics.map(([key, value]) => `<dl class="metric"><dt>${key}</dt><dd>${value}</dd></dl>`).join("")}</div><p>${escapeHtml(reversePlan.failure_result || "")}</p>${blockers.length ? `<ul class="reason-list">${blockers.map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>` : `<ol class="reason-list">${reversePlan.instructions.map(step => `<li>${escapeHtml(step)}</li>`).join("")}</ol>`}`);
  }
  const reductionPlan = item.reduction_plan;
  if (reductionPlan && !["within_limit", "unavailable"].includes(reductionPlan.status)) {
    const reductionMetrics = [
      ["当前仓位", pct(reductionPlan.current_position_pct)], ["目标上限", pct(reductionPlan.target_position_pct)],
      ["最少减少", `${reductionPlan.minimum_reduction_shares}股`], ["预计剩余", `${reductionPlan.remaining_shares}股`],
      ["降仓后仓位", pct(reductionPlan.post_reduction_position_pct)], ["减少比例", pct(reductionPlan.reduction_ratio_pct)],
      ["预计释放现金", money(reductionPlan.estimated_net_proceeds)], ["预计实现盈亏", money(reductionPlan.estimated_realized_pnl_after_fees)],
    ];
    html += detailSection("具体降仓步骤", `<div class="metric-grid">${reductionMetrics.map(([key, value]) => `<dl class="metric"><dt>${key}</dt><dd>${value}</dd></dl>`).join("")}</div><p>${escapeHtml(reductionPlan.objective || "")}</p><ol class="reason-list">${reductionPlan.steps.map(step => `<li>${escapeHtml(step)}</li>`).join("")}</ol>`);
  }
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
