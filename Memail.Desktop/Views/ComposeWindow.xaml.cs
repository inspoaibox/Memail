using System.IO;
using System.Text.RegularExpressions;
using System.Windows;
using Microsoft.Win32;
using Memail.Desktop.Models;
using Memail.Desktop.Services;

namespace Memail.Desktop.Views;

public partial class ComposeWindow : Window
{
    private readonly MemailApiClient _apiClient;
    private readonly MailAccount _account;
    private readonly string _mode;
    private readonly MailDetail? _source;
    private readonly MailItem? _draft;
    private readonly List<MailAttachment> _attachments = [];
    private bool _busy;

    public ComposeWindow(MemailApiClient apiClient, MailAccount account, string mode, MailDetail? source = null, MailItem? draft = null)
    {
        _apiClient = apiClient;
        _account = account;
        _mode = mode;
        _source = source;
        _draft = draft;
        InitializeComponent();
        InitializeFields();
    }

    private void InitializeFields()
    {
        HeaderText.Text = _mode switch
        {
            "draft" => "编辑草稿",
            "reply" => "回复邮件",
            "reply-all" => "回复全部",
            "forward" => "转发邮件",
            _ => "写邮件",
        };
        Title = HeaderText.Text;
        FromEmailText.Text = _account.Address;
        FromNameTextBox.Text = _account.SendName;
        UpdateAttachmentSummary();

        if (_source is null)
        {
            if (_mode == "draft" && _draft is not null)
            {
                ToTextBox.Text = _draft.To;
                CcTextBox.Text = _draft.Cc;
                BccTextBox.Text = _draft.Bcc;
                SubjectTextBox.Text = _draft.Subject;
                BodyTextBox.Text = !string.IsNullOrWhiteSpace(_draft.Text) ? _draft.Text : StripHtml(_draft.Html);
                _attachments.Clear();
                _attachments.AddRange(_draft.Attachments.Where(item => !string.IsNullOrWhiteSpace(item.Path) || !string.IsNullOrWhiteSpace(item.ContentBase64)));
                UpdateAttachmentSummary();
            }
            return;
        }

        if (_mode == "reply" || _mode == "reply-all")
        {
            ToTextBox.Text = string.Join(", ", ExtractEmailAddresses(_source.From).Where(address => !IsSelf(address)));
            if (_mode == "reply-all" && !string.IsNullOrWhiteSpace(_source.To))
            {
                var all = ExtractEmailAddresses(_source.From)
                    .Concat(ExtractEmailAddresses(_source.To))
                    .Concat(ExtractEmailAddresses(_source.Cc))
                    .Where(address => !IsSelf(address))
                    .Distinct(StringComparer.OrdinalIgnoreCase);
                ToTextBox.Text = string.Join(", ", all);
            }
            CcTextBox.Text = _mode == "reply-all"
                ? string.Join(", ", ExtractEmailAddresses(_source.Cc).Where(address => !IsSelf(address)).Distinct(StringComparer.OrdinalIgnoreCase))
                : string.Empty;
            SubjectTextBox.Text = PrefixSubject(_source.Subject, "Re:");
            BodyTextBox.Text = BuildQuotedText(_source);
        }
        else if (_mode == "forward")
        {
            SubjectTextBox.Text = PrefixSubject(_source.Subject, "Fwd:");
            BodyTextBox.Text = BuildForwardText(_source);
        }
    }

    private async void SendButton_OnClick(object sender, RoutedEventArgs e)
    {
        if (_busy)
        {
            return;
        }
        StatusTextBlock.Text = string.Empty;
        var to = ToTextBox.Text.Trim();
        var cc = CcTextBox.Text.Trim();
        var bcc = BccTextBox.Text.Trim();
        var subject = SubjectTextBox.Text.Trim();
        var body = BodyTextBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(to) || string.IsNullOrWhiteSpace(subject) || string.IsNullOrWhiteSpace(body))
        {
            StatusTextBlock.Text = "请填写收件人、主题和正文。";
            return;
        }

