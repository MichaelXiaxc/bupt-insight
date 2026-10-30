# 变更记录

## 2.0.0 — BYR-only / Linux 部署

### 移除

- 信息门户信息源（`sources/portal.py`、`core/portal_auth.py`、`core/http.py` 及门户测试）
- Windows 专用依赖与入口（`pywin32`、`keyring`、DPAPI 会话、PowerShell Toast、计划任务脚本）
- 未接入生产路径的旧模块：`core/push.py`、`core/digest.py`、`core/store.py`、
  `core/rules.py`、`rules.yaml`、`sources/public_portal.py`
- 门户/Windows 相关的旧验收文档（历史保留在 Git 中）

### 新增

- `core/secrets.py`：AES-256-GCM 加密的凭据与会话存储，主密钥与密文分离
- `wsgi.py`：gunicorn/uwsgi 入口
- `GET /healthz`：无副作用的存活/就绪探针
- Webhook 与 ntfy 推送渠道
- `deploy/`：systemd 单元与 nginx 示例
- `docs/DEPLOY.md`、`docs/SECURITY.md`、`docs/UPSTREAM.md`、`NOTICE`

### 变更

- SQLite 启用 WAL 与 `busy_timeout`；采集库只由扫描进程写入
- 报表与状态只包含 `byr`；`database_path` 不再按账号作用域分目录
- 阅读端时区、Host 校验、Cookie 安全、会话密钥均可配置
- `smart` 摘要按渠道分组，不再依赖 `desktop`
- 通知去掉了“桌面通知即默认渠道”的假设
- 依赖新增 `cryptography`，移除 `pywin32`/`keyring`
- 测试重写为 97 项，覆盖加密、WAL、outbox、Web 安全与部署配置

### 迁移

旧版本数据不兼容：凭据作用域目录与 `*.bin` 会话无法在 Linux 解密。
在新机器上重新执行 `credentials save` 与 `login byr`，并用 `scan --baseline` 建立基线。
