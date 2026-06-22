# pywebview 前端规格

## 1. 范围

本规格承接 `docs/ui_design_pywebview.md`，用于指导 pywebview + 原生静态 HTML/CSS/JS 替换当前 PySide6 UI。实现阶段必须保留既有业务层：SQLite 主库、`BackupPipeline`、百度授权 workflow、远端校对、Cloud Sync、原始数据清理和恢复服务均继续由 Python 后端调用。

本轮不引入 React、Vue、Vite、Node 构建链或前端包管理器。前端文件直接作为 PyInstaller data 进入发布包。

## 2. 静态文件结构

静态资源放入 `client/src/auto_backup_client/webui/`：

```text
webui/
  index.html
  styles.css
  js/
    api.js
    state.js
    app.js
    render.js
    views/
      dashboard.js
      backup.js
      baidu.js
      restore.js
      cleanup.js
      reconcile.js
      settings.js
```

约束：

- `index.html` 只承载壳、导航、主内容容器、toast/confirm/modal 容器和 `<script type="module">` 入口。
- `styles.css` 定义全局布局、表格、按钮、表单、状态 badge、modal、toast 和响应式约束。
- `api.js` 是唯一直接访问 `window.pywebview.api` 的模块；其他模块只能调用 `api.js` 导出的函数。
- `state.js` 保存内存状态，不使用 localStorage/sessionStorage。
- `views/*.js` 只负责渲染和事件绑定，不直接拼接敏感业务规则。
- 所有动态文本写入 DOM 时使用 `textContent` 或安全的节点创建 helper，禁止用未清洗数据赋给 `innerHTML`。

## 3. 启动与 pywebview 约束

Python 入口：

- `auto_backup_client.app.main()` 调用 `run_webview_app()`。
- `webview_app.py` 创建 `AutoBackupWebviewBridge`，用 `webview.create_window(title, index_html_path, js_api=bridge, width=1280, height=820, min_size=(1100, 720))` 启动。
- `webview.start()` 负责 GUI 主循环；静态 `index.html` 使用 pywebview 内置本地 HTTP server 承载相对资源。
- 打包运行时优先从 `sys._MEIPASS/webui/index.html` 定位静态 UI；源码运行从包内 `webui/index.html` 定位。

前端启动：

- 前端必须监听 `window.pywebviewready` 后才调用 bridge。
- `app.js` 初始化导航、全局错误处理、modal、toast，然后调用 `get_app_state()` 和默认工作台渲染。
- 每次页面切换都允许从 bridge 重新获取最新摘要，不依赖过期前端缓存。

## 4. Bridge API

所有 bridge 方法返回 JSON 可序列化 dict/list/str/int/bool/null。错误统一返回：

```json
{
  "ok": false,
  "error": {
    "code": "safe_error_code",
    "message": "脱敏错误说明",
    "action": "用户下一步"
  }
}
```

成功统一返回：

```json
{
  "ok": true,
  "data": {}
}
```

### 4.1 应用状态

- `get_app_state()`
  - 返回设备摘要、当前账号摘要、任务统计、最近任务、风险提醒、当前缓存目录、版本信息和运行中的 operations。

### 4.2 备份任务

- `choose_sources()`
  - 打开可多选的来源选择入口；返回路径摘要列表，后端识别 `file/directory`。
  - 若 pywebview 文件对话框无法同时选择文件和文件夹，第一版可返回多次选择合并结果，但 UI 对用户仍表现为单一“添加来源”命令。
- `choose_directory(purpose)`
  - `purpose` 支持 `cache_root`、`restore_target`、`quarantine_dir`。
- `list_jobs(filter)`
  - 返回最近任务，默认按更新时间倒序。
  - 每条任务必须包含 `job_id_hint/entity_id/scope/scope_label/owner_device_hint/current_device/can_start/can_continue/can_pause/can_cancel/source_count/local_source_count`。
  - `scope=local` 才允许本机继续、暂停、取消；`scope=global` 只读展示。
- `list_job_choices()`
  - 返回恢复、清理、校对页任务下拉使用的脱敏任务 DTO。
  - 页面不得要求普通用户手动输入内部 `job_id` 才能恢复、清理或校对。
- `create_job(name, sources)`
  - `sources` 来自 `choose_sources()` 或拖拽路径；后端重新校验路径存在性和类型。
- `start_job(job_id, passwords, options)`
  - `passwords.archive_password` 和 `passwords.authorization_password` 只用于本次调用。
  - 返回 `operation_id`。
- `transition_job(job_id, action)`
  - `action` 支持 `pause`、`resume`、`cancel`。
  - `resume` 对 `running/paused/failed_retryable` 可触发继续语义；`start_job` 只用于 `queued` 或用户明确继续后需要密码的场景。

### 4.3 百度授权

- `list_baidu_accounts()`
  - 返回账号/设备绑定行，设备 ID 只返回前四后四摘要和本机标记。
- `start_baidu_authorization()`
  - 返回 `session_id`、用户码、授权 URL、二维码 URL/文本、过期时间。
