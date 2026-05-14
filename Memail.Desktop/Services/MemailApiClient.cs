using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.RegularExpressions;
using System.Text.Json;
using System.IO;
using Memail.Desktop.Models;

namespace Memail.Desktop.Services;

public sealed class MemailApiClient
{
    private readonly CookieContainer _cookieContainer = new();
    private readonly HttpClient _httpClient;
    private readonly JsonSerializerOptions _json = new()
    {
        PropertyNameCaseInsensitive = true,
    };

    public MemailApiClient()
    {
        var handler = new HttpClientHandler
        {
            CookieContainer = _cookieContainer,
            UseCookies = true,
            AutomaticDecompression = DecompressionMethods.GZip | DecompressionMethods.Deflate,
        };
        _httpClient = new HttpClient(handler)
        {
            Timeout = TimeSpan.FromSeconds(45),
        };
    }

    public string BaseUrl { get; private set; } = string.Empty;
    public string CsrfToken { get; private set; } = string.Empty;
    public string DeviceToken { get; private set; } = string.Empty;

    public void Configure(string baseUrl)
    {
        BaseUrl = baseUrl.Trim().TrimEnd('/');
        _httpClient.BaseAddress = new Uri($"{BaseUrl}/");
    }

    public async Task<AuthResult> LoginAsync(string username, string password, string totpCode, CancellationToken cancellationToken)
    {
        DeviceToken = string.Empty;
        _httpClient.DefaultRequestHeaders.Authorization = null;
        var loginPage = await _httpClient.GetAsync("login", cancellationToken);
        if (!loginPage.IsSuccessStatusCode)
        {
            return new AuthResult { Success = false, Message = "无法打开登录页面" };
        }

        var payload = new FormUrlEncodedContent(new Dictionary<string, string>
        {
            ["username"] = username,
            ["password"] = password,
            ["totp_code"] = totpCode,
        });
        var resp = await _httpClient.PostAsync("login", payload, cancellationToken);
        var loginBody = await resp.Content.ReadAsStringAsync(cancellationToken);
        if (!resp.IsSuccessStatusCode)
        {
            return new AuthResult { Success = false, Message = ExtractLoginError(loginBody, $"登录失败: HTTP {(int)resp.StatusCode}") };
        }
        if (loginBody.Contains("用户名或密码错误", StringComparison.Ordinal) ||
            loginBody.Contains("二次验证码错误", StringComparison.Ordinal) ||
            loginBody.Contains("登录失败次数过多", StringComparison.Ordinal))
        {
            return new AuthResult { Success = false, Message = ExtractLoginError(loginBody, "登录失败，请检查账号、密码或 2FA 验证码") };
        }

        var indexHtml = await _httpClient.GetStringAsync("", cancellationToken);
        var tokenMatch = Regex.Match(indexHtml, @"const CSRF_TOKEN = ""([^""]+)"";");
        CsrfToken = tokenMatch.Success ? tokenMatch.Groups[1].Value : string.Empty;
        if (string.IsNullOrWhiteSpace(CsrfToken) && indexHtml.Contains("登录", StringComparison.Ordinal))
        {
            return new AuthResult { Success = false, Message = "登录未成功，请检查账号、密码或 2FA 验证码" };
        }
        return new AuthResult { Success = true };
    }

    public async Task<AuthResult> LoginWithDeviceTokenAsync(string token, CancellationToken cancellationToken)
    {
        token = token.Trim();
        if (string.IsNullOrWhiteSpace(token))
        {
            return new AuthResult { Success = false, Message = "请填写设备 Token。" };
        }

        DeviceToken = token;
        CsrfToken = string.Empty;
        _httpClient.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
        try
        {
            var resp = await _httpClient.GetAsync("api/mailboxes", cancellationToken);
            var body = await resp.Content.ReadAsStringAsync(cancellationToken);
            using var doc = ParseJsonOrThrow(body, "设备 Token 登录失败");
            if (!resp.IsSuccessStatusCode || !JsonSuccess(doc.RootElement))
            {
                return new AuthResult { Success = false, Message = ReadError(doc.RootElement, $"设备 Token 登录失败，请确认 Token 具备 client:full 权限。HTTP {(int)resp.StatusCode}") };
            }
            return new AuthResult { Success = true };
        }
        catch
        {
            DeviceToken = string.Empty;
            _httpClient.DefaultRequestHeaders.Authorization = null;
            throw;
        }
    }

    public async Task<List<MailAccount>> GetAccountsAsync(CancellationToken cancellationToken)
    {
        var localJson = await _httpClient.GetStringAsync("api/mailboxes", cancellationToken);
        var externalJson = await _httpClient.GetStringAsync("imap/api/accounts", cancellationToken);
        var local = ParseLocalAccounts(localJson);
        var external = ParseExternalAccounts(externalJson);
        return local.Concat(external).ToList();
    }

