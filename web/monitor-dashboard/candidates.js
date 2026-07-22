const candidateState = {
  search: "",
  exchange: "",
  board: "",
  industry: "",
  strategy: "",
  portfolio_fit_status: "",
  data_quality_status: "",
  technical_health_status: "",
  sort: "combined_score",
  direction: "desc",
  lastFilters: null,
};

const candidateLabels = {
  exchange: {SSE: "上证", SZSE: "深证", BSE: "北交所", UNKNOWN: "未知"},
  board: {
    sse_main: "沪市主板",
    szse_main: "深市主板",
    star: "科创板",
    chinext: "创业板",
    bse: "北交所",
    unknown: "未知",
  },
  portfolio_fit_status: {
    ready_for_plan: "可准备计划",
    watch: "观察复核",
    deferred_by_portfolio: "组合暂缓",
  },
  data_quality_status: {
    complete: "完整",
    partial: "部分",
    weak: "偏弱",
  },
  technical_health_status: {
    strong: "强",
    watch: "观察",
    weak: "偏弱",
    blocked: "拦截",
    insufficient: "样本不足",
  },
  strategy: {
    trend_strength: "趋势强度",
    value_quality: "价值质量",
    event_catalyst: "事件催化",
  },
};

const candidateEscape = value => String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
const candidateNumber = (value, digits = 2) => value == null || value === "" ? "-" : Number(value).toFixed(digits);
const candidatePct = value => value == null || value === "" ? "-" : `${Number(value).toFixed(2)}%`;
const candidateLabel = (group, value) => candidateLabels[group]?.[value] || value || "-";

function candidateQuery() {
  const params = new URLSearchParams();
  ["search", "exchange", "board", "industry", "strategy", "portfolio_fit_status", "data_quality_status", "technical_health_status", "sort", "direction"].forEach(key => {
    if (candidateState[key]) params.set(key, candidateState[key]);
  });
  params.set("limit", "300");
  return params.toString();
}

async function loadCandidates() {
  const response = await fetch(`/api/candidate-pool?${candidateQuery()}`);
  if (!response.ok) throw new Error(`candidate api failed: ${response.status}`);
  return response.json();
}

function setOptions(select, counts, labelGroup, placeholder) {
  if (!select) return;
  const current = select.value;
  const options = [`<option value="">${candidateEscape(placeholder)}</option>`];
  Object.entries(counts || {}).forEach(([value, count]) => {
    options.push(`<option value="${candidateEscape(value)}">${candidateEscape(candidateLabel(labelGroup, value))} (${count})</option>`);
  });
  select.innerHTML = options.join("");
  select.value = current && counts?.[current] != null ? current : "";
}

function syncFilterOptions(filters) {
  candidateState.lastFilters = filters;
  setOptions(document.querySelector("#candidateExchangeFilter"), filters.exchange, "exchange", "全部交易所");
  setOptions(document.querySelector("#candidateBoardFilter"), filters.board, "board", "全部板块");
  setOptions(document.querySelector("#candidateIndustryFilter"), filters.industry, "industry", "全部行业");
  setOptions(document.querySelector("#candidateStrategyFilter"), filters.strategy, "strategy", "全部策略");
  setOptions(document.querySelector("#candidatePortfolioFilter"), filters.portfolio_fit_status, "portfolio_fit_status", "全部组合状态");
  setOptions(document.querySelector("#candidateQualityFilter"), filters.data_quality_status, "data_quality_status", "全部质量");
  setOptions(document.querySelector("#candidateTechnicalFilter"), filters.technical_health_status, "technical_health_status", "全部技术面");
}

function statusClass(status) {
  return {
    ready_for_plan: "candidate-ready",
    watch: "candidate-watch",
    deferred_by_portfolio: "candidate-deferred",
  }[status] || "candidate-unknown";
}

function technicalClass(status) {
  return {
    strong: "candidate-tech-strong",
    watch: "candidate-tech-watch",
    weak: "candidate-tech-weak",
    blocked: "candidate-tech-blocked",
    insufficient: "candidate-tech-insufficient",
  }[status] || "candidate-tech-insufficient";
}

function strategiesText(item) {
  return (item.strategies_list || []).map(strategy => candidateLabel("strategy", strategy)).join(", ") || "-";
}

function candidateEvidence(item) {
  const parts = [
    item.strategy_confluence_evidence,
    item.data_quality_evidence,
    item.risk_penalty_evidence,
    item.technical_health_evidence,
    item.portfolio_fit_evidence,
  ].filter(Boolean);
  return parts.length ? parts.join(" | ") : "暂无扩展证据。";
}

