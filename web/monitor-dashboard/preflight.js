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

  function parseTime(value) {
    const time = value ? new Date(value) : null;
    return time && Number.isFinite(time.getTime()) ? time : null;
  }

  function minutesSince(value, now = new Date()) {
    const time = parseTime(value);
    if (!time) return null;
    return Math.max(0, (now.getTime() - time.getTime()) / 60000);
  }

  function addCheck(checks, check) {
    checks.push({
      tone: "warn",
      title: "--",
      value: "--",
      message: "",
      actionLabel: "",
      actionType: "none",
      actionValue: "",
      ...check,
    });
  }

  function activeQueue(queue) {
    return (queue || []).filter(item => !["handled", "ignored"].includes(item.review_resolution));
  }

  function dataSourceCheck(source) {
    const required = [
      ["portfolio_check_available", "组合检查"],
      ["t_opportunities_available", "做T检查"],
      ["action_backtests_available", "动作回测"],
      ["data_quality_available", "数据质量"],
      ["technical_indicators_available", "技术指标"],
      ["investment_profile_available", "投资配置"],
    ];
    const missing = required.filter(([key]) => source?.[key] !== true).map(([, label]) => label);
    return {missing, total: required.length};
  }

  function buildSessionPreflight({snapshot = {}, report = {}, refreshCheck = null, monitorStatus = {}, triggerReviewQueue = []} = {}) {
    const checks = [];
    const session = refreshCheck?.market_session || {};
    const source = report?.source || {};
    const now = new Date();
    const snapshotAge = minutesSince(snapshot?.generated_at, now);
    const reportAge = minutesSince(report?.generated_at, now);
    const liveRequired = session.live_quote_required === true;
    const activeReviews = activeQueue(triggerReviewQueue);
    const actionReviews = activeReviews.filter(item => item.status === "action_required" && !item.expired);
    const expiredReviews = activeReviews.filter(item => item.expired);
    const cards = report?.cards || [];
    const items = snapshot?.items || [];

    addCheck(checks, {
      key: "monitor",
      tone: monitorStatus?.running ? "pass" : "block",
      title: "监控服务",
      value: monitorStatus?.running ? "运行中" : "未运行",
      message: monitorStatus?.running ? `进程 ${monitorStatus.pid || "--"} 正在更新行情快照。` : "监控未运行，开盘后不能按页面旧数据决策。",
      actionLabel: monitorStatus?.running ? "继续监控" : "先启动监控",
      actionType: monitorStatus?.running ? "none" : "status",
      actionValue: monitorStatus?.running ? "" : "监控未运行：先启动 stock-intraday-monitor 服务，再刷新页面。",
    });

    addCheck(checks, {
      key: "session",
      tone: liveRequired ? "warn" : "pass",
      title: "交易时段",
      value: session.label || "--",
      message: session.message || (liveRequired ? "当前需要实时行情参与判断。" : "当前不要求实时行情，盘中开窗后会切换检查口径。"),
      actionLabel: liveRequired ? "按实时口径检查" : "等待交易窗口",
      actionType: "status",
      actionValue: liveRequired ? "当前进入实时行情口径：红色阻断先处理，黄色提醒先确认再操作。" : "当前不是连续竞价执行窗口：不按旧价格区间主动交易，开盘后等待自动刷新。",
    });

    const snapshotFreshLimit = liveRequired ? 2 : 720;
    const snapshotFresh = snapshotAge != null && snapshotAge <= snapshotFreshLimit;
    addCheck(checks, {
      key: "snapshot",
      tone: snapshotFresh ? "pass" : liveRequired ? "block" : "warn",
      title: "行情快照",
      value: snapshotAge == null ? "无时间" : `${snapshotAge.toFixed(snapshotAge < 10 ? 1 : 0)}分钟前`,
      message: snapshotFresh ? `${items.length} 只持仓已有快照。` : liveRequired ? "实时窗口内快照过旧，先等待监控刷新。" : "非盘中快照可能停留在上一交易段，仅作准备参考。",
      actionLabel: snapshotFresh ? "快照可用" : liveRequired ? "等待下一轮快照" : "仅作参考",
      actionType: "status",
      actionValue: snapshotFresh ? "行情快照可用，继续看阻断聚合和个股结论。" : liveRequired ? "行情快照过旧：先等监控下一轮刷新，不做T、不追买；风控退出仍需打开详情复核。" : "非盘中快照只用于准备，开盘后等实时快照刷新。",
    });

    const staleReport = Boolean(report?.stale_due_to_snapshot_date);
    const reportFreshLimit = liveRequired ? 5 : 1440;
    const reportFresh = reportAge != null && reportAge <= reportFreshLimit && !staleReport;
    addCheck(checks, {
      key: "decision",
      tone: cards.length && reportFresh ? "pass" : liveRequired || staleReport ? "block" : "warn",
      title: "实时决策卡",
      value: cards.length ? `${cards.length}张` : "缺失",
      message: staleReport ? "决策卡早于行情快照，旧结论已停用。" : cards.length ? `生成于 ${String(report?.generated_at || "--").replace("T", " ")}。` : "没有决策卡，无法给出盘中建议。",
      actionLabel: staleReport || !cards.length ? "复制刷新命令" : "决策卡可用",
      actionType: staleReport || !cards.length ? "copy_command" : "none",
      actionValue: refreshCheck?.refresh_command?.shell || "",
    });

    const sourceStatus = dataSourceCheck(source);
    addCheck(checks, {
      key: "source",
      tone: sourceStatus.missing.length ? "block" : "pass",
      title: "决策链数据",
      value: sourceStatus.missing.length ? `缺 ${sourceStatus.missing.length}项` : "齐全",
      message: sourceStatus.missing.length ? `缺少：${sourceStatus.missing.join("、")}。` : `${sourceStatus.total} 项关键输入已生成。`,
      actionLabel: sourceStatus.missing.length ? "复制刷新命令" : "输入齐全",
      actionType: sourceStatus.missing.length ? "copy_command" : "none",
      actionValue: refreshCheck?.refresh_command?.shell || "",
    });

    const minuteAvailable = source.minute_bars_available === true;
    addCheck(checks, {
      key: "minute",
      tone: minuteAvailable ? "pass" : liveRequired ? "warn" : "wait",
      title: "分钟线",
      value: minuteAvailable ? "已接入" : "未接入",
      message: minuteAvailable ? "分时确认可以参与盘中门禁。" : liveRequired ? "实时窗口内缺少分钟线时，分时确认会降级或阻断。" : "非盘中可等待开盘后自动补齐。",
      actionLabel: minuteAvailable ? "分时可用" : liveRequired ? "暂停主动T" : "等待开盘",
      actionType: "status",
      actionValue: minuteAvailable ? "分钟线已接入，可以按分时确认门禁判断。" : liveRequired ? "分钟线缺失：暂停主动正T/反T，只处理已确认风控路径；等待下一轮刷新。" : "非盘中缺分钟线正常，开盘后等待自动接入。",
    });

    const refreshNeeded = Boolean(refreshCheck?.action_required || staleReport);
    addCheck(checks, {
      key: "refresh",
      tone: refreshNeeded ? "block" : refreshCheck?.conclusion === "wait_for_market" ? "wait" : "pass",
      title: "刷新要求",
      value: refreshNeeded ? "需要刷新" : refreshCheck?.conclusion === "wait_for_market" ? "等待开盘" : "无需刷新",
      message: refreshNeeded ? "先刷新完整日内决策链，再看交易步骤。" : refreshCheck?.message || "当前决策链不要求手工刷新。",
      actionLabel: refreshNeeded ? "复制刷新命令" : refreshCheck?.conclusion === "wait_for_market" ? "等待开盘" : "无需处理",
      actionType: refreshNeeded ? "copy_command" : "status",
      actionValue: refreshNeeded ? (refreshCheck?.refresh_command?.shell || "") : refreshCheck?.conclusion === "wait_for_market" ? "等待开盘后系统刷新；当前不主动下单。" : "当前不需要手工刷新。",
    });

    const coverageOk = !items.length || cards.length === items.length || staleReport;
    const coveredCodes = new Set(cards.map(card => card?.code).filter(Boolean));
    const missingCoverage = items
      .filter(item => item?.code && !coveredCodes.has(item.code))
      .map(item => item.name && item.code ? `${item.name} ${item.code}` : item.code)
      .slice(0, 5);
    addCheck(checks, {
      key: "coverage",
      tone: coverageOk ? "pass" : "warn",
      title: "持仓覆盖",
      value: `${cards.length}/${items.length || "--"}`,
      message: coverageOk ? "决策卡覆盖当前持仓列表。" : `持仓数量和决策卡数量不一致，缺少：${missingCoverage.join("、") || "部分股票"}。`,
      actionLabel: coverageOk ? "覆盖完整" : "复制刷新命令",
      actionType: coverageOk ? "none" : "copy_command",
      actionValue: refreshCheck?.refresh_command?.shell || "",
    });

    addCheck(checks, {
      key: "queue",
      tone: expiredReviews.length || actionReviews.length ? "warn" : "pass",
      title: "触发队列",
      value: expiredReviews.length ? `${expiredReviews.length}过期` : actionReviews.length ? `${actionReviews.length}待处理` : "无待办",
      message: expiredReviews.length ? "存在过期触发计划，先刷新后处理。" : actionReviews.length ? "有盘中触发事项，先处理队列。" : "没有未处理的强触发事项。",
      actionLabel: expiredReviews.length ? "复制刷新命令" : actionReviews.length ? "打开事件页" : "无待办",
      actionType: expiredReviews.length ? "copy_command" : actionReviews.length ? "open_events" : "none",
      actionValue: expiredReviews.length ? (refreshCheck?.refresh_command?.shell || "") : "",
    });

    const capital = report?.intraday_capital_usage || {};
    addCheck(checks, {
      key: "capital",
      tone: capital.available ? capital.remaining_add_amount <= 0 ? "warn" : "pass" : "warn",
      title: "资金预算",
      value: capital.available ? (capital.status_label || "--") : "不可用",
      message: capital.available ? (capital.scope || "组合资金约束已生成。") : "缺少盘中资金预算时，不应主动追加仓位。",
      actionLabel: capital.available ? capital.remaining_add_amount <= 0 ? "禁止追加" : "预算可用" : "禁止补仓",
      actionType: "status",
      actionValue: capital.available ? capital.remaining_add_amount <= 0 ? "组合日内新增预算已用尽：禁止正T追加和补仓，只处理风控或已有做T闭环。" : "资金预算可用，但仍需通过个股门禁和执行前检查。" : "资金预算不可用：禁止正T/补仓，只允许风控类动作。",
    });

    const blockCount = checks.filter(check => check.tone === "block").length;
    const warnCount = checks.filter(check => check.tone === "warn").length;
    const waitCount = checks.filter(check => check.tone === "wait").length;
    const tone = blockCount ? "block" : warnCount ? "warn" : waitCount ? "wait" : "pass";
    const title = blockCount
      ? "开盘自检未通过"
      : warnCount
        ? "开盘自检有提醒"
        : waitCount
          ? "等待开盘刷新"
          : "开盘自检通过";
    const nextStep = blockCount
      ? "先处理红色阻断项，不按旧价格区间交易。"
      : warnCount
        ? "开盘后先看提醒项是否自动恢复，再进入个股详情。"
        : waitCount
          ? "当前非连续竞价窗口，开盘后系统会按实时行情重新判断。"
          : "可以按首页阻断聚合和个股唯一结论进入盘中盯盘。";

    return {tone, title, nextStep, checks};
  }

  function renderSessionPreflight(input) {
    const preflight = buildSessionPreflight(input);
    const actionButton = check => {
      if (!check.actionLabel || check.actionType === "none") return "";
      const valueAttr = check.actionValue ? ` data-preflight-value="${escapeHtml(check.actionValue)}"` : "";
      return `<button class="session-check-action" type="button" data-preflight-action="${escapeHtml(check.actionType)}"${valueAttr}>${escapeHtml(check.actionLabel)}</button>`;
    };
    return `<section class="session-preflight session-preflight-${escapeHtml(preflight.tone)}" id="sessionPreflightPanel" aria-label="开盘盘中自检">
      <div class="session-preflight-head">
        <span>开盘/盘中自检</span>
        <strong>${escapeHtml(preflight.title)}</strong>
        <p>${escapeHtml(preflight.nextStep)}</p>
      </div>
      <div class="session-preflight-grid">
        ${preflight.checks.map(check => `<article class="session-check session-check-${escapeHtml(check.tone)}">
          <div><strong>${escapeHtml(check.title)}</strong><span>${escapeHtml(check.value)}</span></div>
          <p>${escapeHtml(check.message)}</p>
          ${actionButton(check)}
        </article>`).join("")}
      </div>
    </section>`;
  }

  window.DashboardPreflight = {buildSessionPreflight, renderSessionPreflight};
}());
