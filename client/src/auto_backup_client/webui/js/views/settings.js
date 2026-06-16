import { el, field, input } from "../render.js";
import { state } from "../state.js";

export async function renderSettings(root, context) {
  await context.refreshAppState();
  const app = state.appState.app;
  const upload = (state.appState.settings && state.appState.settings.upload) || {};
  root.appendChild(
    el("div", { class: "grid two" }, [
      el("section", { class: "panel" }, [
        el("h2", { text: "运行配置" }),
        field("云端 API", input("cloud-api", { value: app.cloud_api_base_url })),
        field("设备摘要", input("device-hint", { value: app.device_id_hint || "" })),
        field("凭据来源", input("credential-source", { value: app.device_credential_source || "" })),
        field("Device Token", input("device-token-state", { value: app.device_token_available ? "已加载" : "未加载" })),
        field("凭据错误", input("credential-error", { value: app.device_credential_error || "" })),
        field("版本", input("version", { value: app.version || "" })),
      ]),
      el("section", { class: "panel" }, [
        el("h2", { text: "百度上传默认值" }),
        field("远端根目录", input("default-root-dir", { value: upload.root_dir || "" })),
        field("分片大小", input("default-part-size", { value: String(upload.part_size || "") })),
        field("单归档上限", input("default-max-archive-size", { value: String(upload.max_archive_size_bytes || "") })),
        field("同步任务记录", input("default-sync-outbox", { value: upload.sync_outbox === false ? "关闭" : "开启" })),
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
