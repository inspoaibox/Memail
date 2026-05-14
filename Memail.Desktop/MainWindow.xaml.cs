using System.Collections.ObjectModel;
using System.Net;
using System.Text;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Input;
using System.Windows.Threading;
using Microsoft.Win32;
using Memail.Desktop.Models;
using Memail.Desktop.Services;

namespace Memail.Desktop;

public partial class MainWindow : Window
{
    private readonly AppConfigService _configService = new();
    private readonly MailCacheService _mailCacheService = new();
    private readonly MemailApiClient _apiClient;
    private readonly ObservableCollection<MailAccount> _accounts = [];
    private readonly ObservableCollection<MailFolder> _folders = [];
    private readonly ObservableCollection<MailItem> _mails = [];
    private readonly List<MailAccount> _realAccounts = [];
    private readonly List<KeywordRule> _keywordRules = [];

    private MailAccount? _selectedAccount;
    private MailFolder? _selectedFolder;
    private MailItem? _selectedMail;
    private MailDetail? _selectedDetail;
    private int _currentPage = 1;
    private int _pageSize = 50;
    private int _totalMessages;
    private string _searchQuery = string.Empty;
    private bool _webViewReady;
    private readonly DispatcherTimer _autoRefreshTimer = new();
    private CancellationTokenSource? _mailLoadCts;
    private CancellationTokenSource? _detailLoadCts;
    private int _mailLoadVersion;
    private int _detailLoadVersion;
    private bool _isLoadingMail;
    private bool _suppressSelectionChanged;

    public MainWindow(MemailApiClient apiClient)
    {
        _apiClient = apiClient;
        InitializeComponent();
        Title = "Memail Desktop";
        var accountView = CollectionViewSource.GetDefaultView(_accounts);
        accountView.GroupDescriptions.Add(new PropertyGroupDescription(nameof(MailAccount.GroupLabel)));
        AccountsList.ItemsSource = accountView;
        FolderList.ItemsSource = _folders;
        MailList.ItemsSource = _mails;
        _autoRefreshTimer.Tick += AutoRefreshTimer_OnTick;
        Loaded += OnLoadedAsync;
        Closed += OnClosed;
    }

    private async void OnLoadedAsync(object sender, RoutedEventArgs e)
    {
        var config = _configService.Load();
        _pageSize = config.PageSize is 10 or 20 or 50 or 100 ? config.PageSize : 50;
        AutoRefreshCheckBox.IsChecked = config.AutoRefreshEnabled;
        ConfigureAutoRefreshTimer(config.AutoRefreshSeconds);
        SelectPageSizeComboItem(_pageSize);
        if (string.IsNullOrWhiteSpace(_apiClient.BaseUrl))
        {
            _apiClient.Configure(config.BaseUrl);
        }
        await TryInitializeWebViewAsync();
        await LoadAccountsAsync();
        if (config.AutoRefreshEnabled)
        {
            _autoRefreshTimer.Start();
        }
    }

    private void OnClosed(object? sender, EventArgs e)
    {
        _autoRefreshTimer.Stop();
        _mailLoadCts?.Cancel();
        _detailLoadCts?.Cancel();
    }

    private async Task TryInitializeWebViewAsync()
    {
        try
        {
            await MailBodyWebView.EnsureCoreWebView2Async();
            MailBodyWebView.CoreWebView2.Settings.AreDefaultContextMenusEnabled = true;
            MailBodyWebView.CoreWebView2.Settings.AreDevToolsEnabled = false;
            MailBodyWebView.CoreWebView2.Settings.IsScriptEnabled = false;
            MailBodyWebView.CoreWebView2.Settings.AreHostObjectsAllowed = false;
            MailBodyWebView.CoreWebView2.Settings.AreDefaultScriptDialogsEnabled = false;
            _webViewReady = true;
        }
        catch
        {
            _webViewReady = false;
            MailBodyWebView.Visibility = Visibility.Collapsed;
            MailBodyFallbackText.Visibility = Visibility.Visible;
        }
    }

    private async Task LoadAccountsAsync(bool reloadFolders = true)
    {
        try
        {
            SetStatus("正在拉取账号...");
            var selectedKey = _selectedAccount is null ? string.Empty : AccountKey(_selectedAccount);
            var items = await _apiClient.GetAccountsAsync(CancellationToken.None);
            var keywordRules = await TryLoadKeywordRulesAsync();
            _realAccounts.Clear();
            _realAccounts.AddRange(items.Where(IsRealAccount));
            _keywordRules.Clear();
            _keywordRules.AddRange(keywordRules.Where(rule => rule.Enabled));
            _accounts.Clear();
            foreach (var item in BuildAccountTree(items, _keywordRules))
            {
                _accounts.Add(item);
            }
            _selectedAccount = _accounts.FirstOrDefault(item => AccountKey(item) == selectedKey) ?? _accounts.FirstOrDefault();
            _suppressSelectionChanged = true;
            try
            {
                AccountsList.SelectedItem = _selectedAccount;
            }
            finally
            {
                _suppressSelectionChanged = false;
            }
            if (reloadFolders && _selectedAccount is not null)
            {
                await LoadFoldersAsync(_selectedAccount);
            }
            SetStatus($"已加载 {_realAccounts.Count} 个账号");
        }
        catch (Exception ex)
        {
            SetStatus($"加载账号失败: {ex.Message}");
        }
    }

    private async Task<List<KeywordRule>> TryLoadKeywordRulesAsync()
    {
        try
        {
            return await _apiClient.GetKeywordRulesAsync(CancellationToken.None);
        }
        catch
        {
            return [];
        }
    }

