using System.Collections.ObjectModel;
using System.Windows;
using System.Windows.Controls;
using Memail.Desktop.Models;
using Memail.Desktop.Services;

namespace Memail.Desktop.Views;

public partial class SettingsWindow : Window
{
    private readonly MemailApiClient _apiClient;
    private readonly ObservableCollection<MailAccount> _accounts = [];
    private readonly ObservableCollection<AiChannel> _channels = [];
    private readonly ObservableCollection<DeviceTokenInfo> _deviceTokens = [];
    private readonly ObservableCollection<SecuritySessionInfo> _securitySessions = [];
    private readonly ObservableCollection<SecurityAuditLog> _auditLogs = [];
    private readonly ObservableCollection<KeywordRule> _keywordRules = [];
    private AiSettings _aiSettings = new();
    private SecurityStatus _securityStatus = new();
    private bool _refreshRequested;
    private bool _loadingExternalAccount;

    public SettingsWindow(MemailApiClient apiClient)
    {
        _apiClient = apiClient;
        InitializeComponent();
        AccountListBox.ItemsSource = _accounts;
        AiChannelListBox.ItemsSource = _channels;
        AiDefaultChannelComboBox.ItemsSource = _channels;
        AiDefaultChannelComboBox.DisplayMemberPath = nameof(AiChannel.DisplayName);
        DeviceTokenListBox.ItemsSource = _deviceTokens;
        SecuritySessionListBox.ItemsSource = _securitySessions;
        AuditLogListBox.ItemsSource = _auditLogs;
        KeywordRuleListBox.ItemsSource = _keywordRules;
        Loaded += OnLoadedAsync;
    }

    public bool RefreshRequested => _refreshRequested;

    private async void OnLoadedAsync(object sender, RoutedEventArgs e)
    {
        await ReloadAsync();
    }

    private async Task ReloadAsync()
    {
        try
        {
            SetStatus("正在加载设置...");
            var accounts = await _apiClient.GetAccountsAsync(CancellationToken.None);
            _accounts.Clear();
            foreach (var account in accounts.OrderBy(x => x.GroupLabel).ThenBy(x => x.DisplayTitle))
            {
                _accounts.Add(account);
            }
            _aiSettings = await _apiClient.GetAiSettingsAsync(CancellationToken.None);
            RenderAiSettings();
            await LoadSecurityAsync();
            await LoadKeywordRulesAsync();
            SetStatus("设置已加载");
        }
        catch (Exception ex)
        {
            SetStatus($"加载设置失败: {ex.Message}");
        }
    }

    private void RenderAiSettings()
    {
        _channels.Clear();
        foreach (var channel in _aiSettings.Channels)
        {
            _channels.Add(channel);
        }
        var selected = _channels.FirstOrDefault(x => x.Id == _aiSettings.DefaultModel.ChannelId) ?? _channels.FirstOrDefault();
        AiDefaultChannelComboBox.SelectedItem = selected;
        RenderModelsForChannel(selected);
        if (selected is not null)
        {
            AiDefaultModelComboBox.SelectedItem = selected.Models.FirstOrDefault(x => x == _aiSettings.DefaultModel.Model) ?? selected.Models.FirstOrDefault();
        }
    }

    private async Task LoadDeviceTokensAsync()
    {
        var tokens = await _apiClient.GetDeviceTokensAsync(CancellationToken.None);
        _deviceTokens.Clear();
        foreach (var token in tokens.OrderByDescending(x => x.CreatedAt))
        {
            _deviceTokens.Add(token);
        }
    }

    private async Task LoadSecurityAsync()
    {
        _securityStatus = await _apiClient.GetSecurityStatusAsync(CancellationToken.None);
        TotpStatusTextBlock.Text = _securityStatus.TotpEnabled ? "已开启。登录和敏感操作会要求动态验证码。" : "未开启。建议启用动态验证码。";
        _securitySessions.Clear();
        foreach (var item in _securityStatus.Sessions.OrderByDescending(x => x.IsCurrent).ThenByDescending(x => x.LastSeen))
        {
            _securitySessions.Add(item);
        }
        await LoadDeviceTokensAsync();
        var logs = await _apiClient.GetAuditLogsAsync(CancellationToken.None);
        _auditLogs.Clear();
        foreach (var log in logs)
        {
            _auditLogs.Add(log);
        }
    }