        try
        {
            SetBusy(true, "正在发送...");
            if (_account.Type == "external")
            {
                await _apiClient.SendExternalAsync(_account, FromNameTextBox.Text.Trim(), to, cc, bcc, subject, body, string.Empty, _attachments, CancellationToken.None);
            }
            else
            {
                await _apiClient.SendLocalAsync(_account.Address, FromNameTextBox.Text.Trim(), to, cc, bcc, subject, body, string.Empty, _source?.From ?? string.Empty, _attachments, CancellationToken.None);
            }
            if (_mode == "draft" && _draft is not null)
            {
                await _apiClient.DeleteDraftAsync(_draft.Id, CancellationToken.None);
            }
            DialogResult = true;
            Close();
        }
        catch (Exception ex)
        {
            StatusTextBlock.Text = $"发送失败: {ex.Message}";
        }
        finally
        {
            SetBusy(false);
        }
    }

    private async void SaveDraftButton_OnClick(object sender, RoutedEventArgs e)
    {
        if (_busy)
        {
            return;
        }
        StatusTextBlock.Text = string.Empty;
        try
        {
            SetBusy(true, "正在保存草稿...");
            var draftId = _mode == "draft" ? _draft?.Id : null;
            var draft = await _apiClient.SaveDraftAsync(
                _account,
                draftId,
                FromNameTextBox.Text.Trim(),
                ToTextBox.Text.Trim(),
                CcTextBox.Text.Trim(),
                BccTextBox.Text.Trim(),
                SubjectTextBox.Text.Trim(),
                BodyTextBox.Text,
                string.Empty,
                _attachments,
                CancellationToken.None);
            StatusTextBlock.Text = draft is null ? "草稿已保存。" : $"草稿已保存: {draft.Subject}";
            DialogResult = true;
            Close();
        }
        catch (Exception ex)
        {
            StatusTextBlock.Text = $"保存草稿失败: {ex.Message}";
        }
        finally
        {
            SetBusy(false);
        }
    }

    private void CancelButton_OnClick(object sender, RoutedEventArgs e)
    {
        Close();
    }

    private void AddAttachmentButton_OnClick(object sender, RoutedEventArgs e)
    {
        var dialog = new OpenFileDialog
        {
            Title = "选择附件",
            Multiselect = true,
        };
        if (dialog.ShowDialog(this) != true)
        {
            return;
        }
        foreach (var path in dialog.FileNames)
        {
            if (_attachments.Any(item => string.Equals(item.Path, path, StringComparison.OrdinalIgnoreCase)))
            {
                continue;
            }
            var info = new FileInfo(path);
            _attachments.Add(new MailAttachment
            {
                Index = _attachments.Count,
                Filename = info.Name,
                Path = info.FullName,
                ContentBase64 = Convert.ToBase64String(File.ReadAllBytes(info.FullName)),
                Size = info.Length,
                ContentType = "application/octet-stream",
            });
        }
        UpdateAttachmentSummary();
    }

    private void ClearAttachmentsButton_OnClick(object sender, RoutedEventArgs e)
    {
        _attachments.Clear();
        UpdateAttachmentSummary();
    }

    private static string PrefixSubject(string subject, string prefix)
    {
        subject = subject.Trim();
        return subject.StartsWith(prefix, StringComparison.OrdinalIgnoreCase) ? subject : $"{prefix} {subject}";
    }

    private void UpdateAttachmentSummary()
    {
        if (_attachments.Count == 0)
        {
            AttachmentSummaryTextBlock.Text = "无附件";
            return;
        }
        var size = _attachments.Sum(item => item.Size);
        AttachmentSummaryTextBlock.Text = $"{_attachments.Count} 个附件，{FormatSize(size)}";
    }

    private static string FormatSize(long bytes)
    {
        if (bytes < 1024) return $"{bytes} B";
        if (bytes < 1024 * 1024) return $"{bytes / 1024.0:F1} KB";
        return $"{bytes / 1024.0 / 1024.0:F1} MB";
    }

    private static string BuildQuotedText(MailDetail detail)
    {
        var body = !string.IsNullOrWhiteSpace(detail.Text) ? detail.Text : StripHtml(detail.Html);
        return $"\r\n\r\n----- 原始邮件 -----\r\n发件人: {detail.From}\r\n时间: {detail.Date:yyyy/MM/dd HH:mm:ss}\r\n主题: {detail.Subject}\r\n\r\n{body}";
    }

    private static string BuildForwardText(MailDetail detail)
    {
        var body = !string.IsNullOrWhiteSpace(detail.Text) ? detail.Text : StripHtml(detail.Html);
        return $"----- 转发邮件 -----\r\n发件人: {detail.From}\r\n收件人: {detail.To}\r\n时间: {detail.Date:yyyy/MM/dd HH:mm:ss}\r\n主题: {detail.Subject}\r\n\r\n{body}";
    }

    private bool IsSelf(string address)
    {
        return string.Equals(address.Trim(), _account.Address.Trim(), StringComparison.OrdinalIgnoreCase);
    }

    private void SetBusy(bool busy, string? message = null)
    {
        _busy = busy;
        SendButton.IsEnabled = !busy;
        SaveDraftButton.IsEnabled = !busy;
        CancelButton.IsEnabled = !busy;
        if (!string.IsNullOrWhiteSpace(message))
        {
            StatusTextBlock.Text = message;
        }
    }

    private static List<string> ExtractEmailAddresses(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return [];
        }
        var matches = Regex.Matches(value, @"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", RegexOptions.IgnoreCase);
        if (matches.Count == 0)
        {
            return string.IsNullOrWhiteSpace(value) ? [] : [value.Trim()];
        }
        return matches.Select(x => x.Value).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
    }

    private static string StripHtml(string html)
    {
        if (string.IsNullOrWhiteSpace(html))
        {
            return string.Empty;
        }
        var text = Regex.Replace(html, @"<br\s*/?>", "\n", RegexOptions.IgnoreCase);
        text = Regex.Replace(text, @"</p\s*>", "\n\n", RegexOptions.IgnoreCase);
        text = Regex.Replace(text, "<[^>]+>", " ");
        return System.Net.WebUtility.HtmlDecode(Regex.Replace(text, @"[ \t]+", " ").Trim());
    }
}
