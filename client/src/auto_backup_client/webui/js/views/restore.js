import { clearSelection, state, toggleSelection, upsertOperation } from "../state.js";
import { badge, button, clear, el, field, input, operationView, statusTone, table } from "../render.js";

export async function renderRestore(root, context) {
  const jobsData = await context.call("list_job_choices");
  const jobs = jobsData.jobs || [];
  const jobSelect = jobChoiceInput("restore-job-filter", jobs);
  const keyword = input("restore-keyword", { placeholder: "按任务、来源或归档摘要筛选" });
  const candidatePanel = el("div", { class: "panel", style: "margin-top:16px" });
  const summaryPanel = el("div");
  const statusBox = el("div");
  let currentData = await loadCandidates(context, jobSelect.value, keyword.value);
  redrawSummary(summaryPanel, currentData.summary);
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
        field("任务", jobSelect),
        field("关键字", keyword),
        summaryPanel,
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
                target_mode: "manual_root",
                target_root: targetRoot.value,
                archive_password: archivePassword.value,
                authorization_password: authPassword.value,
                account_id: accounts,
                conflict_policy: conflict.value,
              });
              upsertOperation(op.operation);
              context.showToast("恢复操作已提交");
              redrawOperationStatus(statusBox);
              context.pollOperation(op.operation.operation_id, () => redrawOperationStatus(statusBox));
            },
          }),
        ]),
      ]),
      el("section", { class: "panel" }, [
        el("h2", { text: "操作状态" }),
        statusBox,
      ]),
    ]),
  );
  redrawOperationStatus(statusBox);
  async function refreshCandidates() {
    currentData = await loadCandidates(context, jobSelect.value, keyword.value);
    redrawSummary(summaryPanel, currentData.summary);
    redrawCandidatePanel(candidatePanel, currentData);
  }
  jobSelect.addEventListener("change", refreshCandidates);
  keyword.addEventListener("change", refreshCandidates);
  root.appendChild(candidatePanel);
  redrawCandidatePanel(candidatePanel, currentData);
}

async function loadCandidates(context, jobId, keyword) {
  return context.call("list_restore_candidates", { job_id: jobId || "", keyword: keyword || "", limit: 200 });
}

function summaryBox(data) {
  return el("div", { class: "kpi-row" }, [
    kpi("候选", data.total_count || 0),
    kpi("可恢复", data.restorable_count || 0),
    kpi("本地可用", data.local_ready_count || 0),
    kpi("需下载", data.needs_download_count || 0),
  ]);
}

function redrawSummary(target, data) {
  clear(target);
  target.appendChild(summaryBox(data || {}));
}

function kpi(label, value) {
  return el("div", { class: "kpi" }, [el("div", { class: "kpi-value", text: value }), el("div", { class: "kpi-label", text: label })]);
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

function redrawCandidatePanel(panel, data) {
  clear(panel);
  panel.appendChild(el("div", { class: "item-row" }, [el("h2", { text: "恢复候选" }), badge(`${(data.candidates || []).length} 项`, "blue")]));
  panel.appendChild(candidatesTable(data.candidates || []));
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
      { label: "任务", key: "job_name" },
      { label: "来源", key: "source_display_name" },
      { label: "类型", key: "source_type" },
      { label: "状态", render: (row) => badge(row.candidate_status, statusTone(row.candidate_status)) },
      { label: "文件数", key: "file_count" },
      { label: "大小", key: "size_label" },
      { label: "本地归档", render: (row) => badge(row.local_archive_available ? "可用" : "不可用", row.local_archive_available ? "green" : "yellow") },
      { label: "远端归档", render: (row) => badge(row.remote_archive_status || "-", statusTone(row.remote_archive_status)) },
      { label: "阻塞原因", render: (row) => row.ready ? "-" : row.blockers.join("；") },
      { label: "路径摘要", render: (row) => el("span", { class: "mono", text: row.path_digest }) },
    ],
    candidates,
    "暂无恢复候选",
  );
}

function latestRestoreOperation() {
  return latestOperation((item) => item.kind === "restore");
}

function redrawOperationStatus(target) {
  clear(target);
  target.appendChild(operationView(latestRestoreOperation()));
}

function latestOperation(predicate) {
  return [...state.operations.values()]
    .filter(predicate)
    .sort((left, right) => String(right.created_at || right.updated_at || "").localeCompare(String(left.created_at || left.updated_at || "")))[0] || null;
}
