import { addSources, clearSources, state, upsertOperation } from "../state.js";
import { badge, button, clear, el, field, input, operationView, showToast, statusTone, table } from "../render.js";

export async function renderJobs(root, context) {
  const data = await context.call("list_jobs");
  state.jobs = data.jobs || [];
  const statusBox = el("div");
  redrawOperationStatus(statusBox);
  root.appendChild(
    el("div", { class: "grid two" }, [
      el("section", { class: "panel" }, [
        el("h2", { text: "新建备份" }),
        jobForm(context, statusBox),
      ]),
      el("section", { class: "panel" }, [
        el("h2", { text: "执行状态" }),
        statusBox,
      ]),
    ]),
  );
  const localJobs = state.jobs.filter((job) => job.current_device);
  const globalJobs = state.jobs.filter((job) => !job.current_device);
  root.appendChild(el("div", { class: "panel", style: "margin-top:16px" }, [el("h2", { text: "本机任务" }), jobsTable(context, statusBox, localJobs, "暂无本机备份任务")]));
  root.appendChild(el("div", { class: "panel", style: "margin-top:16px" }, [el("h2", { text: "全局任务" }), jobsTable(context, statusBox, globalJobs, "暂无其他设备或全局历史任务")]));
}

function jobForm(context, statusBox) {
  const uploadDefaults = (state.appState && state.appState.settings && state.appState.settings.upload) || {};
  const name = input("job-name", { placeholder: "任务名称" });
  const manual = input("manual-source", { type: "textarea", placeholder: "每行一个完整文件或目录路径" });
  const archivePassword = input("archive-password", { type: "password", placeholder: "压缩密码" });
  const authPassword = input("auth-password", { type: "password", placeholder: "百度授权密码" });
  const runUpload = input("run-upload", { type: "checkbox" });
  const rootDir = input("baidu-root-dir", { value: uploadDefaults.root_dir || "/apps/auto_backup_bdnetdesk/backups" });
  const partSize = input("baidu-part-size", {
    select: true,
    value: String(uploadDefaults.part_size || 4 * 1024 * 1024),
    options: [
      { value: String(4 * 1024 * 1024), label: "4 MiB" },
      { value: String(16 * 1024 * 1024), label: "16 MiB" },
      { value: String(32 * 1024 * 1024), label: "32 MiB" },
    ],
  });
  const maxArchiveSize = input("max-archive-size", {
    select: true,
    value: String(uploadDefaults.max_archive_size_bytes || 4 * 1024 * 1024 * 1024),
    options: [
      { value: String(4 * 1024 * 1024 * 1024), label: "4 GiB" },
      { value: String(10 * 1024 * 1024 * 1024), label: "10 GiB" },
      { value: String(20 * 1024 * 1024 * 1024), label: "20 GiB" },
    ],
  });
  const checkQuota = input("check-quota", { type: "checkbox" });
  const syncOutbox = input("sync-outbox", { type: "checkbox" });
  const reconcileRemote = input("reconcile-remote", { type: "checkbox" });
  const cleanupCache = input("cleanup-cache", { type: "checkbox" });
  const enforceCacheBudget = input("enforce-cache-budget", { type: "checkbox" });
  runUpload.checked = uploadDefaults.run_upload !== false;
  checkQuota.checked = uploadDefaults.check_quota !== false;
  syncOutbox.checked = uploadDefaults.sync_outbox !== false;
  reconcileRemote.checked = uploadDefaults.reconcile_remote !== false;
  cleanupCache.checked = uploadDefaults.cleanup_cache_artifacts === true;
  enforceCacheBudget.checked = uploadDefaults.enforce_cache_budget === true;
  const sourceList = el("div", { class: "source-list" });
  const sourcePicker = el("div", { class: "source-picker", hidden: true });

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

  function addManualSources() {
    const values = manual.value
      .split(/\r?\n|;/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (!values.length) {
      return;
    }
    addSources(
      values.map((value) => ({
        path: value,
        display_name: value.split(/[\\/]/).pop() || value,
        source_type: "",
        path_digest: "后端识别",
      })),
    );
    manual.value = "";
    redrawSources();
  }

  async function chooseNativeSources(kind) {
    const data = await context.call("choose_sources", kind);
    addSources(data.sources || []);
    redrawSources();
  }

  sourcePicker.replaceChildren(
    field("粘贴路径", manual),
    el("div", { class: "toolbar" }, [
      button("添加粘贴路径", { onClick: addManualSources }),
      button("选择本地文件", { onClick: () => chooseNativeSources("file") }),
      button("选择本地文件夹", { onClick: () => chooseNativeSources("directory") }),
      button("完成", { onClick: () => sourcePicker.setAttribute("hidden", "") }),
    ]),
  );

  return el("div", {}, [
    field("任务名称", name),
    el("div", { class: "toolbar" }, [
      button("添加来源", {
        onClick: async () => {
          if (sourcePicker.hasAttribute("hidden")) {
            sourcePicker.removeAttribute("hidden");
          } else {
            sourcePicker.setAttribute("hidden", "");
          }
        },
      }),
      button("清空", {
        onClick: () => {
          clearSources();
          redrawSources();
        },
      }),
    ]),
    sourcePicker,
    sourceList,
    field("压缩密码", archivePassword),
    field("授权密码", authPassword),
    el("label", { class: "field inline" }, [el("span", { class: "field-label", text: "上传百度" }), runUpload]),
    el("details", { class: "options-box", open: true }, [
      el("summary", { text: "百度上传参数" }),
      field("远端根目录", rootDir),
      field("分片大小", partSize),
      field("单归档上限", maxArchiveSize),
      field("检查百度容量", checkQuota, { inline: true }),
      field("同步任务记录", syncOutbox, { inline: true }),
      field("云端校对", reconcileRemote, { inline: true }),
      field("执行缓存预算", enforceCacheBudget, { inline: true }),
      field("完成后清理缓存", cleanupCache, { inline: true }),
    ]),
    button("创建并启动", {
      variant: "primary",
      onClick: async () => {
        const passwords = readRuntimePasswords();
        if (!passwords.archive_password) {
          showToast("请输入压缩密码");
          return;
        }
        const created = await context.call("create_job", name.value.trim(), state.selectedSources);
        const operationData = await context.call(
          "start_job",
          created.job.job_id,
          passwords,
          {
            run_upload: runUpload.checked,
            root_dir: rootDir.value.trim(),
            part_size: Number(partSize.value),
            max_archive_size_bytes: Number(maxArchiveSize.value),
            check_quota: checkQuota.checked,
            sync_outbox: syncOutbox.checked,
            reconcile_remote: reconcileRemote.checked,
            enforce_cache_budget: enforceCacheBudget.checked,
            cleanup_cache_artifacts: cleanupCache.checked,
          },
        );
        upsertOperation(operationData.operation);
        showToast("备份任务已启动");
        redrawOperationStatus(statusBox);
        context.pollOperation(operationData.operation.operation_id, () => redrawOperationStatus(statusBox));
      },
    }),
  ]);
}

function jobsTable(context, statusBox, rows = state.jobs, emptyText = "暂无备份任务") {
  return jobsTableForRows(context, statusBox, rows, emptyText);
}

function jobsTableForRows(context, statusBox, rows, emptyText) {
  return table(
    [
      { label: "任务", render: (job) => el("strong", { text: job.name }) },
      { label: "状态", render: (job) => badge(job.status_label, statusTone(job.status)) },
      { label: "范围", render: (job) => badge(job.scope_label || "-", job.current_device ? "green" : "blue") },
      { label: "设备", render: (job) => el("span", { class: "mono", text: job.owner_device_hint || "-" }) },
      { label: "来源", render: (job) => `${job.source_count} 个` },
      { label: "阶段", key: "last_stage" },
      { label: "同步", key: "sync_status" },
      {
        label: "操作",
        render: (job) =>
          el("div", { class: "toolbar" }, [
            button(job.status === "queued" ? "开始" : "继续", {
              disabled: !job.can_continue,
              title: job.current_device ? "使用上方压缩密码和授权密码继续任务" : "全局任务只读，不能在本机继续",
              onClick: async () => {
                const passwords = readRuntimePasswords();
                if (!passwords.archive_password) {
                  showToast("请输入压缩密码");
                  return;
                }
                const data = await context.call("start_job", job.job_id, passwords, {});
                upsertOperation(data.operation);
                showToast(`已提交继续任务：${job.name}`);
                redrawOperationStatus(statusBox);
                context.pollOperation(data.operation.operation_id, () => redrawOperationStatus(statusBox));
              },
            }),
            button("暂停", {
              disabled: !job.can_pause,
              onClick: async () => {
                await context.call("transition_job", job.job_id, "pause");
                showToast("任务已暂停");
              },
            }),
            button("取消", {
              disabled: !job.can_cancel,
              onClick: async () => {
                await context.call("transition_job", job.job_id, "cancel");
                showToast("任务已取消");
              },
            }),
          ]),
      },
    ],
    rows,
    emptyText,
  );
}

function latestBackupOperation() {
  return latestOperation((item) => item.kind === "backup");
}

function redrawOperationStatus(target) {
  clear(target);
  target.appendChild(operationView(latestBackupOperation()));
}

function latestOperation(predicate) {
  return [...state.operations.values()]
    .filter(predicate)
    .sort((left, right) => String(right.created_at || right.updated_at || "").localeCompare(String(left.created_at || left.updated_at || "")))[0] || null;
}

function readRuntimePasswords() {
  const archivePassword = document.querySelector("#archive-password");
  const authPassword = document.querySelector("#auth-password");
  const passwords = {
    archive_password: archivePassword ? archivePassword.value : "",
    authorization_password: authPassword ? authPassword.value : "",
  };
  if (archivePassword) {
    archivePassword.value = "";
  }
  if (authPassword) {
    authPassword.value = "";
  }
  return passwords;
}
