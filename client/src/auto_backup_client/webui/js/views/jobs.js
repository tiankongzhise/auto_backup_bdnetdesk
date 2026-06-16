import { addSources, clearSources, state, upsertOperation } from "../state.js";
import { badge, button, clear, el, field, input, operationView, showToast, statusTone, table } from "../render.js";

export async function renderJobs(root, context) {
  const data = await context.call("list_jobs");
  state.jobs = data.jobs || [];
  root.appendChild(
    el("div", { class: "grid two" }, [
      el("section", { class: "panel" }, [
        el("h2", { text: "新建备份" }),
        jobForm(context),
      ]),
      el("section", { class: "panel" }, [
        el("h2", { text: "执行状态" }),
        operationView([...state.operations.values()].find((item) => item.kind === "backup" && ["running", "pending"].includes(item.status))),
      ]),
    ]),
  );
  root.appendChild(el("div", { class: "panel", style: "margin-top:16px" }, [el("h2", { text: "备份任务" }), jobsTable(context)]));
}

function jobForm(context) {
  const name = input("job-name", { placeholder: "任务名称" });
  const manual = input("manual-source", { placeholder: "可粘贴完整文件或目录路径" });
  const archivePassword = input("archive-password", { type: "password", placeholder: "压缩密码" });
  const authPassword = input("auth-password", { type: "password", placeholder: "百度授权密码" });
  const runUpload = input("run-upload", { type: "checkbox" });
  runUpload.checked = true;
  const sourceList = el("div", { class: "source-list" });

  function redrawSources() {
    clear(sourceList);
    if (!state.selectedSources.length) {
      sourceList.appendChild(el("div", { class: "empty", text: "尚未添加来源" }));
      return;
    }
    for (const source of state.selectedSources) {
      sourceList.appendChild(
        el("div", { class: "source-chip" }, [
          el("span", { text: `${source.display_name || "已选择来源"} / ${source.source_type || "auto"}` }),
          badge(source.path_digest || "待识别", "blue"),
        ]),
      );
    }
  }
  redrawSources();

  return el("div", {}, [
    field("任务名称", name),
    field("手动路径", manual),
    el("div", { class: "toolbar" }, [
      button("添加手动路径", {
        onClick: () => {
          const value = manual.value.trim();
          if (!value) {
            return;
          }
          addSources([{ path: value, display_name: value.split(/[\\/]/).pop() || value, source_type: "", path_digest: "后端识别" }]);
          manual.value = "";
          redrawSources();
        },
      }),
      button("添加来源", {
        onClick: async () => {
          const data = await context.call("choose_sources", "mixed");
          addSources(data.sources || []);
          redrawSources();
        },
      }),
      button("清空", {
        onClick: () => {
          clearSources();
          redrawSources();
        },
      }),
    ]),
    sourceList,
    field("压缩密码", archivePassword),
    field("授权密码", authPassword),
    el("label", { class: "field inline" }, [el("span", { class: "field-label", text: "上传百度" }), runUpload]),
    button("创建并启动", {
      variant: "primary",
      onClick: async () => {
        const created = await context.call("create_job", name.value.trim(), state.selectedSources);
        const operationData = await context.call(
          "start_job",
          created.job.job_id,
          { archive_password: archivePassword.value, authorization_password: authPassword.value },
          { run_upload: runUpload.checked, sync_outbox: runUpload.checked, reconcile_remote: runUpload.checked },
        );
        upsertOperation(operationData.operation);
        showToast("备份任务已启动");
        context.pollOperation(operationData.operation.operation_id);
      },
    }),
  ]);
}

function jobsTable(context) {
  return table(
    [
      { label: "任务", render: (job) => el("strong", { text: job.name }) },
      { label: "状态", render: (job) => badge(job.status_label, statusTone(job.status)) },
      { label: "来源", render: (job) => `${job.source_count} 个` },
      { label: "阶段", key: "last_stage" },
      { label: "同步", key: "sync_status" },
      {
        label: "操作",
        render: (job) =>
          el("div", { class: "toolbar" }, [
            button("继续", {
              disabled: !["paused", "failed_retryable", "queued"].includes(job.status),
              onClick: async () => {
                const archivePassword = window.prompt("输入备份压缩密码") || "";
                const authPassword = window.prompt("输入百度授权密码") || "";
                const data = await context.call("start_job", job.job_id, { archive_password: archivePassword, authorization_password: authPassword }, {});
                upsertOperation(data.operation);
                showToast("已提交继续任务");
              },
            }),
            button("暂停", {
              disabled: job.status !== "running",
              onClick: async () => {
                await context.call("transition_job", job.job_id, "pause");
                showToast("任务已暂停");
              },
            }),
            button("取消", {
              disabled: ["completed", "canceled"].includes(job.status),
              onClick: async () => {
                await context.call("transition_job", job.job_id, "cancel");
                showToast("任务已取消");
              },
            }),
          ]),
      },
    ],
    state.jobs,
    "暂无备份任务",
  );
}
