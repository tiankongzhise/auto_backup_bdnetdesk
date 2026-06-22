import { badge, button, el, operationView, statusTone } from "../render.js";
import { state } from "../state.js";

export async function renderDashboard(root, context) {
  await context.refreshAppState();
  const dashboard = state.appState.dashboard;
  const counts = dashboard.status_counts || {};
  root.appendChild(
    el("div", { class: "grid" }, [
      el("div", { class: "kpi-row" }, [
        kpi("任务总数", state.jobs.length),
        kpi("运行中", counts.running || 0),
        kpi("已完成", counts.completed || 0),
        kpi("待处理风险", dashboard.risks.length),
      ]),
      el("div", { class: "grid two" }, [
        el("section", { class: "panel" }, [
          el("div", { class: "item-row" }, [
            el("h2", { text: "下一步动作" }),
            button("新建备份", { variant: "primary", onClick: () => document.querySelector('[data-route="jobs"]').click() }),
          ]),
          nextActions(dashboard),
        ]),
        el("section", { class: "panel" }, [el("h2", { text: "最近操作" }), recentOperations(dashboard.operations)]),
      ]),
      el("div", { class: "grid two" }, [
        el("section", { class: "panel" }, [el("h2", { text: "最近任务" }), recentJobs()]),
        el("section", { class: "panel" }, [el("h2", { text: "风险提醒" }), risks(dashboard.risks)]),
      ]),
    ]),
  );
}

function recentOperations(operations) {
  if (!operations || !operations.length) {
    return operationView(null);
  }
  return el(
    "div",
    { class: "list" },
    operations.slice(0, 4).map((operation) => el("div", { class: "item" }, operationView(operation, { compact: true }))),
  );
}

function kpi(label, value) {
  return el("div", { class: "kpi" }, [el("div", { class: "kpi-value", text: value }), el("div", { class: "kpi-label", text: label })]);
}

function nextActions(dashboard) {
  const selectedAccount = dashboard.accounts && dashboard.accounts.selected_account_id;
  const actions = [];
  if (!selectedAccount) {
    actions.push(["绑定百度账号", "进入百度授权页完成 device-code 授权", "baidu"]);
  }
  if (!state.jobs.length) {
    actions.push(["创建第一个备份任务", "选择文件或目录，设置压缩密码后启动", "jobs"]);
  }
  if (state.jobs.some((job) => job.can_continue)) {
    actions.push(["继续未完成任务", "从备份页选择可继续任务", "jobs"]);
  }
  if (state.jobs.some((job) => job.status === "completed")) {
    actions.push(["做一次恢复演练", "从恢复页选择候选并恢复到新目录", "restore"]);
  }
  if (!actions.length) {
    actions.push(["检查远端一致性", "运行百度远端校对，确认索引没有漂移", "reconcile"]);
  }
  return el(
    "div",
    { class: "list" },
    actions.map(([title, meta, route]) =>
      el("div", { class: "item" }, [
        el("div", { class: "item-row" }, [
          el("div", { class: "item-title", text: title }),
          button("前往", { onClick: () => document.querySelector(`[data-route="${route}"]`).click() }),
        ]),
        el("div", { class: "item-meta", text: meta }),
      ]),
    ),
  );
}

function recentJobs() {
  if (!state.jobs.length) {
    return el("div", { class: "empty", text: "暂无备份任务" });
  }
  return el(
    "div",
    { class: "list" },
    state.jobs.slice(0, 6).map((job) =>
      el("div", { class: "item" }, [
        el("div", { class: "item-row" }, [
          el("div", { class: "item-title", text: job.name }),
          badge(job.status_label, statusTone(job.status)),
        ]),
        el("div", { class: "item-meta", text: `${job.scope_label || "-"} / ${job.owner_device_hint || "-"} / ${job.source_count} 个来源` }),
        el("div", { class: "item-meta", text: `${job.last_stage || "queued"} / ${job.updated_at}` }),
      ]),
    ),
  );
}

function risks(items) {
  if (!items || !items.length) {
    return el("div", { class: "empty", text: "暂无风险提醒" });
  }
  return el(
    "div",
    { class: "list" },
    items.map((item) =>
      el("div", { class: "item" }, [
        el("div", { class: "item-row" }, [el("div", { class: "item-title", text: item.title }), badge(item.level, statusTone(item.level))]),
        el("div", { class: "item-meta", text: item.message }),
      ]),
    ),
  );
}