- `poll_baidu_authorization()`
  - 返回等待、已授权、过期、失败等状态。
- `complete_baidu_authorization(authorization_password)`
  - 完成 token 加密入库；返回账号摘要。
- `select_baidu_account(account_id)`
  - 选择当前账号。
- `verify_baidu_token(account_id, authorization_password)`
  - 只返回可用/不可用和脱敏原因。

### 4.4 校对与同步

- `list_source_mappings(filter)`
  - 默认拉取设备云端历史后返回来源映射行。
- `run_remote_reconcile(scope)`
  - `scope.type` 支持 `job`、`upload_session`、`remote_dir`。
  - 需要账号 ID 和授权密码时由前端弹一次性 modal。
  - 返回 `operation_id`。
- `apply_remote_repairs(selection, confirmation)`
  - 需要确认短语 `APPLY_REMOTE_REPAIR`。
- `get_cloud_sync_summary(entity_id)`
  - 回读 Cloud Sync summary，用于证明真实云端同步。

### 4.5 清理

- `list_cleanup_candidates(filter)`
  - 默认拉取设备云端历史后列出候选。
- `apply_cleanup(selection, options)`
  - `options.method` 支持 `recycle_bin`、`quarantine`、`permanent_delete`。
  - 永久删除必须 `advanced_enabled=true` 且确认短语为 `DELETE_ORIGINALS_PERMANENTLY`。
  - 返回 `operation_id` 或同步执行摘要。

### 4.6 恢复

- `list_restore_candidates(filter)`
  - 默认拉取设备云端历史后返回来源级候选。
- `apply_restore(selection, options)`
  - `options.target_mode` 支持 `manual_root`、`original_path`。
  - 默认 `conflict_policy=keep_both`。
  - 需要远端下载时要求账号 ID 和授权密码。
  - 返回 `operation_id`。

### 4.7 长操作

- `get_operation(operation_id)`
  - 返回 `pending/running/completed/failed/canceling`、阶段、进度、脱敏消息、结果摘要、`operation_id_hint/kind_label/context`。
  - `context` 只能包含任务名、任务 ID 摘要、候选数量、来源显示名等脱敏上下文。
  - 前端必须展示失败原因，不能只显示“操作失败”。
- `cancel_operation(operation_id)`
  - 标记取消请求。第一版允许不可中断底层任务完成当前阶段后停止；UI 必须诚实显示“正在请求取消”。

## 5. DTO 脱敏规则

统一规则：

- 不返回 Device Token、access token、refresh token、authorization password、archive password、wrapping key、密文 envelope 正文。
- 不返回完整本地绝对路径；来源行可返回 `display_name`、`source_type`、`path_digest`、`path_hint`。
- 不返回完整远端路径；远端行可返回目录层级摘要、文件名摘要、`fs_id` 摘要和校对状态。
- stdout/stderr 只保留通过既有脱敏工具处理后的摘要。
- 前端错误 toast 只展示 `message` 和 `action`。

核心 DTO：

```json
{
  "job": {
    "backup_job_id": "uuid",
    "job_id_hint": "job_001",
    "entity_id": "backup_job_job_001",
    "job_name": "文档备份",
    "status": "completed",
    "status_label": "已完成",
    "scope": "local",
    "scope_label": "本机任务",
    "owner_device_hint": "dev_01234567...cdef",
    "current_device": true,
    "can_continue": false,
    "source_count": 2,
    "local_source_count": 2,
    "last_stage": "reconcile_remote",
    "last_error": "",
    "sync_status": "synced",
    "updated_at": "2026-06-16T10:00:00Z"
  }
}
```

```json
{
  "source": {
    "source_id": "uuid",
    "source_type": "directory",
    "display_name": "照片",
    "path_digest": "ab12cd34",
    "file_count": 128,
    "total_size_label": "3.2 GiB"
  }
}
```

```json
{
  "operation": {
    "operation_id": "uuid",
    "operation_id_hint": "uuid...hint",
    "kind": "backup",
    "kind_label": "备份任务",
    "status": "running",
    "stage": "upload",
    "message": "正在上传第 2 个 archive",
    "progress": 0.62,
    "context": {
      "job_name": "文档备份",
      "job_id_hint": "job_001",
      "source_count": 2,
      "target_label": "文档备份"
    }
  }
}
```

## 6. 页面规格

### 6.1 工作台

必备区域：

- 系统摘要条：设备、账号、云端、缓存。
- 下一步动作区：根据状态展示一个主按钮和最多两个次按钮。
- 最近任务表：展示本机/全局任务、设备摘要、来源数和状态；只有本机任务支持开始、继续、查看详情、恢复。
- 风险提醒列表：缓存不足、未授权、待重试、校对差异、同步待补偿。
- 最近操作列表：展示操作类型、关联任务或候选范围、阶段、进度、失败原因和 operation 编号。
- 运行中操作条：显示阶段、进度、取消按钮；继续任务提交后必须在本页操作状态区可见。

### 6.2 备份

控件：

