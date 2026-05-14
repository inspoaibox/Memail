# Memail Desktop

Memail Desktop 是原生 Windows WPF 客户端，使用服务端已有接口登录、读取账号、读取文件夹、分页查看邮件、发送/回复/转发邮件，并复用服务端 AI 翻译、收藏、置顶和已读未读状态。

> 邮件正文使用 WebView2 渲染 HTML 邮件，这是邮件客户端的正文渲染组件，不是 Electron 或网页套壳。

## 运行要求

- Windows 10/11
- GitHub `Memail.Desktop-win-x64-self-contained` 包不需要单独安装 .NET 8
- 如果使用 `Memail.Desktop-win-x64` 轻量包，需要安装 .NET 8 Desktop Runtime
- Microsoft Edge WebView2 Runtime
- 已部署并可访问的 Memail 服务端，例如 `https://mail.aboen.co.uk`

## 本地构建

在仓库根目录执行：

```powershell
dotnet build .\Memail.Desktop\Memail.Desktop.csproj
```

## 发布 Windows x64 包

```powershell
dotnet publish .\Memail.Desktop\Memail.Desktop.csproj -c Release -r win-x64 --self-contained false -o .\Memail.Desktop\publish\win-x64
```

自包含发布包：

```powershell
dotnet publish .\Memail.Desktop\Memail.Desktop.csproj -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -o .\Memail.Desktop\publish\win-x64-self-contained
```

生成的入口文件：

```text
Memail.Desktop\publish\win-x64\Memail.Desktop.exe
```

## 已实现功能

- 服务端地址、管理员账号、密码登录
- 支持已启用 2FA/TOTP 的后台登录
- 支持设备 Token 登录，适合桌面端长期授权
- 多邮箱账号加载，本地邮箱和外部 IMAP 邮箱共用账号列表
- 账号按分组显示
- 文件夹列表：收件箱、所有邮件、未读邮件、已发送、草稿箱、发送失败、回收站，以及外部 IMAP 文件夹
- 邮件分页：上一页、下一页、每页 20/50/100，页大小会记住
- 邮件详情：HTML 正文、纯文本回退、附件列表
- 附件下载
- 写邮件、回复、回复全部、转发，支持抄送、密送和附件
- 保存草稿、编辑草稿、发送草稿后自动删除草稿
- 草稿和发送失败记录会保留抄送、密送和附件，发送失败记录可重试
- AI 翻译为中文
- 收藏、置顶、已读/未读状态操作
- 当前页批量标记已读/未读
- 删除邮件；本地回收站支持恢复和彻底删除，敏感操作会弹出二次确认
- 草稿和发送失败记录可查看，发送失败记录可重试
- 桌面端设置中心：本地邮箱新增/编辑/删除、外部 IMAP/SMTP 账号新增/编辑/删除
- 外部账号预设：Gmail、Outlook、QQ、163、MXRoute、自定义 IMAP/SMTP
- AI 渠道管理：OpenAI、Gemini、OpenAI-compatible 中转站，支持拉取模型并保存默认模型
- 设备 Token 管理：查看、创建、撤销；新 Token 只显示一次并自动复制到剪贴板
- 邮件 HTML 正文禁用脚本执行，避免邮件内容运行脚本
- 自动刷新：默认每 60 秒提交一次外部邮箱后台同步任务，并刷新当前视图
- 本地邮件页缓存：网络短暂失败时会显示最近一次成功加载的当前页缓存，避免界面空白
- 请求防抖：切换账号、文件夹或邮件时会取消旧请求，防止慢请求覆盖新界面

## 当前边界

- 搜索接口沿用服务端现有搜索能力，外部搜索分页依赖 IMAP 缓存或服务端实时搜索能力。
- Gmail / Outlook OAuth 客户端 ID、Client Secret、公开回调地址等系统级 OAuth 配置仍在 Web 管理后台维护；桌面端负责账号日常新增、编辑和邮件处理。
- 设备 Token 分为客户端完整访问和同步访问。桌面端登录页支持使用 `client:full` Token 直接访问服务端 API；同步接口仍可使用同步 Token。
