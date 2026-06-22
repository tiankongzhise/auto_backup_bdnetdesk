import { state, upsertOperation } from "../state.js";
import { badge, button, clear, el, field, input, operationView, statusTone, table } from "../render.js";

export async function renderReconcile(root, context) {
  const jobsData = await context.call("list_job_choices");
  const jobs = jobsData.jobs || [];
  const jobSelect = jobChoiceInput("reconcile-job-filter", jobs);
  let mappings = await loadMappings(context, jobSelect.value);
  const uploadSessionId = input("reconcile-upload-id", { placeholder: "upload_session_id" });
  const remoteDir = input("reconcile-remote-dir", { placeholder: "/apps/..." });
  const authPassword = input("reconcile-auth-password", { type: "password", placeholder: "百度授权密码" });
  const repairConfirm = input("repair-confirm", { placeholder: mappings.confirm_text || "APPLY_REMOTE_REPAIR" });
  const entityId = input("cloud-summary-entity-id", { placeholder: "backup_jobs:job_id" });
  const summaryBox = el("div", { class: "summary-box" }, [el("div", { class: "empty", text: "输入实体 ID 后回读云端 summary" })]);
  const operationBox = el("div");
  const mappingPanel = el("div", { class: "panel", style: "margin-top:16px" });
  function selectedJob() {
    return jobs.find((job) => job.job_id === jobSelect.value) || null;
  }
  function syncEntityFromJob() {
    const job = selectedJob();
    if (job) {
      entityId.value = job.entity_id || `backup_jobs:${job.job_id}`;
    }
  }
  jobSelect.addEventListener("change", async () => {
    syncEntityFromJob();
    mappings = await loadMappings(context, jobSelect.value);
    redrawMappingPanel(mappingPanel, mappings);
  });
  syncEntityFromJob();
  root.appendChild(
    el("div", { class: "grid two" }, [
      el("section", { class: "panel" }, [
        el("h2", { text: "远端校对" }),
        field("任务", jobSelect),
        field("上传会话", uploadSessionId),
        field("远端目录", remoteDir),
        field("授权密码", authPassword),
        el("div", { class: "toolbar" }, [
          button("运行校对", {
            variant: "primary",
            onClick: async () => {
              const selected = (state.appState.dashboard.accounts && state.appState.dashboard.accounts.selected_account_id) || "";
              const data = await context.call("run_remote_reconcile", {
                job_id: jobSelect.value,
                upload_session_id: uploadSessionId.value,
                remote_dir: remoteDir.value,
                authorization_password: authPassword.value,
                account_id: selected,
              });
              upsertOperation(data.operation);
              context.showToast("远端校对已提交");
              redrawOperationStatus(operationBox);
              context.pollOperation(data.operation.operation_id, () => redrawOperationStatus(operationBox));
            },
          }),
        ]),
        field("修复确认词", repairConfirm),
        el("div", { class: "toolbar" }, [
          button("修复预演", { onClick: () => repair(context, operationBox, repairConfirm.value, true) }),
          button("应用本地修复", { variant: "danger", onClick: () => repair(context, operationBox, repairConfirm.value, false) }),
        ]),
      ]),
      el("section", { class: "panel" }, [
        el("h2", { text: "Cloud Sync 回读" }),
        field("实体 ID", entityId),
        el("div", { class: "toolbar" }, [
          button("查询 summary", {
            onClick: async () => {
              const value = entityId.value.trim();
              if (!value) {
                context.showToast("请输入实体 ID");
                return;
              }
              const data = await context.call("get_cloud_sync_summary", value);
              clear(summaryBox);
              summaryBox.appendChild(cloudSummaryView(data.summary));
              context.showToast("Cloud Sync summary 已回读");
            },
          }),
        ]),
        summaryBox,
      ]),
    ]),
  );
  root.appendChild(
    el("div", { class: "panel", style: "margin-top:16px" }, [
      el("h2", { text: "操作状态" }),
      operationBox,
    ]),
  );
  redrawOperationStatus(operationBox);
  root.appendChild(mappingPanel);
  redrawMappingPanel(mappingPanel, mappings);
}

async function loadMappings(context, jobId) {
  return context.call("list_source_mappings", { job_id: jobId || "", limit: 200 });
}

function jobChoiceInput(name, jobs) {
  return input(name, {
    select: true,
    options: [
      { value: "", label: "全部任务" },
      ...jobs.map((job) => ({
        value: job.job_id,
        label: `${job.name} / ${job.status_label} / ${job.source_count} 个来源`,
      })),
    ],
  });
}

function redrawMappingPanel(panel, mappings) {
  clear(panel);
  const rows = mappings.rows || [];
  panel.appendChild(el("div", { class: "item-row" }, [el("h2", { text: "来源映射" }), badge(`${rows.length} 项`, "blue")]));
  panel.appendChild(mappingsTable(rows));
}

function mappingsTable(rows) {
  return table(
    [
      { label: "任务", key: "job_name" },
      { label: "任务状态", render: (row) => badge(row.job_status || "-", statusTone(row.job_status)) },
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

async function repair(context, operationBox, confirmation, dryRun) {
  const data = await context.call("apply_remote_repairs", { dry_run: dryRun }, confirmation);
  upsertOperation(data.operation);
  context.showToast(dryRun ? "修复预演已提交" : "修复操作已提交");
  redrawOperationStatus(operationBox);
  context.pollOperation(data.operation.operation_id, () => redrawOperationStatus(operationBox));
}

function latestReconcileOperation() {
  return latestOperation((item) => ["remote_reconcile", "remote_repair"].includes(item.kind));
}

function redrawOperationStatus(target) {
  clear(target);
  target.appendChild(operationView(latestReconcileOperation()));
}

function latestOperation(predicate) {
  return [...state.operations.values()]
    .filter(predicate)
    .sort((left, right) => String(right.created_at || right.updated_at || "").localeCompare(String(left.created_at || left.updated_at || "")))[0] || null;
}

function cloudSummaryView(summary) {
  return el("div", { class: "stack" }, [
    el("div", { class: "item-row" }, [
      el("strong", { text: summary.entity_type || "entity" }),
      badge(`v${summary.data_version || 0}`, "blue"),
    ]),
    el("div", { class: "item-meta", text: `实体 ${summary.entity_id || "-"}` }),
    el("div", { class: "item-meta", text: `当前 revision ${summary.revision_id_hint || "-"} / hash ${summary.canonical_record_sha256_hint || "-"}` }),
    el("div", { class: "item-meta", text: `更新设备 ${summary.updated_by_device_hint || "-"}` }),
    table(
      [
        { label: "版本", key: "data_version" },
        { label: "状态", render: (row) => badge(row.apply_status || "-", statusTone(row.apply_status)) },
        { label: "Revision", key: "revision_id_hint" },
        { label: "Hash", key: "canonical_record_sha256_hint" },
        { label: "创建时间", key: "created_at" },
      ],
      summary.recent_revisions || [],
      "暂无 recent revisions",
    ),
  ]);
}