function candidateRow(item) {
  const status = item.portfolio_fit_status || "";
  const technicalStatus = item.technical_health_status || "";
  return `<tr class="candidate-row ${statusClass(status)}">
    <td><strong>${candidateEscape(item.code)}</strong><span>${candidateEscape(item.name || "-")}</span><em>${candidateEscape(item.industry || "-")}</em></td>
    <td>${candidateEscape(candidateLabel("exchange", item.exchange))}<br><span>${candidateEscape(candidateLabel("board", item.board))}</span></td>
    <td class="number">${candidateNumber(item.combined_score)}</td>
    <td>${candidateEscape(strategiesText(item))}</td>
    <td class="number">${candidateNumber(item.industry_strength_score)}</td>
    <td class="number">${candidateNumber(item.liquidity_score)}</td>
    <td class="number">${candidateNumber(item.risk_penalty_score)}</td>
    <td><span class="candidate-status-pill ${technicalClass(technicalStatus)}">${candidateEscape(candidateLabel("technical_health_status", technicalStatus))}</span><br><span class="number">${candidateNumber(item.technical_health_score)}</span></td>
    <td class="number">${candidatePct(item.expected_total_position_pct_after_buy)}</td>
    <td><span class="candidate-status-pill">${candidateEscape(candidateLabel("portfolio_fit_status", status))}</span></td>
  </tr>
  <tr class="candidate-evidence-row">
    <td colspan="10">${candidateEscape(candidateEvidence(item))}</td>
  </tr>`;
}

function candidateCard(item) {
  const status = item.portfolio_fit_status || "";
  const technicalStatus = item.technical_health_status || "";
  return `<article class="candidate-card ${statusClass(status)}">
    <div><strong>${candidateEscape(item.code)} ${candidateEscape(item.name || "")}</strong><span>${candidateEscape(candidateLabel("portfolio_fit_status", status))}</span></div>
    <p>${candidateEscape(item.industry || "-")} · ${candidateEscape(candidateLabel("board", item.board))} · 综合分 ${candidateNumber(item.combined_score)}</p>
    <dl>
      <dt>策略</dt><dd>${candidateEscape(strategiesText(item))}</dd>
      <dt>技术面</dt><dd>${candidateEscape(candidateLabel("technical_health_status", technicalStatus))} ${candidateNumber(item.technical_health_score)}</dd>
      <dt>流动性</dt><dd>${candidateNumber(item.liquidity_score)}</dd>
      <dt>风险扣分</dt><dd>${candidateNumber(item.risk_penalty_score)}</dd>
      <dt>买入后总仓位</dt><dd>${candidatePct(item.expected_total_position_pct_after_buy)}</dd>
    </dl>
    <p>${candidateEscape(candidateEvidence(item))}</p>
  </article>`;
}

function renderCandidateSortIndicators() {
  document.querySelectorAll("[data-candidate-sort]").forEach(button => {
    const active = button.dataset.candidateSort === candidateState.sort;
    button.classList.toggle("active-sort", active);
    button.textContent = button.textContent.replace(/\s[↑↓]$/, "") + (active ? (candidateState.direction === "asc" ? " ↑" : " ↓") : "");
  });
}

function renderCandidateList(data) {
  syncFilterOptions(data.filters || {});
  const items = data.items || [];
  document.querySelector("#candidateSummary").innerHTML = `
    <strong>${candidateEscape(String(data.filtered_count || 0))}</strong>
    <span> / ${candidateEscape(String(data.total_count || 0))} 只候选</span>
    <em>来源：${candidateEscape(data.source || "-")}</em>`;
  document.querySelector("#candidateTableBody").innerHTML = items.map(candidateRow).join("");
  document.querySelector("#candidateMobileList").innerHTML = items.map(candidateCard).join("");
  document.querySelector("#candidateEmptyState").hidden = items.length > 0;
  renderCandidateSortIndicators();
}

async function refreshCandidateList() {
  try {
    renderCandidateList(await loadCandidates());
  } catch (error) {
    document.querySelector("#candidateSummary").innerHTML = `<span class="negative">选股列表读取失败：${candidateEscape(error.message)}</span>`;
  }
}

function updateCandidateFilter(key, value) {
  candidateState[key] = value;
  void refreshCandidateList();
}

function initCandidateList() {
  document.querySelector("#candidateSearchInput")?.addEventListener("input", event => updateCandidateFilter("search", event.target.value.trim()));
  [
    ["#candidateExchangeFilter", "exchange"],
    ["#candidateBoardFilter", "board"],
    ["#candidateIndustryFilter", "industry"],
    ["#candidateStrategyFilter", "strategy"],
    ["#candidatePortfolioFilter", "portfolio_fit_status"],
    ["#candidateQualityFilter", "data_quality_status"],
    ["#candidateTechnicalFilter", "technical_health_status"],
  ].forEach(([selector, key]) => {
    document.querySelector(selector)?.addEventListener("change", event => updateCandidateFilter(key, event.target.value));
  });
  document.querySelectorAll("[data-candidate-sort]").forEach(button => button.addEventListener("click", () => {
    const key = button.dataset.candidateSort;
    if (candidateState.sort === key) {
      candidateState.direction = candidateState.direction === "asc" ? "desc" : "asc";
    } else {
      candidateState.sort = key;
      candidateState.direction = key === "code" || key === "board" ? "asc" : "desc";
    }
    void refreshCandidateList();
  }));
  document.querySelector('[data-view="candidates"]')?.addEventListener("click", () => refreshCandidateList());
  void refreshCandidateList();
}

document.addEventListener("DOMContentLoaded", initCandidateList);
