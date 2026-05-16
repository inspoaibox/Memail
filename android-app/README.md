# Memail Android

原生 Android 客户端，不套 WebView 主页，也不复用桌面四栏布局。

## 功能

- 通过 `/api/mobile/login` 使用管理员账号签发移动端设备 Token。
- 调用服务端 API 读取本地邮箱、外部 IMAP 账号、文件夹和邮件列表。
- 首次联网全量拉取后会写入手机本地 SQLite 缓存；后续打开先展示本地数据，再后台拉取最新邮件增量更新。
- 手机端单栏交互：账户/分组、邮件列表、邮件详情、写信、设置。
- 邮件列表支持搜索和分页加载更多。
- 邮件详情支持已读/未读、星标、删除、回复、转发、翻译为中文。
- 写信支持保存草稿；草稿箱和发送失败记录走服务端持久化接口。
- 发送失败会写入 outbox，支持在手机端重试发送和删除失败记录。
- 使用 Android 本机通知展示新邮件提醒，支持按账号分组选择通知范围。
- 前台运行时通过 `/api/mobile/events` 保持服务端事件连接；服务端检测到邮件状态变化后推送事件，手机端再拉取增量并写入本地缓存。
- 使用 `logo.png` 作为应用内品牌图，启动图标使用同色系原生矢量图标。

## 构建

本机环境：

- Android SDK: `D:\Android\Sdk`
- Gradle: `D:\Android\gradle\wrapper\dists\gradle-9.3.1-bin\23ovyewtku6u96viwx3xl3oks\gradle-9.3.1\bin\gradle.bat`

命令：

```powershell
$env:ANDROID_HOME='D:\Android\Sdk'
$env:ANDROID_SDK_ROOT='D:\Android\Sdk'
$env:GRADLE_USER_HOME='D:\Android\gradle'
& 'D:\Android\gradle\wrapper\dists\gradle-9.3.1-bin\23ovyewtku6u96viwx3xl3oks\gradle-9.3.1\bin\gradle.bat' assembleDebug
```

输出：

```text
android-app\app\build\outputs\apk\debug\app-debug.apk
```

## 服务端要求

服务端需包含 `/api/mobile/login` 接口。登录成功后 Android 客户端保存设备 Token，后续通过 `Authorization: Bearer <token>` 调用 API。
