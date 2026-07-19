(function () {
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, char => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "'": "&#39;",
      '"': "&quot;",
    }[char]));
  }

  function cardLabel(card) {
    const name = card?.name || card?.code || "--";
    return card?.code && card?.name ? `${name} ${card.code}` : name;
  }

  function uniqueItems(items) {
    return [...new Set((items || []).filter(Boolean))];
  }

  function addCategory(categories, category) {
    if (!category.count) return;
    categories.push({
      tone: "caution",
      priority: 0,
      examples: [],
      ...category,
    });
  }

  function activeReviewItems(queue) {
    return (queue || []).filter(item => !["handled", "ignored"].includes(item.review_resolution));
  }

  function cardMatchesDataBlocker(card) {
    return ["market_wait", "data_stale", "data_insufficient"].includes(card?.state)
      || ["missing", "insufficient", "stale"].includes(card?.data_quality?.overall_status);
  }

  function cardMatchesCapitalBlocker(card) {
    const plan = card?.decision?.technical_operation?.capital_plan || card?.technical_operation?.capital_plan;
    const actions = card?.decision?.actions || {};
    return plan?.portfolio_capital_link?.status === "portfolio_budget_blocked"
      || actions["正T买入"]?.status === "blocked_by_capital_budget"
      || actions["补仓买入"]?.status === "blocked_by_capital_budget";
  }

  function cardMatchesLiquidityBlocker(card) {
    return card?.liquidity_activity_gate?.status === "blocked";
  }

  function cardMatchesLiquidityCaution(card) {
    return card?.liquidity_activity_gate?.status === "caution";
  }

  function cardMatchesMinuteBlocker(card) {
    const minute = card?.minute_confirmation || {};
    const actions = card?.decision?.actions || {};
    return minute.status === "block"
      || actions["反T卖出"]?.status_label === "分钟阻断"
      || actions["正T买入"]?.status_label === "分钟阻断";
  }

  function cardMatchesTechnicalBlocker(card) {
    const review = card?.post_unlock_review_summary || {};
    const tier = card?.decision?.technical_operation?.tier;
    return ["blocked_after_unlock", "technical_locked"].includes(review.status)
      || ["risk_control_first", "forbid_chase", "observe_only"].includes(tier);
  }

  function cardMatchesExecutionBlocker(card) {
    const gate = card?.t_performance_gate || {};
    const quality = card?.execution_quality_gate || {};
    const actions = card?.decision?.actions || {};
    return gate.status === "blocked"
      || quality.status === "blocked"
      || actions["反T卖出"]?.status_label === "绩效阻断"
      || actions["反T卖出"]?.status_label === "执行评分阻断";
  }

  function examplesFrom(cards) {
    return uniqueItems(cards.map(cardLabel)).slice(0, 3);
  }

  function codesFrom(cards) {
    return uniqueItems(cards.map(card => card?.code)).slice(0, 80);
  }

  function buildBlockerSummary({cards = [], triggerReviewQueue = [], report = {}} = {}) {
    const categories = [];
    const activeQueue = activeReviewItems(triggerReviewQueue);
    const actionableQueue = activeQueue.filter(item => item.status === "action_required" && !item.expired);
    const expiredQueue = activeQueue.filter(item => item.expired);
    const reviewQueue = activeQueue.filter(item => item.status === "review_required" && !item.expired);

    addCategory(categories, {
      key: "stale",
      tone: "blocked",
      priority: 100,
      title: "决策链过期",
      count: report?.stale_due_to_snapshot_date ? Number(report.original_card_count || 1) : 0,
      summary: "实时决策卡早于行情快照，旧价格区间和操作步骤已停用。",
      nextStep: "先刷新完整日内决策链，再看新的唯一结论。",
      targetView: "refresh",
    });

    addCategory(categories, {
      key: "trigger",
      tone: "risk",
      priority: 95,
      title: "触发队列待处理",
      count: actionableQueue.length + expiredQueue.length,
      summary: expiredQueue.length ? "存在已过期触发计划，不能继续按旧区间交易。" : "已有盘中触发计划进入待处理队列。",
      nextStep: expiredQueue.length ? "先刷新过期计划；未过期的再逐个确认。" : "先处理事件队列，再看普通持仓机会。",
      examples: uniqueItems([...actionableQueue, ...expiredQueue].map(item => item.name || item.code)).slice(0, 3),
      codes: uniqueItems([...actionableQueue, ...expiredQueue].map(item => item.code)).slice(0, 80),
      targetView: "events",
    });

    const riskCards = cards.filter(card => ["exit_risk_review", "risk_reduction_review"].includes(card?.state));
    addCategory(categories, {
      key: "risk",
      tone: "risk",
      priority: 90,
      title: "退出风控优先",
      count: riskCards.length,
      summary: "部分持仓处在止损、减仓或退出复核路径，主动买入和做T靠后。",
      nextStep: "先看对应个股的硬止损、反抽减仓、站稳降级三条路径。",
      examples: examplesFrom(riskCards),
      codes: codesFrom(riskCards),
      targetView: "positions",
    });

    const dataCards = cards.filter(cardMatchesDataBlocker);
    const liveGateCards = cards.filter(card => !cardMatchesDataBlocker(card));
    addCategory(categories, {
      key: "data",
      tone: "caution",
      priority: 80,
      title: "数据/时段阻断",
      count: dataCards.length,
      summary: "行情时段、快照新鲜度或数据可信度不足，不能扩大主动交易。",
      nextStep: "只处理已确认风控；等待数据恢复或下一轮刷新。",
      examples: examplesFrom(dataCards),
      codes: codesFrom(dataCards),
      targetView: "positions",
    });

    const usage = report?.intraday_capital_usage || {};
    const capitalCards = cards.filter(cardMatchesCapitalBlocker);
    addCategory(categories, {
      key: "capital",
      tone: usage.remaining_add_amount <= 0 ? "blocked" : "caution",
      priority: 74,
      title: "资金预算受限",
      count: capitalCards.length || (usage.status === "budget_exhausted" ? 1 : 0),
      summary: "组合日内新增资金或单票仓位限制正在约束买入计划。",
      nextStep: "先确认剩余额度；预算未释放前不继续追加正T或补仓。",
      examples: examplesFrom(capitalCards),
      codes: codesFrom(capitalCards),
      targetView: "positions",
    });

    const liquidityBlockedCards = liveGateCards.filter(cardMatchesLiquidityBlocker);
    const liquidityCautionCards = liveGateCards.filter(cardMatchesLiquidityCaution);
    addCategory(categories, {
      key: "liquidity",
      tone: liquidityBlockedCards.length ? "caution" : "observe",
      priority: liquidityBlockedCards.length ? 70 : 45,
      title: "成交活跃度复核",
      count: liquidityBlockedCards.length || liquidityCautionCards.length,
      summary: liquidityBlockedCards.length ? "盘口或成交活跃度未通过，主动交易被阻断。" : "成交活跃度偏弱，只适合小额限价观察。",
      nextStep: liquidityBlockedCards.length ? "等待量能或报价恢复后再评估。" : "若其他门禁通过，也只按最小股数限价。",
      examples: examplesFrom(liquidityBlockedCards.length ? liquidityBlockedCards : liquidityCautionCards),
      codes: codesFrom(liquidityBlockedCards.length ? liquidityBlockedCards : liquidityCautionCards),
      targetView: "positions",
    });

    const minuteCards = liveGateCards.filter(cardMatchesMinuteBlocker);
    addCategory(categories, {
      key: "minute",
      tone: "caution",
      priority: 68,
      title: "分钟确认不足",
      count: minuteCards.length,
      summary: "日线条件可能接近，但分时没有二次确认。",
      nextStep: "等待分钟确认，不提前挂主动单。",
      examples: examplesFrom(minuteCards),
      codes: codesFrom(minuteCards),
      targetView: "positions",
    });

    const technicalCards = liveGateCards.filter(cardMatchesTechnicalBlocker);
    addCategory(categories, {
      key: "technical",
      tone: "caution",
      priority: 64,
      title: "技术门禁未解除",
      count: technicalCards.length,
      summary: "指标反弹或观察信号还没有转成可执行结论。",
      nextStep: "只保留观察；等趋势、量能和分时同时修复。",
      examples: examplesFrom(technicalCards),
      codes: codesFrom(technicalCards),
      targetView: "positions",
    });

    const executionCards = cards.filter(cardMatchesExecutionBlocker);
    addCategory(categories, {
      key: "execution",
      tone: "blocked",
      priority: 72,
      title: "执行绩效阻断",
      count: executionCards.length,
      summary: "做T或执行评分没有通过，避免继续放大低质量交易。",
      nextStep: "先复盘历史执行质量；未解除前不按该方向做T。",
      examples: examplesFrom(executionCards),
      codes: codesFrom(executionCards),
      targetView: "positions",
    });

    addCategory(categories, {
      key: "review",
      tone: "caution",
      priority: 60,
      title: "人工复核未完成",
      count: reviewQueue.length,
      summary: "存在未处理的人工复核项，系统不会把它们当成可直接执行。",
      nextStep: "先打开今日处理顺序，完成复核后再看是否可交易。",
      examples: uniqueItems(reviewQueue.map(item => item.name || item.code)).slice(0, 3),
      codes: uniqueItems(reviewQueue.map(item => item.code)).slice(0, 80),
      targetView: "events",
    });

    categories.sort((left, right) => right.priority - left.priority || right.count - left.count);
    const primary = categories[0] || {
      key: "none",
      tone: "observe",
      priority: 0,
      title: "暂无硬阻断",
      count: 0,
      summary: "当前没有聚合级硬阻断，但仍需以个股唯一结论为准。",
      nextStep: "继续盯盘；只在详情页执行前检查全部通过后操作。",
      examples: [],
      codes: [],
      targetView: "positions",
    };
    return {primary, categories: categories.slice(0, 4)};
  }

  function renderReason(category, isPrimary) {
    const examples = category.examples?.length ? ` · ${category.examples.join("、")}` : "";
    return `<button class="blocker-reason blocker-reason-${escapeHtml(category.tone)}${isPrimary ? " blocker-reason-primary" : ""}" type="button" data-blocker-key="${escapeHtml(category.key)}">
      <div>
        <strong>${escapeHtml(category.title)}</strong>
        <span>${category.count ? `涉及 ${category.count} 项${examples}` : "当前通过"}</span>
      </div>
      <p>${escapeHtml(category.nextStep)}</p>
    </button>`;
  }

  function renderBlockerSummary(input) {
    const summary = buildBlockerSummary(input);
    const reasons = summary.categories.length ? summary.categories : [summary.primary];
    return `<section class="blocker-summary blocker-summary-${escapeHtml(summary.primary.tone)}" id="blockerSummaryPanel" aria-label="当前阻断原因">
      <button class="blocker-primary" type="button" data-blocker-key="${escapeHtml(summary.primary.key)}">
        <span>当前首要阻断</span>
        <strong>${escapeHtml(summary.primary.title)}</strong>
        <p>${escapeHtml(summary.primary.summary)}</p>
      </button>
      <div class="blocker-reasons">
        ${reasons.map(reason => renderReason(reason, reason.key === summary.primary.key)).join("")}
      </div>
    </section>`;
  }

  window.DashboardBlockers = {buildBlockerSummary, renderBlockerSummary};
}());
