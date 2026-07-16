const state = {
  snapshot: null,
  research: new Map(),
  backtests: new Map(),
  forecasts: new Map(),
  decisionCards: new Map(),
  decisionReport: null,
  events: [],
  filter: "all",
  search: "",
  selectedCode: null,
};

const labels = {
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

function qualitySummary(quality) {
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

function decisionCardFor(item) {
  return state.decisionCards.get(item.code);
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
  const dataPaused = decisionCards.filter(card => ["data_stale", "data_insufficient"].includes(card.state)).length;
  const qualityStale = decisionCards.filter(card => card.data_quality?.overall_status === "stale").length;
  const qualityBlocked = decisionCards.filter(card => ["insufficient", "missing"].includes(card.data_quality?.overall_status)).length;
  const trustHigh = decisionCards.filter(card => card.data_quality?.data_trust?.level === "high").length;
  const trustLow = decisionCards.filter(card => card.data_quality?.data_trust?.level === "low").length;
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
    ["退出风险", `${exitRisk || risk} 只`, `${dataPaused} 只暂停决策`],
    ["数据可信", `${trustHigh} 高 / ${trustLow} 低`, `${qualityStale} 过期 · ${qualityBlocked} 阻断`],
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

function tableRow(item) {
  const lag = item.quote.quote_lag_seconds;
  const card = decisionCardFor(item);
  const displayState = displayStateFor(item);
  const reverseTag = isReverseTPriceAlert(item)
    ? `<div class="advice-tag">已到反T卖出观察区 · 回补参考${money(item.reverse_t_plan.buyback_max_price)}</div>`
    : isReverseTCandidate(item) ? '<div class="advice-tag">反T候选 · 先卖100股</div>' : "";
  const cardTag = card ? `<div class="advice-tag">${escapeHtml(card.decision.confidence)} · ${escapeHtml(card.reason)}</div>` : "";
  const dataTag = card ? `<div class="quality-line">${qualityBadge(item)}<span>${escapeHtml(qualitySummary(dataQualityFor(item)))}</span></div>` : "";
  return `<tr data-code="${item.code}" tabindex="0">
    <td><div class="stock-name">${escapeHtml(item.name)}</div><div class="stock-code">${item.code}</div></td>
    <td class="number"><div>${num(item.quote.latest_price)}</div><div class="secondary ${tone(item.quote.change_pct)}">${pct(item.quote.change_pct)}</div></td>
    <td class="number"><div class="${tone(item.position.unrealized_pnl)}">${money(item.position.unrealized_pnl)}</div><div class="secondary ${tone(item.position.return_pct)}">${pct(item.position.return_pct)}</div></td>
    <td class="number"><div>${pct(item.position.live_position_pct)}</div><div class="secondary">${Number(item.position.shares).toFixed(0)}股</div></td>
    <td><span class="state-badge state-${displayState}">${escapeHtml(displayStateLabelFor(item))}</span></td>
    <td class="advice">${escapeHtml(adviceFor(item))}${cardTag}${dataTag}${reverseTag}</td>
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
  const dataTag = card ? `<div class="quality-line">${qualityBadge(item)}<span>${escapeHtml(qualitySummary(dataQualityFor(item)))}</span></div>` : "";
  return `<article class="position-card" data-code="${item.code}" tabindex="0">
    <div class="card-top">
      <div><div class="stock-name">${escapeHtml(item.name)}</div><div class="stock-code">${item.code}</div></div>
      <div class="number"><strong>${num(item.quote.latest_price)}</strong><div class="secondary ${tone(item.quote.change_pct)}">${pct(item.quote.change_pct)}</div></div>
    </div>
    <div class="card-row"><span>持仓盈亏</span><span class="${tone(item.position.unrealized_pnl)}">${money(item.position.unrealized_pnl)} · ${pct(item.position.return_pct)}</span></div>
    <div class="card-row"><span class="state-badge state-${displayState}">${escapeHtml(displayStateLabelFor(item))}</span><span>仓位 ${pct(item.position.live_position_pct)}</span></div>
    <div class="card-row"><span>主力净额</span><span class="${tone(item.capital_flow?.main_net_inflow)}">${compactMoney(item.capital_flow?.main_net_inflow)} · ${pct(item.capital_flow?.main_net_inflow_ratio_pct)}</span></div>
    <div class="card-advice">${escapeHtml(adviceFor(item))}${cardTag}${dataTag}${reverseTag}</div>
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
  const backtest = state.backtests.get(code);
  const forecast = state.forecasts.get(code);
  const decisionCard = state.decisionCards.get(code);
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
  if (decisionCard) {
    const levels = decisionCard.price_levels || {};
    const decision = decisionCard.decision || {};
    const quality = decisionCard.data_quality || {};
    const qualityMetrics = [
      ["总状态", qualityLabel(quality)],
      ["可信等级", trustLabel(quality)],
      ["盘中确认", quality.data_trust?.intraday_decision_allowed ? "允许" : "禁止"],
      ["行情延迟", quality.quote?.lag_seconds == null ? "--" : `${Number(quality.quote.lag_seconds).toFixed(1)}s`],
      ["日线最新", quality.daily?.latest_trade_date || "--"],
      ["日线样本", quality.daily?.row_count ?? "--"],
      ["分钟线最新", quality.minute?.latest_timestamp || "--"],
      ["分钟线样本", quality.minute?.bar_count ?? "--"],
    ];
    const qualityMessages = [...(quality.data_trust?.reasons || []), ...(quality.blockers || []), ...(quality.warnings || [])];
    const cardMetrics = [
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
    html += detailSection(
      "实时决策卡",
      `<div class="metric-grid">${cardMetrics.map(([key, value]) => `<dl class="metric"><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></dl>`).join("")}</div>
      <p><strong>${escapeHtml(decision.next_step || "")}</strong></p>
      ${blockers.length ? `<h4>阻断原因</h4><ul class="reason-list">${blockers.slice(0, 6).map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>` : ""}
      <h4>证据链</h4><ul class="reason-list">${evidence.slice(0, 8).map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>`
    );
    html += detailSection(
      "数据质量",
      `<p>${qualityBadge(item)} <strong>${escapeHtml(qualitySummary(quality))}</strong></p>
      <div class="metric-grid">${qualityMetrics.map(([key, value]) => `<dl class="metric"><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></dl>`).join("")}</div>
      ${qualityMessages.length ? `<h4>质量问题</h4><ul class="reason-list">${qualityMessages.slice(0, 6).map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>` : ""}`
    );
  }
  const decision = item.action_decision;
  const decisionDetails = item.reduction_plan?.status === "actionable" ? "" : backtest?.verdict === "rule_observation_only" && decision
    ? `<h4>再次触发条件</h4><ol class="reason-list">${decision.execute_when.map(condition => `<li>${escapeHtml(condition)}</li>`).join("")}</ol><h4>操作后的效果</h4><ul class="reason-list">${decision.expected_effects.map(effect => `<li>${escapeHtml(effect)}</li>`).join("")}</ul><p class="secondary">${escapeHtml(decision.prediction_note)}</p>`
    : `<h4>重新开放反T的条件</h4><ol class="reason-list"><li>至少积累30次历史触发。</li><li>回补成功率达到65%以上，且95%成功率下限不低于50%。</li><li>再经过模拟盘验证后，才恢复反T候选。</li></ol>`;
  html += detailSection("自动操作结论", `<p><span class="state-badge state-${item.state}">${escapeHtml(automaticDecision.level)}</span></p><p><strong>${escapeHtml(automaticDecision.headline)}</strong></p><p>${escapeHtml(automaticDecision.action)}</p>${automaticDecision.reasons.length ? `<h4>程序判定依据</h4><ul class="reason-list">${automaticDecision.reasons.map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>` : ""}${decisionDetails}`);
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

async function loadData() {
  try {
    const [snapshot, research, backtests, forecasts, decisionCards, status, events] = await Promise.all([
      fetch("/api/snapshot", { cache: "no-store" }).then(response => response.json()),
      fetch("/api/research", { cache: "no-store" }).then(response => response.json()),
      fetch("/api/reverse-t-backtest", { cache: "no-store" }).then(response => response.json()),
      fetch("/api/reverse-t-forecast", { cache: "no-store" }).then(response => response.json()),
      fetch("/api/decision-cards", { cache: "no-store" }).then(response => response.ok ? response.json() : { cards: [] }),
      fetch("/api/status", { cache: "no-store" }).then(response => response.json()),
      fetch("/api/events?limit=20", { cache: "no-store" }).then(response => response.json()),
    ]);
    state.snapshot = snapshot;
    state.research = new Map((research.items || []).map(item => [item.code, item]));
    state.backtests = new Map((backtests.items || []).map(item => [item.code, item]));
    state.forecasts = new Map((forecasts.items || []).map(item => [item.code, item]));
    state.decisionReport = decisionCards;
    state.decisionCards = new Map((decisionCards.cards || []).map(card => [card.code, card]));
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