    private async Task LoadFoldersAsync(MailAccount account)
    {
        var accountKey = AccountKey(account);
        try
        {
            _folders.Clear();
            ClearDetail();
            var folders = account.IsAggregate
                ? await _apiClient.GetAggregateFoldersAsync(CancellationToken.None)
                : account.Type == "external"
                ? await _apiClient.GetExternalFoldersAsync(account.Id, CancellationToken.None)
                : await _apiClient.GetLocalFoldersAsync(CancellationToken.None);
            if (account.IsAggregate)
            {
                var unread = AccountsForAggregate(account).Sum(item => item.UnreadCount);
                var unreadFolder = folders.FirstOrDefault(item => item.Key == "unread");
                if (unreadFolder is not null)
                {
                    unreadFolder.Count = account.AggregateScope == "keyword" ? 0 : unread;
                }
            }
            foreach (var folder in folders)
            {
                _folders.Add(folder);
            }
            if (_selectedAccount is null || AccountKey(_selectedAccount) != accountKey)
            {
                return;
            }
            _selectedFolder = _folders.FirstOrDefault();
            _suppressSelectionChanged = true;
            try
            {
                FolderList.SelectedItem = _selectedFolder;
            }
            finally
            {
                _suppressSelectionChanged = false;
            }
            if (_selectedFolder is not null)
            {
                await LoadMailPageAsync(1);
            }
        }
        catch (Exception ex)
        {
            SetStatus($"加载文件夹失败: {ex.Message}");
        }
    }

    private async Task LoadMailPageAsync(int page)
    {
        if (_selectedAccount is null || _selectedFolder is null)
        {
            return;
        }

        _mailLoadCts?.Cancel();
        _mailLoadCts = new CancellationTokenSource();
        var token = _mailLoadCts.Token;
        var version = ++_mailLoadVersion;
        var account = _selectedAccount;
        var folder = _selectedFolder;
        var accountKey = AccountKey(account);
        var cacheKey = BuildMailCacheKey(account, folder, _searchQuery);

        try
        {
            _isLoadingMail = true;
            UpdateNavigationState();
            _currentPage = Math.Max(1, page);
            SetStatus($"正在加载 {folder.Title}...");
            (List<MailItem> Items, int Total) result;
            if (!string.IsNullOrWhiteSpace(_searchQuery))
            {
                result = account.IsAggregate
                    ? await QueryAggregateOrKeywordAsync(account, folder.Key, _searchQuery, _currentPage, _pageSize, token)
                    : account.Type == "external"
                    ? await _apiClient.SearchExternalAsync(account.Id, folder.Key, _searchQuery, _currentPage, _pageSize, token)
                    : await _apiClient.SearchLocalAsync(account.Address, _searchQuery, _currentPage, _pageSize, token);
            }
            else if (account.IsAggregate)
            {
                result = await QueryAggregateOrKeywordAsync(account, folder.Key, string.Empty, _currentPage, _pageSize, token);
            }
            else if (account.Type == "external")
            {
                result = await _apiClient.QueryExternalInboxAsync(account.Id, folder.Key, _currentPage, _pageSize, token);
            }
            else
            {
                result = await _apiClient.QueryLocalInboxAsync(account.Address, folder.Key, _currentPage, _pageSize, token);
            }

            if (version != _mailLoadVersion || token.IsCancellationRequested || _selectedAccount is null || _selectedFolder is null || AccountKey(_selectedAccount) != accountKey || _selectedFolder.Key != folder.Key)
            {
                return;
            }

            _totalMessages = result.Total;
            await _apiClient.ApplyMessageMetaAsync(account, result.Items, token);
            _mails.Clear();
            foreach (var item in result.Items.OrderByDescending(x => x.Pinned).ThenByDescending(x => x.Date))
            {
                if (string.IsNullOrWhiteSpace(item.AccountLabel))
                {
                    item.AccountLabel = account.IsAggregate ? SourceAccountLabel(item) : account.DisplayTitle;
                }
                _mails.Add(item);
            }

            UpdatePaginationText();
            _mailCacheService.Save(cacheKey, _currentPage, _pageSize, _totalMessages, _mails);
            SetStatus($"已加载 {result.Items.Count} 封邮件");
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception ex)
        {
            if (TryLoadMailPageFromCache(cacheKey, _currentPage, _pageSize, account, out var cachedAt))
            {
                SetStatus($"网络加载失败，已显示本地缓存（{cachedAt:MM/dd HH:mm}）：{ex.Message}");
            }
            else
            {
                SetStatus($"加载邮件失败: {ex.Message}");
            }
        }
        finally
        {
            if (version == _mailLoadVersion)
            {
                _isLoadingMail = false;
                UpdateNavigationState();
            }
        }
    }

    private void UpdatePaginationText()
    {
        var pages = Math.Max(1, (int)Math.Ceiling(_totalMessages / Math.Max(1, (double)_pageSize)));
        MailListHeaderText.Text = $"第 {_currentPage} / {pages} 页  共 {_totalMessages} 封";
        UpdateNavigationState();
    }

    private async Task LoadSelectedMailDetailAsync(MailItem item)
    {
        var account = ResolveMailAccount(item);
        if (account is null)
        {
            RenderTextBody("无法定位邮件来源账号。");
            return;
        }

        _detailLoadCts?.Cancel();
        _detailLoadCts = new CancellationTokenSource();
        var token = _detailLoadCts.Token;
        var version = ++_detailLoadVersion;
        var itemId = item.Id;
        var folderKey = item.Folder;

        try
        {
            _selectedMail = item;
            _selectedDetail = null;
            MailFromText.Text = $"发件人: {item.From}";
            MailSubjectText.Text = $"主题: {item.Subject}";
            MailMetaText.Text = item.Date?.ToString("yyyy/MM/dd HH:mm:ss") ?? string.Empty;
            AttachmentList.ItemsSource = null;
            RenderTextBody("正在加载...");

            var detail = account.Type == "local" && (item.Folder == "drafts" || item.Folder == "outbox")
                ? BuildLocalStoredDetail(item)
                : account.Type == "external"
                ? await _apiClient.GetExternalDetailAsync(account.Id, item.Folder, item.Id, token)
                : await _apiClient.GetLocalDetailAsync(account.Address, item.Id, item.Folder == "sent", item.Folder == "trash", token);

            if (version != _detailLoadVersion || token.IsCancellationRequested || _selectedMail?.Id != itemId || _selectedMail?.Folder != folderKey)
            {
                return;
            }

            if (detail is null)
            {
                RenderTextBody("无法读取邮件详情。");
                return;
            }

            _selectedDetail = detail;
            await MarkOpenedMailSeenAsync(item);
            MailFromText.Text = $"发件人: {detail.From}";
            MailSubjectText.Text = $"主题: {detail.Subject}";
            MailMetaText.Text = BuildMetaLine(detail);
            AttachmentList.ItemsSource = detail.Attachments;
            RenderMailBody(detail);
            UpdateActionButtonText();
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception ex)
        {
            RenderTextBody($"加载详情失败: {ex.Message}");
        }
    }