    public async Task SyncExternalAccountAsync(string accountId, bool background, bool force, CancellationToken cancellationToken)
    {
        var path = background ? "imap/api/accounts/sync" : $"imap/api/accounts/{Uri.EscapeDataString(accountId)}/sync";
        var payload = new Dictionary<string, object?>
        {
            ["background"] = background,
            ["force"] = force,
        };
        if (background)
        {
            payload["accountIds"] = accountId;
        }

        var req = CreateJsonRequest(path, payload);
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task SyncExternalAccountsAsync(IEnumerable<string> accountIds, bool background, bool force, CancellationToken cancellationToken)
    {
        var ids = string.Join(",", accountIds.Where(id => !string.IsNullOrWhiteSpace(id)).Distinct(StringComparer.OrdinalIgnoreCase));
        var req = CreateJsonRequest("imap/api/accounts/sync", new Dictionary<string, object?>
        {
            ["accountIds"] = ids,
            ["background"] = background,
            ["force"] = force,
        });
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task SaveLocalMailboxAsync(MailAccount account, bool isEdit, CancellationToken cancellationToken)
    {
        var payload = new Dictionary<string, object?>
        {
            ["address"] = account.Address,
            ["display_name"] = account.DisplayName,
            ["send_name"] = account.SendName,
            ["group"] = account.Group,
        };
        HttpResponseMessage resp;
        if (isEdit)
        {
            var req = CreateJsonRequest($"api/mailboxes/{Uri.EscapeDataString(account.Address)}", payload);
            req.Method = HttpMethod.Patch;
            resp = await _httpClient.SendAsync(req, cancellationToken);
        }
        else
        {
            var req = CreateJsonRequest("api/mailboxes", payload);
            resp = await _httpClient.SendAsync(req, cancellationToken);
        }
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task DeleteLocalMailboxAsync(string address, CancellationToken cancellationToken)
    {
        var req = new HttpRequestMessage(HttpMethod.Delete, $"api/mailboxes/{Uri.EscapeDataString(address)}");
        AddCsrf(req);
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task SaveExternalAccountAsync(MailAccount account, string password, string preset, bool isEdit, CancellationToken cancellationToken)
    {
        var payload = new Dictionary<string, object?>
        {
            ["preset"] = preset,
            ["email"] = account.Address,
            ["password"] = password,
            ["displayName"] = account.DisplayName,
            ["sendName"] = account.SendName,
            ["group"] = account.Group,
            ["host"] = account.Host,
            ["port"] = account.Port,
            ["smtpHost"] = account.SmtpHost,
            ["smtpPort"] = account.SmtpPort,
        };
        var path = isEdit ? $"imap/api/accounts/{Uri.EscapeDataString(account.Id)}" : "imap/api/accounts";
        var req = CreateJsonRequest(path, payload);
        if (isEdit)
        {
            req.Method = HttpMethod.Patch;
        }
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task DeleteExternalAccountAsync(string accountId, CancellationToken cancellationToken)
    {
        var req = new HttpRequestMessage(HttpMethod.Delete, $"imap/api/accounts/{Uri.EscapeDataString(accountId)}");
        AddCsrf(req);
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task ConfirmSensitiveActionAsync(string action, string password, string totpCode, CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest("api/security/confirm", new Dictionary<string, object?>
        {
            ["action"] = string.IsNullOrWhiteSpace(action) ? "*" : action,
            ["password"] = password,
            ["totp_code"] = totpCode,
        });
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task<List<DeviceTokenInfo>> GetDeviceTokensAsync(CancellationToken cancellationToken)
    {
        var json = await _httpClient.GetStringAsync("api/devices/tokens", cancellationToken);
        using var doc = ParseJsonOrThrow(json, "读取设备 Token 失败");
        if (!doc.RootElement.TryGetProperty("tokens", out var tokens) || tokens.ValueKind != JsonValueKind.Array)
        {
            return [];
        }
        return tokens.EnumerateArray().Select(ReadDeviceToken).ToList();
    }

    public async Task<string> CreateDeviceTokenAsync(string name, CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest("api/devices/tokens", new Dictionary<string, object?>
        {
            ["name"] = string.IsNullOrWhiteSpace(name) ? "Memail Desktop" : name.Trim(),
        });
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        var json = await resp.Content.ReadAsStringAsync(cancellationToken);
        using var doc = ParseJsonOrThrow(json, "创建设备 Token 失败");
        if (!resp.IsSuccessStatusCode || !JsonSuccess(doc.RootElement))
        {
            throw new MemailApiException(ReadError(doc.RootElement, $"创建设备 Token 失败: HTTP {(int)resp.StatusCode}"), resp.StatusCode,
                doc.RootElement.TryGetProperty("require_confirmation", out var confirm) && confirm.ValueKind is JsonValueKind.True or JsonValueKind.False && confirm.GetBoolean(),
                doc.RootElement.TryGetProperty("action", out var action) ? action.GetString() ?? string.Empty : string.Empty);
        }
        return doc.RootElement.TryGetProperty("token", out var token) ? token.GetString() ?? string.Empty : string.Empty;
    }

    public async Task RevokeDeviceTokenAsync(string tokenId, CancellationToken cancellationToken)
    {
        var req = new HttpRequestMessage(HttpMethod.Delete, $"api/devices/tokens/{Uri.EscapeDataString(tokenId)}");
        AddCsrf(req);
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task<SecurityStatus> GetSecurityStatusAsync(CancellationToken cancellationToken)
    {
        var json = await _httpClient.GetStringAsync("api/security/sessions", cancellationToken);
        using var doc = ParseJsonOrThrow(json, "读取安全状态失败");
        var status = new SecurityStatus
        {
            TotpEnabled = doc.RootElement.TryGetProperty("totp_enabled", out var totp)
                && totp.ValueKind is JsonValueKind.True or JsonValueKind.False
                && totp.GetBoolean(),
            CurrentSessionId = doc.RootElement.TryGetProperty("current_session_id", out var current)
                ? current.GetString() ?? string.Empty
                : string.Empty,
        };
        if (doc.RootElement.TryGetProperty("sessions", out var sessions) && sessions.ValueKind == JsonValueKind.Array)
        {
            status.Sessions = sessions.EnumerateArray().Select(item => ReadSecuritySession(item, status.CurrentSessionId)).ToList();
        }
        return status;
    }

    public async Task<TotpSetupInfo> SetupTotpAsync(CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest("api/security/totp/setup", new Dictionary<string, object?>());
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        var json = await resp.Content.ReadAsStringAsync(cancellationToken);
        using var doc = ParseJsonOrThrow(json, "生成 TOTP 密钥失败");
        if (!resp.IsSuccessStatusCode || !JsonSuccess(doc.RootElement))
        {
            throw new MemailApiException(ReadError(doc.RootElement, $"生成 TOTP 密钥失败: HTTP {(int)resp.StatusCode}"), resp.StatusCode,
                doc.RootElement.TryGetProperty("require_confirmation", out var confirm) && confirm.ValueKind is JsonValueKind.True or JsonValueKind.False && confirm.GetBoolean(),
                doc.RootElement.TryGetProperty("action", out var action) ? action.GetString() ?? string.Empty : string.Empty);
        }
        return new TotpSetupInfo
        {
            Secret = doc.RootElement.TryGetProperty("secret", out var secret) ? secret.GetString() ?? string.Empty : string.Empty,
            OtpAuthUri = doc.RootElement.TryGetProperty("otpauth_uri", out var uri) ? uri.GetString() ?? string.Empty : string.Empty,
        };
    }

    public async Task EnableTotpAsync(string code, CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest("api/security/totp/enable", new Dictionary<string, object?>
        {
            ["code"] = code,
        });
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task DisableTotpAsync(CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest("api/security/totp/disable", new Dictionary<string, object?>());
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task RevokeSessionAsync(string sessionId, CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest($"api/security/sessions/{Uri.EscapeDataString(sessionId)}/revoke", new Dictionary<string, object?>());
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task<List<SecurityAuditLog>> GetAuditLogsAsync(CancellationToken cancellationToken)
    {
        var json = await _httpClient.GetStringAsync("api/security/audit?limit=120", cancellationToken);
        using var doc = ParseJsonOrThrow(json, "读取审计日志失败");
        if (!doc.RootElement.TryGetProperty("logs", out var logs) || logs.ValueKind != JsonValueKind.Array)
        {
            return [];
        }
        return logs.EnumerateArray().Select(ReadSecurityAuditLog).ToList();
    }

    public async Task<AiSettings> GetAiSettingsAsync(CancellationToken cancellationToken)
    {
        var json = await _httpClient.GetStringAsync("api/ai/settings", cancellationToken);
        using var doc = ParseJsonOrThrow(json, "读取 AI 设置失败");
        var settings = new AiSettings();
        if (doc.RootElement.TryGetProperty("default_model", out var defaultModel) && defaultModel.ValueKind == JsonValueKind.Object)
        {
            settings.DefaultModel = new AiDefaultModel
            {
                ChannelId = defaultModel.TryGetProperty("channel_id", out var channelId) ? channelId.GetString() ?? string.Empty : string.Empty,
                Model = defaultModel.TryGetProperty("model", out var model) ? model.GetString() ?? string.Empty : string.Empty,
            };
        }
        if (doc.RootElement.TryGetProperty("channels", out var channels) && channels.ValueKind == JsonValueKind.Array)
        {
            settings.Channels = channels.EnumerateArray().Select(ReadAiChannel).ToList();
        }
        return settings;
    }

    public async Task<AiSettings> AddAiChannelAsync(string name, string provider, string baseUrl, string apiKey, CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest("api/ai/channels", new Dictionary<string, object?>
        {
            ["name"] = name,
            ["provider"] = provider,
            ["base_url"] = baseUrl,
            ["api_key"] = apiKey,
        });
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
        return await GetAiSettingsAsync(cancellationToken);
    }

    public async Task<AiSettings> RefreshAiModelsAsync(string channelId, CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest($"api/ai/channels/{Uri.EscapeDataString(channelId)}/models", new Dictionary<string, object?>());
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
        return await GetAiSettingsAsync(cancellationToken);
    }

    public async Task DeleteAiChannelAsync(string channelId, CancellationToken cancellationToken)
    {
        var req = new HttpRequestMessage(HttpMethod.Delete, $"api/ai/channels/{Uri.EscapeDataString(channelId)}");
        AddCsrf(req);
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task SaveAiDefaultModelAsync(string channelId, string model, CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest("api/ai/default-model", new Dictionary<string, object?>
        {
            ["channel_id"] = channelId,
            ["model"] = model,
        });
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task<List<KeywordRule>> GetKeywordRulesAsync(CancellationToken cancellationToken)
    {
        var json = await _httpClient.GetStringAsync("api/keyword-rules", cancellationToken);
        using var doc = ParseJsonOrThrow(json, "读取关键词规则失败");
        if (!doc.RootElement.TryGetProperty("rules", out var rules) || rules.ValueKind != JsonValueKind.Array)
        {
            return [];
        }
        return rules.EnumerateArray().Select(ReadKeywordRule).ToList();
    }

    public async Task<List<KeywordRule>> SaveKeywordRuleAsync(KeywordRule rule, CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest("api/keyword-rules", new Dictionary<string, object?>
        {
            ["id"] = string.IsNullOrWhiteSpace(rule.Id) ? null : rule.Id,
            ["name"] = rule.Name,
            ["keywords"] = rule.Keywords,
            ["match_mode"] = string.IsNullOrWhiteSpace(rule.MatchMode) ? "any" : rule.MatchMode,
            ["fields"] = rule.Fields.Count == 0 ? new[] { "subject", "from", "intro" } : rule.Fields,
            ["scope_type"] = string.IsNullOrWhiteSpace(rule.ScopeType) ? "all" : rule.ScopeType,
            ["scope_group"] = rule.ScopeGroup,
            ["scope_accounts"] = rule.ScopeAccounts,
            ["enabled"] = rule.Enabled,
            ["created_at"] = rule.CreatedAt,
        });
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        var json = await resp.Content.ReadAsStringAsync(cancellationToken);
        using var doc = ParseJsonOrThrow(json, "保存关键词规则失败");
        if (!resp.IsSuccessStatusCode || !JsonSuccess(doc.RootElement))
        {
            throw new InvalidOperationException(ReadError(doc.RootElement, "保存关键词规则失败"));
        }
        if (!doc.RootElement.TryGetProperty("rules", out var rules) || rules.ValueKind != JsonValueKind.Array)
        {
            return [];
        }
        return rules.EnumerateArray().Select(ReadKeywordRule).ToList();
    }

    public async Task<List<KeywordRule>> DeleteKeywordRuleAsync(string ruleId, CancellationToken cancellationToken)
    {
        var req = new HttpRequestMessage(HttpMethod.Delete, $"api/keyword-rules/{Uri.EscapeDataString(ruleId)}");
        AddCsrf(req);
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        var json = await resp.Content.ReadAsStringAsync(cancellationToken);
        using var doc = ParseJsonOrThrow(json, "删除关键词规则失败");
        if (!resp.IsSuccessStatusCode || !JsonSuccess(doc.RootElement))
        {
            throw new InvalidOperationException(ReadError(doc.RootElement, "删除关键词规则失败"));
        }
        if (!doc.RootElement.TryGetProperty("rules", out var rules) || rules.ValueKind != JsonValueKind.Array)
        {
            return [];
        }
        return rules.EnumerateArray().Select(ReadKeywordRule).ToList();
    }

    public Task<List<MailFolder>> GetLocalFoldersAsync(CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        return Task.FromResult(new List<MailFolder>
        {
            new() { Key = "inbox", Title = "收件箱" },
            new() { Key = "all", Title = "所有邮件" },
            new() { Key = "unread", Title = "未读邮件" },
            new() { Key = "sent", Title = "已发送" },
            new() { Key = "drafts", Title = "草稿箱" },
            new() { Key = "outbox", Title = "发送失败" },
            new() { Key = "trash", Title = "回收站" },
        });
    }

    public Task<List<MailFolder>> GetAggregateFoldersAsync(CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        return Task.FromResult(new List<MailFolder>
        {
            new() { Key = "all", Title = "所有邮件" },
            new() { Key = "unread", Title = "未读邮件" },
        });
    }

    public async Task<List<MailFolder>> GetExternalFoldersAsync(string accountId, CancellationToken cancellationToken)
    {
        var json = await _httpClient.GetStringAsync($"imap/api/accounts/{Uri.EscapeDataString(accountId)}/folders", cancellationToken);
        using var doc = ParseJsonOrThrow(json, "读取外部邮箱文件夹失败");
        if (doc.RootElement.ValueKind != JsonValueKind.Array)
        {
            return [];
        }

        var folders = new List<MailFolder>
        {
            new() { Key = "__memail_all__", Title = "所有邮件" },
            new() { Key = "__memail_unread__", Title = "未读邮件" },
        };
        folders.AddRange(doc.RootElement.EnumerateArray().Select(item => new MailFolder
        {
            Key = item.TryGetProperty("path", out var path) ? path.GetString() ?? string.Empty : string.Empty,
            Title = item.TryGetProperty("name", out var name) ? name.GetString() ?? string.Empty : string.Empty,
        }).Where(item => !string.IsNullOrWhiteSpace(item.Key)));
        return folders;
    }

    public async Task<(List<MailItem> Items, int Total)> QueryLocalInboxAsync(string email, string folderKey, int page, int pageSize, CancellationToken cancellationToken)
    {
        var offset = Math.Max(0, (page - 1) * pageSize);
        if (folderKey == "drafts")
        {
            return await QueryDraftsAsync(email, "local", page, pageSize, cancellationToken);
        }
        if (folderKey == "outbox")
        {
            return await QueryOutboxAsync(email, "local", page, pageSize, cancellationToken);
        }
        if (folderKey == "sent")
        {
            return await QueryLocalCollectionAsync("api/sent/query", email, pageSize, offset, cancellationToken, "sent");
        }
        if (folderKey == "trash")
        {
            return await QueryLocalCollectionAsync("api/trash/query", email, pageSize, offset, cancellationToken, "trash");
        }
        if (folderKey == "all" || folderKey == "unread")
        {
            return await QueryLocalCollectionAsync("api/inbox/query", email, pageSize, offset, cancellationToken, folderKey == "unread" ? "unread" : "all", folderKey == "unread");
        }
        return await QueryLocalCollectionAsync("api/inbox/query", email, pageSize, offset, cancellationToken, "inbox");
    }

    public async Task<(List<MailItem> Items, int Total)> QueryExternalInboxAsync(string accountId, string folderKey, int page, int pageSize, CancellationToken cancellationToken)
    {
        var query = $"imap/api/accounts/{Uri.EscapeDataString(accountId)}/mails?folder={Uri.EscapeDataString(folderKey)}&count={pageSize}&page={page}&offset={(page - 1) * pageSize}";
        var json = await _httpClient.GetStringAsync(query, cancellationToken);
        using var doc = ParseJsonOrThrow(json, "读取外部邮件列表失败");
        var total = doc.RootElement.TryGetProperty("total", out var totalEl) ? totalEl.GetInt32() : 0;
        var mails = doc.RootElement.TryGetProperty("mails", out var mailsEl) && mailsEl.ValueKind == JsonValueKind.Array
            ? mailsEl.EnumerateArray().Select(item => new MailItem
            {
                Id = item.TryGetProperty("uid", out var uid) ? uid.ToString() : string.Empty,
                AccountType = "external",
                AccountId = accountId,
                Folder = item.TryGetProperty("folder", out var folder) ? folder.GetString() ?? folderKey : folderKey,
                From = ReadAddressLike(item, "from"),
                Subject = item.TryGetProperty("subject", out var subject) ? subject.GetString() ?? string.Empty : string.Empty,
                Intro = item.TryGetProperty("intro", out var intro) ? intro.GetString() ?? string.Empty : string.Empty,
                To = ReadAddressLike(item, "to"),
                Html = item.TryGetProperty("html", out var html) ? html.GetString() ?? string.Empty : string.Empty,
                Text = item.TryGetProperty("text", out var text) ? text.GetString() ?? string.Empty : string.Empty,
                Seen = item.TryGetProperty("seen", out var seen) && seen.ValueKind is JsonValueKind.True or JsonValueKind.False && seen.GetBoolean(),
                Favorite = item.TryGetProperty("flagged", out var flagged) && flagged.ValueKind is JsonValueKind.True or JsonValueKind.False && flagged.GetBoolean(),
                Date = item.TryGetProperty("date", out var date) && DateTimeOffset.TryParse(date.GetString(), out var parsed) ? parsed : null,
            }).ToList()
            : [];
        return (mails, total);
    }

    public async Task<(List<MailItem> Items, int Total)> QueryAggregateAsync(
        IEnumerable<MailAccount> accounts,
        string folderKey,
        string query,
        int page,
        int pageSize,
        CancellationToken cancellationToken)
    {
        var accountList = accounts.Where(account => account.Type is "local" or "external").ToList();
        var offset = Math.Max(0, (page - 1) * pageSize);
        var windowCount = Math.Min(5000, offset + pageSize);
        var unreadOnly = folderKey == "unread" || folderKey == "__memail_unread__";
        var keyword = query.Trim();
        var items = new List<MailItem>();
        var total = 0;

        foreach (var local in accountList.Where(account => account.Type == "local"))
        {
            cancellationToken.ThrowIfCancellationRequested();
            var localResult = string.IsNullOrWhiteSpace(keyword)
                ? await QueryLocalCollectionAsync(
                    "api/inbox/query",
                    local.Address,
                    windowCount,
                    0,
                    cancellationToken,
                    unreadOnly ? "unread" : "all",
                    unreadOnly)
                : await SearchLocalAsync(local.Address, keyword, 1, windowCount, cancellationToken);
            if (!string.IsNullOrWhiteSpace(keyword) && unreadOnly)
            {
                localResult.Items = localResult.Items.Where(item => !item.Seen).ToList();
                localResult.Total = localResult.Items.Count;
            }
            total += localResult.Total;
            foreach (var item in localResult.Items)
            {
                item.AccountType = "local";
                item.AccountId = local.Address;
                item.AccountLabel = local.DisplayTitle;
                items.Add(item);
            }
        }

        var externalIds = accountList
            .Where(account => account.Type == "external")
            .Select(account => account.Id)
            .Where(id => !string.IsNullOrWhiteSpace(id))
            .ToList();
        if (externalIds.Count > 0)
        {
            var externalResult = string.IsNullOrWhiteSpace(keyword)
                ? await QueryExternalAggregateAsync(externalIds, unreadOnly, 1, windowCount, cancellationToken)
                : await SearchExternalAggregateAsync(externalIds, keyword, unreadOnly, 1, windowCount, cancellationToken);
            total += externalResult.Total;
            foreach (var item in externalResult.Items)
            {
                var account = accountList.FirstOrDefault(candidate => candidate.Type == "external" && candidate.Id == item.AccountId);
                if (account is not null)
                {
                    item.AccountLabel = account.DisplayTitle;
                }
                items.Add(item);
            }
        }

        var pageItems = items
            .OrderByDescending(item => item.Pinned)
            .ThenByDescending(item => item.Date)
            .Skip(offset)
            .Take(pageSize)
            .ToList();
        return (pageItems, total);
    }

    public async Task<(List<MailItem> Items, int Total)> QueryExternalAggregateAsync(
        IEnumerable<string> accountIds,
        bool unreadOnly,
        int page,
        int pageSize,
        CancellationToken cancellationToken)
    {
        var ids = string.Join(",", accountIds.Where(id => !string.IsNullOrWhiteSpace(id)).Distinct(StringComparer.OrdinalIgnoreCase));
        var query = $"imap/api/mails?accountIds={Uri.EscapeDataString(ids)}&count={pageSize}&page={page}&offset={(page - 1) * pageSize}&unread={(unreadOnly ? "1" : "0")}";
        var json = await _httpClient.GetStringAsync(query, cancellationToken);
        using var doc = ParseJsonOrThrow(json, "读取外部聚合邮件失败");
        var total = doc.RootElement.TryGetProperty("total", out var totalEl) && totalEl.TryGetInt32(out var totalValue) ? totalValue : 0;
        var mails = doc.RootElement.TryGetProperty("mails", out var mailsEl) && mailsEl.ValueKind == JsonValueKind.Array
            ? mailsEl.EnumerateArray().Select(item => new MailItem
            {
                Id = item.TryGetProperty("uid", out var uid) ? uid.ToString() : string.Empty,
                AccountType = "external",
                AccountId = item.TryGetProperty("accountId", out var accountId) ? accountId.ToString() : string.Empty,
                Folder = item.TryGetProperty("folder", out var folder) ? folder.GetString() ?? "INBOX" : "INBOX",
                From = ReadAddressLike(item, "from"),
                Subject = item.TryGetProperty("subject", out var subject) ? subject.GetString() ?? string.Empty : string.Empty,
                Intro = item.TryGetProperty("intro", out var intro) ? intro.GetString() ?? string.Empty : string.Empty,
                To = ReadAddressLike(item, "to"),
                Seen = item.TryGetProperty("seen", out var seen) && seen.ValueKind is JsonValueKind.True or JsonValueKind.False && seen.GetBoolean(),
                Favorite = item.TryGetProperty("flagged", out var flagged) && flagged.ValueKind is JsonValueKind.True or JsonValueKind.False && flagged.GetBoolean(),
                Date = item.TryGetProperty("date", out var date) && DateTimeOffset.TryParse(date.GetString(), out var parsed) ? parsed : null,
            }).ToList()
            : [];
        return (mails, total);
    }

    public async Task<(List<MailItem> Items, int Total)> SearchExternalAggregateAsync(
        IEnumerable<string> accountIds,
        string keyword,
        bool unreadOnly,
        int page,
        int pageSize,
        CancellationToken cancellationToken)
    {
        var ids = string.Join(",", accountIds.Where(id => !string.IsNullOrWhiteSpace(id)).Distinct(StringComparer.OrdinalIgnoreCase));
        var query = $"imap/api/search?accountIds={Uri.EscapeDataString(ids)}&q={Uri.EscapeDataString(keyword)}&count={pageSize}&page={page}&offset={(page - 1) * pageSize}&unread={(unreadOnly ? "1" : "0")}";
        var json = await _httpClient.GetStringAsync(query, cancellationToken);
        using var doc = ParseJsonOrThrow(json, "搜索外部聚合邮件失败");
        var total = doc.RootElement.TryGetProperty("total", out var totalEl) && totalEl.TryGetInt32(out var totalValue) ? totalValue : 0;
        var mails = doc.RootElement.TryGetProperty("mails", out var mailsEl) && mailsEl.ValueKind == JsonValueKind.Array
            ? mailsEl.EnumerateArray().Select(item => new MailItem
            {
                Id = item.TryGetProperty("uid", out var uid) ? uid.ToString() : string.Empty,
                AccountType = "external",
                AccountId = item.TryGetProperty("accountId", out var accountId) ? accountId.ToString() : string.Empty,
                Folder = item.TryGetProperty("folder", out var folder) ? folder.GetString() ?? "INBOX" : "INBOX",
                From = ReadAddressLike(item, "from"),
                Subject = item.TryGetProperty("subject", out var subject) ? subject.GetString() ?? string.Empty : string.Empty,
                Intro = item.TryGetProperty("intro", out var intro) ? intro.GetString() ?? string.Empty : string.Empty,
                To = ReadAddressLike(item, "to"),
                Seen = item.TryGetProperty("seen", out var seen) && seen.ValueKind is JsonValueKind.True or JsonValueKind.False && seen.GetBoolean(),
                Favorite = item.TryGetProperty("flagged", out var flagged) && flagged.ValueKind is JsonValueKind.True or JsonValueKind.False && flagged.GetBoolean(),
                Date = item.TryGetProperty("date", out var date) && DateTimeOffset.TryParse(date.GetString(), out var parsed) ? parsed : null,
            }).ToList()
            : [];
        return (mails, total > 0 ? total : mails.Count);
    }

    public async Task<(List<MailItem> Items, int Total)> SearchExternalAsync(string accountId, string folderKey, string query, int page, int pageSize, CancellationToken cancellationToken)
    {
        var path = $"imap/api/accounts/{Uri.EscapeDataString(accountId)}/search?folder={Uri.EscapeDataString(folderKey)}&field=all&q={Uri.EscapeDataString(query)}&count={pageSize}&page={page}&offset={(page - 1) * pageSize}";
        var json = await _httpClient.GetStringAsync(path, cancellationToken);
        using var doc = ParseJsonOrThrow(json, "搜索外部邮件失败");
        var total = 0;
        JsonElement results;
        if (doc.RootElement.ValueKind == JsonValueKind.Array)
        {
            results = doc.RootElement;
            total = results.GetArrayLength();
        }
        else if (doc.RootElement.ValueKind == JsonValueKind.Object)
        {
            total = doc.RootElement.TryGetProperty("total", out var totalEl) && totalEl.TryGetInt32(out var totalValue) ? totalValue : 0;
            results = doc.RootElement.TryGetProperty("mails", out var mailsEl) && mailsEl.ValueKind == JsonValueKind.Array ? mailsEl : default;
        }
        else
        {
            return ([], 0);
        }
        if (results.ValueKind != JsonValueKind.Array)
        {
            return ([], 0);
        }
        var mails = results.EnumerateArray().Select(item => new MailItem
        {
            Id = item.TryGetProperty("uid", out var uid) ? uid.ToString() : string.Empty,
            AccountType = "external",
            AccountId = accountId,
            Folder = item.TryGetProperty("folder", out var folder) ? folder.GetString() ?? folderKey : folderKey,
            From = ReadAddressLike(item, "from"),
            Subject = item.TryGetProperty("subject", out var subject) ? subject.GetString() ?? string.Empty : string.Empty,
            Seen = item.TryGetProperty("seen", out var seen) && seen.ValueKind is JsonValueKind.True or JsonValueKind.False && seen.GetBoolean(),
            Favorite = item.TryGetProperty("flagged", out var flagged) && flagged.ValueKind is JsonValueKind.True or JsonValueKind.False && flagged.GetBoolean(),
            Date = item.TryGetProperty("date", out var date) && DateTimeOffset.TryParse(date.GetString(), out var parsed) ? parsed : null,
        }).ToList();
        return (mails, total > 0 ? total : mails.Count);
    }

    public async Task<(List<MailItem> Items, int Total)> SearchLocalAsync(string email, string query, int page, int pageSize, CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest("api/inbox/search", new Dictionary<string, object?>
        {
            ["email"] = email,
            ["query"] = query,
        });
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        var json = await resp.Content.ReadAsStringAsync(cancellationToken);
        using var doc = ParseJsonOrThrow(json, "搜索本地邮件失败");
        if (!resp.IsSuccessStatusCode || !JsonSuccess(doc.RootElement))
        {
            throw new InvalidOperationException(ReadError(doc.RootElement, "搜索失败"));
        }
        var messages = doc.RootElement.TryGetProperty("messages", out var msgs) && msgs.ValueKind == JsonValueKind.Array ? msgs : default;
        var items = messages.ValueKind == JsonValueKind.Array
            ? messages.EnumerateArray().Select(item => new MailItem
            {
                Id = item.TryGetProperty("id", out var id) ? id.ToString() : string.Empty,
                AccountType = "local",
                AccountId = email,
                Folder = "inbox",
                From = ReadAddressLike(item, "from"),
                Subject = item.TryGetProperty("subject", out var subject) ? subject.GetString() ?? string.Empty : string.Empty,
                Intro = item.TryGetProperty("intro", out var intro) ? intro.GetString() ?? string.Empty : string.Empty,
                Seen = item.TryGetProperty("seen", out var seen) && seen.ValueKind is JsonValueKind.True or JsonValueKind.False && seen.GetBoolean(),
                Date = item.TryGetProperty("createdAt", out var createdAt) && DateTimeOffset.TryParse(createdAt.GetString(), out var parsed) ? parsed :
                       item.TryGetProperty("date", out var date) && DateTimeOffset.TryParse(date.GetString(), out parsed) ? parsed : null,
            }).ToList()
            : [];
        return PageLocalList(items, page, pageSize);
    }

    public async Task<MailDetail?> GetExternalDetailAsync(string accountId, string folderKey, string uid, CancellationToken cancellationToken)
    {
        var json = await _httpClient.GetStringAsync($"imap/api/accounts/{Uri.EscapeDataString(accountId)}/mails/{Uri.EscapeDataString(uid)}?folder={Uri.EscapeDataString(folderKey)}", cancellationToken);
        using var doc = ParseJsonOrThrow(json, "读取外部邮件详情失败");
        return new MailDetail
        {
            Id = uid,
            Subject = doc.RootElement.TryGetProperty("subject", out var subject) ? subject.GetString() ?? string.Empty : string.Empty,
            From = doc.RootElement.TryGetProperty("from", out var from) ? from.GetString() ?? string.Empty : string.Empty,
            To = doc.RootElement.TryGetProperty("to", out var to) ? to.GetString() ?? string.Empty : string.Empty,
            Cc = doc.RootElement.TryGetProperty("cc", out var cc) ? cc.GetString() ?? string.Empty : string.Empty,
            Html = doc.RootElement.TryGetProperty("html", out var html) ? html.GetString() ?? string.Empty : string.Empty,
            Text = doc.RootElement.TryGetProperty("text", out var text) ? text.GetString() ?? string.Empty : string.Empty,
            Date = doc.RootElement.TryGetProperty("date", out var date) && DateTimeOffset.TryParse(date.GetString(), out var parsed) ? parsed : null,
            Attachments = ReadAttachments(doc.RootElement),
        };
    }

    public async Task<MailDetail?> GetLocalDetailAsync(string email, string messageId, bool sent, bool trash, CancellationToken cancellationToken)
    {
        var path = sent ? "api/sent/detail" : trash ? "api/trash/detail" : "api/inbox/detail";
        var payload = new Dictionary<string, object?>
        {
            ["email"] = email,
            ["message_id"] = messageId,
        };
        var req = CreateJsonRequest(path, payload);
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        var json = await resp.Content.ReadAsStringAsync(cancellationToken);
        using var doc = ParseJsonOrThrow(json, "读取本地邮件详情失败");
        if (!doc.RootElement.TryGetProperty("success", out var success) || !success.GetBoolean())
        {
            return null;
        }
        var detail = doc.RootElement.TryGetProperty("detail", out var detailEl) ? detailEl : doc.RootElement;
        return new MailDetail
        {
            Id = messageId,
            Subject = detail.TryGetProperty("subject", out var subject) ? subject.GetString() ?? string.Empty : string.Empty,
            From = detail.TryGetProperty("from", out var from) ? from.ToString() : string.Empty,
            To = detail.TryGetProperty("to", out var to) ? to.ToString() : string.Empty,
            Cc = detail.TryGetProperty("cc", out var cc) ? cc.ToString() : string.Empty,
            Html = detail.TryGetProperty("html", out var html) ? html.GetString() ?? string.Empty : string.Empty,
            Text = detail.TryGetProperty("text", out var text) ? text.GetString() ?? string.Empty : string.Empty,
            Date = detail.TryGetProperty("createdAt", out var createdAt) && DateTimeOffset.TryParse(createdAt.GetString(), out var parsed) ? parsed :
                   detail.TryGetProperty("date", out var date) && DateTimeOffset.TryParse(date.GetString(), out parsed) ? parsed : null,
            Seen = detail.TryGetProperty("seen", out var seen) && seen.ValueKind is JsonValueKind.True or JsonValueKind.False && seen.GetBoolean(),
            Attachments = ReadAttachments(detail),
        };
    }

    public async Task<bool> UpdateMessageMetaAsync(MailAccount account, MailItem mail, bool? favorite = null, bool? pinned = null, CancellationToken cancellationToken = default)
    {
        var patch = new Dictionary<string, object?>();
        if (favorite.HasValue) patch["favorite"] = favorite.Value;
        if (pinned.HasValue) patch["pinned"] = pinned.Value;
        var payload = new Dictionary<string, object?>
        {
            ["account_type"] = EffectiveMailAccountType(account, mail),
            ["account_id"] = EffectiveMailAccountId(account, mail),
            ["folder"] = mail.Folder,
            ["message_id"] = mail.Id,
            ["meta"] = patch,
        };
        var req = CreateJsonRequest("api/message-meta", payload);
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
        return true;
    }

    public async Task ApplyMessageMetaAsync(MailAccount account, IEnumerable<MailItem> mails, CancellationToken cancellationToken)
    {
        var refs = mails
            .Where(mail => !string.IsNullOrWhiteSpace(mail.Id))
            .Select(mail => new Dictionary<string, object?>
            {
                ["account_type"] = EffectiveMailAccountType(account, mail),
                ["account_id"] = EffectiveMailAccountId(account, mail),
                ["folder"] = mail.Folder,
                ["message_id"] = mail.Id,
            })
            .ToList();
        if (refs.Count == 0)
        {
            return;
        }

        var req = CreateJsonRequest("api/message-meta/batch", new Dictionary<string, object?>
        {
            ["refs"] = refs,
        });
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        var json = await resp.Content.ReadAsStringAsync(cancellationToken);
        using var doc = ParseJsonOrThrow(json, "读取消息标记失败");
        if (!resp.IsSuccessStatusCode || !JsonSuccess(doc.RootElement) || !doc.RootElement.TryGetProperty("meta", out var meta) || meta.ValueKind != JsonValueKind.Object)
        {
            return;
        }

        foreach (var mail in mails)
        {
            var key = MessageMetaKey(EffectiveMailAccountType(account, mail), EffectiveMailAccountId(account, mail), mail.Folder, mail.Id);
            if (!meta.TryGetProperty(key, out var item) || item.ValueKind != JsonValueKind.Object)
            {
                continue;
            }
            if (item.TryGetProperty("favorite", out var favorite) && favorite.ValueKind is JsonValueKind.True or JsonValueKind.False)
            {
                mail.Favorite = favorite.GetBoolean();
            }
            if (item.TryGetProperty("pinned", out var pinned) && pinned.ValueKind is JsonValueKind.True or JsonValueKind.False)
            {
                mail.Pinned = pinned.GetBoolean();
            }
        }
    }

    public async Task<bool> MarkLocalReadAsync(string email, string messageId, bool seen, CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest("api/inbox/mark", new Dictionary<string, object?>
        {
            ["email"] = email,
            ["message_id"] = messageId,
            ["seen"] = seen,
        });
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
        return true;
    }

    public async Task BatchLocalInboxAsync(string email, IEnumerable<string> messageIds, string action, CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest("api/inbox/batch", new Dictionary<string, object?>
        {
            ["email"] = email,
            ["action"] = action,
            ["message_ids"] = messageIds.Where(id => !string.IsNullOrWhiteSpace(id)).ToList(),
        });
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task DeleteLocalMailAsync(string email, string messageId, CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest("api/inbox/delete", new Dictionary<string, object?>
        {
            ["email"] = email,
            ["message_id"] = messageId,
        });
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task RestoreLocalMailAsync(string email, string messageId, CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest("api/inbox/restore", new Dictionary<string, object?>
        {
            ["email"] = email,
            ["message_id"] = messageId,
        });
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task PermanentDeleteLocalMailAsync(string email, string messageId, CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest("api/inbox/permanent-delete", new Dictionary<string, object?>
        {
            ["email"] = email,
            ["message_id"] = messageId,
        });
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task<bool> SetExternalFlagAsync(string accountId, string folder, string uid, string flag, bool enabled, CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest(
            $"imap/api/accounts/{Uri.EscapeDataString(accountId)}/mails/{Uri.EscapeDataString(uid)}/flags?folder={Uri.EscapeDataString(folder)}",
            new Dictionary<string, object?>
            {
                ["action"] = enabled ? "add" : "remove",
                ["flags"] = new[] { flag },
            });
        req.Method = HttpMethod.Put;
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
        return true;
    }

    public async Task BatchExternalAsync(string accountId, string folder, IEnumerable<string> uids, string action, CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest(
            $"imap/api/accounts/{Uri.EscapeDataString(accountId)}/batch?folder={Uri.EscapeDataString(folder)}",
            new Dictionary<string, object?>
            {
                ["uids"] = uids.Where(id => !string.IsNullOrWhiteSpace(id)).ToList(),
                ["action"] = action,
            });
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task DeleteExternalMailAsync(string accountId, string folder, string uid, CancellationToken cancellationToken)
    {
        var req = new HttpRequestMessage(HttpMethod.Delete, $"imap/api/accounts/{Uri.EscapeDataString(accountId)}/mails/{Uri.EscapeDataString(uid)}?folder={Uri.EscapeDataString(folder)}");
        AddCsrf(req);
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task<string> TranslateAsync(MailDetail detail, CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest("api/ai/translate", new Dictionary<string, object?>
        {
            ["subject"] = detail.Subject,
            ["html"] = detail.Html,
            ["text"] = detail.Text,
        });
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        var json = await resp.Content.ReadAsStringAsync(cancellationToken);
        using var doc = ParseJsonOrThrow(json, "翻译邮件失败");
        if (!resp.IsSuccessStatusCode || !JsonSuccess(doc.RootElement))
        {
            throw new InvalidOperationException(ReadError(doc.RootElement, "翻译失败"));
        }
        return doc.RootElement.TryGetProperty("translation", out var translation)
            ? translation.GetString() ?? string.Empty
            : string.Empty;
    }

    public async Task<bool> SendLocalAsync(string fromEmail, string fromName, string to, string cc, string bcc, string subject, string text, string html, string replyTo, IEnumerable<MailAttachment> attachments, CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest("api/send", new Dictionary<string, object?>
        {
            ["from_email"] = fromEmail,
            ["from_name"] = fromName,
            ["to"] = to,
            ["cc"] = cc,
            ["bcc"] = bcc,
            ["subject"] = subject,
            ["text"] = text,
            ["html"] = html,
            ["reply_to"] = replyTo,
            ["attachments"] = BuildSendAttachments(attachments),
        });
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
        return true;
    }

    public async Task<bool> SendExternalAsync(MailAccount account, string fromName, string to, string cc, string bcc, string subject, string text, string html, IEnumerable<MailAttachment> attachments, CancellationToken cancellationToken)
    {
        var attachmentPayload = BuildSendAttachments(attachments);
        var sendPayload = new Dictionary<string, object?>
        {
            ["fromName"] = fromName,
            ["to"] = to,
            ["cc"] = cc,
            ["bcc"] = bcc,
            ["subject"] = subject,
            ["text"] = text,
            ["html"] = html,
            ["attachments"] = attachmentPayload,
        };
        var req = CreateJsonRequest($"imap/api/accounts/{Uri.EscapeDataString(account.Id)}/send", sendPayload);
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        var body = await resp.Content.ReadAsStringAsync(cancellationToken);
        using var doc = ParseJsonOrThrow(body, "外部账号发送失败");
        if (!resp.IsSuccessStatusCode || !JsonSuccess(doc.RootElement))
        {
            var error = ReadError(doc.RootElement, $"外部账号发送失败: HTTP {(int)resp.StatusCode}");
            await StoreOutboxFailureAsync(new Dictionary<string, object?>
            {
                ["account_type"] = "external",
                ["account_id"] = account.Id,
                ["from_email"] = account.Address,
                ["from_name"] = fromName,
                ["to"] = to,
                ["cc"] = cc,
                ["bcc"] = bcc,
                ["subject"] = subject,
                ["text"] = text,
                ["html"] = html,
                ["attachments"] = attachmentPayload,
                ["error"] = error,
            }, cancellationToken);
            throw new InvalidOperationException(error);
        }
        return true;
    }

    public async Task<MailItem?> SaveDraftAsync(MailAccount account, string? draftId, string fromName, string to, string cc, string bcc, string subject, string text, string html, IEnumerable<MailAttachment> attachments, CancellationToken cancellationToken)
    {
        var payload = new Dictionary<string, object?>
        {
            ["id"] = string.IsNullOrWhiteSpace(draftId) ? null : draftId,
            ["account_type"] = account.Type,
            ["account_id"] = account.Type == "external" ? account.Id : account.Address,
            ["from_email"] = account.Address,
            ["from_name"] = fromName,
            ["to"] = to,
            ["cc"] = cc,
            ["bcc"] = bcc,
            ["subject"] = subject,
            ["text"] = text,
            ["html"] = html,
            ["attachments"] = BuildSendAttachments(attachments),
        };
        var req = CreateJsonRequest("api/drafts", payload);
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        var json = await resp.Content.ReadAsStringAsync(cancellationToken);
        using var doc = ParseJsonOrThrow(json, "保存草稿失败");
        if (!resp.IsSuccessStatusCode || !JsonSuccess(doc.RootElement))
        {
            throw new InvalidOperationException(ReadError(doc.RootElement, "保存草稿失败"));
        }
        if (!doc.RootElement.TryGetProperty("draft", out var draft) || draft.ValueKind != JsonValueKind.Object)
        {
            return null;
        }
        return new MailItem
        {
            Id = draft.TryGetProperty("id", out var id) ? id.GetString() ?? string.Empty : string.Empty,
            AccountType = account.Type,
            AccountId = account.Type == "external" ? account.Id : account.Address,
            Folder = "drafts",
            From = draft.TryGetProperty("from_email", out var from) ? from.GetString() ?? string.Empty : account.Address,
            To = draft.TryGetProperty("to", out var draftTo) ? draftTo.GetString() ?? string.Empty : to,
            Cc = draft.TryGetProperty("cc", out var draftCc) ? draftCc.GetString() ?? string.Empty : cc,
            Bcc = draft.TryGetProperty("bcc", out var draftBcc) ? draftBcc.GetString() ?? string.Empty : bcc,
            Subject = draft.TryGetProperty("subject", out var draftSubject) ? draftSubject.GetString() ?? string.Empty : subject,
            Text = draft.TryGetProperty("text", out var draftText) ? draftText.GetString() ?? string.Empty : text,
            Html = draft.TryGetProperty("html", out var draftHtml) ? draftHtml.GetString() ?? string.Empty : html,
            Attachments = draft.TryGetProperty("attachments", out var draftAttachments) ? ReadAttachments(draftAttachments) : BuildDraftAttachments(attachments),
            Date = draft.TryGetProperty("updated_at", out var updatedAt) && DateTimeOffset.TryParse(updatedAt.GetString(), out var parsed) ? parsed : null,
        };
    }

    public async Task DeleteDraftAsync(string draftId, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(draftId))
        {
            return;
        }
        var req = new HttpRequestMessage(HttpMethod.Delete, $"api/drafts/{Uri.EscapeDataString(draftId)}");
        if (!string.IsNullOrWhiteSpace(CsrfToken))
        {
            req.Headers.Add("X-CSRF-Token", CsrfToken);
        }
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    private async Task StoreOutboxFailureAsync(Dictionary<string, object?> payload, CancellationToken cancellationToken)
    {
        var req = CreateJsonRequest("api/outbox", payload);
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task RetryOutboxAsync(string messageId, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(messageId))
        {
            return;
        }
        var req = CreateJsonRequest($"api/outbox/{Uri.EscapeDataString(messageId)}/retry", new Dictionary<string, object?>());
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        await EnsureJsonSuccessAsync(resp, cancellationToken);
    }

    public async Task DownloadAttachmentAsync(MailAccount account, MailItem mail, MailAttachment attachment, string targetPath, CancellationToken cancellationToken)
    {
        if (!string.IsNullOrWhiteSpace(attachment.Path) && File.Exists(attachment.Path))
        {
            File.Copy(attachment.Path, targetPath, overwrite: true);
            return;
        }
        if (!string.IsNullOrWhiteSpace(attachment.ContentBase64))
        {
            await File.WriteAllBytesAsync(targetPath, Convert.FromBase64String(attachment.ContentBase64), cancellationToken);
            return;
        }
        var path = account.Type == "external"
            ? $"imap/api/accounts/{Uri.EscapeDataString(account.Id)}/mails/{Uri.EscapeDataString(mail.Id)}/attachments/{attachment.Index}?folder={Uri.EscapeDataString(mail.Folder)}"
            : $"api/inbox/attachment/{Uri.EscapeDataString(mail.Id)}/{Uri.EscapeDataString(AttachmentId(attachment))}?email={Uri.EscapeDataString(account.Address)}&filename={Uri.EscapeDataString(attachment.Filename)}";
        using var resp = await _httpClient.GetAsync(path, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
        if (!resp.IsSuccessStatusCode)
        {
            throw new InvalidOperationException($"附件下载失败: HTTP {(int)resp.StatusCode}");
        }
        await using var input = await resp.Content.ReadAsStreamAsync(cancellationToken);
        await using var output = File.Create(targetPath);
        await input.CopyToAsync(output, cancellationToken);
    }

    private async Task<(List<MailItem> Items, int Total)> QueryLocalCollectionAsync(string path, string email, int pageSize, int offset, CancellationToken cancellationToken, string folder, bool unreadOnly = false)
    {
        var req = CreateJsonRequest(path, new Dictionary<string, object?>
        {
            ["email"] = email,
            ["offset"] = offset,
            ["limit"] = pageSize,
            ["unread_only"] = unreadOnly,
        });
        var resp = await _httpClient.SendAsync(req, cancellationToken);
        var json = await resp.Content.ReadAsStringAsync(cancellationToken);
        using var doc = ParseJsonOrThrow(json, "读取本地邮件集合失败");
        var total = doc.RootElement.TryGetProperty("total", out var totalEl) ? totalEl.GetInt32() : 0;
        var items = doc.RootElement.TryGetProperty("messages", out var msgs) && msgs.ValueKind == JsonValueKind.Array
            ? msgs.EnumerateArray().Select(item => new MailItem
            {
                Id = item.TryGetProperty("id", out var id) ? id.ToString() : string.Empty,
                AccountType = "local",
                AccountId = email,
                Folder = folder,
                From = ReadAddressLike(item, "from"),
                Subject = item.TryGetProperty("subject", out var subject) ? subject.GetString() ?? string.Empty : string.Empty,
                Intro = item.TryGetProperty("intro", out var intro) ? intro.GetString() ?? string.Empty : string.Empty,
                Seen = item.TryGetProperty("seen", out var seen) && seen.ValueKind is JsonValueKind.True or JsonValueKind.False && seen.GetBoolean(),
                Favorite = item.TryGetProperty("meta", out var meta)
                    && meta.ValueKind == JsonValueKind.Object
                    && meta.TryGetProperty("favorite", out var favorite)
                    && favorite.ValueKind is JsonValueKind.True or JsonValueKind.False
                    && favorite.GetBoolean(),
                Pinned = item.TryGetProperty("meta", out var metaPinned)
                    && metaPinned.ValueKind == JsonValueKind.Object
                    && metaPinned.TryGetProperty("pinned", out var pinned)
                    && pinned.ValueKind is JsonValueKind.True or JsonValueKind.False
                    && pinned.GetBoolean(),
                Date = item.TryGetProperty("createdAt", out var createdAt) && DateTimeOffset.TryParse(createdAt.GetString(), out var parsed) ? parsed :
                       item.TryGetProperty("created_at", out var createdAt2) && DateTimeOffset.TryParse(createdAt2.GetString(), out parsed) ? parsed :
                       item.TryGetProperty("date", out var date) && DateTimeOffset.TryParse(date.GetString(), out parsed) ? parsed : null,
            }).ToList()
            : [];
        return (items, total);
    }

    private static string EffectiveMailAccountType(MailAccount account, MailItem mail)
    {
        if (!string.IsNullOrWhiteSpace(mail.AccountType) && mail.AccountType != "aggregate")
        {
            return mail.AccountType;
        }
        if (account.Type != "aggregate")
        {
            return account.Type;
        }
        return mail.AccountId.Contains('@', StringComparison.Ordinal) ? "local" : "external";
    }

    private static string EffectiveMailAccountId(MailAccount account, MailItem mail)
    {
        if (!string.IsNullOrWhiteSpace(mail.AccountId))
        {
            return mail.AccountId;
        }
        return account.Type == "external" ? account.Id : account.Address;
    }

    private async Task<(List<MailItem> Items, int Total)> QueryDraftsAsync(string accountId, string accountType, int page, int pageSize, CancellationToken cancellationToken)
    {
        var json = await _httpClient.GetStringAsync($"api/drafts?account_id={Uri.EscapeDataString(accountId)}&account_type={Uri.EscapeDataString(accountType)}", cancellationToken);
        using var doc = ParseJsonOrThrow(json, "读取草稿箱失败");
        if (!JsonSuccess(doc.RootElement) || !doc.RootElement.TryGetProperty("drafts", out var drafts) || drafts.ValueKind != JsonValueKind.Array)
        {
            return ([], 0);
        }
        var all = drafts.EnumerateArray().Select(item => new MailItem
        {
            Id = item.TryGetProperty("id", out var id) ? id.GetString() ?? string.Empty : string.Empty,
            AccountType = accountType,
            AccountId = accountId,
            Folder = "drafts",
            From = item.TryGetProperty("from_email", out var from) ? from.GetString() ?? string.Empty : string.Empty,
            Subject = item.TryGetProperty("subject", out var subject) ? subject.GetString() ?? string.Empty : "(无主题)",
            Intro = item.TryGetProperty("to", out var to) ? $"收件人: {to.GetString()}" : string.Empty,
            To = item.TryGetProperty("to", out var toDetail) ? toDetail.GetString() ?? string.Empty : string.Empty,
            Cc = item.TryGetProperty("cc", out var cc) ? cc.GetString() ?? string.Empty : string.Empty,
            Bcc = item.TryGetProperty("bcc", out var bcc) ? bcc.GetString() ?? string.Empty : string.Empty,
            Html = item.TryGetProperty("html", out var html) ? html.GetString() ?? string.Empty : string.Empty,
            Text = item.TryGetProperty("text", out var text) ? text.GetString() ?? string.Empty : string.Empty,
            Attachments = item.TryGetProperty("attachments", out var attachments) ? ReadAttachments(attachments) : [],
            Date = item.TryGetProperty("updated_at", out var updatedAt) && DateTimeOffset.TryParse(updatedAt.GetString(), out var parsed) ? parsed : null,
        }).ToList();
        return PageLocalList(all, page, pageSize);
    }

    private async Task<(List<MailItem> Items, int Total)> QueryOutboxAsync(string accountId, string accountType, int page, int pageSize, CancellationToken cancellationToken)
    {
        var json = await _httpClient.GetStringAsync($"api/outbox?account_id={Uri.EscapeDataString(accountId)}&account_type={Uri.EscapeDataString(accountType)}", cancellationToken);
        using var doc = ParseJsonOrThrow(json, "读取发送失败记录失败");
        if (!JsonSuccess(doc.RootElement) || !doc.RootElement.TryGetProperty("messages", out var messages) || messages.ValueKind != JsonValueKind.Array)
        {
            return ([], 0);
        }
        var all = messages.EnumerateArray().Select(item => new MailItem
        {
            Id = item.TryGetProperty("id", out var id) ? id.GetString() ?? string.Empty : string.Empty,
            AccountType = accountType,
            AccountId = accountId,
            Folder = "outbox",
            From = item.TryGetProperty("from_email", out var from) ? from.GetString() ?? string.Empty : string.Empty,
            Subject = item.TryGetProperty("subject", out var subject) ? subject.GetString() ?? string.Empty : "(无主题)",
            Intro = item.TryGetProperty("error", out var error) ? error.GetString() ?? string.Empty : string.Empty,
            To = item.TryGetProperty("to", out var to) ? to.GetString() ?? string.Empty : string.Empty,
            Cc = item.TryGetProperty("cc", out var cc) ? cc.GetString() ?? string.Empty : string.Empty,
            Bcc = item.TryGetProperty("bcc", out var bcc) ? bcc.GetString() ?? string.Empty : string.Empty,
            Html = item.TryGetProperty("html", out var html) ? html.GetString() ?? string.Empty : string.Empty,
            Text = item.TryGetProperty("text", out var text) ? text.GetString() ?? string.Empty : string.Empty,
            Error = item.TryGetProperty("error", out var errorDetail) ? errorDetail.GetString() ?? string.Empty : string.Empty,
            Attachments = item.TryGetProperty("attachments", out var attachments) ? ReadAttachments(attachments) : [],
            Date = item.TryGetProperty("updated_at", out var updatedAt) && DateTimeOffset.TryParse(updatedAt.GetString(), out var parsed) ? parsed : null,
        }).ToList();
        return PageLocalList(all, page, pageSize);
    }

    private static string MessageMetaKey(string accountType, string accountId, string folder, string messageId)
    {
        var normalizedType = (accountType ?? string.Empty).Trim().ToLowerInvariant();
        if (string.IsNullOrWhiteSpace(normalizedType))
        {
            normalizedType = "local";
        }
        var normalizedFolder = (folder ?? string.Empty).Trim();
        if (string.IsNullOrWhiteSpace(normalizedFolder))
        {
            normalizedFolder = "INBOX";
        }
        return string.Join("|", new[]
        {
            normalizedType,
            (accountId ?? string.Empty).Trim().ToLowerInvariant(),
            normalizedFolder,
            (messageId ?? string.Empty).Trim(),
        });
    }

    private static (List<MailItem> Items, int Total) PageLocalList(List<MailItem> all, int page, int pageSize)
    {
        var total = all.Count;
        var offset = Math.Max(0, (Math.Max(1, page) - 1) * pageSize);
        return (all.Skip(offset).Take(pageSize).ToList(), total);
    }

    private HttpRequestMessage CreateJsonRequest(string path, object payload)
    {
        var req = new HttpRequestMessage(HttpMethod.Post, path)
        {
            Content = JsonContent.Create(payload)
        };
        AddCsrf(req);
        return req;
    }

    private void AddCsrf(HttpRequestMessage req)
    {
        if (!string.IsNullOrWhiteSpace(CsrfToken))
        {
            req.Headers.Add("X-CSRF-Token", CsrfToken);
        }
    }

    private static string AttachmentId(MailAttachment attachment)
    {
        if (!string.IsNullOrWhiteSpace(attachment.Id))
        {
            return attachment.Id;
        }
        return attachment.Index.ToString();
    }

    private static List<Dictionary<string, object?>> BuildSendAttachments(IEnumerable<MailAttachment> attachments)
    {
        var result = new List<Dictionary<string, object?>>();
        long totalSize = 0;
        foreach (var attachment in attachments)
        {
            var content = attachment.ContentBase64;
            if (string.IsNullOrWhiteSpace(content))
            {
                if (string.IsNullOrWhiteSpace(attachment.Path) || !File.Exists(attachment.Path))
                {
                    continue;
                }
                var file = new FileInfo(attachment.Path);
                totalSize += file.Length;
                content = Convert.ToBase64String(File.ReadAllBytes(attachment.Path));
                if (file.Length > 15L * 1024L * 1024L)
                {
                    throw new InvalidOperationException($"单个附件不能超过 15 MB: {attachment.Filename}");
                }
            }
            else
            {
                var size = attachment.Size > 0 ? attachment.Size : Convert.FromBase64String(content).LongLength;
                totalSize += size;
                if (size > 15L * 1024L * 1024L)
                {
                    throw new InvalidOperationException($"单个附件不能超过 15 MB: {attachment.Filename}");
                }
            }
            if (totalSize > 25L * 1024L * 1024L)
            {
                throw new InvalidOperationException("附件总大小不能超过 25 MB");
            }
            result.Add(new Dictionary<string, object?>
            {
                ["filename"] = string.IsNullOrWhiteSpace(attachment.Filename) ? Path.GetFileName(attachment.Path) : attachment.Filename,
                ["content_type"] = string.IsNullOrWhiteSpace(attachment.ContentType) ? "application/octet-stream" : attachment.ContentType,
                ["content"] = content,
            });
        }
        return result;
    }

    private static List<MailAttachment> BuildDraftAttachments(IEnumerable<MailAttachment> attachments)
    {
        return attachments.Select((item, index) => new MailAttachment
        {
            Index = index,
            Filename = item.Filename,
            Path = item.Path,
            ContentBase64 = item.ContentBase64,
            Size = item.Size,
            ContentType = item.ContentType,
        }).ToList();
    }

    private async Task EnsureJsonSuccessAsync(HttpResponseMessage resp, CancellationToken cancellationToken)
    {
        var body = await resp.Content.ReadAsStringAsync(cancellationToken);
        if (string.IsNullOrWhiteSpace(body))
        {
            if (resp.IsSuccessStatusCode)
            {
                return;
            }
            throw new InvalidOperationException($"请求失败: HTTP {(int)resp.StatusCode}");
        }
        using var doc = ParseJsonOrThrow(body, $"请求失败: HTTP {(int)resp.StatusCode}");
        if (!resp.IsSuccessStatusCode || !JsonSuccess(doc.RootElement))
        {
            var requireConfirmation = doc.RootElement.TryGetProperty("require_confirmation", out var confirm)
                && confirm.ValueKind is JsonValueKind.True or JsonValueKind.False
                && confirm.GetBoolean();
            var action = doc.RootElement.TryGetProperty("action", out var actionEl) ? actionEl.GetString() ?? string.Empty : string.Empty;
            throw new MemailApiException(ReadError(doc.RootElement, $"请求失败: HTTP {(int)resp.StatusCode}"), resp.StatusCode, requireConfirmation, action);
        }
    }

    private static bool JsonSuccess(JsonElement element)
    {
        if (element.TryGetProperty("success", out var success) && success.ValueKind is JsonValueKind.True or JsonValueKind.False)
        {
            return success.GetBoolean();
        }
        if (element.TryGetProperty("ok", out var ok) && ok.ValueKind is JsonValueKind.True or JsonValueKind.False)
        {
            return ok.GetBoolean();
        }
        return true;
    }

    private static string ReadError(JsonElement element, string fallback)
    {
        foreach (var name in new[] { "message", "error", "detail" })
        {
            if (element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String)
            {
                var text = value.GetString();
                if (!string.IsNullOrWhiteSpace(text))
                {
                    return text;
                }
            }
        }
        return fallback;
    }

    private static JsonDocument ParseJsonOrThrow(string body, string context)
    {
        try
        {
            return JsonDocument.Parse(body);
        }
        catch (JsonException)
        {
            throw new InvalidOperationException($"{context}: 服务端返回非 JSON 内容：{SummarizeBody(body)}");
        }
    }

    private static string SummarizeBody(string body)
    {
        if (string.IsNullOrWhiteSpace(body))
        {
            return "空响应";
        }
        var text = Regex.Replace(body, @"<script\b[^>]*>.*?</script>", " ", RegexOptions.IgnoreCase | RegexOptions.Singleline);
        text = Regex.Replace(text, @"<style\b[^>]*>.*?</style>", " ", RegexOptions.IgnoreCase | RegexOptions.Singleline);
        text = Regex.Replace(text, "<[^>]+>", " ");
        text = WebUtility.HtmlDecode(Regex.Replace(text, @"\s+", " ")).Trim();
        if (string.IsNullOrWhiteSpace(text))
        {
            text = body.Trim();
        }
        return text.Length <= 240 ? text : $"{text[..240]}...";
    }

    private static string ExtractLoginError(string html, string fallback)
    {
        if (string.IsNullOrWhiteSpace(html))
        {
            return fallback;
        }
        var text = Regex.Replace(html, "<[^>]+>", " ");
        text = WebUtility.HtmlDecode(Regex.Replace(text, @"\s+", " ")).Trim();
        foreach (var marker in new[] { "登录失败次数过多，请稍后再试", "二次验证码错误", "用户名或密码错误" })
        {
            if (text.Contains(marker, StringComparison.Ordinal))
            {
                return marker;
            }
        }
        return fallback;
    }

    private static string ReadAddressLike(JsonElement item, string propertyName)
    {
        if (!item.TryGetProperty(propertyName, out var value))
        {
            return string.Empty;
        }
        if (value.ValueKind == JsonValueKind.String)
        {
            return value.GetString() ?? string.Empty;
        }
        if (value.ValueKind == JsonValueKind.Object)
        {
            var name = value.TryGetProperty("name", out var nameEl) ? nameEl.GetString() ?? string.Empty : string.Empty;
            var address = value.TryGetProperty("address", out var addrEl) ? addrEl.GetString() ?? string.Empty : string.Empty;
            if (!string.IsNullOrWhiteSpace(name) && !string.IsNullOrWhiteSpace(address))
            {
                return $"{name} <{address}>";
            }
            return address;
        }
        if (value.ValueKind == JsonValueKind.Array)
        {
            return string.Join(", ", value.EnumerateArray().Select(part => ReadAddressElement(part)).Where(x => !string.IsNullOrWhiteSpace(x)));
        }
        return value.ToString();
    }

    private static DeviceTokenInfo ReadDeviceToken(JsonElement item)
    {
        return new DeviceTokenInfo
        {
            Id = item.TryGetProperty("id", out var id) ? id.GetString() ?? string.Empty : string.Empty,
            Name = item.TryGetProperty("name", out var name) ? name.GetString() ?? string.Empty : string.Empty,
            CreatedAt = item.TryGetProperty("created_at", out var createdAt) ? createdAt.GetString() ?? string.Empty : string.Empty,
            LastSeen = item.TryGetProperty("last_seen", out var lastSeen) ? lastSeen.GetString() ?? string.Empty : string.Empty,
            LastIp = item.TryGetProperty("last_ip", out var lastIp) ? lastIp.GetString() ?? string.Empty : string.Empty,
            Revoked = item.TryGetProperty("revoked", out var revoked) && revoked.ValueKind is JsonValueKind.True or JsonValueKind.False && revoked.GetBoolean(),
        };
    }

    private static SecuritySessionInfo ReadSecuritySession(JsonElement item, string currentSessionId)
    {
        var id = item.TryGetProperty("id", out var idEl) ? idEl.GetString() ?? string.Empty : string.Empty;
        return new SecuritySessionInfo
        {
            Id = id,
            Username = item.TryGetProperty("username", out var username) ? username.GetString() ?? string.Empty : string.Empty,
            Ip = item.TryGetProperty("ip", out var ip) ? ip.GetString() ?? string.Empty : string.Empty,
            UserAgent = item.TryGetProperty("user_agent", out var userAgent) ? userAgent.GetString() ?? string.Empty : string.Empty,
            CreatedAt = item.TryGetProperty("created_at", out var createdAt) ? createdAt.GetString() ?? string.Empty : string.Empty,
            LastSeen = item.TryGetProperty("last_seen", out var lastSeen) ? lastSeen.GetString() ?? string.Empty : string.Empty,
            Revoked = item.TryGetProperty("revoked", out var revoked) && revoked.ValueKind is JsonValueKind.True or JsonValueKind.False && revoked.GetBoolean(),
            IsCurrent = string.Equals(id, currentSessionId, StringComparison.Ordinal),
        };
    }

    private static SecurityAuditLog ReadSecurityAuditLog(JsonElement item)
    {
        return new SecurityAuditLog
        {
            Id = item.TryGetProperty("id", out var id) ? id.GetString() ?? string.Empty : string.Empty,
            Action = item.TryGetProperty("action", out var action) ? action.GetString() ?? string.Empty : string.Empty,
            Ip = item.TryGetProperty("ip", out var ip) ? ip.GetString() ?? string.Empty : string.Empty,
            Username = item.TryGetProperty("username", out var username) ? username.GetString() ?? string.Empty : string.Empty,
            CreatedAt = item.TryGetProperty("created_at", out var createdAt) ? createdAt.GetString() ?? string.Empty : string.Empty,
            Success = !item.TryGetProperty("success", out var success) || success.ValueKind is not (JsonValueKind.True or JsonValueKind.False) || success.GetBoolean(),
        };
    }

    private static KeywordRule ReadKeywordRule(JsonElement item)
    {
        return new KeywordRule
        {
            Id = item.TryGetProperty("id", out var id) ? id.GetString() ?? string.Empty : string.Empty,
            Name = item.TryGetProperty("name", out var name) ? name.GetString() ?? string.Empty : string.Empty,
            Keywords = ReadStringArray(item, "keywords"),
            MatchMode = item.TryGetProperty("match_mode", out var matchMode) ? matchMode.GetString() ?? "any" : "any",
            Fields = ReadStringArray(item, "fields"),
            ScopeType = item.TryGetProperty("scope_type", out var scopeType) ? scopeType.GetString() ?? "all" : "all",
            ScopeGroup = item.TryGetProperty("scope_group", out var scopeGroup) ? scopeGroup.GetString() ?? string.Empty : string.Empty,
            ScopeAccounts = ReadStringArray(item, "scope_accounts"),
            Enabled = !item.TryGetProperty("enabled", out var enabled) || enabled.ValueKind is not (JsonValueKind.True or JsonValueKind.False) || enabled.GetBoolean(),
            CreatedAt = item.TryGetProperty("created_at", out var createdAt) ? createdAt.GetString() ?? string.Empty : string.Empty,
            UpdatedAt = item.TryGetProperty("updated_at", out var updatedAt) ? updatedAt.GetString() ?? string.Empty : string.Empty,
        };
    }

    private static List<string> ReadStringArray(JsonElement item, string propertyName)
    {
        if (!item.TryGetProperty(propertyName, out var values) || values.ValueKind != JsonValueKind.Array)
        {
            return [];
        }
        return values.EnumerateArray()
            .Where(value => value.ValueKind == JsonValueKind.String)
            .Select(value => value.GetString() ?? string.Empty)
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .ToList();
    }

    private static string ReadAddressElement(JsonElement value)
    {
        if (value.ValueKind == JsonValueKind.String)
        {
            return value.GetString() ?? string.Empty;
        }
        if (value.ValueKind != JsonValueKind.Object)
        {
            return value.ToString();
        }
        var name = value.TryGetProperty("name", out var nameEl) ? nameEl.GetString() ?? string.Empty : string.Empty;
        var address = value.TryGetProperty("address", out var addrEl) ? addrEl.GetString() ?? string.Empty : string.Empty;
        return !string.IsNullOrWhiteSpace(name) && !string.IsNullOrWhiteSpace(address) ? $"{name} <{address}>" : address;
    }

    private static List<MailAttachment> ReadAttachments(JsonElement element)
    {
        var attachments = element;
        if (element.ValueKind == JsonValueKind.Object
            && (!element.TryGetProperty("attachments", out attachments) || attachments.ValueKind != JsonValueKind.Array))
        {
            return [];
        }
        if (attachments.ValueKind != JsonValueKind.Array)
        {
            return [];
        }
        return attachments.EnumerateArray().Select((item, index) => new MailAttachment
        {
            Index = item.TryGetProperty("index", out var idx) && idx.TryGetInt32(out var parsedIndex) ? parsedIndex : index,
            Id = item.TryGetProperty("id", out var id) ? id.GetString() ?? string.Empty : string.Empty,
            Filename = item.TryGetProperty("filename", out var filename) ? filename.GetString() ?? string.Empty :
                       item.TryGetProperty("name", out var name) ? name.GetString() ?? string.Empty :
                       $"attachment_{index}",
            Path = item.TryGetProperty("path", out var path) ? path.GetString() ?? string.Empty : string.Empty,
            ContentBase64 = item.TryGetProperty("content", out var content) ? content.GetString() ?? string.Empty : string.Empty,
            Size = item.TryGetProperty("size", out var size) && size.TryGetInt64(out var parsedSize) ? parsedSize : 0,
            ContentType = item.TryGetProperty("contentType", out var contentType) ? contentType.GetString() ?? string.Empty :
                          item.TryGetProperty("content_type", out var contentType2) ? contentType2.GetString() ?? string.Empty : string.Empty,
        }).ToList();
    }

    private static AiChannel ReadAiChannel(JsonElement item)
    {
        var channel = new AiChannel
        {
            Id = item.TryGetProperty("id", out var id) ? id.GetString() ?? string.Empty : string.Empty,
            Name = item.TryGetProperty("name", out var name) ? name.GetString() ?? string.Empty : string.Empty,
            Provider = item.TryGetProperty("provider", out var provider) ? provider.GetString() ?? string.Empty : string.Empty,
            BaseUrl = item.TryGetProperty("base_url", out var baseUrl) ? baseUrl.GetString() ?? string.Empty : string.Empty,
            UpdatedAt = item.TryGetProperty("updated_at", out var updatedAt) ? updatedAt.GetString() ?? string.Empty : string.Empty,
        };
        if (item.TryGetProperty("models", out var models) && models.ValueKind == JsonValueKind.Array)
        {
            channel.Models = models.EnumerateArray()
                .Where(model => model.ValueKind == JsonValueKind.String)
                .Select(model => model.GetString() ?? string.Empty)
                .Where(model => !string.IsNullOrWhiteSpace(model))
                .ToList();
        }
        return channel;
    }

    private List<MailAccount> ParseLocalAccounts(string json)
    {
        using var doc = ParseJsonOrThrow(json, "读取本地邮箱账号失败");
        if (!doc.RootElement.TryGetProperty("mailboxes", out var mailboxes) || mailboxes.ValueKind != JsonValueKind.Array)
        {
            return [];
        }

        return mailboxes.EnumerateArray().Select(item => new MailAccount
        {
            Id = item.GetProperty("address").GetString() ?? string.Empty,
            Type = "local",
            Address = item.GetProperty("address").GetString() ?? string.Empty,
            DisplayName = item.TryGetProperty("display_name", out var displayName) ? displayName.GetString() ?? string.Empty : string.Empty,
            SendName = item.TryGetProperty("send_name", out var sendName) ? sendName.GetString() ?? string.Empty : string.Empty,
            Group = item.TryGetProperty("group", out var group) ? group.GetString() ?? string.Empty : string.Empty,
            Provider = "local",
        }).ToList();
    }

    private List<MailAccount> ParseExternalAccounts(string json)
    {
        using var doc = ParseJsonOrThrow(json, "读取外部邮箱账号失败");
        if (doc.RootElement.ValueKind != JsonValueKind.Array)
        {
            return [];
        }

        return doc.RootElement.EnumerateArray().Select(item => new MailAccount
        {
            Id = item.GetProperty("id").ToString(),
            Type = "external",
            Address = item.GetProperty("email").GetString() ?? string.Empty,
            DisplayName = item.TryGetProperty("displayName", out var displayName) ? displayName.GetString() ?? string.Empty : string.Empty,
            SendName = item.TryGetProperty("sendName", out var sendName) ? sendName.GetString() ?? string.Empty : string.Empty,
            Group = item.TryGetProperty("group", out var group) ? group.GetString() ?? string.Empty : string.Empty,
            Provider = item.TryGetProperty("name", out var provider) ? provider.GetString() ?? string.Empty : string.Empty,
            Host = item.TryGetProperty("host", out var host) ? host.GetString() ?? string.Empty : string.Empty,
            Port = item.TryGetProperty("port", out var port) && port.TryGetInt32(out var portValue) ? portValue : 993,
            Secure = !item.TryGetProperty("secure", out var secure) || secure.ValueKind is not (JsonValueKind.True or JsonValueKind.False) || secure.GetBoolean(),
            SmtpHost = item.TryGetProperty("smtp", out var smtp) && smtp.ValueKind == JsonValueKind.Object && smtp.TryGetProperty("host", out var smtpHost) ? smtpHost.GetString() ?? string.Empty : string.Empty,
            SmtpPort = item.TryGetProperty("smtp", out var smtpPortRoot) && smtpPortRoot.ValueKind == JsonValueKind.Object && smtpPortRoot.TryGetProperty("port", out var smtpPort) && smtpPort.TryGetInt32(out var smtpPortValue) ? smtpPortValue : 465,
            SmtpSecure = !(item.TryGetProperty("smtp", out var smtpSecureRoot) && smtpSecureRoot.ValueKind == JsonValueKind.Object && smtpSecureRoot.TryGetProperty("secure", out var smtpSecure) && smtpSecure.ValueKind is JsonValueKind.True or JsonValueKind.False) || smtpSecure.GetBoolean(),
            SmtpRequireTls = item.TryGetProperty("smtp", out var smtpTlsRoot) && smtpTlsRoot.ValueKind == JsonValueKind.Object && smtpTlsRoot.TryGetProperty("requireTLS", out var requireTls) && requireTls.ValueKind is JsonValueKind.True or JsonValueKind.False && requireTls.GetBoolean(),
            UnreadCount = item.TryGetProperty("syncStatus", out var syncStatus) && syncStatus.TryGetProperty("unseen", out var unseen)
                ? unseen.GetInt32()
                : 0,
        }).ToList();
    }
}
