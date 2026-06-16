import { state, upsertOperation } from "../state.js";
import { badge, button, el, field, input, operationView, statusTone, table } from "../render.js";

export async function renderReconcile(root, context) {
  const mappings = await context.call("list_source_mappings", { limit: 200 });
  const jobId = input("reconcile-job-id", { placeholder: "job_id、upload_session_id、remote_dir 三选一" });
  const uploadSessionId = input("reconcile-upload-id", { placeholder: "upload_session_id" });
  const remoteDir = input("reconcile-remote-dir", { placeholder: "/apps/..." });
  const authPassword = input("reconcile-auth-password", { type: "password", placeholder: "百度授权密码" });
  const repairConfirm = input("repair-confirm", { placeholder: mappings.confirm_text || "APPLY_REMOTE_REPAIR" });
  root.appendChild(
    el("div", { class: "grid two" }, [
      el("section", { class: "panel" }, [
        el("h2", { text: "远端校对" }),
        field("任务 ID", jobId),
        field("上传会话", uploadSessionId),
        field("远端目录", remoteDir),
        field("授权密码", authPassword),
        el("div", { class: "toolbar" }, [
          button("运行校对", {
            variant: "primary",
            onClick: async () => {
              const selected = (state.appState.dashboard.accounts && state.appState.dashboard.accounts.selected_account_id) || "";
              const data = await context.call("run_remote_reconcile", {
                job_id: jobId.value,
                upload_session_id: uploadSessionId.value,
                remote_dir: remoteDir.value,
                authorization_password: authPassword.value,
                account_id: selected,
              });
              upsertOperation(data.operation);
              context.showToast("远端校对已提交");
              context.pollOperation(data.operation.operation_id);
            },
          }),
        ]),
        field("修复确认词", repairConfirm),
        el("div", { class: "toolbar" }, [
          button("修复预演", { onClick: () => repair(context, repairConfirm.value, true) }),
          button("应用本地修复", { variant: "danger", onClick: () => repair(context, repairConfirm.value, false) }),
        ]),
      ]),
      el("section", { class: "panel" }, [
        el("h2", { text: "操作状态" }),
        operationView([...state.operations.values()].find((item) => ["remote_reconcile", "remote_repair"].includes(item.kind) && ["running", "pending"].includes(item.status))),
      ]),
    ]),
  );
  root.appendChild(el("div", { class: "panel", style: "margin-top:16px" }, [el("h2", { text: "来源映射" }), mappingsTable(mappings.rows || [])]));
}

function mappingsTable(rows) {
  return table(
    [
      { label: "任务", key: "job_name" },
      { label: "来源", key: "source_display_name" },
      { label: "文件", key: "display_name" },
      { label: "百度", render: (row) => badge(row.baidu_ready ? "ready" : "missing", row.baidu_ready ? "green" : "yellow") },
      { label: "归档", render: (row) => badge(row.remote_archive_status || "-", statusTone(row.remote_archive_status)) },
      { label: "路径摘要", render: (row) => el("span", { class: "mono", text: row.path_digest }) },
    ],
    rows,
    "暂无来源映射",
  );
}

async function repair(context, confirmation, dryRun) {
  const data = await context.call("apply_remote_repairs", { dry_run: dryRun }, confirmation);
  upsertOperation(data.operation);
  context.showToast(dryRun ? "修复预演已提交" : "修复操作已提交");
  context.pollOperation(data.operation.operation_id);
}