    private static string BuildMetaLine(MailDetail detail)
    {
        var parts = new List<string>();
        if (!string.IsNullOrWhiteSpace(detail.To)) parts.Add($"收件人: {detail.To}");
        if (!string.IsNullOrWhiteSpace(detail.Cc)) parts.Add($"抄送: {detail.Cc}");
        if (detail.Date is not null) parts.Add($"时间: {detail.Date:yyyy/MM/dd HH:mm:ss}");
        return string.Join("    ", parts);
    }

    private void RenderMailBody(MailDetail detail)
    {
        if (!string.IsNullOrWhiteSpace(detail.Html) && _webViewReady)
        {
            MailBodyFallbackText.Visibility = Visibility.Collapsed;
            MailBodyWebView.Visibility = Visibility.Visible;
            MailBodyWebView.NavigateToString(WrapMailHtml(detail.Html));
            return;
        }

        RenderTextBody(!string.IsNullOrWhiteSpace(detail.Text) ? detail.Text : detail.Html);
    }

    private void RenderTextBody(string text)
    {
        MailBodyWebView.Visibility = Visibility.Collapsed;
        MailBodyFallbackText.Visibility = Visibility.Visible;
        MailBodyFallbackText.Text = text;
    }

    private static string WrapMailHtml(string body)
    {
        return $$"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
html,body{margin:0;padding:0;background:#fbfcfd;color:#1f2a33;font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,"Microsoft YaHei",sans-serif;}
body{padding:16px;overflow-wrap:anywhere;}
img{max-width:100%;height:auto;}
table{max-width:100%;border-collapse:collapse;}
td,th{overflow-wrap:anywhere;word-break:break-word;}
pre{white-space:pre-wrap;overflow-wrap:anywhere;}
a{color:#1b6a78;}
</style>
</head>
<body>{{body}}</body>
</html>
""";
    }

    private async void AccountsList_OnSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_suppressSelectionChanged)
        {
            return;
        }
        if (AccountsList.SelectedItem is not MailAccount account)
        {
            return;
        }

        _selectedAccount = account;
        _currentPage = 1;
        _searchQuery = string.Empty;
        SearchTextBox.Text = string.Empty;
        await LoadFoldersAsync(account);
    }

    private async void FolderList_OnSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_suppressSelectionChanged)
        {
            return;
        }
        if (FolderList.SelectedItem is not MailFolder folder)
        {
            return;
        }

        _selectedFolder = folder;
        _currentPage = 1;
        _searchQuery = string.Empty;
        SearchTextBox.Text = string.Empty;
        await LoadMailPageAsync(1);
    }

    private async void MailList_OnSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (MailList.SelectedItem is MailItem item)
        {
            await LoadSelectedMailDetailAsync(item);
        }
    }

    private async void RefreshButton_OnClick(object sender, RoutedEventArgs e)
    {
        await RefreshCurrentViewAsync(forceExternalSync: true);
    }

    private async void PrevPageButton_OnClick(object sender, RoutedEventArgs e)
    {
        if (_currentPage > 1)
        {
            await LoadMailPageAsync(_currentPage - 1);
        }
    }

    private async void NextPageButton_OnClick(object sender, RoutedEventArgs e)
    {
        var pages = Math.Max(1, (int)Math.Ceiling(_totalMessages / Math.Max(1, (double)_pageSize)));
        if (_currentPage < pages)
        {
            await LoadMailPageAsync(_currentPage + 1);
        }
    }

    private async void PageSizeComboBox_OnSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (PageSizeComboBox.SelectedItem is ComboBoxItem item && int.TryParse(item.Content?.ToString(), out var size))
        {
            _pageSize = size;
            var config = _configService.Load();
            config.PageSize = _pageSize;
            _configService.Save(config);
            if (IsLoaded)
            {
                await LoadMailPageAsync(1);
            }
        }
    }

    private async void AutoRefreshCheckBox_OnChanged(object sender, RoutedEventArgs e)
    {
        if (!IsLoaded)
        {
            return;
        }
        var enabled = AutoRefreshCheckBox.IsChecked == true;
        var config = _configService.Load();
        config.AutoRefreshEnabled = enabled;
        _configService.Save(config);
        if (enabled)
        {
            ConfigureAutoRefreshTimer(config.AutoRefreshSeconds);
            _autoRefreshTimer.Start();
            await RefreshCurrentViewAsync(forceExternalSync: false, silent: true);
        }
        else
        {
            _autoRefreshTimer.Stop();
            SetStatus("自动刷新已关闭");
        }
    }

    private async void AutoRefreshTimer_OnTick(object? sender, EventArgs e)
    {
        await RefreshCurrentViewAsync(forceExternalSync: false, silent: true);
    }

    private void ConfigureAutoRefreshTimer(int seconds)
    {
        var normalized = Math.Clamp(seconds, 30, 600);
        _autoRefreshTimer.Interval = TimeSpan.FromSeconds(normalized);
    }

    private async Task RefreshCurrentViewAsync(bool forceExternalSync, bool silent = false)
    {
        if (_selectedAccount is null || _selectedFolder is null || _isLoadingMail)
        {
            return;
        }
        var selectedAccountKey = AccountKey(_selectedAccount);
        var selectedFolderKey = _selectedFolder.Key;
        try
        {
            var externalAccountIds = _realAccounts
                .Where(account => account.Type == "external")
                .Select(account => account.Id)
                .Where(id => !string.IsNullOrWhiteSpace(id))
                .ToList();
            if (externalAccountIds.Count > 0)
            {
                if (!silent)
                {
                    SetStatus(forceExternalSync ? "正在同步外部邮箱..." : "已提交后台同步...");
                }
                if (forceExternalSync && _selectedAccount.Type == "external")
                {
                    await _apiClient.SyncExternalAccountAsync(_selectedAccount.Id, background: false, force: true, CancellationToken.None);
                }
                else
                {
                    await _apiClient.SyncExternalAccountsAsync(externalAccountIds, background: true, force: false, CancellationToken.None);
                }
            }
            await LoadAccountsAsync(reloadFolders: false);
            if (_selectedAccount is null || AccountKey(_selectedAccount) != selectedAccountKey)
            {
                if (_selectedAccount is not null)
                {
                    await LoadFoldersAsync(_selectedAccount);
                }
                return;
            }

            var refreshedFolder = _folders.FirstOrDefault(folder => folder.Key == selectedFolderKey);
            if (refreshedFolder is null)
            {
                await LoadFoldersAsync(_selectedAccount);
                return;
            }

            _selectedFolder = refreshedFolder;
            _suppressSelectionChanged = true;
            try
            {
                FolderList.SelectedItem = refreshedFolder;
            }
            finally
            {
                _suppressSelectionChanged = false;
            }

            await LoadMailPageAsync(_currentPage);
            if (!silent)
            {
                SetStatus("刷新完成");
            }
        }
        catch (Exception ex)
        {
            SetStatus($"刷新失败: {ex.Message}");
        }
    }

    private async void SearchButton_OnClick(object sender, RoutedEventArgs e)
    {
        _searchQuery = SearchTextBox.Text.Trim();
        await LoadMailPageAsync(1);
    }

    private async void SearchTextBox_OnKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key != Key.Enter)
        {
            return;
        }
        e.Handled = true;
        _searchQuery = SearchTextBox.Text.Trim();
        await LoadMailPageAsync(1);
    }

    private async void FavoriteButton_OnClick(object sender, RoutedEventArgs e)
    {
        var account = ResolveMailAccount(_selectedMail);
        if (account is null || _selectedMail is null)
        {
            return;
        }
        try
        {
            var nextFavorite = !_selectedMail.Favorite;
            await _apiClient.UpdateMessageMetaAsync(account, _selectedMail, favorite: nextFavorite);
            _selectedMail.Favorite = nextFavorite;
            UpdateActionButtonText();
            SetStatus(_selectedMail.Favorite ? "已收藏" : "已取消收藏");
        }
        catch (Exception ex)
        {
            SetStatus($"收藏操作失败: {ex.Message}");
        }
    }

    private async void PinButton_OnClick(object sender, RoutedEventArgs e)
    {
        var account = ResolveMailAccount(_selectedMail);
        if (account is null || _selectedMail is null)
        {
            return;
        }
        try
        {
            var nextPinned = !_selectedMail.Pinned;
            await _apiClient.UpdateMessageMetaAsync(account, _selectedMail, pinned: nextPinned);
            _selectedMail.Pinned = nextPinned;
            UpdateActionButtonText();
            SetStatus(_selectedMail.Pinned ? "已置顶" : "已取消置顶");
        }
        catch (Exception ex)
        {
            SetStatus($"置顶操作失败: {ex.Message}");
        }
    }

    private async void ReadButton_OnClick(object sender, RoutedEventArgs e)
    {
        var account = ResolveMailAccount(_selectedMail);
        if (account is null || _selectedMail is null)
        {
            return;
        }
        try
        {
            var nextSeen = !_selectedMail.Seen;
            if (account.Type == "external")
            {
                await _apiClient.SetExternalFlagAsync(account.Id, _selectedMail.Folder, _selectedMail.Id, "\\Seen", nextSeen, CancellationToken.None);
            }
            else
            {
                await _apiClient.MarkLocalReadAsync(account.Address, _selectedMail.Id, nextSeen, CancellationToken.None);
            }
            _selectedMail.Seen = nextSeen;
            UpdateActionButtonText();
            SetStatus(nextSeen ? "已标记为已读" : "已标记为未读");
        }
        catch (Exception ex)
        {
            SetStatus($"标记失败: {ex.Message}");
        }
    }

    private async void MarkPageReadButton_OnClick(object sender, RoutedEventArgs e)
    {
        await BatchMarkCurrentPageAsync(true);
    }

    private async void MarkPageUnreadButton_OnClick(object sender, RoutedEventArgs e)
    {
        await BatchMarkCurrentPageAsync(false);
    }

    private async Task BatchMarkCurrentPageAsync(bool seen)
    {
        if (_selectedAccount is null || _selectedFolder is null || _mails.Count == 0)
        {
            return;
        }
        try
        {
            var targets = _mails.Where(mail => mail.Seen != seen).ToList();
            if (targets.Count == 0)
            {
                SetStatus(seen ? "当前页已经全是已读" : "当前页已经全是未读");
                return;
            }
            if (_selectedAccount.IsAggregate)
            {
                foreach (var group in targets.GroupBy(mail => $"{mail.AccountType}|{mail.AccountId}|{mail.Folder}"))
                {
                    var sample = group.First();
                    var account = ResolveMailAccount(sample);
                    if (account is null)
                    {
                        continue;
                    }
                    if (account.Type == "external")
                    {
                        await _apiClient.BatchExternalAsync(account.Id, sample.Folder, group.Select(mail => mail.Id), seen ? "read" : "unread", CancellationToken.None);
                    }
                    else
                    {
                        await _apiClient.BatchLocalInboxAsync(account.Address, group.Select(mail => mail.Id), seen ? "mark_read" : "mark_unread", CancellationToken.None);
                    }
                }
            }
            else if (_selectedAccount.Type == "external")
            {
                foreach (var group in targets.GroupBy(mail => mail.Folder))
                {
                    await _apiClient.BatchExternalAsync(_selectedAccount.Id, group.Key, group.Select(mail => mail.Id), seen ? "read" : "unread", CancellationToken.None);
                }
            }
            else
            {
                if (_selectedFolder.Key is not ("inbox" or "all" or "unread"))
                {
                    SetStatus("当前本地文件夹不支持批量已读/未读");
                    return;
                }
                await _apiClient.BatchLocalInboxAsync(_selectedAccount.Address, targets.Select(mail => mail.Id), seen ? "mark_read" : "mark_unread", CancellationToken.None);
            }
            foreach (var mail in targets)
            {
                mail.Seen = seen;
            }
            await LoadMailPageAsync(seen && _selectedFolder?.Key is "unread" or "__memail_unread__" ? 1 : _currentPage);
            SetStatus(seen ? "当前页已全部标为已读" : "当前页已全部标为未读");
        }
        catch (Exception ex)
        {
            SetStatus($"批量标记失败: {ex.Message}");
        }
    }

    private async void DeleteCurrentMailButton_OnClick(object sender, RoutedEventArgs e)
    {
        var account = ResolveMailAccount(_selectedMail);
        if (account is null || _selectedMail is null)
        {
            return;
        }
        try
        {
            SetStatus("正在删除邮件...");
            if (account.Type == "external")
            {
                await _apiClient.DeleteExternalMailAsync(account.Id, _selectedMail.Folder, _selectedMail.Id, CancellationToken.None);
            }
            else
            {
                if (_selectedMail.Folder is not ("inbox" or "all" or "unread"))
                {
                    SetStatus("当前本地文件夹不支持删除，请在收件箱或回收站使用对应操作");
                    return;
                }
                await _apiClient.DeleteLocalMailAsync(account.Address, _selectedMail.Id, CancellationToken.None);
            }
            ClearDetail();
            await LoadMailPageAsync(_currentPage);
            SetStatus("邮件已删除");
        }
        catch (Exception ex)
        {
            SetStatus($"删除失败: {ex.Message}");
        }
    }

    private async void RestoreCurrentMailButton_OnClick(object sender, RoutedEventArgs e)
    {
        var account = ResolveMailAccount(_selectedMail);
        if (account is null || _selectedMail is null)
        {
            return;
        }
        if (account.Type != "local" || _selectedMail.Folder != "trash")
        {
            SetStatus("只有本地回收站邮件支持恢复");
            return;
        }
        try
        {
            await _apiClient.RestoreLocalMailAsync(account.Address, _selectedMail.Id, CancellationToken.None);
            ClearDetail();
            await LoadMailPageAsync(_currentPage);
            SetStatus("邮件已恢复");
        }
        catch (Exception ex)
        {
            SetStatus($"恢复失败: {ex.Message}");
        }
    }

    private async void PermanentDeleteCurrentMailButton_OnClick(object sender, RoutedEventArgs e)
    {
        var account = ResolveMailAccount(_selectedMail);
        if (account is null || _selectedMail is null)
        {
            return;
        }
        if (account.Type != "local" || _selectedMail.Folder != "trash")
        {
            SetStatus("只有本地回收站邮件支持彻底删除");
            return;
        }
        try
        {
            await _apiClient.PermanentDeleteLocalMailAsync(account.Address, _selectedMail.Id, CancellationToken.None);
            ClearDetail();
            await LoadMailPageAsync(_currentPage);
            SetStatus("邮件已彻底删除");
        }
        catch (MemailApiException ex) when (ex.RequireConfirmation)
        {
            await RunSensitiveActionAsync(
                ex,
                "permanent_delete",
                () => _apiClient.PermanentDeleteLocalMailAsync(account.Address, _selectedMail.Id, CancellationToken.None),
                "邮件已彻底删除");
        }
        catch (Exception ex)
        {
            SetStatus($"彻底删除失败: {ex.Message}");
        }
    }

    private async void TranslateButton_OnClick(object sender, RoutedEventArgs e)
    {
        if (_selectedDetail is null)
        {
            return;
        }
        try
        {
            SetStatus("正在翻译...");
            var translation = await _apiClient.TranslateAsync(_selectedDetail, CancellationToken.None);
            if (!string.IsNullOrWhiteSpace(translation))
            {
                if (translation.TrimStart().StartsWith("<", StringComparison.Ordinal))
                {
                    if (_webViewReady)
                    {
                        MailBodyWebView.Visibility = Visibility.Visible;
                        MailBodyFallbackText.Visibility = Visibility.Collapsed;
                        MailBodyWebView.NavigateToString(WrapMailHtml(translation));
                    }
                    else
                    {
                        RenderTextBody(translation);
                    }
                }
                else
                {
                    RenderTextBody(translation);
                }
            }
            SetStatus("翻译完成");
        }
        catch (Exception ex)
        {
            SetStatus($"翻译失败: {ex.Message}");
        }
    }

    private void ComposeButton_OnClick(object sender, RoutedEventArgs e)
    {
        OpenCompose("new");
    }

    private async void SettingsButton_OnClick(object sender, RoutedEventArgs e)
    {
        var window = new Views.SettingsWindow(_apiClient) { Owner = this };
        window.ShowDialog();
        if (window.RefreshRequested)
        {
            await LoadAccountsAsync();
        }
    }

    private void EditDraftButton_OnClick(object sender, RoutedEventArgs e)
    {
        if (_selectedMail?.Folder != "drafts")
        {
            SetStatus("请选择草稿箱中的邮件再编辑");
            return;
        }
        OpenCompose("draft");
    }

    private async void RetryOutboxButton_OnClick(object sender, RoutedEventArgs e)
    {
        if (_selectedMail?.Folder != "outbox")
        {
            SetStatus("请选择发送失败记录再重试");
            return;
        }
        try
        {
            SetStatus("正在重试发送...");
            await _apiClient.RetryOutboxAsync(_selectedMail.Id, CancellationToken.None);
            SetStatus("重试发送成功");
            await LoadMailPageAsync(_currentPage);
        }
        catch (Exception ex)
        {
            SetStatus($"重试发送失败: {ex.Message}");
        }
    }

    private void LogoutButton_OnClick(object sender, RoutedEventArgs e)
    {
        var config = _configService.Load();
        config.RememberMe = false;
        config.AuthMode = "password";
        config.ProtectedDeviceToken = string.Empty;
        _configService.Save(config);
        var login = new Views.LoginWindow();
        login.Show();
        Close();
    }

    private void ReplyButton_OnClick(object sender, RoutedEventArgs e)
    {
        OpenCompose("reply");
    }

    private void ReplyAllButton_OnClick(object sender, RoutedEventArgs e)
    {
        OpenCompose("reply-all");
    }

    private void ForwardButton_OnClick(object sender, RoutedEventArgs e)
    {
        OpenCompose("forward");
    }

    private async void AttachmentButton_OnClick(object sender, RoutedEventArgs e)
    {
        var account = ResolveMailAccount(_selectedMail);
        if (account is null || _selectedMail is null || sender is not Button button || button.DataContext is not MailAttachment attachment)
        {
            return;
        }
        var filename = string.IsNullOrWhiteSpace(attachment.Filename) ? "attachment" : attachment.Filename;
        var dialog = new SaveFileDialog
        {
            FileName = filename,
            Title = "保存附件",
        };
        if (dialog.ShowDialog(this) != true)
        {
            return;
        }
        try
        {
            SetStatus("正在下载附件...");
            await _apiClient.DownloadAttachmentAsync(account, _selectedMail, attachment, dialog.FileName, CancellationToken.None);
            SetStatus("附件已保存");
        }
        catch (Exception ex)
        {
            SetStatus($"附件下载失败: {ex.Message}");
        }
    }

    private void OpenCompose(string mode)
    {
        var account = mode == "new" ? _selectedAccount : ResolveMailAccount(_selectedMail);
        if (account is null || account.IsAggregate)
        {
            SetStatus("请先选择一个真实邮箱账号");
            return;
        }
        var window = new Views.ComposeWindow(_apiClient, account, mode, _selectedDetail, _selectedMail);
        window.Owner = this;
        if (window.ShowDialog() == true)
        {
            _ = LoadMailPageAsync(_currentPage);
        }
    }

    private async Task MarkOpenedMailSeenAsync(MailItem item)
    {
        var account = ResolveMailAccount(item);
        if (account is null || item.Seen || item.Folder is "drafts" or "outbox")
        {
            return;
        }
        try
        {
            if (account.Type == "external")
            {
                await _apiClient.SetExternalFlagAsync(account.Id, item.Folder, item.Id, "\\Seen", true, CancellationToken.None);
            }
            else if (item.Folder is "inbox" or "all" or "unread")
            {
                await _apiClient.MarkLocalReadAsync(account.Address, item.Id, true, CancellationToken.None);
            }
            item.Seen = true;
        }
        catch
        {
            SetStatus("已打开邮件，但服务器未确认已读状态");
        }
    }

    private void UpdateActionButtonText()
    {
        var hasSelectedMail = _selectedMail is not null;
        var isDraft = _selectedMail?.Folder == "drafts";
        var isOutbox = _selectedMail?.Folder == "outbox";
        var actionAccount = ResolveMailAccount(_selectedMail);
        var isLocalTrash = actionAccount?.Type == "local" && _selectedMail?.Folder == "trash";
        if (_selectedMail is null)
        {
            FavoriteButton.Content = "收藏";
            PinButton.Content = "置顶";
            ReadButton.Content = "已读/未读";
            TranslateButton.IsEnabled = false;
            EditDraftButton.IsEnabled = false;
            RetryOutboxButton.IsEnabled = false;
            FavoriteButton.IsEnabled = false;
            PinButton.IsEnabled = false;
            ReadButton.IsEnabled = false;
            DeleteButton.IsEnabled = false;
            ListDeleteButton.IsEnabled = false;
            RestoreButton.IsEnabled = false;
            PermanentDeleteButton.IsEnabled = false;
            ReplyButton.IsEnabled = false;
            ReplyAllButton.IsEnabled = false;
            ForwardButton.IsEnabled = false;
            return;
        }
        FavoriteButton.Content = _selectedMail.Favorite ? "取消收藏" : "收藏";
        PinButton.Content = _selectedMail.Pinned ? "取消置顶" : "置顶";
        ReadButton.Content = _selectedMail.Seen ? "标为未读" : "标为已读";
        TranslateButton.IsEnabled = hasSelectedMail && !isDraft && !isOutbox;
        EditDraftButton.IsEnabled = isDraft;
        RetryOutboxButton.IsEnabled = isOutbox;
        FavoriteButton.IsEnabled = hasSelectedMail && !isDraft && !isOutbox;
        PinButton.IsEnabled = hasSelectedMail && !isDraft && !isOutbox;
        ReadButton.IsEnabled = hasSelectedMail && !isDraft && !isOutbox;
        var canDelete = hasSelectedMail
            && !isDraft
            && !isOutbox
            && (actionAccount?.Type == "external" || _selectedMail?.Folder is "inbox" or "all" or "unread");
        DeleteButton.IsEnabled = canDelete;
        ListDeleteButton.IsEnabled = canDelete;
        RestoreButton.IsEnabled = isLocalTrash;
        PermanentDeleteButton.IsEnabled = isLocalTrash;
        ReplyButton.IsEnabled = hasSelectedMail && !isDraft && !isOutbox;
        ReplyAllButton.IsEnabled = hasSelectedMail && !isDraft && !isOutbox;
        ForwardButton.IsEnabled = hasSelectedMail && !isDraft && !isOutbox;
    }

    private void ClearDetail()
    {
        _selectedMail = null;
        _selectedDetail = null;
        MailFromText.Text = string.Empty;
        MailSubjectText.Text = string.Empty;
        MailMetaText.Text = string.Empty;
        AttachmentList.ItemsSource = null;
        RenderTextBody("请选择一封邮件查看详情。");
        UpdateActionButtonText();
    }

    private void UpdateNavigationState()
    {
        var pages = Math.Max(1, (int)Math.Ceiling(_totalMessages / Math.Max(1, (double)_pageSize)));
        PrevPageButton.IsEnabled = !_isLoadingMail && _currentPage > 1;
        NextPageButton.IsEnabled = !_isLoadingMail && _currentPage < pages;
        SearchButton.IsEnabled = !_isLoadingMail;
        RefreshButton.IsEnabled = !_isLoadingMail;
        MarkPageReadButton.IsEnabled = !_isLoadingMail && _mails.Count > 0;
        MarkPageUnreadButton.IsEnabled = !_isLoadingMail && _mails.Count > 0;
    }

    private void SetStatus(string text)
    {
        StatusTextBlock.Text = text;
    }

    private static string AccountKey(MailAccount account)
    {
        return $"{account.Type}:{account.Id}:{account.Address}";
    }

    private static bool IsRealAccount(MailAccount account)
    {
        return account.Type is "local" or "external";
    }

    private static IEnumerable<MailAccount> BuildAccountTree(IReadOnlyCollection<MailAccount> accounts, IReadOnlyCollection<KeywordRule> keywordRules)
    {
        if (accounts.Count == 0)
        {
            return accounts;
        }

        var nodes = new List<MailAccount>
        {
            new()
            {
                Id = "all",
                Type = "aggregate",
                AggregateScope = "all",
                Address = "所有账号",
                DisplayName = "全部账号",
                Group = "智能视图",
                UnreadCount = accounts.Sum(account => account.UnreadCount),
            },
        };

        nodes.AddRange(accounts
            .Where(account => !string.IsNullOrWhiteSpace(account.GroupLabel))
            .GroupBy(account => account.GroupLabel)
            .OrderBy(group => group.Key, StringComparer.CurrentCultureIgnoreCase)
            .Select(group => new MailAccount
            {
                Id = $"group:{group.Key}",
                Type = "aggregate",
                AggregateScope = "group",
                AggregateGroup = group.Key,
                Address = group.Key,
                DisplayName = $"{group.Key} 汇总",
                Group = "智能视图",
                UnreadCount = group.Sum(account => account.UnreadCount),
            }));

        nodes.AddRange(keywordRules
            .Where(rule => rule.Enabled)
            .OrderBy(rule => rule.Name, StringComparer.CurrentCultureIgnoreCase)
            .Select(rule => new MailAccount
            {
                Id = $"keyword:{rule.Id}",
                Type = "aggregate",
                AggregateScope = "keyword",
                AggregateGroup = rule.Id,
                Address = "关键词规则",
                DisplayName = rule.Name,
                Group = "关键词分组",
            }));

        nodes.AddRange(accounts);
        return nodes;
    }

    private List<MailAccount> AccountsForAggregate(MailAccount account)
    {
        if (!account.IsAggregate)
        {
            return IsRealAccount(account) ? [account] : [];
        }
        if (account.AggregateScope == "group")
        {
            return _realAccounts
                .Where(item => string.Equals(item.GroupLabel, account.AggregateGroup, StringComparison.CurrentCultureIgnoreCase))
                .ToList();
        }
        if (account.AggregateScope == "keyword")
        {
            var rule = _keywordRules.FirstOrDefault(item => item.Id == account.AggregateGroup);
            return AccountsForKeywordRule(rule);
        }
        return _realAccounts.ToList();
    }

    private List<MailAccount> AccountsForKeywordRule(KeywordRule? rule)
    {
        if (rule is null || !rule.Enabled)
        {
            return [];
        }
        if (rule.ScopeType == "group")
        {
            return _realAccounts
                .Where(item => string.Equals(item.GroupLabel, rule.ScopeGroup, StringComparison.CurrentCultureIgnoreCase))
                .ToList();
        }
        if (rule.ScopeType == "accounts")
        {
            var keys = rule.ScopeAccounts.ToHashSet(StringComparer.OrdinalIgnoreCase);
            return _realAccounts
                .Where(item => keys.Contains($"{item.Type}:{(item.Type == "external" ? item.Id : item.Address)}")
                    || keys.Contains(item.Address)
                    || keys.Contains(item.Id))
                .ToList();
        }
        return _realAccounts.ToList();
    }

    private async Task<(List<MailItem> Items, int Total)> QueryAggregateOrKeywordAsync(
        MailAccount account,
        string folderKey,
        string query,
        int page,
        int pageSize,
        CancellationToken token)
    {
        if (account.AggregateScope != "keyword")
        {
            return await _apiClient.QueryAggregateAsync(AccountsForAggregate(account), folderKey, query, page, pageSize, token);
        }

        var rule = _keywordRules.FirstOrDefault(item => item.Id == account.AggregateGroup && item.Enabled);
        if (rule is null || rule.Keywords.Count == 0)
        {
            return ([], 0);
        }

        var accounts = AccountsForKeywordRule(rule);
        var keywordResult = await SearchKeywordRuleAsync(rule, accounts, folderKey, query, page, pageSize, token);
        return keywordResult;
    }

    private async Task<(List<MailItem> Items, int Total)> SearchKeywordRuleAsync(
        KeywordRule rule,
        List<MailAccount> accounts,
        string folderKey,
        string query,
        int page,
        int pageSize,
        CancellationToken token)
    {
        var windowSize = Math.Min(5000, Math.Max(1, page) * pageSize);
        var byKey = new Dictionary<string, MailItem>(StringComparer.OrdinalIgnoreCase);
        foreach (var keyword in rule.Keywords)
        {
            var result = await _apiClient.QueryAggregateAsync(accounts, folderKey, keyword, 1, windowSize, token);
            foreach (var item in result.Items.Where(item => KeywordRuleMatches(rule, item, query)))
            {
                byKey.TryAdd($"{item.AccountType}|{item.AccountId}|{item.Folder}|{item.Id}", item);
            }
        }
        var all = byKey.Values
            .OrderByDescending(item => item.Pinned)
            .ThenByDescending(item => item.Date)
            .ToList();
        var offset = Math.Max(0, (Math.Max(1, page) - 1) * pageSize);
        return (all.Skip(offset).Take(pageSize).ToList(), all.Count);
    }

    private static bool KeywordRuleMatches(KeywordRule rule, MailItem item, string extraQuery)
    {
        var haystack = string.Join(" ", TextPartsForKeywordRule(rule, item)).ToLowerInvariant();
        var keywords = rule.Keywords.Select(x => x.ToLowerInvariant()).ToList();
        var matched = rule.MatchMode == "all"
            ? keywords.All(haystack.Contains)
            : keywords.Any(haystack.Contains);
        if (!matched)
        {
            return false;
        }
        var extra = extraQuery.Trim().ToLowerInvariant();
        return string.IsNullOrWhiteSpace(extra) || haystack.Contains(extra);
    }

    private static IEnumerable<string> TextPartsForKeywordRule(KeywordRule rule, MailItem item)
    {
        var fields = rule.Fields.Count == 0 ? ["subject", "from", "intro"] : rule.Fields;
        if (fields.Contains("subject", StringComparer.OrdinalIgnoreCase)) yield return item.Subject;
        if (fields.Contains("from", StringComparer.OrdinalIgnoreCase)) yield return item.From;
        if (fields.Contains("to", StringComparer.OrdinalIgnoreCase)) yield return item.To;
        if (fields.Contains("intro", StringComparer.OrdinalIgnoreCase)) yield return item.Intro;
        if (fields.Contains("body", StringComparer.OrdinalIgnoreCase))
        {
            yield return item.Text;
            yield return item.Html;
        }
    }

    private MailAccount? ResolveMailAccount(MailItem? item)
    {
        if (item is null)
        {
            return null;
        }
        if (_selectedAccount is not null && IsRealAccount(_selectedAccount) && string.IsNullOrWhiteSpace(item.AccountType))
        {
            return _selectedAccount;
        }
        var accountType = string.IsNullOrWhiteSpace(item.AccountType)
            ? item.AccountId.Contains('@', StringComparison.Ordinal) ? "local" : "external"
            : item.AccountType;
        return _realAccounts.FirstOrDefault(account =>
            account.Type == accountType
            && (string.Equals(account.Id, item.AccountId, StringComparison.OrdinalIgnoreCase)
                || string.Equals(account.Address, item.AccountId, StringComparison.OrdinalIgnoreCase)))
            ?? (_selectedAccount is not null && IsRealAccount(_selectedAccount) ? _selectedAccount : null);
    }

    private string SourceAccountLabel(MailItem item)
    {
        return ResolveMailAccount(item)?.DisplayTitle ?? item.AccountLabel;
    }

    private static string BuildMailCacheKey(MailAccount account, MailFolder folder, string query)
    {
        return $"{AccountKey(account)}|{folder.Key}|{query.Trim()}";
    }

    private bool TryLoadMailPageFromCache(string cacheKey, int page, int pageSize, MailAccount account, out DateTimeOffset cachedAt)
    {
        cachedAt = default;
        var cached = _mailCacheService.Load(cacheKey, page, pageSize);
        if (cached is null || cached.Items.Count == 0)
        {
            return false;
        }
        _totalMessages = cached.Total;
        _mails.Clear();
        foreach (var item in cached.Items)
        {
            if (string.IsNullOrWhiteSpace(item.AccountLabel))
            {
                item.AccountLabel = account.IsAggregate ? SourceAccountLabel(item) : account.DisplayTitle;
            }
            _mails.Add(item);
        }
        cachedAt = cached.CachedAt;
        UpdatePaginationText();
        return true;
    }

    private void SelectPageSizeComboItem(int pageSize)
    {
        foreach (var item in PageSizeComboBox.Items.OfType<ComboBoxItem>())
        {
            item.IsSelected = item.Content?.ToString() == pageSize.ToString();
        }
    }

    private static MailDetail BuildLocalStoredDetail(MailItem item)
    {
        var text = item.Folder == "outbox" && !string.IsNullOrWhiteSpace(item.Error)
            ? $"发送失败原因: {item.Error}\r\n\r\n{item.Text}"
            : item.Text;
        return new MailDetail
        {
            Id = item.Id,
            Subject = item.Subject,
            From = item.From,
            To = item.To,
            Cc = item.Cc,
            Html = item.Html,
            Text = text,
            Date = item.Date,
            Seen = true,
            Attachments = item.Attachments,
        };
    }

    private async Task RunSensitiveActionAsync(MemailApiException prompt, string fallbackAction, Func<Task> action, string successMessage)
    {
        var confirm = new Views.SensitiveConfirmWindow(prompt.Action, fallbackAction) { Owner = this };
        if (confirm.ShowDialog() != true)
        {
            SetStatus("已取消二次确认");
            return;
        }
        try
        {
            await _apiClient.ConfirmSensitiveActionAsync(prompt.Action, confirm.AdminPassword, confirm.TotpCode, CancellationToken.None);
            await action();
            ClearDetail();
            await LoadMailPageAsync(_currentPage);
            SetStatus(successMessage);
        }
        catch (Exception ex)
        {
            SetStatus($"二次确认后操作失败: {ex.Message}");
        }
    }
}
