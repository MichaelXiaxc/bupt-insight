# 在 Ubuntu 服务器上部署

目标形态：**gunicorn（应用）+ systemd（守护与定时）+ nginx（TLS/鉴权）**，数据放在仓库外。
仓库不安装任何系统软件包；以下命令由你按需执行。

## 1. 前置

- Ubuntu 22.04/24.04/26.04，Python 3.11+
- 能访问 `bbs.byr.cn`
- 一个域名（HTTPS）或 Tailscale/WireGuard 内网地址
- 用于扫描的独立系统用户，例如 `bupt`

```bash
sudo adduser --system --group --home /opt/bupt-insight bupt
sudo mkdir -p /opt/bupt-insight /var/lib/bupt-insight
sudo chown -R bupt:bupt /opt/bupt-insight /var/lib/bupt-insight
```

代码放到 `/opt/bupt-insight/app`（`git clone` 或 `rsync` 均可）。

## 2. Python 环境

```bash
sudo apt install -y python3-venv python3-pip   # 发行版包，自行决定是否使用
sudo -u bupt python3 -m venv /opt/bupt-insight/venv
sudo -u bupt /opt/bupt-insight/venv/bin/pip install -r /opt/bupt-insight/app/requirements.txt
```

浏览器二选一：

```bash
# A. Playwright 自带 Chromium（约 300MB，装到 /opt/bupt-insight/browsers）
sudo mkdir -p /opt/bupt-insight/browsers
sudo chown bupt:bupt /opt/bupt-insight/browsers
sudo -u bupt env PLAYWRIGHT_BROWSERS_PATH=/opt/bupt-insight/browsers \
  /opt/bupt-insight/venv/bin/playwright install chromium

# B. 系统 Google Chrome（推荐在国内网络使用）
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install ./google-chrome-stable_current_amd64.deb
```

用 A 时 systemd 单元里的 `PLAYWRIGHT_BROWSERS_PATH` 必须与安装路径一致（示例已设为
`/opt/bupt-insight/browsers`）。用 B 时在 `config.yaml` 设置 `net.browser: chrome`，或直接写
`net.executable_path: /opt/google/chrome/chrome`。

若 Playwright 下载 Chromium 失败（CDN 被拦截），见下方“常见问题”。

## 3. 配置

```bash
sudo -u bupt cp /opt/bupt-insight/app/config.example.yaml /opt/bupt-insight/app/config.yaml
sudo -u bupt vi /opt/bupt-insight/app/config.yaml
```

至少修改：

```yaml
owner: {tz: Asia/Shanghai}
site:
  base_url: https://insight.example.com
  allowed_hosts: [insight.example.com]
paths:
  data_dir: /var/lib/bupt-insight/data
  session_dir: /var/lib/bupt-insight/sessions
  inbox_dir: /var/lib/bupt-insight/inbox
  secrets_dir: /var/lib/bupt-insight/secrets
```

把所有状态目录放在仓库外，备份时只需备份 `/var/lib/bupt-insight`。

## 4. 凭据与登录

```bash
cd /opt/bupt-insight/app
sudo -u bupt /opt/bupt-insight/venv/bin/python -X utf8 run.py credentials save
```

服务器没有桌面时，用下列任一方式完成登录：

```bash
# A. Xvfb + 临时 VNC
sudo apt install -y xvfb x11vnc
sudo -u bupt xvfb-run -a /opt/bupt-insight/venv/bin/python -X utf8 run.py login byr
# 另开终端：x11vnc -display :99 -localhost -once

# B. 在本地机器登录后导出 storage_state，再导入
sudo -u bupt /opt/bupt-insight/venv/bin/python -X utf8 run.py login byr --import-state /tmp/state.json
sudo rm /tmp/state.json
```

会话过期时 `scan` 会返回 `login_expired`；重新执行一次登录即可，不影响已采集数据。

## 5. 运行服务

```bash
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bupt-insight-web.service
sudo systemctl enable --now bupt-insight-scan.timer
```

- `bupt-insight-web.service`：gunicorn，单 worker × 8 线程（阅读端足够，且进程内缓存一致）
- `bupt-insight-scan.timer`：每天 07:30 / 12:30 / 19:00（`Persistent=true` 支持补跑）

