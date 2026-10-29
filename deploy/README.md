# deploy/

示例配置，**不会自动安装任何系统软件包**。请按 [docs/DEPLOY.md](../docs/DEPLOY.md) 手工部署。

| 文件 | 用途 |
|---|---|
| `systemd/bupt-insight-web.service` | gunicorn 阅读服务（127.0.0.1:8000） |
| `systemd/bupt-insight-scan.service` | 一次性采集任务 |
| `systemd/bupt-insight-scan.timer` | 每天 07:30 / 12:30 / 19:00 |
| `nginx.conf.example` | TLS、静态文件、API 限流 |

部署前请修改：

- 路径（`/opt/bupt-insight`、`/var/lib/bupt-insight`）
- 用户名（默认 `bupt`）
- `server_name` 与证书路径
- 是否使用 `EnvironmentFile` 注入主密钥
