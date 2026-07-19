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
    if (!Number.isFinite(amount)) return "--";
    return `¥${amount.toLocaleString("zh-CN", {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
  }

  function pct(value) {
    return value == null || value === "" ? "--" : `${Number(value).toFixed(2)}%`;
  }

  function renderMetric(label, value, sub = "") {
    return `<dl><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>${sub ? `<em>${escapeHtml(sub)}</em>` : ""}</dl>`;
  }

  function renderPortfolioUsage(usage) {
    if (!usage?.available) {
      return `<div class="capital-usage capital-usage-missing">
        <div><span>盘中资金联动</span><strong>${escapeHtml(usage?.status_label || "资金预算不可用")}</strong></div>
      </div>`;
    }
    const remaining = Number(usage.remaining_add_amount || 0);
    const tone = remaining <= 0 ? "blocked" : remaining < Number(usage.max_intraday_add_amount || 0) * 0.35 ? "caution" : "ok";
    const allocations = usage.allocations || [];
    return `<section class="capital-usage capital-usage-${tone}" id="capitalUsagePanel" aria-label="盘中资金使用与仓位联动">
      <div class="capital-usage-head">
        <span>盘中资金联动</span>
        <strong>${escapeHtml(usage.status_label || "--")}</strong>
        <p>${escapeHtml(usage.scope || "新增买入共享组合预算。")}</p>
      </div>
      <div class="capital-usage-metrics">
        ${renderMetric("日内新增上限", money(usage.max_intraday_add_amount), pct(usage.max_intraday_add_pct_total_assets))}
        ${renderMetric("今日已用", money(usage.used_buy_amount))}
        ${renderMetric("候选预留", money(usage.reserved_candidate_amount), `${usage.candidate_count || 0}个候选`)}
        ${renderMetric("剩余额度", money(usage.remaining_add_amount))}
      </div>
      ${allocations.length ? `<div class="capital-usage-allocations">
        ${allocations.slice(0, 4).map(item => `<span class="capital-allocation capital-allocation-${escapeHtml(item.status || "unknown")}">${escapeHtml(item.name || item.code || "--")} ${money(item.allocated_amount)} / ${money(item.requested_amount)}</span>`).join("")}
      </div>` : ""}
    </section>`;
  }

  function renderPlanLink(plan) {
    const link = plan?.portfolio_capital_link;
    if (!link) return "";
    const tone = link.status === "allocated" ? "pass" : "block";
    const rows = [
      ["组合日内上限", money(link.max_intraday_add_amount), pct(link.max_intraday_add_pct_total_assets)],
      ["今日已用买入", money(link.used_buy_amount), ""],
      ["本计划申请", money(link.requested_amount), ""],
      ["本计划预留", money(link.allocated_amount), ""],
      ["预留后剩余", money(link.remaining_after_allocation), ""],
    ];
    return `<div class="capital-plan-link capital-plan-link-${tone}">
      <strong>${escapeHtml(link.status_label || "--")}</strong>
      <p>${escapeHtml(link.next_step || "")}</p>
      <div class="capital-plan-link-grid">
        ${rows.map(([label, value, sub]) => renderMetric(label, value, sub)).join("")}
      </div>
    </div>`;
  }

  window.DashboardCapital = {renderPortfolioUsage, renderPlanLink};
}());
