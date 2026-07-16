const state = {
  snapshot: null,
  research: new Map(),
  backtests: new Map(),
  forecasts: new Map(),
  decisionCards: new Map(),
  decisionReport: null,
  refreshCheck: null,
  events: [],
  filter: "all",
  search: "",
  selectedCode: null,
  pendingManualTrade: null,
};

const labels = {
  market_wait: "等待时段",
  risk_review: "风险处置",
  exit_risk_review: "退出风险",
  risk_reduction_review: "仓位复核",
  data_insufficient: "数据不足",
  positive_t_watch: "正T观察",
  reverse_t_watch: "反T观察",
  hold_no_add: "持有不补仓",
  no_add_watch: "禁止补仓",
  observe: "观察",
  data_stale: "数据失效",
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

const technicalLabels = {
  bullish: "技术偏多",
  slightly_bullish: "技术略偏多",
  neutral: "技术中性",
  slightly_bearish: "技术略偏弱",
  bearish: "技术偏弱",
  missing: "技术缺失",
};

const periodLabels = {
  daily: "日线",
  weekly: "周线",
  monthly: "月线",
};

function dataQualityFor(item) {
  return decisionCardFor(item)?.data_quality || {};
}

function qualityState(quality) {
  return quality?.overall_status || "unknown";
}

function qualityLabel(quality) {
  return quality?.status_label || {
    usable: "数据可用",
    stale: "数据过期",
    insufficient: "样本不足",
    missing: "数据缺失",
    unknown: "质量未知",
  }[qualityState(quality)] || "质量未知";
}

function trustLevel(quality) {
  return quality?.data_trust?.level || "unknown";
}

function trustLabel(quality) {
  return quality?.data_trust?.label || {
    high: "高可信",
    medium: "中可信",
    low: "低可信",
    unknown: "可信未知",
  }[trustLevel(quality)] || "可信未知";
}

function consistencyStatus(quality) {
  return quality?.source_consistency?.status || "unknown";
}

function consistencyLabel(quality) {
  return {
    pass: "一致",
    conflict: "源冲突",
    skipped: "未校验",
    unknown: "未知",
  }[consistencyStatus(quality)] || consistencyStatus(quality);
}

function qualitySummary(quality) {
  const session = quality?.market_session || {};
  if (quality?.quote?.status === "stale" && session.live_quote_required === false) return session.message || "当前不在连续盘中执行窗口。";
  const issues = quality?.source_consistency?.issues || [];
  if (issues.length) return issues[0];
  const messages = [...(quality?.data_trust?.reasons || []), ...(quality?.blockers || []), ...(quality?.warnings || [])];
  if (messages.length) return messages[0];
  return qualityState(quality) === "usable" ? "行情、日线和分钟线可用于盘中判断。" : "尚未生成数据质量快照。";
}

function qualityBadge(item) {
  const quality = dataQualityFor(item);
  const status = qualityState(quality);
  return `<span class="quality-badge quality-${status}">${escapeHtml(qualityLabel(quality))}</span><span class="trust-badge trust-${trustLevel(quality)}">${escapeHtml(trustLabel(quality))}</span>`;
}

function adviceFor(item) {
  const card = state.decisionCards.get(item.code);
  return card?.decision?.action_label || automaticDecisionFor(item).headline;
}

const actionTierLabels = {
  reverse_buyback_first: "反T回补优先",
  immediate_executable: "立即可执行",
  place_wait_order: "可挂单等待",
  observe_only: "只观察",
  forbid_chase: "禁止追买",
  stop_loss_first: "止损优先",
  risk_reduction_first: "减仓优先",
  data_blocked: "数据不足禁止决策",
};

function actionTierFor(item) {
  const tier = item.action_decision?.action_tier;
  if (tier) return {tier, label: item.action_decision.action_tier_label || actionTierLabels[tier] || tier};
  const state = decisionCardFor(item)?.state || item.state;
  if (state === "data_stale" || state === "data_insufficient" || state === "market_wait") return {tier: "data_blocked", label: "数据不足禁止决策"};
  if (state === "exit_risk_review" || state === "risk_review") return {tier: "stop_loss_first", label: "止损优先"};
  if (state === "risk_reduction_review") return {tier: "risk_reduction_first", label: "减仓优先"};
  if (state === "hold_no_add" || state === "no_add_watch") return {tier: "forbid_chase", label: "禁止追买"};
  if (state === "positive_t_watch" || state === "reverse_t_watch") return {tier: "place_wait_order", label: "可挂单等待"};
  return {tier: "observe_only", label: "只观察"};
}

function actionTierBadge(item) {
  const actionTier = actionTierFor(item);
  return `<span class="action-tier action-tier-${escapeHtml(actionTier.tier)}">${escapeHtml(actionTier.label)}</span>`;
}

function decisionCardFor(item) {
  return state.decisionCards.get(item.code);
}

function technicalAssessmentFor(item) {
  return decisionCardFor(item)?.technical_assessment || {};
}

function technicalLabel(assessment) {
  return technicalLabels[assessment?.label] || assessment?.label || "技术未知";
}

function technicalToneClass(assessment) {
  const label = assessment?.label;
  if (["bullish", "slightly_bullish"].includes(label)) return "technical-positive";
  if (["bearish", "slightly_bearish"].includes(label)) return "technical-negative";
  if (label === "neutral") return "technical-neutral";
  return "technical-missing";
}

function technicalBadge(itemOrAssessment) {
  const assessment = itemOrAssessment?.available == null ? technicalAssessmentFor(itemOrAssessment) : itemOrAssessment;
  if (!assessment.available) return "";
  const score = assessment.score == null ? "--" : Number(assessment.score).toFixed(1);
  return `<span class="technical-badge ${technicalToneClass(assessment)}">${escapeHtml(technicalLabel(assessment))} · ${escapeHtml(score)}</span>`;
}

function technicalSummary(item) {
  const assessment = technicalAssessmentFor(item);
  if (!assessment.available) return "";
  const signals = assessment.signals || [];
  return signals[0] || "多周期技术指标已纳入决策评分。";
}

function displayStateFor(item) {
  return decisionCardFor(item)?.state || item.state;
}

function displayStateLabelFor(item) {
  const card = decisionCardFor(item);
  return card?.state_label || labels[item.state] || item.state;
}

function automaticDecisionFor(item) {
  const research = state.research.get(item.code);
  const flags = research?.financial_review?.flags || [];
  const flagCodes = new Set(flags.map(flag => flag.code));
  const severeFundamentals = flagCodes.has("negative_roe") || flagCodes.has("negative_pe");
  const reduction = item.reduction_plan || {};
  const reverse = item.reverse_t_plan || {};
  const backtest = state.backtests.get(item.code);
  const zone = reverse.sell_zone ? `${num(reverse.sell_zone[0])}–${num(reverse.sell_zone[1])}元` : "系统实时区间";

  if (item.state === "data_stale") {
    return {level: "禁止执行", headline: "现在不操作：行情失效", action: "行情恢复前不买、不卖。", reasons: ["实时行情超过允许延迟。"]};
  }
  if ((item.signals || []).some(signal => signal.code === "limit_down_or_near")) {
    return {level: "禁止执行", headline: "现在不操作：接近跌停", action: "不补仓、不做T，等待流动性恢复。", reasons: item.signals.map(signal => signal.message)};
  }
  if (reduction.status === "granularity_review") {
    return {
      level: "当前结论", headline: "现在不减仓，保持现有股数",
      action: `最小卖出100股会把持仓减少${pct(reduction.reduction_ratio_pct)}，不因轻微超限执行。`,
      reasons: [reduction.objective, ...(flags.map(flag => flag.message))].filter(Boolean),
    };
  }
  if (reduction.status === "actionable") {
    const latest = Number(item.quote.latest_price);
    const mainFlow = Number(item.capital_flow?.main_net_inflow_ratio_pct);
    const inZone = reverse.sell_zone && latest >= Number(reverse.sell_zone[0]) && latest <= Number(reverse.sell_zone[1]);
    const turnedDown = reverse.sell_zone && latest <= Number(reverse.sell_zone[1]) - 0.01;
    const flowConfirmed = Number.isFinite(mainFlow) && mainFlow <= 3;
    const executionTriggered = reduction.position_limit_verified && inZone && turnedDown && flowConfirmed && item.state !== "data_stale";
    if (!reduction.position_limit_verified) {
      return {
        level: "禁止执行",
        headline: "仓位上限未确认，暂停减仓",
        action: `当前10%单票上限只是系统默认值，不据此卖出。确认正式仓位上限后重新计算。`,
        reasons: [`当前仓位${pct(reduction.current_position_pct)}。`, `按默认10%计算需减仓${reduction.minimum_reduction_shares}股，但100股交易颗粒度可能造成过度减仓。`],
      };
    }
    if (executionTriggered) {
      return {
        level: "执行信号",
        headline: "现在执行第一笔减仓100股",
        action: `以不低于${num(reverse.sell_zone[0])}元的限价卖出100股，本信号仅在当前监控周期有效；成交后不回补。`,
        reasons: [`价格已进入${zone}并从高点回落。`, `主力净流入占比${pct(mainFlow)}，已低于3%确认线。`, `总目标仍为累计减仓${reduction.minimum_reduction_shares}股。`],
      };
    }
    const unmetConditions = [];
    if (!inZone) unmetConditions.push(`现价${num(latest)}元尚未进入${zone}。`);
    if (inZone && !turnedDown) unmetConditions.push(`现价仍在当日高点附近，尚未从区间高点回落至少0.01元。`);
    if (!flowConfirmed) unmetConditions.push(`主力净流入占比${pct(mainFlow)}，尚未降至3%以下。`);
    return {
      level: "待触发计划",
      headline: `减仓计划待触发：目标${reduction.minimum_reduction_shares}股`,
      action: `当前不卖。价格进入${zone}并从高点回落、主力净流入占比降至3%以下时，才触发第一笔卖出100股；减仓成交后不回补。`,
      reasons: [...unmetConditions, `完成后预计剩余${reduction.remaining_shares}股，仓位约${pct(reduction.post_reduction_position_pct)}。`, ...(flags.map(flag => flag.message))],
    };
  }
  if (severeFundamentals && Number(item.position.shares) >= 200) {
    return {
      level: "当前结论", headline: "持有，禁止补仓",
      action: "基本面风险指标未通过，但尚无已确认的卖出规则；本轮不买、不卖，持续跟踪后续财报和趋势。",
      reasons: flags.map(flag => flag.message),
    };
  }
  if (flags.length) {
    return {level: "当前结论", headline: "持有，禁止补仓", action: "本轮不买、不卖，下一份财报更新后自动重新判定。", reasons: flags.map(flag => flag.message)};
  }
  if (item.state === "no_add_watch") {
    return {level: "当前结论", headline: "持有，禁止补仓", action: "本轮不买、不卖；趋势重新站上系统均线后自动重新判定。", reasons: (item.signals || []).map(signal => signal.message)};
  }
  if (reverse.status === "candidate") {
    if (!backtest || backtest.verdict !== "rule_observation_only") {
      return {level: "禁止执行", headline: "持有，不做反T", action: "当前反T规则未通过历史验证，不执行卖出和回补。", reasons: [backtest?.verdict_label || "尚无有效回测结果。"]};
    }
    return {level: "人工候选", headline: `满足条件可反T ${reverse.trade_shares}股`, action: `在${zone}转弱时卖出，${money(reverse.buyback_max_price)}及以下回补同等股数。`, reasons: [reverse.failure_result]};
  }
  return {level: "当前结论", headline: "持有，不操作", action: "当前不买、不卖，继续监控。", reasons: (reverse.blockers || []).slice(0, 2)};
}

function isReverseTCandidate(item) {
  return item.reverse_t_plan?.status === "candidate" && state.backtests.get(item.code)?.verdict === "rule_observation_only";
}

function isReverseTWatch(item) {
  return ["candidate", "watch"].includes(item.reverse_t_plan?.status) && state.backtests.get(item.code)?.verdict === "rule_observation_only";
}

function isReverseTPriceAlert(item) {
  return Boolean(item.reverse_t_plan?.price_in_sell_zone) && item.state !== "data_stale";
}

function filteredItems() {
  const items = state.snapshot?.items || [];
  return items.filter(item => {
    const card = decisionCardFor(item);
    const decisionState = card?.state;
    const matchesFilter = state.filter === "all"
      || (state.filter === "reverse_t" ? (decisionState === "reverse_t_watch" || isReverseTWatch(item)) : decisionState === state.filter || item.state === state.filter);
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
  const decisionCards = [...state.decisionCards.values()];
  const exitRisk = decisionCards.filter(card => card.state === "exit_risk_review").length;
  const dataPaused = decisionCards.filter(card => ["market_wait", "data_stale", "data_insufficient"].includes(card.state)).length;
  const marketWait = decisionCards.filter(card => card.state === "market_wait").length;
  const qualityStale = decisionCards.filter(card => card.data_quality?.overall_status === "stale").length;
  const qualityBlocked = decisionCards.filter(card => ["insufficient", "missing"].includes(card.data_quality?.overall_status)).length;
  const trustHigh = decisionCards.filter(card => card.data_quality?.data_trust?.level === "high").length;
  const trustLow = decisionCards.filter(card => card.data_quality?.data_trust?.level === "low").length;
  const technicalWeak = decisionCards.filter(card => ["bearish", "slightly_bearish"].includes(card.technical_assessment?.label)).length;
  const technicalStrong = decisionCards.filter(card => ["bullish", "slightly_bullish"].includes(card.technical_assessment?.label)).length;
  const positiveT = decisionCards.filter(card => card.state === "positive_t_watch").length;
  const reverseTByCard = decisionCards.filter(card => card.state === "reverse_t_watch").length;
  const maxLag = Math.max(0, ...items.map(item => Number(item.quote.quote_lag_seconds || 0)));
  const reverseT = items.filter(isReverseTWatch).length;
  const forecastAlerts = items.filter(item => state.forecasts.get(item.code)?.status === "early_warning").length;
  const priceAlerts = items.filter(isReverseTPriceAlert).length;
  const blocks = [
    ["账户总资产", money(state.snapshot?.total_assets), "持仓基准"],
    ["持仓市值", money(marketValue), pct(marketValue / Number(state.snapshot?.total_assets || 1) * 100)],
    ["浮动盈亏", money(pnl), "按最新快照估算"],
    ["退出风险", `${exitRisk || risk} 只`, `${dataPaused} 只暂停决策 · ${marketWait} 只等待时段`],
    ["数据可信", `${trustHigh} 高 / ${trustLow} 低`, `${qualityStale} 过期 · ${qualityBlocked} 阻断`],
    ["技术面", `${technicalStrong} 偏多 / ${technicalWeak} 偏弱`, "来自日周月多指标评分"],
    ["T观察", `${positiveT} 正T / ${reverseTByCard || reverseT} 反T`, `${priceAlerts} 只价格提醒 · ${forecastAlerts} 只概率预警`],
    ["最大延迟", `${maxLag.toFixed(1)} 秒`, "超过60秒自动失效"],
  ];
  document.querySelector("#summaryBand").innerHTML = blocks.map(([label, value, sub]) => `
    <div class="summary-item">
      <div class="summary-label">${label}</div>
      <div class="summary-value ${label === "浮动盈亏" ? tone(pnl) : ""}">${value}</div>
      <div class="summary-sub">${sub}</div>
    </div>`).join("");
}

function renderRefreshAlert() {
  const alert = document.querySelector("#refreshAlert");
  const check = state.refreshCheck;
  if (!check || check.conclusion === "no_market_wait") {
    alert.hidden = true;
    alert.innerHTML = "";
    return;
  }
  const command = check.refresh_command?.shell || "";
  const actionClass = check.action_required ? "refresh-action" : "refresh-wait";
  const commandHtml = command
    ? `<div class="refresh-command"><code>${escapeHtml(command)}</code><button class="copy-refresh" type="button" data-command="${escapeHtml(command)}">复制</button></div>`
    : "";
  alert.hidden = false;
  alert.className = `refresh-alert ${actionClass}`;
  alert.innerHTML = `
    <div>
      <strong>${escapeHtml(check.message || "等待行情刷新。")}</strong>
      <span>${escapeHtml(check.market_session?.label || "--")} · ${escapeHtml(check.market_wait_count ?? 0)} 只等待</span>
    </div>
    ${check.action_required ? commandHtml : ""}`;
}

function tableRow(item) {
  const lag = item.quote.quote_lag_seconds;
  const card = decisionCardFor(item);
  const displayState = displayStateFor(item);
  const reverseTag = isReverseTPriceAlert(item)
    ? `<div class="advice-tag">已到反T卖出观察区 · 回补参考${money(item.reverse_t_plan.buyback_max_price)}</div>`
    : isReverseTCandidate(item) ? '<div class="advice-tag">反T候选 · 先卖100股</div>' : "";
  const cardTag = card ? `<div class="advice-tag">${escapeHtml(card.decision.confidence)} · ${escapeHtml(card.reason)}</div>` : "";
  const techTag = card ? `<div class="technical-line">${technicalBadge(item)}<span>${escapeHtml(technicalSummary(item))}</span></div>` : "";
  const dataTag = card ? `<div class="quality-line">${qualityBadge(item)}<span>${escapeHtml(qualitySummary(dataQualityFor(item)))}</span></div>` : "";
  return `<tr data-code="${item.code}" tabindex="0">
    <td><div class="stock-name">${escapeHtml(item.name)}</div><div class="stock-code">${item.code}</div></td>
    <td class="number"><div>${num(item.quote.latest_price)}</div><div class="secondary ${tone(item.quote.change_pct)}">${pct(item.quote.change_pct)}</div></td>
    <td class="number"><div class="${tone(item.position.unrealized_pnl)}">${money(item.position.unrealized_pnl)}</div><div class="secondary ${tone(item.position.return_pct)}">${pct(item.position.return_pct)}</div></td>
    <td class="number"><div>${pct(item.position.live_position_pct)}</div><div class="secondary">${Number(item.position.shares).toFixed(0)}股</div></td>
    <td><span class="state-badge state-${displayState}">${escapeHtml(displayStateLabelFor(item))}</span><div class="state-tier">${actionTierBadge(item)}</div></td>
    <td class="advice">${escapeHtml(adviceFor(item))}${cardTag}${techTag}${dataTag}${reverseTag}</td>
    <td class="number"><div class="${tone(item.capital_flow?.main_net_inflow)}">${compactMoney(item.capital_flow?.main_net_inflow)}</div><div class="secondary ${tone(item.capital_flow?.main_net_inflow_ratio_pct)}">${pct(item.capital_flow?.main_net_inflow_ratio_pct)}</div></td>
    <td class="number">${lag == null ? "--" : `${Number(lag).toFixed(1)}s`}</td>
  </tr>`;
}

function mobileCard(item) {
  const card = decisionCardFor(item);
  const displayState = displayStateFor(item);
  const reverseTag = isReverseTPriceAlert(item)
    ? `<div class="advice-tag">已到反T卖出观察区 · 回补参考${money(item.reverse_t_plan.buyback_max_price)}</div>`
    : isReverseTCandidate(item) ? '<div class="advice-tag">反T候选 · 先卖100股</div>' : "";
  const cardTag = card ? `<div class="advice-tag">${escapeHtml(card.decision.confidence)} · ${escapeHtml(card.reason)}</div>` : "";
  const techTag = card ? `<div class="technical-line">${technicalBadge(item)}<span>${escapeHtml(technicalSummary(item))}</span></div>` : "";
  const dataTag = card ? `<div class="quality-line">${qualityBadge(item)}<span>${escapeHtml(qualitySummary(dataQualityFor(item)))}</span></div>` : "";
  return `<article class="position-card" data-code="${item.code}" tabindex="0">
    <div class="card-top">
      <div><div class="stock-name">${escapeHtml(item.name)}</div><div class="stock-code">${item.code}</div></div>
      <div class="number"><strong>${num(item.quote.latest_price)}</strong><div class="secondary ${tone(item.quote.change_pct)}">${pct(item.quote.change_pct)}</div></div>
    </div>
    <div class="card-row"><span>持仓盈亏</span><span class="${tone(item.position.unrealized_pnl)}">${money(item.position.unrealized_pnl)} · ${pct(item.position.return_pct)}</span></div>
    <div class="card-row"><span>${actionTierBadge(item)}</span><span>仓位 ${pct(item.position.live_position_pct)}</span></div>
    <div class="card-row"><span class="state-badge state-${displayState}">${escapeHtml(displayStateLabelFor(item))}</span><span>${escapeHtml(displayState)}</span></div>
    <div class="card-row"><span>主力净额</span><span class="${tone(item.capital_flow?.main_net_inflow)}">${compactMoney(item.capital_flow?.main_net_inflow)} · ${pct(item.capital_flow?.main_net_inflow_ratio_pct)}</span></div>
    <div class="card-advice">${escapeHtml(adviceFor(item))}${cardTag}${techTag}${dataTag}${reverseTag}</div>
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

function renderConsistencyChecks(quality) {
  const consistency = quality?.source_consistency || {};
  const checks = consistency.checks || [];
  if (!checks.length) return "";
  return `<div class="consistency-list">${checks.map(check => {
    const diff = check.diff_pct == null ? "--" : `${Number(check.diff_pct).toFixed(2)}%`;
    const referenceTime = check.reference_timestamp || check.reference_date || "--";
    const sourceName = check.source === "minute" ? "分钟线" : check.source === "daily" ? "日线" : check.source || "来源";
    return `<div class="consistency-item consistency-${escapeHtml(check.status || "unknown")}">
      <div>
        <strong>${escapeHtml(sourceName)}</strong>
        <span>${escapeHtml(check.status || "unknown")}</span>
      </div>
      <p>${escapeHtml(check.message || "")}</p>
      <dl>
        <dt>参考时间</dt><dd>${escapeHtml(referenceTime)}</dd>
        <dt>参考价</dt><dd>${check.reference_price == null ? "--" : money(check.reference_price)}</dd>
        <dt>偏差</dt><dd>${escapeHtml(diff)}</dd>
      </dl>
    </div>`;
  }).join("")}</div>`;
}

function renderTechnicalAssessment(assessment) {
  if (!assessment?.available) return "";
  const dimensionLabels = {
    trend: "趋势分",
    risk: "风险分",
    reversal: "反转分",
    volume_confirmation: "量能确认",
    multi_timeframe: "多周期一致",
  };
  const dimensionRows = Object.entries(assessment.dimension_scores || {}).map(([key, value]) => `
    <dl class="metric"><dt>${escapeHtml(dimensionLabels[key] || key)}</dt><dd class="${tone(value)}">${value == null ? "--" : Number(value).toFixed(1)}</dd></dl>
  `).join("");
  const periodRows = Object.entries(assessment.periods || {}).map(([period, data]) => `
    <tr>
      <td>${escapeHtml(periodLabels[period] || period)}</td>
      <td class="number">${data.score == null ? "--" : Number(data.score).toFixed(1)}</td>
      <td class="number">${data.macd_histogram == null ? "--" : Number(data.macd_histogram).toFixed(4)}</td>
      <td class="number">${data.boll_percent_b == null ? "--" : Number(data.boll_percent_b).toFixed(2)}</td>
      <td class="number">${data.rsi14 == null ? "--" : Number(data.rsi14).toFixed(1)}</td>
      <td class="number">${data.kdj_j == null ? "--" : Number(data.kdj_j).toFixed(1)}</td>
      <td class="number">${data.atr_pct == null ? "--" : pct(data.atr_pct)}</td>
      <td class="number">${data.volume_ratio_20 == null ? "--" : Number(data.volume_ratio_20).toFixed(2)}</td>
    </tr>`).join("");
  const signals = assessment.signals || [];
  return detailSection(
    "技术指标",
    `<p>${technicalBadge(assessment)} <strong>${escapeHtml(technicalLabel(assessment))}</strong></p>
    ${assessment.summary ? `<p class="technical-summary"><strong>技术结论：</strong>${escapeHtml(assessment.summary)}</p>` : ""}
    <div class="metric-grid">
      <dl class="metric"><dt>综合技术分</dt><dd>${assessment.score == null ? "--" : Number(assessment.score).toFixed(1)}</dd></dl>
      <dl class="metric"><dt>判断标签</dt><dd>${escapeHtml(technicalLabel(assessment))}</dd></dl>
    </div>
    ${dimensionRows ? `<h4>综合评分拆解</h4><div class="metric-grid">${dimensionRows}</div>` : ""}
    <div class="technical-table-wrap">
      <table class="technical-table">
        <thead><tr><th>周期</th><th>分</th><th>MACD柱</th><th>BOLL%b</th><th>RSI14</th><th>KDJ-J</th><th>ATR%</th><th>量比20</th></tr></thead>
        <tbody>${periodRows}</tbody>
      </table>
    </div>
    ${signals.length ? `<h4>技术证据</h4><ul class="reason-list">${signals.slice(0, 8).map(signal => `<li>${escapeHtml(signal)}</li>`).join("")}</ul>` : ""}`
  );
}

function renderCapitalPlan(plan) {
  if (!plan?.applicable) return "";
  const buyZone = plan.buy_zone ? `${num(plan.buy_zone[0])}–${num(plan.buy_zone[1])}元` : "--";
  const targetZone = plan.target_sell_zone ? `${num(plan.target_sell_zone[0])}–${num(plan.target_sell_zone[1])}元` : "--";
  const metrics = [
    ["状态", plan.status_label || "--"],
    ["账户现金要求", plan.account_cash_required ? "要求已足额" : "可临时补充"],
    ["额度档位", plan.single_add_tier === "strong" ? "强趋势5%" : "基础3%"],
    ["单次追加上限", money(plan.max_additional_capital)],
    ["本轮占总资产上限", pct(plan.effective_single_add_pct_total_assets ?? plan.max_single_add_pct_total_assets)],
    ["单票加仓后上限", pct(plan.max_stock_position_pct_after_add)],
    ["建议买入数量", `${plan.suggested_buy_shares || 0}股`],
    ["预计买入金额", money(plan.estimated_buy_amount)],
    ["加仓后单票仓位", pct(plan.post_add_position_pct)],
    ["新增风险金额", money(plan.added_risk_amount)],
    ["新增风险占总资产", pct(plan.added_risk_pct_total_assets)],
    ["买入观察区", buyZone],
    ["卖出目标区", targetZone],
  ];
  const steps = plan.steps || [];
  const reasons = plan.reasons || [];
  return detailSection(
    "追加资金正T计划",
    `<div class="metric-grid">${metrics.map(([key, value]) => `<dl class="metric"><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></dl>`).join("")}</div>
    <p>${escapeHtml(plan.failure_plan || "")}</p>
    ${steps.length ? `<h4>操作步骤</h4><ol class="reason-list">${steps.map(step => `<li>${escapeHtml(step)}</li>`).join("")}</ol>` : ""}
    ${reasons.length ? `<h4>限制原因</h4><ul class="reason-list">${reasons.map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>` : ""}`
  );
}

function renderTechnicalOperationBlock(operation, mode) {
  if (!operation?.tier) return "";
  const allowed = mode === "positive_t" ? operation.allow_buy_watch : operation.allow_t_watch;
  if (allowed || operation.tier === "not_available") return "";
  const label = mode === "positive_t" ? "正T被技术面阻断" : "反T被技术面阻断";
  const unlock = operation.unlock_conditions || [];
  const unlockHtml = unlock.length ? `<h4>解锁条件</h4><div class="unlock-list">${unlock.map(condition => `
    <div class="unlock-item ${condition.passed ? "unlock-pass" : "unlock-block"}">
      <span>${condition.passed ? "已满足" : "未满足"}</span>
      <strong>${escapeHtml(condition.label || condition.code || "条件")}</strong>
      <p>当前：${escapeHtml(condition.current == null ? "--" : String(condition.current))}；目标：${escapeHtml(condition.target || "--")}</p>
    </div>`).join("")}</div>` : "";
  return `<div class="blocker-item">
    <div><strong>${escapeHtml(label)}</strong><span>${escapeHtml(operation.tier_label || "--")}</span></div>
    <p>${escapeHtml(operation.reason || "技术操作档位不支持本轮交易。")}</p>
    <p class="secondary">${escapeHtml(operation.next_step || "等待技术面修复后再重新评估。")}</p>
    ${unlockHtml}
  </div>`;
}

function renderPositiveTiming(timing, technicalOperation = null) {
  if (!timing || timing.status === "not_applicable") return "";
  const buyZone = timing.buy_zone ? `${num(timing.buy_zone[0])}–${num(timing.buy_zone[1])}元` : "--";
  const targetZone = timing.target_sell_zone ? `${num(timing.target_sell_zone[0])}–${num(timing.target_sell_zone[1])}元` : "--";
  const technicalBlock = renderTechnicalOperationBlock(technicalOperation, "positive_t");
  const metrics = [
    ["状态", timing.status === "confirmed" ? "分时确认" : "继续等待"],
    ["评分", timing.score == null ? "--" : `${Number(timing.score).toFixed(1)} / ${timing.threshold}`],
    ["最新分钟", timing.latest_timestamp || "--"],
    ["买入观察区", buyZone],
    ["目标卖出区", targetZone],
    ["5分钟MA5", money(timing.metrics?.ma5)],
    ["5分钟MA20", money(timing.metrics?.ma20)],
    ["回踩幅度", pct(timing.metrics?.pullback_pct)],
    ["RSI14", timing.metrics?.rsi14 == null ? "--" : Number(timing.metrics.rsi14).toFixed(1)],
    ["5分钟量比", timing.metrics?.volume_ratio == null ? "--" : Number(timing.metrics.volume_ratio).toFixed(2)],
    ["主力净流入占比", pct(timing.metrics?.main_flow_ratio_pct)],
    ["确认信号数", timing.metrics?.confirmation_count == null ? "--" : String(timing.metrics.confirmation_count)],
    ["MA5修复", timing.metrics?.recaptured_ma5 ? "是" : "否"],
    ["放量阳线", timing.metrics?.bullish_volume_candle ? "是" : "否"],
    ["资金流确认", timing.metrics?.flow_confirmed ? "是" : "否"],
    ["大周期技术背景", timing.metrics?.technical_label || "--"],
    ["技术操作档位", timing.metrics?.technical_operation_label || technicalOperation?.tier_label || "--"],
    ["技术背景允许正T", timing.metrics?.technical_supported === false ? "否" : "是"],
  ];
  const signals = timing.signals || [];
  const blockers = timing.blockers || [];
  return detailSection(
    "正T分时评分",
    `<div class="metric-grid">${metrics.map(([key, value]) => `<dl class="metric"><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></dl>`).join("")}</div>
    ${timing.next_action ? `<div class="action-panel action-${timing.status === "confirmed" ? "positive_t_watch" : "hold_no_add"}"><div class="action-panel-title">下一步动作</div><p>${escapeHtml(timing.next_action)}</p></div>` : ""}
    ${technicalBlock ? `<h4>技术面门禁</h4><div class="blocker-list">${technicalBlock}</div>` : ""}
    ${blockers.length ? `<h4>未能执行正T的原因</h4><div class="blocker-list">${blockers.map(blocker => `
      <div class="blocker-item">
        <div><strong>${escapeHtml(blocker.label || blocker.code || "阻断项")}</strong><span>${escapeHtml(blocker.current || "--")}</span></div>
        <p>${escapeHtml(blocker.reason || "")}</p>
        <p class="secondary">${escapeHtml(blocker.next_step || "")}</p>
      </div>`).join("")}</div>` : ""}
    ${signals.length ? `<h4>评分依据</h4><ul class="reason-list">${signals.map(signal => `<li>${escapeHtml(signal)}</li>`).join("")}</ul>` : ""}`
  );
}

function estimateManualTradeFees(side, price, shares) {
  const amount = price * shares;
  const commission = Math.max(amount * 0.0003, 5);
  const stampDuty = side === "sell" ? amount * 0.0005 : 0;
  const transferFee = amount * 0.00001;
  const total = commission + stampDuty + transferFee;
  return {commission, stampDuty, transferFee, total};
}

function manualTradeImpact(item, side, price, shares) {
  const currentShares = Number(item.position?.shares || 0);
  const entryPrice = Number(item.position?.entry_price || 0);
  if (!price || !shares || price <= 0 || shares <= 0) return null;
  const fees = estimateManualTradeFees(side, price, shares);
  if (side === "sell") {
    const remaining = currentShares - shares;
    const realizedPnl = (price - entryPrice) * shares - fees.total;
    return {
      valid: remaining >= 0,
      side,
      remainingShares: remaining,
      fees,
      realizedPnl,
      reverseTSupported: remaining >= 200,
      message: remaining < 0 ? `卖出数量超过当前持仓 ${currentShares.toFixed(0)} 股。` : "",
    };
  }
  const newShares = currentShares + shares;
  const weightedCost = ((entryPrice * currentShares) + price * shares + fees.total) / newShares;
  return {
    valid: true,
    side,
    remainingShares: newShares,
    fees,
    weightedCost,
    reverseTSupported: newShares >= 200,
    message: "",
  };
}

function renderManualTradeImpact(item, side, price, shares) {
  const impact = manualTradeImpact(item, side, price, shares);
  if (!impact) return '<p class="secondary">输入成交价格和数量后显示影响摘要。</p>';
  if (!impact.valid) return `<p class="negative"><strong>${escapeHtml(impact.message)}</strong></p>`;
  const feeText = `预估费用 ${money(impact.fees.total)}（佣金 ${money(impact.fees.commission)}，印花税 ${money(impact.fees.stampDuty)}，过户费 ${money(impact.fees.transferFee)}）`;
  const reverseText = impact.reverseTSupported ? "成交后仍可能支持反T观察。" : "成交后持仓少于200股，不支持反T保留底仓。";
  if (impact.side === "sell") {
    return `<div class="manual-impact-grid">
      <dl><dt>成交后剩余</dt><dd>${impact.remainingShares.toFixed(0)}股</dd></dl>
      <dl><dt>预计实现盈亏</dt><dd class="${tone(impact.realizedPnl)}">${money(impact.realizedPnl)}</dd></dl>
      <dl><dt>费用估算</dt><dd>${feeText}</dd></dl>
      <dl><dt>做T影响</dt><dd>${reverseText}</dd></dl>
    </div>`;
  }
  return `<div class="manual-impact-grid">
    <dl><dt>成交后持仓</dt><dd>${impact.remainingShares.toFixed(0)}股</dd></dl>
    <dl><dt>预计新成本</dt><dd>${money(impact.weightedCost)}</dd></dl>
    <dl><dt>费用估算</dt><dd>${feeText}</dd></dl>
    <dl><dt>做T影响</dt><dd>${reverseText}</dd></dl>
  </div>`;
}

function manualTradePayload(form) {
  return {
    code: form.dataset.code,
    side: form.querySelector('[name="side"]')?.value,
    price: Number(form.querySelector('[name="price"]')?.value),
    shares: Number(form.querySelector('[name="shares"]')?.value),
    note: form.querySelector('[name="note"]')?.value || "",
    trade_intent: form.querySelector('[name="trade_intent"]')?.value || "",
    linked_trade_id: form.querySelector('[name="linked_trade_id"]')?.value || "",
  };
}

function nextPlanAfterManualTrade(item, payload, impact) {
  if (payload.trade_intent === "reverse_t_close") {
    return "回补成交后，系统会关闭这笔开放反T腿，持仓恢复后重新计算成本和下一步建议。";
  }
  if (payload.trade_intent === "reverse_t_open") {
    return "卖出成交后，系统会跟踪这笔开放反T腿；未到回补上限不追买。";
  }
  if (payload.side === "sell") {
    return impact.remainingShares <= 0 ? "成交后该股持仓归零，系统会按退出后的状态重新生成建议。" : "成交后系统会按剩余股数重新评估仓位、止损风险和是否还能做T。";
  }
  return "买入成交后系统会更新持仓成本，并重新评估是否允许继续加仓、做T或需要风险复核。";
}

function reverseTCloseEstimate(item, payload) {
  if (payload.trade_intent !== "reverse_t_close") return null;
  const openLeg = item.reverse_t_plan?.open_reverse_t_leg || {};
  const sellPrice = Number(openLeg.sell_price || 0);
  const shares = Number(payload.shares || 0);
  if (!sellPrice || !payload.price || !shares) return null;
  const sellFees = Number(openLeg.fees?.total_fees || 0);
  const buyFees = estimateManualTradeFees("buy", payload.price, shares).total;
  const grossProfit = (sellPrice - payload.price) * shares;
  const netProfit = grossProfit - sellFees - buyFees;
  return {grossProfit, netProfit, totalFees: sellFees + buyFees};
}

function manualTradePreflightChecks(item, payload, impact) {
  const checks = [];
  const add = (label, status, message, blocking = false) => checks.push({label, status, message, blocking});
  const sideLabel = payload.side === "sell" ? "卖出" : "买入";
  add(
    "成交信息",
    "pass",
    `${sideLabel} ${num(payload.shares, 0)} 股，成交价 ${num(payload.price)} 元；请确认券商软件已真实成交。`,
  );
  if (payload.shares % 100 === 0) {
    add("交易单位", "pass", "数量为100股整数手。");
  } else {
    add("交易单位", "block", "A股手工成交数量应按100股整数手填写。", true);
  }
  if (payload.side === "sell") {
    add(
      "成交后持仓",
      impact.remainingShares >= 0 ? "pass" : "block",
      impact.remainingShares >= 0 ? `成交后剩余 ${num(impact.remainingShares, 0)} 股。` : "卖出数量超过当前持仓。",
      impact.remainingShares < 0,
    );
  } else {
    add("成交后持仓", "pass", `成交后持仓 ${num(impact.remainingShares, 0)} 股，预计新成本 ${money(impact.weightedCost)}。`);
  }

  if (payload.trade_intent === "reverse_t_close") {
    const plan = item.reverse_t_plan || {};
    const openLegId = plan.open_reverse_t_leg?.id || "";
    const buybackMax = plan.buyback_max_price == null ? null : Number(plan.buyback_max_price);
    const linkedOk = Boolean(openLegId && payload.linked_trade_id && payload.linked_trade_id === openLegId);
    add(
      "反T卖出腿",
      linkedOk ? "pass" : "block",
      linkedOk ? `已关联开放卖出腿 ${openLegId}。` : `关联卖出腿缺失或不匹配；当前开放腿是 ${openLegId || "--"}。`,
      !linkedOk,
    );
    const priceOk = buybackMax == null || payload.price <= buybackMax + 1e-9;
    add(
      "回补价格",
      priceOk ? "pass" : "block",
      buybackMax == null ? "费用模型未给出回补上限，请人工复核。" : priceOk ? `回补价 ${num(payload.price)} 不高于上限 ${num(buybackMax)}。` : `回补价 ${num(payload.price)} 高于上限 ${num(buybackMax)}，不要追买。`,
      !priceOk,
    );
    const estimate = reverseTCloseEstimate(item, payload);
    if (estimate) {
      const enoughProfit = estimate.netProfit >= 5;
      add(
        "预计净收益",
        enoughProfit ? "pass" : "warn",
        `毛收益 ${money(estimate.grossProfit)}，总费用 ${money(estimate.totalFees)}，扣费后约 ${money(estimate.netProfit)}。`,
      );
    }
  } else if (payload.trade_intent === "reverse_t_open") {
    const zone = item.reverse_t_plan?.sell_zone || [];
    const zoneOk = zone.length < 2 || (payload.price >= Number(zone[0]) && payload.price <= Number(zone[1]));
    add(
      "反T卖出区间",
      zoneOk ? "pass" : "warn",
      zone.length < 2 ? "当前没有明确卖出观察区，按真实成交记录但不作为系统候选反T。" : zoneOk ? `卖出价位于 ${num(zone[0])}-${num(zone[1])} 元观察区。` : `卖出价不在 ${num(zone[0])}-${num(zone[1])} 元观察区，请确认这是手工决策。`,
    );
  } else {
    add("成交意图", "warn", "这会按普通手工成交写入，不会关闭或打开反T腿。");
  }
  add("写入结果", checks.some(check => check.blocking) ? "block" : "pass", checks.some(check => check.blocking) ? "存在硬性失败项，暂不允许写入。" : "允许写入本地持仓并刷新建议。", checks.some(check => check.blocking));
  return checks;
}

function manualTradePreflightError(item, payload, impact) {
  return manualTradePreflightChecks(item, payload, impact).find(check => check.blocking)?.message || "";
}

function renderPreflightChecks(checks) {
  return `<div class="preflight-list">${checks.map(check => `
    <div class="preflight-item preflight-${escapeHtml(check.status)}">
      <strong>${escapeHtml(check.label)}</strong>
      <span>${check.status === "pass" ? "通过" : check.status === "warn" ? "复核" : "阻断"}</span>
      <p>${escapeHtml(check.message)}</p>
    </div>`).join("")}</div>`;
}

function renderManualTradeConfirmation(item, payload, impact, checks) {
  const sideLabel = payload.side === "sell" ? "卖出" : "买入";
  const intentLabel = {
    reverse_t_open: "反T卖出腿",
    reverse_t_close: "反T回补",
  }[payload.trade_intent] || "普通手工成交";
  const metrics = [
    ["证券", `${item.code} ${item.name}`],
    ["成交方向", sideLabel],
    ["成交意图", intentLabel],
    ["成交价格", `${num(payload.price)}元`],
    ["成交数量", `${payload.shares}股`],
    ["预估费用", money(impact.fees.total)],
    ["成交后股数", `${impact.remainingShares.toFixed(0)}股`],
    ["是否还能反T", impact.reverseTSupported ? "可以继续观察" : "不足200股，不支持保留底仓反T"],
  ];
  if (payload.side === "sell") {
    metrics.push(["预计实现盈亏", money(impact.realizedPnl)]);
  } else {
    metrics.push(["预计新成本", money(impact.weightedCost)]);
  }
  if (payload.linked_trade_id) metrics.push(["关联卖出记录", payload.linked_trade_id]);
  return `<p><strong>请确认这笔成交已经在券商软件真实成交。</strong></p>
    <div class="metric-grid">${metrics.map(([key, value]) => `<dl class="metric"><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></dl>`).join("")}</div>
    <h4>交易前自检</h4>
    ${renderPreflightChecks(checks)}
    <h4>成交后的下一步计划</h4>
    <p>${escapeHtml(nextPlanAfterManualTrade(item, payload, impact))}</p>
    <p class="secondary">确认后会立即写入本地持仓文件，并刷新实时建议。</p>`;
}

function setManualTradeConfirmError(message) {
  const target = document.querySelector("#manualTradeConfirmError");
  target.textContent = message || "";
  target.hidden = !message;
}

function openManualTradeConfirm(form, payload, item, impact, checks) {
  const blocking = checks.some(check => check.blocking);
  state.pendingManualTrade = blocking ? null : { form, payload };
  document.querySelector("#manualTradeConfirmTitle").textContent = `${payload.side === "sell" ? "确认卖出" : "确认买入"} ${item.name}`;
  document.querySelector("#manualTradeConfirmBody").innerHTML = renderManualTradeConfirmation(item, payload, impact, checks);
  document.querySelector("#manualTradeConfirmButton").disabled = blocking;
  setManualTradeConfirmError("");
  document.querySelector("#manualTradeConfirm").hidden = false;
  document.querySelector("#manualTradeConfirm").setAttribute("aria-hidden", "false");
}

function closeManualTradeConfirm() {
  state.pendingManualTrade = null;
  document.querySelector("#manualTradeConfirm").hidden = true;
  document.querySelector("#manualTradeConfirm").setAttribute("aria-hidden", "true");
  document.querySelector("#manualTradeConfirmButton").disabled = false;
  setManualTradeConfirmError("");
}

function prepareManualTradeConfirmation(form) {
  const status = form.querySelector(".manual-trade-status");
  const payload = manualTradePayload(form);
  updateManualTradeImpact(form);
  const item = state.snapshot?.items.find(entry => entry.code === payload.code);
  const impact = item ? manualTradeImpact(item, payload.side, payload.price, payload.shares) : null;
  if (!impact || !impact.valid) {
    status.textContent = impact?.message || "请先输入有效成交信息。";
    return;
  }
  const checks = manualTradePreflightChecks(item, payload, impact);
  const preflightError = manualTradePreflightError(item, payload, impact);
  status.textContent = preflightError ? "交易前自检存在阻断项，请在确认弹层查看。" : "请在确认弹层核对成交后影响。";
  openManualTradeConfirm(form, payload, item, impact, checks);
}

async function submitManualTrade(payload, form) {
  const status = form.querySelector(".manual-trade-status");
  status.textContent = "正在更新持仓并刷新建议...";
  try {
    const response = await fetch("/api/manual-trade", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || `HTTP ${response.status}`);
    status.textContent = result.refresh_error ? `已保存成交，但刷新建议失败：${result.refresh_error}` : "已更新，正在重新加载页面数据。";
    closeManualTradeConfirm();
    await loadData();
  } catch (error) {
    status.textContent = `更新失败：${error.message}`;
    setManualTradeConfirmError(`写入失败：${error.message}`);
    throw error;
  }
}

function reverseTradePresetControls(item) {
  const plan = item.reverse_t_plan || {};
  const technicalOperation = decisionCardFor(item)?.decision?.technical_operation || {};
  const sellZone = plan.sell_zone || [];
  const shares = Number(plan.trade_shares || 0);
  if (!shares) return "";
  if (["buyback_ready", "buyback_wait"].includes(plan.status)) {
    const buybackPrice = plan.buyback_max_price == null ? null : Number(plan.buyback_max_price);
    if (buybackPrice == null) return "";
    const currentPrice = item.quote?.latest_price == null ? null : Number(item.quote.latest_price);
    const fillPrice = currentPrice != null && Number.isFinite(currentPrice) ? Math.min(currentPrice, buybackPrice) : buybackPrice;
    const openLegId = plan.open_reverse_t_leg?.id || "";
    return `<div class="manual-preset"><div class="manual-preset-title">开放反T回补单</div><div class="manual-preset-actions">
      <button class="secondary-action" type="button" data-manual-preset data-side="buy" data-price="${escapeHtml(fillPrice.toFixed(2))}" data-shares="${escapeHtml(shares)}" data-trade-intent="reverse_t_close" data-linked-trade-id="${escapeHtml(openLegId)}" data-note="反T回补：关闭开放反T卖出腿">填入反T回补</button>
    </div></div>`;
  }
  if (plan.status !== "candidate" || sellZone.length < 2) return "";
  if (technicalOperation.tier && !technicalOperation.allow_t_watch) return "";
  const sellPrice = Number(sellZone[0]);
  const buybackPrice = plan.buyback_max_price == null ? null : Number(plan.buyback_max_price);
  const buttons = [
    `<button class="secondary-action" type="button" data-manual-preset data-side="sell" data-price="${escapeHtml(sellPrice.toFixed(2))}" data-shares="${escapeHtml(shares)}" data-trade-intent="reverse_t_open" data-note="反T卖出：按系统候选步骤记录">填入反T卖出</button>`,
  ];
  if (buybackPrice != null) {
    buttons.push(`<button class="secondary-action" type="button" data-manual-preset data-side="buy" data-price="${escapeHtml(buybackPrice.toFixed(2))}" data-shares="${escapeHtml(shares)}" data-trade-intent="reverse_t_close" data-note="反T回补：按系统回补上限记录">填入反T回补</button>`);
  }
  return `<div class="manual-preset"><div class="manual-preset-title">反T快捷填入</div><div class="manual-preset-actions">${buttons.join("")}</div></div>`;
}

function manualTradeSection(item) {
  const currentPrice = item.quote?.latest_price == null ? "" : Number(item.quote.latest_price).toFixed(2);
  const maxSellShares = Number(item.position?.shares || 0);
  const defaultShares = maxSellShares >= 100 ? 100 : maxSellShares || 100;
  const defaultImpact = renderManualTradeImpact(item, "sell", Number(currentPrice), Number(defaultShares));
  return detailSection(
    "手工成交更新",
    `<form class="manual-trade-form" data-code="${escapeHtml(item.code)}">
      ${reverseTradePresetControls(item)}
      <div class="manual-trade-grid">
        <label><span>方向</span><select name="side"><option value="sell">卖出</option><option value="buy">买入</option></select></label>
        <label><span>价格</span><input name="price" type="number" step="0.01" min="0.01" value="${escapeHtml(currentPrice)}" required></label>
        <label><span>数量</span><input name="shares" type="number" step="100" min="1" value="${escapeHtml(defaultShares)}" required></label>
        <label><span>备注</span><input name="note" type="text" placeholder="可选"></label>
        <input name="trade_intent" type="hidden" value="">
        <input name="linked_trade_id" type="hidden" value="">
      </div>
      <div class="manual-trade-impact">${defaultImpact}</div>
      <button class="primary-action" type="submit">记录成交并刷新建议</button>
      <p class="manual-trade-status secondary" aria-live="polite"></p>
    </form>`
  );
}

function reverseTClosureSection(item) {
  const closure = item.latest_reverse_t_closure;
  if (!closure) return "";
  const metrics = [
    ["卖出腿", `${num(closure.sell_price)}元 / ${num(closure.shares, 0)}股`],
    ["回补腿", `${num(closure.buy_price)}元 / ${num(closure.shares, 0)}股`],
    ["毛收益", money(closure.gross_profit)],
    ["总费用", money(closure.fees?.total_fees)],
    ["净收益", money(closure.net_profit)],
    ["每股降本", money(closure.cost_reduction_per_remaining_share)],
  ];
  const statusText = closure.status === "closed_profitable" ? "闭环完成，扣费后盈利" : "闭环完成，但扣费后未盈利";
  const toneClass = closure.status === "closed_profitable" ? "positive" : "negative";
  return detailSection(
    "反T闭环复盘",
    `<div class="closure-summary ${toneClass}">
      <strong>${escapeHtml(statusText)}</strong>
      <span>${escapeHtml(closure.sell_trade_id || "--")} → ${escapeHtml(closure.buy_trade_id || "--")}</span>
    </div>
    <div class="metric-grid">${metrics.map(([key, value]) => `<dl class="metric"><dt>${key}</dt><dd>${value}</dd></dl>`).join("")}</div>
    <h4>下一步计划</h4>
    <p>${escapeHtml(closure.next_plan || "刷新实时建议后，按新的反T区间和风险状态重新判断。")}</p>`
  );
}

function updateManualTradeImpact(form) {
  const code = form.dataset.code;
  const item = state.snapshot?.items.find(entry => entry.code === code);
  if (!item) return;
  const target = form.querySelector(".manual-trade-impact");
  const side = form.querySelector('[name="side"]')?.value;
  const price = Number(form.querySelector('[name="price"]')?.value);
  const shares = Number(form.querySelector('[name="shares"]')?.value);
  target.innerHTML = renderManualTradeImpact(
    item,
    side,
    price,
    shares,
  );
}

function openDetail(code) {
  const item = state.snapshot?.items.find(entry => entry.code === code);
  if (!item) return;
  state.selectedCode = code;
  const research = state.research.get(code);
  const backtest = state.backtests.get(code);
  const forecast = state.forecasts.get(code);
  const decisionCard = state.decisionCards.get(code);
  const technicalOperation = decisionCard?.decision?.technical_operation || {};
  const automaticDecision = automaticDecisionFor(item);
  document.querySelector("#detailCode").textContent = code;
  document.querySelector("#detailName").textContent = item.name;
  const metrics = [
    ["现价", money(item.quote.latest_price)], ["当日涨跌", pct(item.quote.change_pct)],
    ["成本价", money(item.position.entry_price)], ["持仓收益", pct(item.position.return_pct)],
    ["持仓市值", money(item.position.market_value)], ["浮动盈亏", money(item.position.unrealized_pnl)],
    ["5日均线", money(item.technicals.ma5)], ["20日均线", money(item.technicals.ma20)],
  ];
  let html = detailSection("实时状态", `<div class="metric-grid">${metrics.map(([key, value]) => `<dl class="metric"><dt>${key}</dt><dd>${value}</dd></dl>`).join("")}</div>`);
  html += manualTradeSection(item);
  if (decisionCard) {
    const levels = decisionCard.price_levels || {};
    const decision = decisionCard.decision || {};
    const quality = decisionCard.data_quality || {};
    const qualityMetrics = [
      ["总状态", qualityLabel(quality)],
      ["可信等级", trustLabel(quality)],
      ["源一致性", consistencyLabel(quality)],
      ["交易时段", quality.market_session?.label || "--"],
      ["盘中确认", quality.data_trust?.intraday_decision_allowed ? "允许" : "禁止"],
      ["行情延迟", quality.quote?.lag_seconds == null ? "--" : `${Number(quality.quote.lag_seconds).toFixed(1)}s`],
      ["日线最新", quality.daily?.latest_trade_date || "--"],
      ["日线样本", quality.daily?.row_count ?? "--"],
      ["分钟线最新", quality.minute?.latest_timestamp || "--"],
      ["分钟线样本", quality.minute?.bar_count ?? "--"],
    ];
    const qualityMessages = [...(quality.data_trust?.reasons || []), ...(quality.blockers || []), ...(quality.warnings || [])];
    const consistency = quality.source_consistency || {};
    const consistencyMetrics = [
      ["一致性状态", consistencyLabel(quality)],
      ["最大允许偏差", consistency.max_diff_pct == null ? "--" : `${Number(consistency.max_diff_pct).toFixed(2)}%`],
      ["冲突数量", String((consistency.issues || []).length)],
    ];
    const cardMetrics = [
      ["动作等级", actionTierFor(item).label],
      ["技术操作档位", technicalOperation.tier_label || "--"],
      ["技术允许观察", technicalOperation.allow_buy_watch || technicalOperation.allow_t_watch ? "允许进入观察" : "不支持买入/做T"],
      ["状态", decisionCard.state_label],
      ["建议动作", decision.action_label],
      ["置信度", decision.confidence],
      ["执行许可", decision.execution_allowed ? "允许进入人工确认" : "禁止直接执行"],
      ["当前价", money(levels.current_price)],
      ["止损价", money(levels.stop_loss_price)],
      ["做T阻断价", money(levels.near_stop_block_price)],
      ["20日均线", money(levels.ma20)],
    ];
    const blockers = decisionCard.blockers || [];
    const evidence = decisionCard.evidence || [];
    const actionSteps = decision.action_steps || [];
    html += renderTechnicalAssessment(decisionCard.technical_assessment);
    html += detailSection(
      "实时决策卡",
      `<div class="metric-grid">${cardMetrics.map(([key, value]) => `<dl class="metric"><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></dl>`).join("")}</div>
      <div class="action-panel action-${escapeHtml(decisionCard.state || "observe")}">
        <div class="action-panel-title">当前可执行步骤</div>
        <p><strong>${escapeHtml(decision.next_step || "")}</strong></p>
        ${technicalOperation.reason ? `<p class="secondary"><strong>技术理由：</strong>${escapeHtml(technicalOperation.reason)}</p>` : ""}
        ${technicalOperation.next_step ? `<p class="secondary"><strong>技术下一步：</strong>${escapeHtml(technicalOperation.next_step)}</p>` : ""}
        ${actionSteps.length ? `<ol class="reason-list">${actionSteps.map(step => `<li>${escapeHtml(step)}</li>`).join("")}</ol>` : ""}
      </div>
      ${blockers.length ? `<h4>阻断原因</h4><ul class="reason-list">${blockers.slice(0, 6).map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>` : ""}
      <h4>证据链</h4><ul class="reason-list">${evidence.slice(0, 8).map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>`
    );
    html += renderPositiveTiming(decisionCard.positive_timing, technicalOperation);
    html += renderCapitalPlan(decisionCard.capital_plan);
    html += detailSection(
      "数据质量",
      `<p>${qualityBadge(item)} <strong>${escapeHtml(qualitySummary(quality))}</strong></p>
      <div class="metric-grid">${qualityMetrics.map(([key, value]) => `<dl class="metric"><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></dl>`).join("")}</div>
      <h4>数据源一致性</h4>
      <div class="metric-grid">${consistencyMetrics.map(([key, value]) => `<dl class="metric"><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></dl>`).join("")}</div>
      ${renderConsistencyChecks(quality)}
      ${qualityMessages.length ? `<h4>质量问题</h4><ul class="reason-list">${qualityMessages.slice(0, 6).map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>` : ""}`
    );
  }
  const decision = item.action_decision;
  const decisionDetails = item.reduction_plan?.status === "actionable" ? "" : backtest?.verdict === "rule_observation_only" && decision
    ? `<h4>再次触发条件</h4><ol class="reason-list">${decision.execute_when.map(condition => `<li>${escapeHtml(condition)}</li>`).join("")}</ol><h4>操作后的效果</h4><ul class="reason-list">${decision.expected_effects.map(effect => `<li>${escapeHtml(effect)}</li>`).join("")}</ul><p class="secondary">${escapeHtml(decision.prediction_note)}</p>`
    : `<h4>重新开放反T的条件</h4><ol class="reason-list"><li>至少积累30次历史触发。</li><li>回补成功率达到65%以上，且95%成功率下限不低于50%。</li><li>再经过模拟盘验证后，才恢复反T候选。</li></ol>`;
  html += detailSection("自动操作结论", `<p>${actionTierBadge(item)} <span class="state-badge state-${item.state}">${escapeHtml(automaticDecision.level)}</span></p><p><strong>${escapeHtml(automaticDecision.headline)}</strong></p><p>${escapeHtml(automaticDecision.action)}</p>${automaticDecision.reasons.length ? `<h4>程序判定依据</h4><ul class="reason-list">${automaticDecision.reasons.map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>` : ""}${decisionDetails}`);
  if (isReverseTPriceAlert(item)) {
    html += detailSection("反T价格提醒", `<p><strong>实时价格已进入卖出观察区</strong></p><p>这是价格到位提醒，不等待回补价出现，也不代表保证能够低价买回。</p><div class="metric-grid"><dl class="metric"><dt>卖出观察区</dt><dd>${num(item.reverse_t_plan.sell_zone[0])}–${num(item.reverse_t_plan.sell_zone[1])}元</dd></dl><dl class="metric"><dt>回补参考上限</dt><dd>${money(item.reverse_t_plan.buyback_max_price)}</dd></dl></div>`);
  }
  if (forecast) {
    const forecastZone = forecast.predicted_sell_zone ? `${num(forecast.predicted_sell_zone[0])}–${num(forecast.predicted_sell_zone[1])}元` : "--";
    const forecastMetrics = [
      ["预测周期", forecast.horizon_minutes ? `未来${forecast.horizon_minutes}分钟` : "--"],
      ["预测高点区间", forecastZone], ["到达概率", pct(forecast.reach_probability_pct)],
      ["到达后可回补概率", pct(forecast.roundtrip_probability_pct)], ["到达且回补联合概率", pct(forecast.joint_roundtrip_probability_pct)],
      ["预测回补上限", money(forecast.predicted_buyback_max_price)],
      ["相似样本", forecast.neighbor_count || forecast.sample_count || 0],
    ];
    html += detailSection("下一次反T概率预测", `<p><strong>${escapeHtml(forecast.status_label)}</strong></p><div class="metric-grid">${forecastMetrics.map(([key, value]) => `<dl class="metric"><dt>${key}</dt><dd>${value}</dd></dl>`).join("")}</div><p class="secondary">${escapeHtml(forecast.note || "预测仅用于提前预警，不代表价格必然到达。")}</p>`);
  }
  const reverseAudit = item.reverse_t_plan;
  if (reverseAudit?.sell_zone && item.quote.high && item.quote.latest_price) {
    const pullbackFromHigh = (Number(item.quote.high) - Number(item.quote.latest_price)) / Number(item.quote.high) * 100;
    const leftSellZone = Number(item.quote.latest_price) < Number(reverseAudit.sell_zone[0]);
    const auditReasons = [];
    if (Number(item.capital_flow?.main_net_inflow_ratio_pct) >= 3) auditReasons.push(`主力净流入占比${pct(item.capital_flow.main_net_inflow_ratio_pct)}高于3%转弱线。`);
    if (!backtest || backtest.verdict !== "rule_observation_only") auditReasons.push(backtest?.verdict_label || "尚无通过门禁的回测结果。");
    if (leftSellZone) auditReasons.push(`现价已低于卖出观察区下限${num(reverseAudit.sell_zone[0])}元，当前卖点已经过去。`);
    if (reverseAudit.buyback_max_price != null && Number(item.quote.latest_price) > Number(reverseAudit.buyback_max_price)) auditReasons.push(`现价尚未降至参考回补上限${num(reverseAudit.buyback_max_price)}元。`);
    const auditStatus = pullbackFromHigh >= 0.5 ? "已检测到冲高回落" : "尚未形成明显高点回落";
    const auditMetrics = [
      ["当日最高", money(item.quote.high)], ["当前价格", money(item.quote.latest_price)],
      ["高点回落", pct(pullbackFromHigh)], ["卖出观察区", `${num(reverseAudit.sell_zone[0])}–${num(reverseAudit.sell_zone[1])}元`],
    ];
    html += detailSection("反T机会审计", `<p><strong>${auditStatus}</strong></p><div class="metric-grid">${auditMetrics.map(([key, value]) => `<dl class="metric"><dt>${key}</dt><dd>${value}</dd></dl>`).join("")}</div>${auditReasons.length ? `<h4>未发出执行提醒的原因</h4><ul class="reason-list">${auditReasons.map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>` : ""}`);
  }
  const multi = item.technicals?.multi_timeframe || {};
  const multiMetrics = [
    ["周线方向", multi.alignment === "bullish" ? "周月共振向上" : multi.alignment === "bearish" ? "周月共同偏弱" : multi.alignment === "mixed" ? "周期分歧" : "历史不足"],
    ["4周均价", money(multi.weekly_ma4)], ["12周均价", money(multi.weekly_ma12)],
    ["4周收益", pct(multi.weekly_return_4_pct)], ["3月均价", money(multi.monthly_ma3)],
    ["6月均价", money(multi.monthly_ma6)], ["3月收益", pct(multi.monthly_return_3_pct)],
  ];
  html += detailSection("日线 / 周线 / 月线", `<div class="metric-grid">${multiMetrics.map(([key, value]) => `<dl class="metric"><dt>${key}</dt><dd>${value}</dd></dl>`).join("")}</div>`);
  html += detailSection("当前状态", `<p><span class="state-badge state-${displayStateFor(item)}">${escapeHtml(displayStateLabelFor(item))}</span></p>`);
  if (backtest) {
    const backtestMetrics = [
      ["回测交易日", `${backtest.trading_days}日`], ["触发次数", backtest.triggered_count],
      ["成功回补", backtest.completed_count], ["未回补", backtest.not_bought_back_count],
      ["回补成功率", pct(backtest.success_rate_pct)], ["95%成功率下限", pct(backtest.success_rate_wilson_lower_95_pct)],
      ["已完成净收益合计", money(backtest.total_completed_net_profit)],
    ];
    const intraday = backtest.intraday_observation;
    let intradayText = "今日尚未形成完整反T模拟交易。";
    if (intraday?.status === "completed") {
      intradayText = `今日模拟已完成：${intraday.sell_time.slice(11)}按${num(intraday.sell_price)}元卖出${intraday.shares}股，${intraday.buy_time.slice(11)}触及${num(intraday.buy_price)}元回补，扣费后${money(intraday.net_profit)}。该机会已经发生，不追单。`;
    } else if (intraday?.status === "not_bought_back") {
      intradayText = `今日模拟卖出后尚未回补，当前属于未完成风险，不计入历史胜率。`;
    }
    html += detailSection("反T历史回测", `<p><strong>${escapeHtml(backtest.verdict_label)}</strong></p><p>${escapeHtml(intradayText)}</p><div class="metric-grid">${backtestMetrics.map(([key, value]) => `<dl class="metric"><dt>${key}</dt><dd>${value}</dd></dl>`).join("")}</div><p class="secondary">盘中当天不计入历史验证。仅验证5分钟价格规则和估算费用；未覆盖历史资金流、滑点及盘口排队。</p>`);
  }
  html += detailSection("盘中信号", item.signals.length ? `<ul class="signal-list">${item.signals.map(signal => `<li>${escapeHtml(signal.message)}</li>`).join("")}</ul>` : "<p>当前没有新增盘中风险信号。</p>");
  const flow = item.capital_flow || {};
  const flowMetrics = [
    ["主力净额", compactMoney(flow.main_net_inflow)], ["主力净占比", pct(flow.main_net_inflow_ratio_pct)],
    ["超大单净额", compactMoney(flow.super_large_net_inflow)], ["大单净额", compactMoney(flow.large_net_inflow)],
    ["中单净额", compactMoney(flow.medium_net_inflow)], ["小单净额", compactMoney(flow.small_net_inflow)],
  ];
  html += detailSection("主力资金流", `<div class="metric-grid">${flowMetrics.map(([key, value]) => `<dl class="metric"><dt>${key}</dt><dd>${value}</dd></dl>`).join("")}</div><p class="secondary">${escapeHtml(flow.interpretation || "")}</p>`);
  const reversePlan = item.reverse_t_plan;
  html += reverseTClosureSection(item);
  if (reversePlan) {
    const reverseTechnicalBlock = renderTechnicalOperationBlock(technicalOperation, "reverse_t");
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
      planMetrics.push(["最低达标情景总费用", money(reversePlan.cost_estimate.total_fees)]);
      planMetrics.push(["卖出佣金", money(reversePlan.cost_estimate.sell_commission)]);
      planMetrics.push(["买入佣金", money(reversePlan.cost_estimate.buy_commission)]);
      planMetrics.push(["卖出印花税", money(reversePlan.cost_estimate.stamp_duty)]);
      planMetrics.push(["过户费", money(reversePlan.cost_estimate.transfer_fee)]);
      planMetrics.push(["达到回补价时净收益", money(reversePlan.cost_estimate.net_profit)]);
      planMetrics.push(["收益性质", "最低门槛测算，不是价格预测"]);
    }
    planMetrics.push(["费用参数", reversePlan.cost_model_verified ? "已按交割单核验" : "保守假设，尚未核验"]);
    if (reversePlan.high_position_ratio_warning) planMetrics.push(["仓位风险", "单次涉及半仓，高风险"]);
    const blockers = reversePlan.blockers || [];
    const blockerDetails = reversePlan.blocker_details || [];
    const blockerHtml = blockerDetails.length
      ? `<div class="blocker-list">${blockerDetails.map(blocker => `
        <div class="blocker-item">
          <div><strong>${escapeHtml(blocker.label || blocker.code || "阻断项")}</strong><span>${escapeHtml(blocker.current || "--")}</span></div>
          <p>${escapeHtml(blocker.reason || "")}</p>
          <p class="secondary">${escapeHtml(blocker.next_step || "")}</p>
        </div>`).join("")}</div>`
      : blockers.length ? `<ul class="reason-list">${blockers.map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>` : "";
    const executionSteps = reversePlan.execution_steps || reversePlan.instructions || [];
    html += detailSection("反T降低成本", `<div class="metric-grid">${planMetrics.map(([key, value]) => `<dl class="metric"><dt>${key}</dt><dd>${value}</dd></dl>`).join("")}</div>
      ${reversePlan.next_action ? `<div class="action-panel action-${reversePlan.status === "candidate" ? "reverse_t_watch" : "hold_no_add"}"><div class="action-panel-title">下一步动作</div><p>${escapeHtml(reversePlan.next_action)}</p></div>` : ""}
      ${reverseTechnicalBlock ? `<h4>技术面门禁</h4><div class="blocker-list">${reverseTechnicalBlock}</div>` : ""}
      <p>${escapeHtml(reversePlan.failure_result || "")}</p>
      ${blockerHtml || ""}
      ${executionSteps.length ? `<h4>操作步骤</h4><ol class="reason-list">${executionSteps.map(step => `<li>${escapeHtml(step)}</li>`).join("")}</ol>` : ""}`);
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
    const tags = Object.entries(event.signature || {}).map(([code, info]) => `<span class="event-tag">${code} · ${labels[info.state] || info.state}${info.reverse_t_price_alert ? " · 反T价格提醒" : ""}</span>`).join("");
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

function manualTradeInteractionActive() {
  const modal = document.querySelector("#manualTradeConfirm");
  if (modal && !modal.hidden) return true;
  const active = document.activeElement;
  return Boolean(active && active.closest && active.closest(".manual-trade-form"));
}

async function loadData() {
  try {
    const fetchJson = async (url, fallback = null, required = false) => {
      try {
        const response = await fetch(url, { cache: "no-store" });
        if (!response.ok) {
          if (required) throw new Error(`${url} ${response.status}`);
          return fallback;
        }
        return await response.json();
      } catch (error) {
        if (required) throw error;
        return fallback;
      }
    };
    const [snapshot, research, backtests, forecasts, decisionCards, refreshCheck, status, events] = await Promise.all([
      fetchJson("/api/snapshot", null, true),
      fetchJson("/api/research", { items: [] }),
      fetchJson("/api/reverse-t-backtest", { items: [] }),
      fetchJson("/api/reverse-t-forecast", { items: [] }),
      fetchJson("/api/decision-cards", { cards: [] }),
      fetchJson("/api/market-wait-refresh", null),
      fetchJson("/api/status", { running: false }),
      fetchJson("/api/events?limit=20", { events: [] }),
    ]);
    state.snapshot = snapshot;
    state.research = new Map((research.items || []).map(item => [item.code, item]));
    state.backtests = new Map((backtests.items || []).map(item => [item.code, item]));
    state.forecasts = new Map((forecasts.items || []).map(item => [item.code, item]));
    state.decisionReport = decisionCards;
    state.decisionCards = new Map((decisionCards.cards || []).map(card => [card.code, card]));
    state.refreshCheck = refreshCheck;
    state.events = events.events || [];
    updateHeader(status);
    renderRefreshAlert();
    renderSummary();
    renderPositions();
    renderEvents();
    if (state.selectedCode && !manualTradeInteractionActive()) openDetail(state.selectedCode);
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
document.querySelector("#refreshAlert").addEventListener("click", event => {
  const button = event.target.closest(".copy-refresh");
  if (!button) return;
  navigator.clipboard?.writeText(button.dataset.command || "");
  button.textContent = "已复制";
  setTimeout(() => { button.textContent = "复制"; }, 1200);
});
document.querySelector("#detailContent").addEventListener("submit", async event => {
  const form = event.target.closest(".manual-trade-form");
  if (!form) return;
  event.preventDefault();
  prepareManualTradeConfirmation(form);
});
document.querySelector("#detailContent").addEventListener("click", event => {
  const submitButton = event.target.closest(".manual-trade-form button[type='submit']");
  if (submitButton) {
    event.preventDefault();
    prepareManualTradeConfirmation(submitButton.closest(".manual-trade-form"));
    return;
  }
  const button = event.target.closest("[data-manual-preset]");
  if (!button) return;
  const form = button.closest(".manual-trade-form");
  if (!form) return;
  form.querySelector('[name="side"]').value = button.dataset.side || "sell";
  form.querySelector('[name="price"]').value = button.dataset.price || "";
  form.querySelector('[name="shares"]').value = button.dataset.shares || "";
  form.querySelector('[name="note"]').value = button.dataset.note || "";
  form.querySelector('[name="trade_intent"]').value = button.dataset.tradeIntent || "";
  form.querySelector('[name="linked_trade_id"]').value = button.dataset.linkedTradeId || "";
  updateManualTradeImpact(form);
  const status = form.querySelector(".manual-trade-status");
  status.textContent = "已填入系统建议成交参数；确认已真实成交后再点击记录。";
});
document.querySelector("#detailContent").addEventListener("input", event => {
  const form = event.target.closest(".manual-trade-form");
  if (form) updateManualTradeImpact(form);
});
document.querySelector("#detailContent").addEventListener("change", event => {
  const form = event.target.closest(".manual-trade-form");
  if (form) updateManualTradeImpact(form);
});
document.querySelector("#manualTradeCancel").addEventListener("click", closeManualTradeConfirm);
document.querySelector("#manualTradeCancelTop").addEventListener("click", closeManualTradeConfirm);
document.querySelector("#manualTradeConfirm").addEventListener("click", event => {
  if (event.target.id === "manualTradeConfirm") closeManualTradeConfirm();
});
document.querySelector("#manualTradeConfirmButton").addEventListener("click", async event => {
  const pending = state.pendingManualTrade;
  if (!pending) return;
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await submitManualTrade(pending.payload, pending.form);
  } catch (_error) {
    button.disabled = false;
  }
});
document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  if (!document.querySelector("#manualTradeConfirm").hidden) {
    closeManualTradeConfirm();
    return;
  }
  closeDetail();
});

loadData();
setInterval(loadData, 5000);
