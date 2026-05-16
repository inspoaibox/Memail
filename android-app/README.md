# Memail Android

原生 Android 客户端，不套 WebView 主页，也不复用桌面四栏布局。

## 功能

- 通过 `/api/mobile/login` 使用管理员账号签发移动端设备 Token。
- 调用服务端 API 读取本地邮箱、外部 IMAP 账号、文件夹和邮件列表。
- 手机端单栏交互：账户/分组、邮件列表、邮件详情、写信、设置。
- 使用 Android 本机通知展示新邮件提醒。
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
