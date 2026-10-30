# 安全与隐私

## 威胁模型

本项目的信任边界是：**运行服务的系统用户 + 其可读的密钥文件**。

防护目标：

- 备份、代码仓库、日志、错误信息中不出现明文密码或会话；
- 密文被单独拷走时无法解密；
- 凭据文件被误提交到 Git 或上传到网盘时仍不可读；
- 网页不加载任何外部资源，不把内容发给第三方，除非用户显式启用推送。

明确不防护：

- 能读取 `master.key`（或以该用户身份执行命令）的攻击者；
- 已 root 的主机；
- 浏览器端被入侵、或用户主动把密码贴到聊天工具；
- 论坛侧的账号风控与法律风险。

## 加密设计

| 项 | 实现 |
|---|---|
| 算法 | AES-256-GCM（`cryptography`），96-bit 随机 nonce |
| 密钥 | 32 字节随机；`master.key`（0600）或 `BUPT_INSIGHT_MASTER_KEY` |
| 绑定 | AAD = `bupt-insight/<purpose>`，凭据与会话互换会解密失败 |
| 文件 | `byr.enc`、`byr-state.enc`、`llm.enc`，同目录下 `master.key` |
| 权限 | 目录 0700、文件 0600；POSIX 下权限过宽直接拒绝读取 |
| 写入 | 同目录临时文件 + fsync + 原子替换 |
| 派生 | Flask 会话密钥优先 `INSIGHT_SECRET_KEY`，否则 `<secrets_dir>/session.key` |

密钥与密文默认在**同一目录**，但通过环境变量可以把密钥移出（systemd
`LoadCredential`、Kubernetes Secret、Vault agent 等）。如果两者长期放在一起，
加密的主要收益是“防止密文被单独复制/提交”，而不是“防止主机被入侵”。

## 密钥轮换

```bash
python -X utf8 run.py credentials save   # 重新加密凭据（沿用现有 master.key）
```

更换主密钥：

```bash
python -X utf8 run.py credentials save   # 先确认新密钥可用
# 删除旧 master.key 与 *.enc，重新执行 credentials save / login byr
```

更换后旧密文无法解密，属于预期行为；请先确认新凭据可登录。

## 网页安全

- 只监听 `127.0.0.1`，对外由 nginx 终止 TLS；
- Host 白名单防 DNS rebinding，`Sec-Fetch-Site`/`Origin` 限制跨站请求；
- POST 需要 CSRF token；Cookie `HttpOnly` + `SameSite=Strict`；HTTPS 下 `Secure`；
- CSP 禁止外部脚本/字体/图片，正文按纯文本渲染；
- 采集库以 `mode=ro` 打开，网页不初始化或迁移采集表；
- 应用没有用户体系：**不要直接暴露到公网**，用 Tailscale 或反代层鉴权。

## 数据

`insight.sqlite3` 保存论坛正文（公开内容），`reader.sqlite3` 保存已读/收藏/反馈。
两者都未额外加密，依赖磁盘与目录权限；备份同样敏感。评估快照可能包含较长正文，
只应留在本机。仓库的 `tools/audit_git.py` 会检查历史中的私有路径与常见密钥格式。

## 报告问题

发现安全问题请勿公开提交 issue 附带利用细节；先通过仓库主页给出的联系方式私下联系维护者，
并在修复发布后再公开讨论。