查看状态与日志：

```bash
systemctl status bupt-insight-web
journalctl -u bupt-insight-web -f
journalctl -u bupt-insight-scan -n 100
```

## 6. nginx 与 TLS

示例见 `deploy/nginx.conf.example`。要点：

- `proxy_set_header Host $host;`（应用按 Host 白名单校验）
- `/static/` 由 nginx 直接提供并长缓存
- `/api/` 加 `limit_req`，`client_max_body_size 16k`
- TLS 用 certbot 或云厂商证书

鉴权（应用本身没有登录）：

- **推荐**：只监听 Tailscale/WireGuard 地址，不暴露公网；
- 或 nginx `auth_basic` + fail2ban；
- 或有 IdP 时用 oauth2-proxy。

## 6.1 域名与 Host 白名单

应用会校验请求的 `Host` 头（防 DNS rebinding），只接受 `site.allowed_hosts` 中的名字。
**域名解析正确、但页面返回 `{"error":"forbidden"}` 时，就是这里没加域名。**

1. 在域名商添加解析（阿里云：云解析 DNS → 记录）：

   | 主机记录 | 类型 | 记录值 | 说明 |
   |---|---|---|---|
   | `@` | A | 服务器公网 IP | 裸域名 |
   | `www` | A 或 CNAME | 同上 / 裸域名 | 可选 |

   解析生效后 `dig +short 你的域名` 应返回该 IP。

2. 修改 `config.yaml`：

   ```yaml
   site:
     base_url: http://insight.example.com
     allowed_hosts:
       - insight.example.com
       - www.insight.example.com
       - 43.143.242.36          # 保留 IP 直连
   ```

3. 让 nginx 的 `server_name` 也包含域名（有 `default_server` 时非必需，但更清晰）：

   ```nginx
   server_name insight.example.com www.insight.example.com 43.143.242.36;
   ```

4. 重启并验证：

   ```bash
   sudo systemctl restart bupt-insight-web
   sudo nginx -t && sudo systemctl reload nginx
   curl -sS -o /dev/null -w '%{http_code}\n' -H 'Host: insight.example.com' http://127.0.0.1/
   ```

   返回 `200` 即配置正确；仍为 `403` 时执行
   `journalctl -u bupt-insight-web -n 20`，日志会打印被拒绝的 Host 与允许列表。

5. 上 HTTPS 时把 `base_url` 改为 `https://`、`cookie_secure: true`，
   并按 `deploy/nginx.conf.example` 增加 443 监听与证书（certbot 需要域名已解析到本机）。

## 7. 备份

应用每次 `scan` 会按 `backup.interval_hours` 做在线快照。异地备份二选一：

```bash
# A. 定时把 /var/lib/bupt-insight/data 同步到对象存储
# B. litestream 持续复制（WAL）
litestream replicate /var/lib/bupt-insight/data/insight.sqlite3 s3://bucket/bupt-insight
```

每月至少做一次恢复演练：

```bash
python -X utf8 run.py backup list
python -X utf8 run.py backup verify 'snapshot-....sqlite3'
python -X utf8 run.py backup restore 'snapshot-....sqlite3'   # 先预览指纹
```

## 8. 常见问题

**Playwright 下载 Chromium 卡住或 400**：官方 CDN 在部分网络不可用。改用系统 Chrome（上文 B），
或设置 `PLAYWRIGHT_DOWNLOAD_HOST` 指向可用镜像。

**`secrets_permissions`**：密钥或凭据文件权限过宽。执行 `chmod 700 <secrets_dir>`、
`chmod 600 <secrets_dir>/*`。

**网页 403**：`site.allowed_hosts` 与浏览器地址不一致。把实际域名加入列表；
反向代理必须传 `Host`，不要改写成 `127.0.0.1`。

**登录失效频繁**：降低扫描频率或 `rows_per_board`；不要在多个 IP 上并发登录同一账号。

**SQLite 放在 NFS/SMB 上**：不支持，WAL 会损坏。数据目录必须在本地磁盘。