    private void AccountListBox_OnSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (AccountListBox.SelectedItem is not MailAccount account)
        {
            return;
        }
        if (account.Type == "local")
        {
            LocalAddressTextBox.Text = account.Address;
            LocalDisplayNameTextBox.Text = account.DisplayName;
            LocalSendNameTextBox.Text = account.SendName;
            LocalGroupTextBox.Text = account.Group;
            return;
        }
        _loadingExternalAccount = true;
        SelectComboItem(ExternalPresetComboBox, string.IsNullOrWhiteSpace(account.Provider) ? "auto" : account.Provider);
        ExternalEmailTextBox.Text = account.Address;
        ExternalDisplayNameTextBox.Text = account.DisplayName;
        ExternalSendNameTextBox.Text = account.SendName;
        ExternalGroupTextBox.Text = account.Group;
        ExternalHostTextBox.Text = account.Host;
        ExternalPortTextBox.Text = account.Port.ToString();
        ExternalSmtpHostTextBox.Text = account.SmtpHost;
        ExternalSmtpPortTextBox.Text = account.SmtpPort.ToString();
        _loadingExternalAccount = false;
    }

    private async Task LoadKeywordRulesAsync()
    {
        var rules = await _apiClient.GetKeywordRulesAsync(CancellationToken.None);
        _keywordRules.Clear();
        foreach (var rule in rules.OrderByDescending(item => item.Enabled).ThenBy(item => item.Name))
        {
            _keywordRules.Add(rule);
        }
        if (KeywordScopeComboBox.Items.Count > 0 && KeywordScopeComboBox.SelectedIndex < 0)
        {
            KeywordScopeComboBox.SelectedIndex = 0;
        }
        if (KeywordMatchModeComboBox.Items.Count > 0 && KeywordMatchModeComboBox.SelectedIndex < 0)
        {
            KeywordMatchModeComboBox.SelectedIndex = 0;
        }
    }

    private async void RefreshButton_OnClick(object sender, RoutedEventArgs e)
    {
        await ReloadAsync();
    }

    private async void SaveLocalButton_OnClick(object sender, RoutedEventArgs e)
    {
        var account = new MailAccount
        {
            Type = "local",
            Address = LocalAddressTextBox.Text.Trim(),
            DisplayName = LocalDisplayNameTextBox.Text.Trim(),
            SendName = LocalSendNameTextBox.Text.Trim(),
            Group = LocalGroupTextBox.Text.Trim(),
        };
        if (string.IsNullOrWhiteSpace(account.Address))
        {
            SetStatus("请填写本地邮箱地址");
            return;
        }
        try
        {
            var isEdit = _accounts.Any(x => x.Type == "local" && string.Equals(x.Address, account.Address, StringComparison.OrdinalIgnoreCase));
            await _apiClient.SaveLocalMailboxAsync(account, isEdit, CancellationToken.None);
            _refreshRequested = true;
            await ReloadAsync();
            SetStatus("本地邮箱已保存");
        }
        catch (Exception ex)
        {
            SetStatus($"保存本地邮箱失败: {ex.Message}");
        }
    }

    private async void DeleteLocalButton_OnClick(object sender, RoutedEventArgs e)
    {
        var address = LocalAddressTextBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(address))
        {
            SetStatus("请选择或填写要删除的本地邮箱");
            return;
        }
        await RunSensitiveAsync(
            () => _apiClient.DeleteLocalMailboxAsync(address, CancellationToken.None),
            "delete_mailbox",
            "本地邮箱已删除");
    }

    private async void SaveExternalButton_OnClick(object sender, RoutedEventArgs e)
    {
        var selected = AccountListBox.SelectedItem as MailAccount;
        var isEdit = selected?.Type == "external" && string.Equals(selected.Address, ExternalEmailTextBox.Text.Trim(), StringComparison.OrdinalIgnoreCase);
        var account = new MailAccount
        {
            Id = isEdit ? selected?.Id ?? string.Empty : string.Empty,
            Type = "external",
            Address = ExternalEmailTextBox.Text.Trim(),
            DisplayName = ExternalDisplayNameTextBox.Text.Trim(),
            SendName = ExternalSendNameTextBox.Text.Trim(),
            Group = ExternalGroupTextBox.Text.Trim(),
            Host = ExternalHostTextBox.Text.Trim(),
            Port = IntOrDefault(ExternalPortTextBox.Text, 993),
            SmtpHost = ExternalSmtpHostTextBox.Text.Trim(),
            SmtpPort = IntOrDefault(ExternalSmtpPortTextBox.Text, 465),
        };
        if (string.IsNullOrWhiteSpace(account.Address))
        {
            SetStatus("请填写外部邮箱地址");
            return;
        }
        if (!isEdit && string.IsNullOrWhiteSpace(ExternalPasswordBox.Password))
        {
            SetStatus("新增外部账号必须填写密码或授权码");
            return;
        }
        try
        {
            SetStatus("正在检查 IMAP/SMTP 并保存账号...");
            await _apiClient.SaveExternalAccountAsync(account, ExternalPasswordBox.Password, ComboValue(ExternalPresetComboBox, "auto"), isEdit, CancellationToken.None);
            ExternalPasswordBox.Password = string.Empty;
            _refreshRequested = true;
            await ReloadAsync();
            SetStatus("外部账号已保存");
        }
        catch (Exception ex)
        {
            SetStatus($"保存外部账号失败: {ex.Message}");
        }
    }

    private void ExternalPresetComboBox_OnSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_loadingExternalAccount)
        {
            return;
        }
        ApplyExternalPresetDefaults(ComboValue(ExternalPresetComboBox, "auto"));
    }

    private async void DeleteExternalButton_OnClick(object sender, RoutedEventArgs e)
    {
        if (AccountListBox.SelectedItem is not MailAccount account || account.Type != "external")
        {
            SetStatus("请选择要删除的外部账号");
            return;
        }
        await RunSensitiveAsync(
            () => _apiClient.DeleteExternalAccountAsync(account.Id, CancellationToken.None),
            "delete_external_account",
            "外部账号已删除");
    }

    private async void AddAiChannelButton_OnClick(object sender, RoutedEventArgs e)
    {
        try
        {
            SetStatus("正在拉取模型...");
            _aiSettings = await _apiClient.AddAiChannelAsync(
                AiNameTextBox.Text.Trim(),
                ComboValue(AiProviderComboBox, "openai_compatible"),
                AiBaseUrlTextBox.Text.Trim(),
                AiApiKeyPasswordBox.Password,
                CancellationToken.None);
            AiApiKeyPasswordBox.Password = string.Empty;
            RenderAiSettings();
            SetStatus("AI 渠道已新增");
        }
        catch (Exception ex)
        {
            SetStatus($"新增 AI 渠道失败: {ex.Message}");
        }
    }

    private async void DeleteAiChannelButton_OnClick(object sender, RoutedEventArgs e)
    {
        if (AiChannelListBox.SelectedItem is not AiChannel channel)
        {
            SetStatus("请选择要删除的 AI 渠道");
            return;
        }
        try
        {
            await _apiClient.DeleteAiChannelAsync(channel.Id, CancellationToken.None);
            _aiSettings = await _apiClient.GetAiSettingsAsync(CancellationToken.None);
            RenderAiSettings();
            SetStatus("AI 渠道已删除");
        }
        catch (Exception ex)
        {
            SetStatus($"删除 AI 渠道失败: {ex.Message}");
        }
    }

    private async void RefreshAiModelsButton_OnClick(object sender, RoutedEventArgs e)
    {
        var channel = AiDefaultChannelComboBox.SelectedItem as AiChannel ?? AiChannelListBox.SelectedItem as AiChannel;
        if (channel is null)
        {
            SetStatus("请选择要刷新的 AI 渠道");
            return;
        }
        try
        {
            SetStatus("正在刷新模型...");
            _aiSettings = await _apiClient.RefreshAiModelsAsync(channel.Id, CancellationToken.None);
            RenderAiSettings();
            SetStatus("模型已刷新");
        }
        catch (Exception ex)
        {
            SetStatus($"刷新模型失败: {ex.Message}");
        }
    }

    private async void SaveAiDefaultButton_OnClick(object sender, RoutedEventArgs e)
    {
        if (AiDefaultChannelComboBox.SelectedItem is not AiChannel channel || AiDefaultModelComboBox.SelectedItem is not string model)
        {
            SetStatus("请选择渠道和模型");
            return;
        }
        try
        {
            await _apiClient.SaveAiDefaultModelAsync(channel.Id, model, CancellationToken.None);
            _aiSettings.DefaultModel = new AiDefaultModel { ChannelId = channel.Id, Model = model };
            SetStatus("默认模型已保存");
        }
        catch (Exception ex)
        {
            SetStatus($"保存默认模型失败: {ex.Message}");
        }
    }

    private async void CreateDeviceTokenButton_OnClick(object sender, RoutedEventArgs e)
    {
        await RunSensitiveAsync(
            async () =>
            {
                var token = await _apiClient.CreateDeviceTokenAsync(DeviceTokenNameTextBox.Text.Trim(), CancellationToken.None);
                await LoadDeviceTokensAsync();
                Clipboard.SetText(token);
                MessageBox.Show(this, $"设备 Token 已创建并复制到剪贴板，只显示一次：\n\n{token}", "Memail", MessageBoxButton.OK, MessageBoxImage.Information);
            },
            "create_device_token",
            "设备 Token 已创建");
    }

    private async void RevokeDeviceTokenButton_OnClick(object sender, RoutedEventArgs e)
    {
        if ((sender as FrameworkElement)?.DataContext is not DeviceTokenInfo token)
        {
            return;
        }
        await RunSensitiveAsync(
            () => _apiClient.RevokeDeviceTokenAsync(token.Id, CancellationToken.None),
            "revoke_device_token",
            "设备 Token 已撤销");
    }

    private async void SetupTotpButton_OnClick(object sender, RoutedEventArgs e)
    {
        try
        {
            await SetupTotpWithConfirmationAsync();
        }
        catch (Exception ex)
        {
            SetStatus($"生成 TOTP 密钥失败: {ex.Message}");
        }
    }

    private async Task SetupTotpWithConfirmationAsync()
    {
        try
        {
            var setup = await _apiClient.SetupTotpAsync(CancellationToken.None);
            TotpSecretTextBox.Text = setup.Secret;
            if (!string.IsNullOrWhiteSpace(setup.OtpAuthUri))
            {
                Clipboard.SetText(setup.OtpAuthUri);
            }
            SetStatus("TOTP 密钥已生成，otpauth 地址已复制到剪贴板。请在验证器中添加后输入 6 位验证码启用。");
        }
        catch (MemailApiException ex) when (ex.RequireConfirmation)
        {
            var confirm = new SensitiveConfirmWindow(ex.Action, "save_settings") { Owner = this };
            if (confirm.ShowDialog() != true)
            {
                SetStatus("已取消二次确认");
                return;
            }
            await _apiClient.ConfirmSensitiveActionAsync(ex.Action, confirm.AdminPassword, confirm.TotpCode, CancellationToken.None);
            var setup = await _apiClient.SetupTotpAsync(CancellationToken.None);
            TotpSecretTextBox.Text = setup.Secret;
            if (!string.IsNullOrWhiteSpace(setup.OtpAuthUri))
            {
                Clipboard.SetText(setup.OtpAuthUri);
            }
            SetStatus("TOTP 密钥已生成，otpauth 地址已复制到剪贴板。");
        }
    }

    private async void EnableTotpButton_OnClick(object sender, RoutedEventArgs e)
    {
        var code = TotpCodeTextBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(code))
        {
            SetStatus("请输入验证器里的 6 位验证码");
            return;
        }
        try
        {
            await _apiClient.EnableTotpAsync(code, CancellationToken.None);
            TotpCodeTextBox.Text = string.Empty;
            TotpSecretTextBox.Text = string.Empty;
            await LoadSecurityAsync();
            SetStatus("2FA 已启用");
        }
        catch (Exception ex)
        {
            SetStatus($"启用 2FA 失败: {ex.Message}");
        }
    }

    private async void DisableTotpButton_OnClick(object sender, RoutedEventArgs e)
    {
        await RunSensitiveAsync(
            async () =>
            {
                await _apiClient.DisableTotpAsync(CancellationToken.None);
                await LoadSecurityAsync();
            },
            "save_settings",
            "2FA 已关闭");
    }

    private async void RevokeSessionButton_OnClick(object sender, RoutedEventArgs e)
    {
        if ((sender as FrameworkElement)?.DataContext is not SecuritySessionInfo session)
        {
            return;
        }
        if (session.IsCurrent)
        {
            SetStatus("不能在这里踢出当前会话，请使用主窗口退出登录。");
            return;
        }
        await RunSensitiveAsync(
            async () =>
            {
                await _apiClient.RevokeSessionAsync(session.Id, CancellationToken.None);
                await LoadSecurityAsync();
            },
            "revoke_session",
            "会话已踢出");
    }

    private void KeywordRuleListBox_OnSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (KeywordRuleListBox.SelectedItem is not KeywordRule rule)
        {
            return;
        }
        KeywordRuleIdTextBox.Text = rule.Id;
        KeywordRuleNameTextBox.Text = rule.Name;
        KeywordRuleKeywordsTextBox.Text = rule.KeywordText;
        KeywordRuleFieldsTextBox.Text = rule.FieldText;
        KeywordRuleGroupTextBox.Text = rule.ScopeGroup;
        KeywordRuleAccountsTextBox.Text = rule.ScopeAccountText;
        KeywordRuleEnabledCheckBox.IsChecked = rule.Enabled;
        SelectComboItem(KeywordMatchModeComboBox, rule.MatchMode);
        SelectComboItem(KeywordScopeComboBox, rule.ScopeType);
    }

    private async void SaveKeywordRuleButton_OnClick(object sender, RoutedEventArgs e)
    {
        var rule = new KeywordRule
        {
            Id = KeywordRuleIdTextBox.Text.Trim(),
            Name = KeywordRuleNameTextBox.Text.Trim(),
            KeywordText = KeywordRuleKeywordsTextBox.Text,
            MatchMode = ComboValue(KeywordMatchModeComboBox, "any"),
            ScopeType = ComboValue(KeywordScopeComboBox, "all"),
            ScopeGroup = KeywordRuleGroupTextBox.Text.Trim(),
            ScopeAccountText = KeywordRuleAccountsTextBox.Text,
            FieldText = KeywordRuleFieldsTextBox.Text,
            Enabled = KeywordRuleEnabledCheckBox.IsChecked != false,
        };
        if (string.IsNullOrWhiteSpace(rule.Name) || rule.Keywords.Count == 0)
        {
            SetStatus("规则名称和关键词不能为空");
            return;
        }
        try
        {
            var rules = await _apiClient.SaveKeywordRuleAsync(rule, CancellationToken.None);
            _keywordRules.Clear();
            foreach (var item in rules.OrderByDescending(x => x.Enabled).ThenBy(x => x.Name))
            {
                _keywordRules.Add(item);
            }
            ResetKeywordRuleForm();
            _refreshRequested = true;
            SetStatus("关键词规则已保存");
        }
        catch (Exception ex)
        {
            SetStatus($"保存关键词规则失败: {ex.Message}");
        }
    }

    private async void DeleteKeywordRuleButton_OnClick(object sender, RoutedEventArgs e)
    {
        var ruleId = KeywordRuleIdTextBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(ruleId) && KeywordRuleListBox.SelectedItem is KeywordRule selected)
        {
            ruleId = selected.Id;
        }
        if (string.IsNullOrWhiteSpace(ruleId))
        {
            SetStatus("请选择要删除的关键词规则");
            return;
        }
        try
        {
            var rules = await _apiClient.DeleteKeywordRuleAsync(ruleId, CancellationToken.None);
            _keywordRules.Clear();
            foreach (var item in rules.OrderByDescending(x => x.Enabled).ThenBy(x => x.Name))
            {
                _keywordRules.Add(item);
            }
            ResetKeywordRuleForm();
            _refreshRequested = true;
            SetStatus("关键词规则已删除");
        }
        catch (Exception ex)
        {
            SetStatus($"删除关键词规则失败: {ex.Message}");
        }
    }

    private void ResetKeywordRuleButton_OnClick(object sender, RoutedEventArgs e)
    {
        ResetKeywordRuleForm();
    }

    private void ResetKeywordRuleForm()
    {
        KeywordRuleIdTextBox.Text = string.Empty;
        KeywordRuleNameTextBox.Text = string.Empty;
        KeywordRuleKeywordsTextBox.Text = string.Empty;
        KeywordRuleFieldsTextBox.Text = "subject,from,intro";
        KeywordRuleGroupTextBox.Text = string.Empty;
        KeywordRuleAccountsTextBox.Text = string.Empty;
        KeywordRuleEnabledCheckBox.IsChecked = true;
        SelectComboItem(KeywordMatchModeComboBox, "any");
        SelectComboItem(KeywordScopeComboBox, "all");
    }

    private void AiDefaultChannelComboBox_OnSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        RenderModelsForChannel(AiDefaultChannelComboBox.SelectedItem as AiChannel);
    }

    private void AiChannelListBox_OnSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (AiChannelListBox.SelectedItem is AiChannel channel)
        {
            AiDefaultChannelComboBox.SelectedItem = channel;
        }
    }

    private void RenderModelsForChannel(AiChannel? channel)
    {
        AiDefaultModelComboBox.ItemsSource = channel?.Models ?? [];
        if (channel?.Models.Count > 0)
        {
            AiDefaultModelComboBox.SelectedItem = channel.Models[0];
        }
    }

    private async Task RunSensitiveAsync(Func<Task> action, string fallbackAction, string successMessage)
    {
        try
        {
            await action();
            _refreshRequested = true;
            await ReloadAsync();
            SetStatus(successMessage);
        }
        catch (MemailApiException ex) when (ex.RequireConfirmation)
        {
            var confirm = new SensitiveConfirmWindow(ex.Action, fallbackAction) { Owner = this };
            if (confirm.ShowDialog() != true)
            {
                SetStatus("已取消二次确认");
                return;
            }
            try
            {
                await _apiClient.ConfirmSensitiveActionAsync(ex.Action, confirm.AdminPassword, confirm.TotpCode, CancellationToken.None);
                await action();
                _refreshRequested = true;
                await ReloadAsync();
                SetStatus(successMessage);
            }
            catch (Exception confirmEx)
            {
                SetStatus($"二次确认后操作失败: {confirmEx.Message}");
            }
        }
        catch (Exception ex)
        {
            SetStatus($"操作失败: {ex.Message}");
        }
    }

    private static string ComboValue(ComboBox comboBox, string fallback)
    {
        if (comboBox.SelectedItem is ComboBoxItem item)
        {
            return item.Content?.ToString() ?? fallback;
        }
        return comboBox.Text.Trim() is { Length: > 0 } value ? value : fallback;
    }

    private static void SelectComboItem(ComboBox comboBox, string value)
    {
        foreach (var item in comboBox.Items.OfType<ComboBoxItem>())
        {
            if (string.Equals(item.Content?.ToString(), value, StringComparison.OrdinalIgnoreCase))
            {
                comboBox.SelectedItem = item;
                return;
            }
        }
        comboBox.SelectedIndex = 0;
    }

    private void ApplyExternalPresetDefaults(string preset)
    {
        switch (preset.Trim().ToLowerInvariant())
        {
            case "gmail":
                SetExternalServerDefaults("imap.gmail.com", 993, "smtp.gmail.com", 465);
                break;
            case "outlook":
                SetExternalServerDefaults("outlook.office365.com", 993, "smtp-mail.outlook.com", 587);
                break;
            case "qq":
                SetExternalServerDefaults("imap.qq.com", 993, "smtp.qq.com", 465);
                break;
            case "163":
                SetExternalServerDefaults("imap.163.com", 993, "smtp.163.com", 465);
                break;
            case "mxroute":
                SetExternalServerDefaults("glacier.mxrouting.net", 993, "glacier.mxrouting.net", 465);
                break;
        }
    }

    private void SetExternalServerDefaults(string imapHost, int imapPort, string smtpHost, int smtpPort)
    {
        ExternalHostTextBox.Text = imapHost;
        ExternalPortTextBox.Text = imapPort.ToString();
        ExternalSmtpHostTextBox.Text = smtpHost;
        ExternalSmtpPortTextBox.Text = smtpPort.ToString();
    }

    private static int IntOrDefault(string value, int fallback)
    {
        return int.TryParse(value, out var parsed) ? parsed : fallback;
    }

    private void SetStatus(string text)
    {
        StatusTextBlock.Text = text;
    }
}