- 添加来源按钮、拖拽区域、待添加来源表。
- 任务名输入、缓存目录选择、缓存预算检查开关。
- 归档密码和授权密码在页面内一次性输入，提交开始/继续后立即清空，不使用 `window.prompt`。
- 任务表展示状态、来源数、阶段、同步状态、更新时间。
- 任务表拆分本机任务和全局任务；全局任务只读，不显示可执行动作。

按钮启停：

- 没有来源时不可创建任务。
- `queued` 显示开始。
- `running/paused/failed_retryable` 显示继续。
- `completed/canceled` 不显示开始。

### 6.3 百度授权

控件：

- 账号表：设备摘要、本机标记、账号名、UID 摘要、token 状态、选择状态。
- 新增授权 panel：授权 URL、用户码、二维码、轮询状态、完成授权密码框。
- token 自检：输入授权密码后只显示可用/不可用。

### 6.4 恢复

控件：

- 任务筛选，默认全部最近记录。
- 来源级候选表：状态、来源类型、来源名、任务名、文件数、大小、本地 archive 可用性、远端状态、阻塞原因。
- 目标模式：手动目录或原路径。
- 冲突策略：默认保留两者。
- 远端下载需要账号和授权密码 modal。

### 6.5 清理

控件：

- 任务筛选，默认全部最近记录。
- 候选表：任务名、来源名、清理资格、清理状态、远端确认、源文件复查、同步状态、阻塞原因。
- 清理方式：回收站、隔离目录；永久删除在高级区。
- 确认短语输入。

### 6.6 校对与同步

分区：

- 来源映射：只读表，按任务/关键字筛选。
- 远端校对：任务下拉为普通用户主入口，上传会话和远端目录为排障补充入口；运行按钮、差异表、修复确认区。
- 云端同步：本地 outbox 统计、最近同步实体、Cloud Sync summary 回读。

### 6.7 设置

内容：

- 默认缓存目录、缓存额度、上传分片参数、备份根目录。
- 当前版本、WebView2 运行时提示、SQLite 位置摘要、发布候选验收提示。
- 设置保存必须走 bridge，且不能保存密码。

## 7. 前端状态管理

`state.js` 保存：

- `currentView`
- `appState`
- `jobs`
- `selectedJobId`
- `operations`
- `toastQueue`
- `modal`

状态更新只能通过 action 函数：

- `setAppState(data)`
- `setJobs(data)`
- `setCurrentView(view)`
- `upsertOperation(operation)`
- `showToast(message, level)`
- `openModal(config)`
- `closeModal()`

轮询：

- 有 running operation 时每 1000ms 调用 `get_operation`。
- 无 running operation 时停止轮询。
- operation 成功或失败后刷新当前页面数据和工作台摘要。

## 8. 测试规格

Python 测试：

- `test_webview_bridge.py`
  - bridge 初始化和 `get_app_state()`。
  - `create_job()` 后写入 SQLite，DTO 不泄露完整路径。
  - `start_job()` 返回 operation，密码不进入 operation payload。
  - `list_restore_candidates()` 返回来源级候选。
  - `apply_cleanup()` 校验确认短语。
  - 写操作在并发调用时串行化。
- `test_release_build.py`
  - PyInstaller args 包含 `webui` data 和 `migrations/sqlite` data。
  - 入口仍为 `client/src/auto_backup_client/app.py`。

前端测试：

- 如果不引入 Node，使用 Python 读取静态 JS/HTML 做结构断言：
  - `index.html` 引用 `js/app.js` 和 `styles.css`。
  - `api.js` 是唯一包含 `window.pywebview.api` 的文件。
  - 不存在 `localStorage`、`sessionStorage`。
  - 页面按钮和表格容器 ID 与 spec 一致。

验收命令：

```powershell
$env:UV_LINK_MODE='copy'
$env:UV_CACHE_DIR='<repo>\.cache\uv'
$env:TMP='<repo>\.cache\tmp'
$env:TEMP='<repo>\.cache\tmp'
uv sync
uv run python -m pytest -p no:cacheprovider --basetemp <repo>\.cache\pytest-basetemp
uv run python -m compileall src tests
```

服务端不改业务 API 时仍需用仓库内 Go cache 复跑：

```powershell
$env:GOCACHE='<repo>\.cache\go-build'
$env:GOMODCACHE='<repo>\.cache\go-mod'
go test -p=1 ./...
```

## 9. 实现顺序

1. 新增 pywebview 依赖并移除 PySide6 直接 UI 依赖。
2. 新增 `webview_app.py` 和静态资源定位逻辑。
3. 新增 `webview_bridge.py`，先实现 DTO 查询和 operation registry。
4. 新增静态 UI 壳、工作台和备份页。
5. 迁移百度授权、恢复、清理、校对与同步页面。
6. 替换 `app.py` 入口，删除旧 PySide6 UI 模块和测试。
7. 更新 release build，把 `webui` 作为 PyInstaller data。
8. 更新用户手册、发布验收矩阵和客户端 README。
