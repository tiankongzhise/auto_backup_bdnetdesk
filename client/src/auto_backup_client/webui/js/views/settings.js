import { el, field, input } from "../render.js";
import { state } from "../state.js";

export async function renderSettings(root, context) {
  await context.refreshAppState();
  const app = state.appState.app;
  root.appendChild(
    el("div", { class: "grid two" }, [
      el("section", { class: "panel" }, [
        el("h2", { text: "运行配置" }),
        field("云端 API", input("cloud-api", { value: app.cloud_api_base_url })),
        field("设备摘要", input("device-hint", { value: app.device_id_hint || "" })),
        field("版本", input("version", { value: app.version || "" })),
      ]),
      el("section", { class: "panel" }, [
        el("h2", { text: "安全边界" }),
        el("div", { class: "list" }, [
          policy("前端只接收脱敏 DTO"),
          policy("密码只作为一次性任务参数传入 bridge"),
          policy("本地完整路径、token、wrapping key 不进入前端缓存"),
          policy("备份、恢复、清理、修复写操作由 Python 串行化"),
        ]),
      ]),
    ]),
  );
}

function policy(text) {
  return el("div", { class: "item" }, [el("div", { class: "item-title", text })]);
}
