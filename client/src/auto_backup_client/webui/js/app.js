import { call } from "./api.js";
import { clear, qs, showToast } from "./render.js";
import { setAppState, setRoute, state, upsertOperation } from "./state.js";
import { renderDashboard } from "./views/dashboard.js";
import { renderJobs } from "./views/jobs.js";
import { renderBaidu } from "./views/baidu.js";
import { renderRestore } from "./views/restore.js";
import { renderCleanup } from "./views/cleanup.js";
import { renderReconcile } from "./views/reconcile.js";
import { renderSettings } from "./views/settings.js";

const routes = {
  dashboard: { title: "工作台", render: renderDashboard },
  jobs: { title: "备份", render: renderJobs },
  baidu: { title: "百度授权", render: renderBaidu },
  restore: { title: "恢复", render: renderRestore },
  cleanup: { title: "清理", render: renderCleanup },
  reconcile: { title: "校对与同步", render: renderReconcile },
  settings: { title: "设置", render: renderSettings },
};

async function refreshAppState() {
  const data = await call("get_app_state");
  setAppState(data);
  const status = qs("#sidebar-status");
  const accountCount = data.dashboard && data.dashboard.accounts ? data.dashboard.accounts.items.length : 0;
  status.textContent = `Bridge 已连接 / 账号 ${accountCount} / 任务 ${state.jobs.length}`;
}

async function render() {
  const route = routes[state.route] || routes.dashboard;
  qs("#view-title").textContent = route.title;
  qs("#view-eyebrow").textContent = route.title;
  clear(qs("#topbar-actions"));
  const root = qs("#view-root");
  clear(root);
  try {
    await route.render(root, { call, refreshAppState, pollOperation, showToast });
  } catch (error) {
    root.appendChild(document.createTextNode(error.message));
    showToast(error.message);
  }
}

async function navigate(route) {
  setRoute(route);
  for (const item of document.querySelectorAll(".nav-item")) {
    item.classList.toggle("is-active", item.dataset.route === route);
  }
  await render();
}

async function pollOperation(operationId, onUpdate) {
  let keepPolling = true;
  while (keepPolling) {
    const data = await call("get_operation", operationId);
    const operation = data.operation;
    upsertOperation(operation);
    if (onUpdate) {
      onUpdate(operation);
    }
    keepPolling = ["pending", "running", "canceling"].includes(operation.status);
    if (keepPolling) {
      await new Promise((resolve) => window.setTimeout(resolve, 1200));
    }
  }
  return state.operations.get(operationId);
}

async function boot() {
  for (const item of document.querySelectorAll(".nav-item")) {
    item.addEventListener("click", () => navigate(item.dataset.route));
  }
  try {
    await refreshAppState();
    await render();
  } catch (error) {
    qs("#sidebar-status").textContent = "Bridge 连接失败";
    qs("#view-root").textContent = error.message;
    showToast(error.message);
  }
}

boot();
