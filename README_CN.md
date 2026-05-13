<div align="center">

<img src="docs/banner.svg" alt="Memail" width="700">

**轻量级自建邮箱服务 —— 一键部署，开箱即用**

SMTP 收件 &bull; REST API &bull; Web 查看器 &bull; IMAP 桥接

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Flask](https://img.shields.io/badge/Flask-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com)
[![Node.js](https://img.shields.io/badge/Node.js-20-339933?logo=node.js&logoColor=white)](https://nodejs.org)
[![MongoDB](https://img.shields.io/badge/MongoDB-7-47A248?logo=mongodb&logoColor=white)](https://www.mongodb.com)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**[English](README.md)**

---

<img src="docs/screenshot.jpg" alt="Memail 截图" width="900">

<sub>*截图中所有邮件均为测试邮件，无实际意义。*</sub>

</div>

## 概述

Memail 是一套完整的自建邮箱解决方案，包含三个核心服务：

| 服务 | 技术栈 | 端口 | 说明 |
|:-----|:-------|:-----|:-----|
| **mail-service** | FastAPI + aiosmtpd | `:25` `:8080` | SMTP 收件 + DuckMail 兼容 REST API |
| **mail-viewer** | Flask + bleach | `:5000` | Web 邮件查看器，支持搜索、回复、发信 |
| **imap-bridge** | Node.js + imapflow | `:3939` | IMAP 桥接，接入 Gmail / Outlook / QQ 等 |

<br>

## 架构

```
    互联网                                 你的服务器
    ──────                                 ──────────
                      ┌──────────────────────────────────────────────┐
                      │                                              │
   外部邮件     ──────┤►  mail-service        ┌───────────────┐     │
   (SMTP :25)         │   (FastAPI+aiosmtpd)  │   MongoDB 7   │     │
                      │   ┌──────────────┐    │   ┌─────────┐ │     │
                      │   │ SMTP 处理器  │────┤►  │ accounts│ │     │
                      │   │ REST API     │◄───┤   │ messages│ │     │
                      │   └──────┬───────┘    │   │ domains │ │     │
                      │          │ :8080      └───┴─────────┘─┘     │
                      │          │                                    │
                      │          ▼                                    │
   浏览器      ───────┤►  mail-viewer          imap-bridge           │
   (HTTP :5000)       │   (Flask)              (Node.js)             │
                      │   ┌──────────────┐    ┌──────────────┐      │
                      │   │ 收件箱视图   │    │ Gmail        │      │
                      │   │ 搜索         │◄───┤ Outlook      │      │
                      │   │ 回复 / 发信  │    │ QQ / 163     │      │
                      │   │ HTML 安全过滤│    │ Yahoo / GMX  │      │
                      │   └──────────────┘    └──────────────┘      │
                      │                         :3939                 │
                      └──────────────────────────────────────────────┘
```

<br>

## 快速开始

### 1. 克隆并配置

```bash
git clone https://github.com/inspoaibox/Memail.git
cd Memail
cp .env.example .env
```

先只改这些必填项：

```env
APP_SECRET=你的长随机启动密钥
ACCESS_PASSWORD=查看器登录密码

SMTP_HOSTNAME=mail.yourdomain.com
DOMAINS=yourdomain.com
```

`.env.example` 里的其他项目大多是可选项。`APP_SECRET` 会默认作为 JWT/API/Session/加密密钥使用，除非你在高级配置里分别覆盖。Gmail OAuth2、Resend API Key、邮箱统一密码、后台登录密码启动后在 Web UI 右上角齿轮里配置。

生产环境建议使用哈希密码，而不是只用明文 `ACCESS_PASSWORD`。可在本机生成：

```bash
python - <<'PY'
from werkzeug.security import generate_password_hash
print(generate_password_hash("替换成你的强密码"))
PY
```

然后写入 `.env`：

```env
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=上一步生成的哈希
SESSION_TIMEOUT_MINUTES=60
LOGIN_MAX_ATTEMPTS=5
LOGIN_LOCK_MINUTES=15
```

如果已经配置 `ADMIN_PASSWORD_HASH`，系统会优先使用哈希密码，旧的 `ACCESS_PASSWORD` 只作为首次启动兜底，不再同时作为第二把可登录密码。

### 2. 使用服务器本地源码构建启动

> 如果你的服务器执行 `docker compose` 提示 `unknown command: docker compose`，说明没有安装 Docker Compose v2 插件，请使用本文下面的 `docker-compose` 命令。两种命令作用相同，只是版本不同。

```bash
docker-compose up -d --build
docker-compose ps
```

这种方式适合直接在服务器上用当前源码构建镜像。此时 `.env` 中不要设置 `MAIL_SERVICE_IMAGE`、`MAIL_VIEWER_IMAGE`、`IMAP_MAIL_IMAGE`、`IMAP_SERVER_IMAGE`。

本项目会持久化 MongoDB 数据、后台运行配置、外部邮箱账号和外部 IMAP 邮件缓存。常规更新不要删除 volume，也不要使用 `docker-compose down -v`。

以后更新代码时，必须重新构建镜像；否则页面仍然会使用旧容器镜像里的旧模板：

```bash
git pull
docker-compose build mail-service mail-viewer imap-mail imap-server
docker-compose up -d --no-deps mail-service imap-mail imap-server mail-viewer
docker-compose ps
```

如果你已经执行过 `git pull`，但页面没有变化，通常就是少了重新构建。可以直接执行：

```bash
docker-compose build --no-cache mail-viewer imap-mail imap-server mail-service
docker-compose stop mail-viewer imap-mail imap-server mail-service
docker-compose rm -f mail-viewer imap-mail imap-server mail-service
docker-compose up -d --no-deps mail-service imap-mail imap-server mail-viewer
docker-compose ps
```

不要对整套服务直接使用 `docker-compose up -d --force-recreate`。旧版 `docker-compose 1.29.x` 搭配新版 Docker 时，重建旧容器可能出现 `KeyError: 'ContainerConfig'`，而 MongoDB 本身也不需要因为前端代码更新而重建。

如果已经遇到 `KeyError: 'ContainerConfig'`，先清理 Compose 重建过程中留下的临时旧容器，再启动服务；不要加 `-v`，这样不会删除 MongoDB、外部邮箱账号和后台配置的 volume：

```bash
docker ps -a --format '{{.Names}}' | grep -E '^[0-9a-f]+_mail-' | xargs -r docker rm -f
docker-compose up -d mongodb
docker-compose up -d --no-deps mail-service imap-mail imap-server mail-viewer
docker-compose ps
```

### 3. 使用 GitHub 构建的 Docker 镜像启动

这种方式适合由 GitHub Actions 负责构建 Docker 镜像，服务器只负责拉取镜像并运行。项目包含 Docker 工作流，会在推送到 `main` / `master`、创建 `v*.*.*` 标签、Pull Request 或手动触发时构建并推送镜像到 GitHub Container Registry：

```text
ghcr.io/<owner>/manymail-mail-service
ghcr.io/<owner>/manymail-mail-viewer
ghcr.io/<owner>/manymail-imap-mail
ghcr.io/<owner>/manymail-imap-server
```

注意：GitHub Actions 只负责构建并推送镜像，不会自动登录你的服务器，也不会自动运行 `docker-compose up`。如果需要自动部署，还要另外写 SSH 部署工作流。

推送代码到 GitHub 后，先到仓库的 **Actions** 页面，确认 **Docker** 工作流已经成功。然后在服务器的 `.env` 中设置镜像地址。把 `<owner>` 替换成你的 GitHub 用户名或组织名，标签通常使用 `main` 或 `master`：

```env
MAIL_SERVICE_IMAGE=ghcr.io/<owner>/manymail-mail-service:master
MAIL_VIEWER_IMAGE=ghcr.io/<owner>/manymail-mail-viewer:master
IMAP_MAIL_IMAGE=ghcr.io/<owner>/manymail-imap-mail:master
IMAP_SERVER_IMAGE=ghcr.io/<owner>/manymail-imap-server:master
```

如果 GHCR 包是私有的，服务器需要先登录：

```bash
echo <github-token> | docker login ghcr.io -u <github-username> --password-stdin
```

首次启动：

```bash
docker-compose pull
docker-compose up -d
docker-compose ps
```

只有 `.env` 中设置了上面的 4 个 `*_IMAGE` 变量时，`docker-compose pull` 才会拉取 GitHub 构建好的 GHCR 镜像。如果没有设置这些变量，Compose 会使用本地源码构建的 `manymail-*:local` 镜像，此时 `docker-compose pull` 不会让刚 `git pull` 下来的源码生效，必须回到第 2 节执行本地构建更新命令。

以后 GitHub 有新代码并构建成功后，在服务器执行：

```bash
# 如果 docker-compose.yml 或文档也变了，先更新服务器上的仓库文件
git pull

# 拉取 GitHub 新构建的镜像，并重建有变化的容器
docker-compose pull
docker-compose up -d
docker-compose ps
```

`.env` 和 Docker volume 会保留。不要运行 `docker-compose down -v`，除非你明确想删除 MongoDB 数据、外部邮箱账号和后台运行配置。

外部 IMAP 聚合账号、文件夹列表、邮件列表、已缓存正文以及后台运行配置都会保存在 Docker volume / MongoDB 中。默认情况下，外部 IMAP 邮件列表和已打开过的正文会缓存 24 小时；刷新按钮仍会主动拉取远端最新数据。可以通过 `.env` 中的 `IMAP_CACHE_TTL_SECONDS` 调整缓存过期时间，默认 `86400` 秒。

后台同步器默认启用：服务启动约 20 秒后会检查外部 IMAP 账号，之后每 15 分钟检查一次。未过期账号不会重复拉取。左侧账号后的同步标签只是状态提示，不需要手动点击；进入“全部账号”后点刷新会把所有外部账号加入后台同步队列并立即显示已有缓存，单个外部账号内点刷新会立即同步当前文件夹。可通过 `IMAP_SYNC_CHECK_INTERVAL_SECONDS`、`IMAP_SYNC_STARTUP_DELAY_SECONDS`、`IMAP_SYNC_SCHEDULER_ENABLED=0` 调整或关闭。

草稿、发送失败记录和已发送记录会和本地持久化配置一起保存。写邮件页面是右侧内嵌页面，发件账号来自当前选中的邮箱；点击“保存草稿”后会进入当前账号的“应用草稿”，发送失败会进入“发送失败”，本地账号和外部 SMTP 账号都可以在失败记录中重试。外部账号自身的远端 Drafts/Sent 文件夹仍然会作为普通 IMAP 文件夹显示。

### 安全与多端同步

Web UI 的设置页提供安全设置：

- TOTP / 2FA：生成密钥后用认证器 App 验证并启用。
- 登录设备：查看当前会话、IP、User-Agent，并可踢出指定会话。
- 设备 Token：为未来桌面端 / 手机端生成同步 Token。Token 只显示一次，请妥善保存。
- 审计日志：记录登录、敏感确认、设置修改、设备 Token、发信成功 / 失败等操作。

敏感操作会触发二次确认，包括保存系统设置、删除本地邮箱、删除外部账号、彻底删除邮件、创建/撤销设备 Token、踢出登录设备。二次确认通过后台密码校验；如果已启用 TOTP，还需要同时输入 6 位验证码。

多端同步 API 已预留，桌面端和手机端可以使用设备 Token 调用：

```http
GET  /api/sync/bootstrap   # 初始同步：邮箱配置、草稿、发送失败记录、协议说明
GET  /api/sync/changes     # 增量拉取：?since=<sync_seq>&limit=200
POST /api/sync/push        # 离线变更上推：当前支持 draft.upsert / draft.delete
```

认证方式：

```http
Authorization: Bearer memail_dev_xxx
```

同步协议当前采用 `server-wins` 冲突策略：客户端离线编辑草稿时需要携带最新 `version`，如果服务端版本更新，接口会返回 conflict，客户端应提示用户选择覆盖或另存副本。邮件正文缓存按不可变快照处理，客户端通过 `/api/sync/changes` 获取失效事件后再按需重新拉取。

需要回滚时，把 `.env` 中的镜像 tag 改成旧版本，比如 release tag 或 GitHub Actions 里显示的 `sha-...` tag，然后执行：

```bash
docker-compose pull
docker-compose up -d
```

### 后台运行配置

Memail 现在不要求所有运行配置都写进 `.env`。Docker 启动后，登录 Web UI，点击右上角齿轮，可以配置：

- Resend API Key，用于发信。
- 邮箱统一密码，用于内置收件箱查看器。
- 后台登录密码，用于替换或轮换 Web UI 管理员密码。
- Gmail / Outlook OAuth2 的 Public Base URL、Client ID、Client Secret。Redirect URI 由系统自动生成，只读显示，不能手动填写。

这些配置会保存到 Docker volume，容器重启、镜像更新、GitHub 构建镜像更新后都不会丢。敏感值使用 `CONFIG_ENCRYPTION_KEY` 加密；如果没有设置该值，`docker-compose.yml` 会回退使用 `IMAP_ACCOUNTS_SECRET`，再回退到 `SECRET_KEY`，最后回退到 `APP_SECRET`。

`.env` 仍然用于启动前必须存在的配置，比如登录密码、服务间 API Key、Session/JWT 密钥、加密密钥等。

### Gmail 外部邮箱聚合：应用专用密码或 OAuth2

外部 IMAP 聚合器会持久化保存账号配置，并使用 `IMAP_ACCOUNTS_SECRET` 加密敏感凭据；默认 Compose 部署下未单独设置时会回退使用 `APP_SECRET`。

Gmail 支持两种接入方式：

- **应用专用密码**：个人 Gmail 账号开启两步验证后，可以在 Google 账号里生成 16 位应用专用密码。添加外部邮箱时选择 `Gmail / Google` 或自动识别，密码栏填写这 16 位应用专用密码即可。普通 Google 登录密码不能用于 IMAP/SMTP。
- **OAuth2 授权登录**：适合长期使用、Workspace 或不想保存邮箱密码的场景。OAuth2 使用 `https://mail.google.com/` scope，这属于 Gmail 敏感/受限权限；公开给多用户使用时可能需要 Google 审核。如果只是自用或内部测试，可以按 Google Cloud 的测试用户流程配置。

配置 Gmail OAuth2：

1. 在 Google Cloud Console 创建 OAuth Client，类型选择 Web application。
2. 在 Memail 设置里填写 Public Base URL，例如：

```text
https://mail.yourdomain.com
```

3. 系统会自动生成只读回调地址，把它复制到 Google Cloud Console 的 Authorized redirect URI：

```text
https://mail.yourdomain.com/imap/api/oauth/gmail/callback
```

4. 登录 Memail Web UI，点击右上角齿轮，填写 Gmail OAuth2 配置：

   - Public Base URL：`https://mail.yourdomain.com`
   - Google Client ID
   - Google Client Secret
   - Redirect URI：系统自动生成，只读展示，不能手动修改

如果使用 Google Workspace，IMAP、应用专用密码和第三方客户端可能受管理员策略影响；这种情况下即使密码正确也可能被拒绝，需要管理员开启相应访问或改用 OAuth2。

### Outlook / Hotmail 外部邮箱

Outlook.com 支持手动填写 IMAP/SMTP 服务器配置：

- IMAP：`outlook.office365.com:993`，SSL/TLS
- SMTP：`smtp-mail.outlook.com:587`，STARTTLS

注意：手动填写服务器不等于一定支持“邮箱密码 Basic Auth”。Microsoft 官方文档中的认证方式是 OAuth2/Modern Auth。Memail 的“添加外部”表单会允许你手动尝试邮箱+密码/应用密码；如果服务器返回 `basic authentication is disabled` 或 `535 5.7.139`，说明当前账号不接受这种传统认证，需要使用“Outlook 登录”OAuth2 授权，或在 Microsoft 账号/组织策略中确认应用密码、IMAP 和 SMTP AUTH 是否被允许。

如果配置 Microsoft OAuth2，Azure / Microsoft Entra 应用中的 Redirect URI 必须填写 Memail 设置页面自动生成的只读值：

```text
https://mail.yourdomain.com/imap/api/oauth/outlook/callback
```

### 4. 验证

```bash
# 检查服务状态
docker compose ps

# 查看日志
docker compose logs -f

# 健康检查
curl http://127.0.0.1:8080/health
```

<br>

## DNS 配置

为你的域名添加以下 DNS 记录：

```dns
; MX 记录 — 告诉其他邮件服务器投递到哪里
yourdomain.com.       IN  MX   10  mail.yourdomain.com.

; A 记录 — 指向你的服务器 IP
mail.yourdomain.com.  IN  A        <你的服务器IP>

; SPF 记录 — 声明哪些 IP 可以代表你的域名发信
yourdomain.com.       IN  TXT      "v=spf1 ip4:<你的服务器IP> -all"

; DKIM 记录 — 邮件签名验证（需先生成密钥对）
default._domainkey.yourdomain.com.  IN  TXT  "v=DKIM1; k=rsa; p=<你的公钥>"

; DMARC 记录 — SPF/DKIM 验证失败时的处理策略
_dmarc.yourdomain.com.  IN  TXT  "v=DMARC1; p=quarantine; rua=mailto:dmarc@yourdomain.com"
```

> **STARTTLS**：Memail 支持 STARTTLS 加密传输。在 `docker-compose.yml` 中挂载 TLS 证书，并在 `.env` 中设置 `SMTP_TLS_CERT` / `SMTP_TLS_KEY`。详见 `.env.example`。

<br>

## API 参考

> 基础地址：`http://127.0.0.1:8080`
>
> 认证方式：`Authorization: Bearer <token>`（`/health`、`/token` 除外）
>
> 生产环境默认 `REQUIRE_API_KEY_FOR_ACCOUNTS=1`，创建账户需要传入 `Authorization: Bearer <API_KEY>` 或 `X-API-Key`。

### 账户管理

```http
POST /accounts              # 创建邮箱账户
POST /token                 # 登录获取 JWT Token
```

### 邮件操作

```http
GET  /messages              # 查询收件箱（分页：?offset=0&limit=30）
GET  /messages/{id}         # 获取邮件详情
GET  /messages/search?q=    # 全文搜索
PATCH /messages/{id}        # 标记已读 / 删除
GET  /sent                  # 已发送邮件列表
```

### 系统接口

```http
GET  /health                # 健康检查（无需认证）
GET  /domains               # 可用域名列表
```

<details>
<summary><strong>示例：创建账户并读取收件箱</strong></summary>

```bash
# 创建账户
curl -X POST http://127.0.0.1:8080/accounts \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_KEY" \
  -d '{"address": "user@yourdomain.com", "password": "secret123"}'

# 获取 Token
TOKEN=$(curl -s -X POST http://127.0.0.1:8080/token \
  -H "Content-Type: application/json" \
  -d '{"address": "user@yourdomain.com", "password": "secret123"}' \
  | jq -r '.token')

# 查看收件箱
curl http://127.0.0.1:8080/messages \
  -H "Authorization: Bearer $TOKEN"
```

</details>

<br>

## 项目结构

```
Memail/
│
├── mail-service/                # SMTP + REST API 服务
│   ├── app.py                   #   FastAPI 主程序
│   ├── Dockerfile               #   Python 3.11-slim
│   └── requirements.txt         #   fastapi, aiosmtpd, pymongo, jwt, bcrypt
│
├── mail-viewer/                 # Web 邮件查看器
│   ├── app.py                   #   Flask 主程序
│   ├── Dockerfile               #   Python 3.11-slim + gunicorn
│   ├── requirements.txt         #   flask, bleach, tinycss2
│   ├── templates/
│   │   ├── index.html           #   收件箱界面
│   │   └── login.html           #   登录页面
│   └── imap-mail-app/           #   IMAP 桥接服务 (Node.js)
│       ├── server.js            #     Express REST API
│       ├── client.js            #     ImapFlow 封装
│       ├── config.js            #     邮件服务商预设配置
│       └── package.json         #     imapflow, mailparser
│
├── docker-compose.yml           # 统一编排 4 个服务
├── .env.example                 # 环境变量模板
├── deploy.sh                    # 部署脚本
├── test_smtp.py                 # SMTP 测试
└── test_external_smtp.py        # 外部 SMTP 测试
```

<br>

## 安全特性

| 层级 | 特性 |
|:-----|:-----|
| **认证** | JWT Token 鉴权（24h 自动过期）+ API Key 保护账户创建和管理端点 |
| **密码** | bcrypt 哈希存储，不可逆 |
| **速率限制** | API 和 SMTP 双层 IP 限流，防止滥用 |
| **SMTP 防护** | IP 黑名单 / 灰名单，收件人数量限制，邮件大小限制 |
| **邮件渲染** | HTML 安全过滤 (bleach + CSSSanitizer)，iframe 沙箱隔离 |
| **网络安全** | 服务端图片代理，防止收件人 IP 泄露 |
| **数据清理** | MongoDB TTL 索引自动清理过期邮件（默认 3 天） |
| **访问控制** | 查看器登录保护，支持管理员密码哈希、登录失败临时锁定、会话过期、HttpOnly Session Cookie |
| **请求防护** | 后台写操作启用 CSRF Token 校验，响应头阻止 iframe 嵌套、MIME 嗅探并限制 Referrer |
| **凭据存储** | 外部 IMAP 聚合账号和后台运行配置使用 `IMAP_ACCOUNTS_SECRET` / `CONFIG_ENCRYPTION_KEY` / `APP_SECRET` fallback 加密后保存 |

<br>

## 技术栈

<table>
<tr>
<td align="center" width="150"><br><strong>Python 3.11</strong><br>FastAPI &bull; Flask<br><br></td>
<td align="center" width="150"><br><strong>Node.js 20</strong><br>Express &bull; ImapFlow<br><br></td>
<td align="center" width="150"><br><strong>MongoDB 7</strong><br>pymongo<br><br></td>
<td align="center" width="150"><br><strong>Docker</strong><br>Compose<br><br></td>
</tr>
</table>

| 组件 | 依赖 |
|:-----|:-----|
| mail-service | `fastapi` `uvicorn` `aiosmtpd` `pymongo` `PyJWT` `bcrypt` |
| mail-viewer | `flask` `gunicorn` `requests` `bleach` `tinycss2` |
| imap-bridge | `express` `imapflow` `mailparser` `dotenv` |

<br>

## 社区

Memail 感谢 [linux.do](https://linux.do/) 社区。这里有很多真实、友好的技术分享，也给了这个项目不少自托管和产品体验方面的启发。

<br>

## 许可证

[MIT](LICENSE)

---

<div align="center">
<sub>为自托管而生，掌控你自己的邮件基础设施。</sub>
</div>
