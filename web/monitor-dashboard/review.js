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

  function activeQueue(queue) {
    return (queue || []).filter(item => !["handled", "ignored"].includes(item.review_resolution));
  }

  function itemLabel(item) {
    return item?.name && item?.code ? `${item.name} ${item.code}` : item?.name || item?.code || "--";
  }

  function reviewAttrs(item) {
    return `data-review-code="${escapeHtml(item.code || "")}" data-review-path="${escapeHtml(item.active_path || "")}" data-review-event="${escapeHtml(item.event_generated_at || "")}"`;
  }

  function primaryActionCode(primary) {
    return primary?.item?.code || "";
  }

  function primaryActionTarget(primary) {
    return primary?.item?.target || "decision-card";
  }

  function classify({cards = [], triggerReviewQueue = [], report = {}} = {}) {
    const queue = activeQueue(triggerReviewQueue);
    const expired = queue.filter(item => item.expired);
    const action = queue.filter(item => item.status === "action_required" && !item.expired);
    const review = queue.filter(item => item.status === "review_required" && !item.expired);
    const watch = queue.filter(item => item.status === "watch_only" && !item.expired);
    const exitRisk = cards.filter(card => card?.state === "exit_risk_review");
    const riskReduction = cards.filter(card => card?.state === "risk_reduction_review");
    const candidates = cards.filter(card => ["positive_t_watch", "reverse_t_watch", "reverse_buyback_review"].includes(card?.state)
      || card?.post_unlock_review_summary?.status === "manual_candidate");
    const stale = Boolean(report?.stale_due_to_snapshot_date);
    const buckets = [
      {
        key: "must",
        label: "必须处理",
        count: (stale ? 1 : 0) + expired.length + action.length,
        tone: stale || expired.length ? "block" : action.length ? "risk" : "pass",
        summary: stale ? "决策链过期，旧结论停用。" : expired.length ? "存在过期触发，先刷新。" : action.length ? "存在未过期触发，先打开详情。" : "暂无强制待办。",
      },
      {
        key: "review",
        label: "需要复核",
        count: review.length + exitRisk.length + riskReduction.length,
        tone: review.length || exitRisk.length || riskReduction.length ? "warn" : "pass",
        summary: review.length ? "有触发后复核项。" : exitRisk.length ? "有退出风险个股。" : riskReduction.length ? "有仓位复核个股。" : "暂无复核待办。",
      },
      {
        key: "watch",
        label: "只观察",
        count: watch.length,
        tone: watch.length ? "observe" : "pass",
        summary: watch.length ? "仅保留背景，不直接交易。" : "暂无观察队列。",
      },
      {
        key: "candidate",
        label: "机会候选",
        count: candidates.length,
        tone: candidates.length ? "candidate" : "pass",
        summary: candidates.length ? "候选需进详情逐项确认。" : "暂无可复核候选。",
      },
    ];

    let primary = {
      tone: "pass",
      title: "暂无盘中强待办",
      headline: "当前没有需要立刻处理的触发队列；继续按个股唯一主指令观察。",
      action: "先保持监控，不从低优先级信息里找交易。",
      target: "positions",
    };
    if (stale) {
      primary = {
        tone: "block",
        title: "先刷新决策链",
        headline: "实时决策卡早于行情快照，旧价格区间和旧步骤停用。",
        action: "先处理顶部刷新提醒，再回到详情看新的唯一主指令。",
        target: "refresh",
      };
    } else if (expired.length) {
      primary = {
        tone: "block",
        title: "先刷新过期触发",
        headline: `${expired.length} 条触发计划已过期，不能按旧价格直接操作。`,
        action: `先刷新 ${itemLabel(expired[0])}，刷新成功后系统会打开该股详情。`,
        target: "expired",
        item: expired[0],
      };
    } else if (action.length) {
      primary = {
        tone: "risk",
        title: "先处理触发详情",
        headline: `${action.length} 条触发仍在有效期内，先打开详情看执行前检查。`,
        action: `先处理 ${itemLabel(action[0])}，再看其他持仓。`,
        target: "detail",
        item: action[0],
      };
    } else if (review.length) {
      primary = {
        tone: "warn",
        title: "先完成触发复核",
        headline: `${review.length} 条触发需要复核，未通过前不能当成可执行计划。`,
        action: `先打开 ${itemLabel(review[0])} 的详情，看阻断或复核原因。`,
        target: "detail",
        item: review[0],
      };
    } else if (exitRisk.length || riskReduction.length) {
      const item = exitRisk[0] || riskReduction[0];
      primary = {
        tone: "risk",
        title: "风控个股优先",
        headline: `${exitRisk.length + riskReduction.length} 只股票处于退出或仓位风险复核。`,
        action: `先打开 ${itemLabel(item)}，看止损/减仓路径。`,
        target: "detail",
        item,
      };
    } else if (candidates.length) {
      primary = {
        tone: "candidate",
        title: "候选只进详情复核",
        headline: `${candidates.length} 个机会候选可观察，但不能从首页直接下单。`,
        action: `先打开 ${itemLabel(candidates[0])}，确认唯一主指令和执行前检查。`,
        target: "detail",
        item: candidates[0],
      };
    }

    const tasks = [
      ...expired.map(item => ({type: "expired", tone: "block", label: "过期需刷新", item, text: item.expiry_action || "先刷新完整日内决策链。"})),
      ...action.map(item => ({type: "detail", tone: "risk", label: "必须处理", item, text: item.after_label || "打开详情处理。"})),
      ...review.map(item => ({type: "detail", tone: "warn", label: "需要复核", item, text: item.after_label || "打开详情复核。"})),
      ...watch.map(item => ({type: "detail", tone: "observe", label: "只观察", item, text: item.after_label || "只观察。"})),
    ].slice(0, 4);

    return {primary, buckets, tasks};
  }

  function primaryButton(primary) {
    if (primary.target === "expired" && primary.item) {
      return `<button class="primary-action" type="button" data-review-desk-action="refresh_item" ${reviewAttrs(primary.item)}>刷新后看详情</button>`;
    }
    if (primary.target === "detail" && primary.item) {
      return `<button class="primary-action" type="button" data-review-desk-action="open_detail" data-review-code="${escapeHtml(primary.item.code || "")}" data-review-target="${escapeHtml(primary.item.target || "decision-card")}">打开详情</button>`;
    }
    if (primary.target === "refresh") {
      return `<button class="primary-action" type="button" data-review-desk-action="refresh_alert">看刷新提醒</button>`;
    }
    return `<button class="secondary-action" type="button" data-review-desk-action="events">看事件页</button>`;
  }

  function taskButton(task) {
    if (task.type === "expired") {
      return `<button class="secondary-action" type="button" data-review-desk-action="refresh_item" ${reviewAttrs(task.item)}>刷新</button>`;
    }
    return `<button class="secondary-action" type="button" data-review-desk-action="open_detail" data-review-code="${escapeHtml(task.item.code || "")}" data-review-target="${escapeHtml(task.item.target || "decision-card")}">详情</button>`;
  }

  function renderIntradayReviewDesk(input) {
    const desk = classify(input);
    return `<section class="review-desk review-desk-${escapeHtml(desk.primary.tone)}" id="intradayReviewDesk" aria-label="盘中一键复核">
      <div class="review-desk-main">
        <span>盘中一键复核</span>
        <strong>${escapeHtml(desk.primary.title)}</strong>
        <p>${escapeHtml(desk.primary.headline)}</p>
        <em>${escapeHtml(desk.primary.action)}</em>
        <div class="review-desk-actions">
          ${primaryButton(desk.primary)}
          <button class="secondary-action" type="button" data-review-desk-action="events">事件页</button>
        </div>
      </div>
      <div class="review-desk-buckets">
        ${desk.buckets.map(bucket => `<article class="review-bucket review-bucket-${escapeHtml(bucket.tone)}">
          <span>${escapeHtml(bucket.label)}</span>
          <strong>${escapeHtml(bucket.count)}</strong>
          <p>${escapeHtml(bucket.summary)}</p>
        </article>`).join("")}
      </div>
      <div class="review-desk-tasks">
        ${desk.tasks.length ? desk.tasks.map(task => `<article class="review-task review-task-${escapeHtml(task.tone)}">
          <div>
            <span>${escapeHtml(task.label)}</span>
            <strong>${escapeHtml(itemLabel(task.item))}</strong>
            <p>${escapeHtml(task.text)}</p>
          </div>
          ${taskButton(task)}
        </article>`).join("") : `<p class="secondary">当前没有触发队列待办；继续保持监控。</p>`}
      </div>
    </section>`;
  }

  function shouldShowGlobalBar(primary) {
    return ["block", "risk"].includes(primary?.tone) && primary?.target !== "positions";
  }

  function notificationKey(input) {
    const desk = classify(input);
    if (!shouldShowGlobalBar(desk.primary)) return "";
    const item = desk.primary.item || {};
    return [
      desk.primary.tone,
      desk.primary.target,
      item.code || "",
      item.active_path || "",
      item.event_generated_at || "",
      desk.primary.title,
    ].join("|");
  }

  function notificationBody(input) {
    const desk = classify(input);
    if (!shouldShowGlobalBar(desk.primary)) return "";
    return `${desk.primary.headline} ${desk.primary.action}`.trim();
  }

  function renderGlobalReviewBar(input) {
    const desk = classify(input);
    const primary = desk.primary;
    if (!shouldShowGlobalBar(primary)) return "";
    const notifyButton = typeof Notification !== "undefined" && Notification.permission === "default"
      ? `<button class="secondary-action" type="button" data-enable-review-notifications>启用桌面通知</button>`
      : "";
    const primaryData = primary.target === "expired" && primary.item
      ? `data-review-desk-action="refresh_item" ${reviewAttrs(primary.item)}`
      : primary.target === "refresh"
        ? `data-review-desk-action="refresh_alert"`
        : primary.item
          ? `data-review-desk-action="open_detail" data-review-code="${escapeHtml(primaryActionCode(primary))}" data-review-target="${escapeHtml(primaryActionTarget(primary))}"`
          : `data-review-desk-action="events"`;
    const label = primary.target === "expired" ? "刷新后看详情" : primary.target === "refresh" ? "看刷新提醒" : "打开详情";
    return `<section class="global-review-bar global-review-${escapeHtml(primary.tone)}" aria-label="盘中必须处理">
      <div>
        <span>盘中必须处理</span>
        <strong>${escapeHtml(primary.title)}</strong>
        <p>${escapeHtml(primary.action)}</p>
      </div>
      <div class="global-review-actions">
        <button class="primary-action" type="button" ${primaryData}>${escapeHtml(label)}</button>
        ${notifyButton}
      </div>
    </section>`;
  }

  window.IntradayReviewDesk = {
    classify,
    notificationBody,
    notificationKey,
    renderGlobalReviewBar,
    renderIntradayReviewDesk,
  };
}());
