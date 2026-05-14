namespace Memail.Desktop.Models;

public sealed class MailDetail
{
    public string Id { get; set; } = string.Empty;
    public string Subject { get; set; } = string.Empty;
    public string From { get; set; } = string.Empty;
    public string To { get; set; } = string.Empty;
    public string Cc { get; set; } = string.Empty;
    public DateTimeOffset? Date { get; set; }
    public string Html { get; set; } = string.Empty;
    public string Text { get; set; } = string.Empty;
    public bool Seen { get; set; }
    public bool Flagged { get; set; }
    public List<MailAttachment> Attachments { get; set; } = [];
}
