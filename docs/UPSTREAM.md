# 与上游的关系

## 来源

- 原始项目：[yantingweb/bupt-information-collector](https://github.com/yantingweb/bupt-information-collector)（MIT）
- 本项目 fork 自：[LiJunhao09100910/bupt-insight](https://github.com/LiJunhao09100910/bupt-insight)，
  该仓库在原始项目上增加了门户认证、SQLite 版本化存储、Phase 2 分析与 Windows 阅读页。
- 本分支（BYR-only / Linux）在其基础上裁剪与改造，保留 MIT 许可证与 Git 历史。

## 本分支的改动

1. **移除信息门户信息源**：删除门户适配器、CAS/HTTP 认证、门户选择器与相关测试；
   采集只保留 BYR。原因是门户只在校内网可达，云服务器无法完成回调。
2. **凭据改为加密文件**：用 `core/secrets.py`（AES-256-GCM + 独立主密钥）替换
   Windows Credential Manager / DPAPI；会话快照同样加密。支持环境变量注入密钥。
3. **跨平台**：移除 `pywin32`、`keyring`、PowerShell Toast、Windows 计划任务脚本；
   浏览器启动改为 Chrome/Chromium 通用逻辑，Playwright 延迟导入。
4. **可部署的 Web**：新增 `wsgi.py`；Host 白名单、`SECRET_KEY`、Secure Cookie 可配置；
   新增无副作用的 `/healthz`；SQLite 改为 WAL。
5. **通知渠道**：新增通用 Webhook 与 ntfy；`smart` 摘要不再绑定 `desktop` 渠道。
6. **清理**：删除未接入生产路径的旧模块（`core/push.py`、`core/digest.py`、
   `core/store.py`、`core/rules.py`、`rules.yaml`、`sources/public_portal.py`），
   删除 WebValidator 等 Windows 专用脚本，删除门户相关的旧验收文档。
7. **文档**：重写 README，新增部署、安全、变更记录与 NOTICE。

## 许可证与署名

原始代码与衍生代码均按 MIT 分发，见 `LICENSE` 与 `NOTICE`。上游提交历史保留在
`git log` 中；未改写已有提交的作者信息。
