import { clearSelection, state, toggleSelection, upsertOperation } from "../state.js";
import { badge, button, el, field, input, operationView, statusTone, table } from "../render.js";

export async function renderCleanup(root, context) {
  const data = await context.call("list_cleanup_candidates", { limit: 200 });
  const candidates = data.candidates || [];
  const confirmText = data.confirm_text;
  const permanentConfirmText = data.permanent_confirm_text;
  const confirm = input("cleanup-confirm", { placeholder: confirmText });
  const permanent = input("cleanup-permanent-confirm", { placeholder: permanentConfirmText });
  const method = input("cleanup-method", {
    select: true,
    options: [
      { value: "recycle_bin", label: "回收站" },
      { value: "quarantine", label: "隔离目录" },
    ],
  });
  const advanced = input("cleanup-advanced", { type: "checkbox" });
  const permanentDelete = input("cleanup-permanent-method", { type: "checkbox" });
  const quarantine = input("cleanup-quarantine", { placeholder: "隔离目录" });
  const advancedBox = el("div", { class: "advanced-cleanup", style: "display:none" }, [
    field("永久删除", permanentDelete),
    field("永久删除确认词", permanent),
  ]);
  advanced.addEventListener("change", () => {
    advancedBox.style.display = advanced.checked ? "" : "none";
    if (!advanced.checked) {
      permanentDelete.checked = false;
      permanent.value = "";
    }
  });
  root.appendChild(
    el("div", { class: "grid two" }, [
      el("section", { class: "panel" }, [
        el("h2", { text: "清理确认" }),
        summary(data.summary),
        field("清理方式", method),
        field("确认词", confirm),
        field("高级清理", advanced),
        advancedBox,
        field("隔离目录", quarantine),
        el("div", { class: "toolbar" }, [
          button("预演", { onClick: () => submit(context, true, method, confirm, permanent, quarantine, advanced, permanentDelete) }),
          button("执行清理", { variant: "danger", onClick: () => submit(context, false, method, confirm, permanent, quarantine, advanced, permanentDelete) }),
        ]),
      ]),
      el("section", { class: "panel" }, [
        el("h2", { text: "操作状态" }),
        operationView([...state.operations.values()].find((item) => item.kind === "cleanup" && ["running", "pending"].includes(item.status))),
      ]),
    ]),
  );
  root.appendChild(el("div", { class: "panel", style: "margin-top:16px" }, [el("h2", { text: "候选" }), candidatesTable(candidates)]));
}

function summary(data) {
  return el("div", { class: "kpi-row" }, [
    kpi("候选", data.total_count || 0),
    kpi("可清理", data.eligible_count || 0),
    kpi("阻塞", data.blocked_count || 0),
    kpi("待同步", data.sync_pending_count || 0),
  ]);
}

function kpi(label, value) {
  return el("div", { class: "kpi" }, [el("div", { class: "kpi-value", text: value }), el("div", { class: "kpi-label", text: label })]);
}

function candidatesTable(candidates) {
  clearSelection("selectedCleanup");
  return table(
    [
      {
        label: "",
        render: (row) => {
          const box = input(`cleanup-${row.content_reference_id}`, { type: "checkbox" });
          box.disabled = !row.ready;
          box.addEventListener("change", () => toggleSelection("selectedCleanup", row.content_reference_id, box.checked));
          return box;
        },
      },
      { label: "来源", key: "source_display_name" },
      { label: "状态", render: (row) => badge(row.ready ? "可清理" : "阻塞", row.ready ? "green" : "yellow") },
      { label: "大小", key: "size_label" },
      { label: "归档", key: "archive_status" },
      { label: "路径摘要", render: (row) => el("span", { class: "mono", text: row.path_digest }) },
    ],
    candidates,
    "暂无清理候选",
  );
}

async function submit(context, dryRun, method, confirm, permanent, quarantine, advanced, permanentDelete) {
  const selection = [...state.selectedCleanup];
  const selectedMethod = advanced.checked && permanentDelete.checked ? "permanent_delete" : method.value;
  const data = await context.call("apply_cleanup", selection, {
    dry_run: dryRun,
    method: selectedMethod,
    advanced_enabled: advanced.checked,
    confirm_text: confirm.value,
    permanent_confirm_text: permanent.value,
    quarantine_dir: quarantine.value,
  });
  upsertOperation(data.operation);
  context.showToast(dryRun ? "清理预演已提交" : "清理操作已提交");
  context.pollOperation(data.operation.operation_id);
}
