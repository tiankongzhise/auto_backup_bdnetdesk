# 开发排期与进度

本文件用于记录项目开发排期、当前进度和提交后的完成情况。每次开发开始时和 commit 后都必须更新本文件。

## 当前阶段

项目初始化与文档落地已完成。

## 当前工作项

- 初始化 Git 仓库。
- 创建 `AGENTS.MD` 项目记忆文件。
- 创建产品规格文档 `docs/product_spec_v1.3.md`。
- 创建 `.env.example`、`.gitignore`、`.editorconfig`、`.gitattributes` 和 `README.md`。
- 创建本进度文件。

## 本次验收标准

- 当前目录已初始化为 Git 仓库。
- 不存在 `PROJECT_MEMORY.md`。
- `AGENTS.MD` 包含项目规则、PowerShell UTF-8 约束、提交规则和敏感信息约束。
- `.env.example` 存在且只包含占位配置。
- `.gitignore` 排除 `.env`、数据库、日志、缓存和密钥目录。
- `docs/product_spec_v1.3.md` 包含完整 v1.3 规格。
- 产生一次中文初始化 commit。

## 开发排期

| 阶段 | 工作内容 | 状态 |
| --- | --- | --- |
| 0 | 仓库初始化、项目记忆、产品文档、进度文件 | 已完成 |
| 1 | Python 项目骨架、配置加载、日志脱敏、基础目录结构 | 未开始 |
| 2 | SQLite schema、版本字段、outbox、迁移机制 | 未开始 |
| 3 | PySide6 基础 UI、任务页、设置页 | 未开始 |
| 4 | 扫描、快速指纹、完整 MD5/SHA256、文件夹哈希 | 未开始 |
| 5 | 去重索引、本地/云端内容对象、来源引用 | 未开始 |
| 6 | 7-Zip 加密压缩、manifest、标准/严格验证 | 未开始 |
| 7 | 百度 OAuth、预上传、分片上传、创建文件 | 未开始 |
| 8 | 断点续传、上传恢复、失败重试 | 未开始 |
| 9 | 缓存额度、动态清理等级、缓存 artifact 管理 | 未开始 |
| 10 | 来源与远端映射、数据库/百度校对 UI | 未开始 |
| 11 | 原始数据清理记录、恢复到原路径/手动路径 | 未开始 |
| 12 | 打包、验收测试、使用文档 | 未开始 |

## 完成记录

### 初始化项目仓库与产品文档

- 完成 Git 仓库初始化。
- 新增 `AGENTS.MD`，记录项目记忆、PowerShell UTF-8 约束、提交规则、敏感信息规则和架构约束。
- 新增 `docs/product_spec_v1.3.md`，落地完整产品与技术规格。
- 新增 `.env.example`，提供本地数据库、云端 API、PostgreSQL 和百度开放平台配置占位。
- 新增 `.gitignore`，排除 `.env`、本地数据库、日志、缓存和密钥目录。
- 新增 `README.md`、`.editorconfig` 和 `.gitattributes`。

### 修正 Git 换行策略

- 发现初始化提交时 Git 提示部分文本文件后续可能被 CRLF 替换。
- 已将仓库本地 `core.autocrlf` 设置为 `false`。
- 已在 `.gitattributes` 中使用 `* text=auto eol=lf`，并对常见文本文件类型明确指定 LF。
- 已将该问题和后续约束补充到 `AGENTS.MD`。

下一步计划：搭建 Python 项目骨架、配置加载、日志脱敏和基础目录结构。
