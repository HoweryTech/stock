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

  function money(value) {
    if (value == null || value === "") return "--";
    const amount = Number(value);
    if (!Number.isFinite(amount)) return String(value);
    return `¥${amount.toLocaleString("zh-CN", {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
  }

  function pathLabel(path) {
    return {
      path1_break: "路径1-下破",
      path2_rebound: "路径2-反抽",
      path3_recover: "路径3-站稳",
      price_action_ready: "价格动作触发",
    }[path] || path || "盘中触发";
  }

  function reviewKey(code, activePath, eventGeneratedAt) {
    return `${code || ""}:${activePath || ""}:${eventGeneratedAt || ""}`;
  }

  function indexReviewItems(reviewItems) {
    const result = new Map();
    (reviewItems || []).forEach(item => {
      result.set(item.review_key || reviewKey(item.code, item.active_path, item.event_generated_at), item);
    });
    return result;
  }

  function compactTime(value) {
    const text = String(value || "");
    if (!text) return "--";
    return text.replace("T", " ").replace(/\+\d{2}:\d{2}$/, "");
  }

  function buildEntries(event, snapshot, reviewItem) {
    const before = snapshot.before || {};
    const after = snapshot.after || {};
    const confirmation = snapshot.confirmation || {};
    const entries = [
      {
        title: "触发前",
        time: event.requested_at || event.generated_at,
        badge: pathLabel(snapshot.active_path),
        body: before.label || "刷新前未找到决策卡。",
      },
      {
        title: "二次确认",
        time: confirmation.confirmed_at || confirmation.first_seen_at,
        badge: confirmation.window_seconds ? `${confirmation.window_seconds}s` : "确认",
        body: `确认价 ${money(confirmation.confirmed_price || snapshot.current_price)}；首次出现 ${confirmation.first_seen_at || "--"}。`,
      },
      {
        title: "自动刷新",
        time: event.generated_at,
        badge: "决策链刷新",
        body: `新决策卡 ${compactTime(event.decision_generated_at)}；状态统计 ${JSON.stringify(event.state_counts || {})}。`,
      },
      {
        title: "刷新后",
        time: event.decision_generated_at || event.generated_at,
        badge: after.primary_status_label || after.manual_plan_status_label || after.status || "结果",
        body: after.label || "刷新后暂无可用结论。",
      },
    ];
    if (reviewItem) {
      entries.push({
        title: "人工处理",
        time: reviewItem.review_updated_at,
        badge: reviewItem.review_resolution_label || "待处理",
        body: reviewItem.review_note || (reviewItem.review_resolution === "open" ? "尚未标记处理结果。" : "已记录处理结果。"),
      });
    }
    return entries;
  }

  function renderTimeline(entries) {
    return `<ol class="decision-replay-timeline">${entries.map(entry => `
      <li>
        <div class="replay-dot"></div>
        <div class="replay-card">
          <div class="replay-head">
            <strong>${escapeHtml(entry.title)}</strong>
            <span>${escapeHtml(entry.badge || "")}</span>
          </div>
          <p>${escapeHtml(entry.body || "")}</p>
          <em>${escapeHtml(compactTime(entry.time))}</em>
        </div>
      </li>`).join("")}</ol>`;
  }

  function renderForCode(events, reviewItems, code) {
    const reviewByKey = indexReviewItems(reviewItems);
    const blocks = [];
    (events || []).forEach(event => {
      const snapshots = Array.isArray(event.trigger_action_snapshots) ? event.trigger_action_snapshots : [];
      snapshots
        .filter(snapshot => String(snapshot.code || "") === String(code || ""))
        .forEach(snapshot => {
          const key = reviewKey(snapshot.code, snapshot.active_path, event.generated_at);
          const reviewItem = reviewByKey.get(key);
          const title = `${snapshot.name || snapshot.code || "--"} · ${snapshot.title || pathLabel(snapshot.active_path)}`;
          blocks.push(`
            <article class="decision-replay-block">
              <h4>${escapeHtml(title)}</h4>
              ${renderTimeline(buildEntries(event, snapshot, reviewItem))}
            </article>`);
        });
    });
    if (!blocks.length) return "";
    return blocks.slice(0, 5).join("");
  }

  window.DecisionReplay = {renderForCode};
}());
