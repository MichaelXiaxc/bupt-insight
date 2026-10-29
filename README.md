# BUPT Insight · BYR

一个只保留 [北邮人论坛](https://bbs.byr.cn) 信息源的本地阅读站：采集关注的版面与原帖正文，按内容和版本去重，用可解释的本地规则做策展，再通过网页或可选推送渠道阅读。

本项目可在 **Linux 服务器** 上长期运行，也可在 Windows/macOS 本地开发。与原项目的差异见 [docs/UPSTREAM.md](docs/UPSTREAM.md)。

- 只读网页：推荐、科研招募、最新收录、未读、收藏、全部历史、不感兴趣
- 采集：Playwright 无头浏览器，按版面读取列表与首帖正文，跳过置顶，分页去重
- 存储：SQLite（WAL），按内容哈希维护版本与每渠道 outbox
- 凭据：BYR 账号密码与会话快照均以 AES-256-GCM 加密保存，密钥与密文分离
- 推送：通用 Webhook / ntfy / PushPlus / Server酱（可选，默认关闭）
- 零前端构建链：Flask + 原生 HTML/CSS/JS

## 快速开始

需要 Python 3.11+（服务器上已验证 3.14），以及可用的 Chrome/Chromium。

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

# 任选其一：Playwright 自带 Chromium，或使用系统 Chrome
playwright install chromium
# 或者：sudo apt install ./google-chrome-stable_current_amd64.deb

python -X utf8 run.py init            # 生成 config.yaml
python -X utf8 run.py credentials save # 交互输入 BYR 账号（加密保存）
python -X utf8 run.py login byr        # 浏览器登录一次，保存加密会话
python -X utf8 run.py scan --baseline  # 建立基线，不推送历史
python -X utf8 run.py web              # http://127.0.0.1:8000
```

`login byr` 需要图形界面。服务器上可用 `xvfb-run` + VNC 登录一次，或从已登录的机器导出
Playwright `storage_state` JSON 后执行：

```bash
python -X utf8 run.py login byr --import-state ./state.json
rm ./state.json   # 导入后请删除明文文件
```

## 凭据与加密

- 账号密码保存在 `paths.secrets_dir`（默认 `.local/secrets`）下的 `byr.enc`；
  会话快照在同一目录的 `byr-state.enc`。
- 两者都是 **AES-256-GCM** 密文，密钥来自独立的 `master.key`，或环境变量
  `BUPT_INSIGHT_MASTER_KEY`（32 字节 base64/hex）。
- 密文与密钥分离、按用途绑定 AAD；权限不符（非 0600/0700）会拒绝读取。
- 任何日志、CLI 输出、错误信息都不会包含密码或密钥。
- 细节与威胁模型见 [docs/SECURITY.md](docs/SECURITY.md)。

## 常用命令

```bash
python -X utf8 run.py scan --push         # 采集并发送待处理提醒
python -X utf8 run.py status              # 采集状态、队列、健康
python -X utf8 run.py health              # 仅健康检查（退出码可用于监控）
python -X utf8 run.py history --limit 20
python -X utf8 run.py backup create
python -X utf8 run.py profile path        # 个人画像位置
python -X utf8 run.py recommend --limit 10
python -X utf8 run.py credentials status
```

完整命令以 `run.py --help` 为准。

## 部署

服务器部署（systemd + gunicorn + nginx 反代 + TLS）见 [docs/DEPLOY.md](docs/DEPLOY.md)。
仓库只包含应用与示例配置，**不包含也不安装** nginx、SQLite、Python 等系统软件包。

```bash
gunicorn --bind 127.0.0.1:8000 --workers 1 --threads 8 --worker-class gthread wsgi:app
```

## 数据布局

```
<data_dir>/
  insight.sqlite3        采集与投递队列（WAL）
  reader.sqlite3         已读/收藏/隐藏/反馈
  backups/               在线快照
  reader-backups/        阅读库按日快照（保留 7 份）
  evaluations/           本地评估批次
<secrets_dir>/
  master.key             主密钥（0600，可用环境变量替代）
  byr.enc                加密账号
  byr-state.enc          加密会话
  session.key            Flask 会话签名密钥
```

`config.yaml`、`.local/`、`secrets/`、`*.enc`、`*.key`、数据库与日志都在 `.gitignore` 中。

## 开发与测试

```bash
pip install -r requirements-dev.txt
python -X utf8 -m pytest -q
python -X utf8 -m tools.audit_git --staged
```

测试只使用虚构数据，不访问论坛、不读取真实凭据。网络安全与隐私边界见 [docs/SECURITY.md](docs/SECURITY.md)。

## 许可证

MIT，保留上游版权与历史。见 [LICENSE](LICENSE)、[NOTICE](NOTICE) 与 [docs/UPSTREAM.md](docs/UPSTREAM.md)。
