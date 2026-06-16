import { clearSelection, state, toggleSelection, upsertOperation } from "../state.js";
import { badge, button, el, field, input, operationView, statusTone, table } from "../render.js";

export async function renderRestore(root, context) {
  const data = await context.call("list_restore_candidates", { limit: 200 });
  const candidates = data.candidates || [];
  const targetRoot = input("restore-target", { placeholder: "恢复目标目录" });
  const archivePassword = input("restore-archive-password", { type: "password", placeholder: "压缩密码" });
  const authPassword = input("restore-auth-password", { type: "password", placeholder: "如需下载远端归档，输入授权密码" });
  const conflict = input("restore-conflict", {
    select: true,
    options: [
      { value: "keep_both", label: "保留两者" },
      { value: "skip_existing", label: "跳过已存在" },
    ],
  });
  root.appendChild(
    el("div", { class: "grid two" }, [
      el("section", { class: "panel" }, [
        el("h2", { text: "恢复参数" }),
        summary(data.summary),
        field("目标目录", targetRoot),
        field("压缩密码", archivePassword),
        field("授权密码", authPassword),
        field("冲突策略", conflict),
        el("div", { class: "toolbar" }, [
          button("选择目标目录", {
            onClick: async () => {
              const selected = await context.call("choose_directory", "restore");
              if (selected.directory) {
                targetRoot.value = selected.directory;
              }
            },
          }),
          button("开始恢复", {
            variant: "primary",
            onClick: async () => {
              const accounts = (state.appState.dashboard.accounts && state.appState.dashboard.accounts.selected_account_id) || "";
              const op = await context.call("apply_restore", [...state.selectedRestore], {
                target_mode: "manual_path",
                target_root: targetRoot.value,
                archive_password: archivePassword.value,
                authorization_password: authPassword.value,
                account_id: accounts,
                conflict_strategy: conflict.value,
              });
              upsertOperation(op.operation);
              context.showToast("恢复操作已提交");
              context.pollOperation(op.operation.operation_id);
            },
          }),
        ]),
      ]),
      el("section", { class: "panel" }, [
        el("h2", { text: "操作状态" }),
        operationView([...state.operations.values()].find((item) => item.kind === "restore" && ["running", "pending"].includes(item.status))),
      ]),
    ]),
  );
  root.appendChild(el("div", { class: "panel", style: "margin-top:16px" }, [el("h2", { text: "恢复候选" }), candidatesTable(candidates)]));
}

function summary(data) {
  return el("div", { class: "kpi-row" }, [
    kpi("候选", data.total_count || 0),
    kpi("可恢复", data.restorable_count || 0),
    kpi("本地可用", data.local_ready_count || 0),
    kpi("需下载", data.needs_download_count || 0),
  ]);
}

function kpi(label, value) {
  return el("div", { class: "kpi" }, [el("div", { class: "kpi-value", text: value }), el("div", { class: "kpi-label", text: label })]);
}

function candidatesTable(candidates) {
  clearSelection("selectedRestore");
  return table(
    [
      {
        label: "",
        render: (row) => {
          const box = input(`restore-${row.restore_candidate_id}`, { type: "checkbox" });
          box.disabled = !row.ready;
          box.addEventListener("change", () => toggleSelection("selectedRestore", row.restore_candidate_id, box.checked));
          return box;
        },
      },
      { label: "来源", key: "source_display_name" },
      { label: "状态", render: (row) => badge(row.candidate_status, statusTone(row.candidate_status)) },
      { label: "大小", key: "size_label" },
      { label: "归档", key: "archive_status" },
      { label: "路径摘要", render: (row) => el("span", { class: "mono", text: row.path_digest }) },
    ],
    candidates,
    "暂无恢复候选",
  );
}
