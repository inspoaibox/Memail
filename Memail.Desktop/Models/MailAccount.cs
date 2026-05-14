namespace Memail.Desktop.Models;

public sealed class MailAccount
{
    public string Id { get; set; } = string.Empty;
    public string Type { get; set; } = "local";
    public string AggregateScope { get; set; } = string.Empty;
    public string AggregateGroup { get; set; } = string.Empty;
    public string Address { get; set; } = string.Empty;
    public string DisplayName { get; set; } = string.Empty;
    public string SendName { get; set; } = string.Empty;
    public string Group { get; set; } = string.Empty;
    public string Provider { get; set; } = string.Empty;
    public int UnreadCount { get; set; }
    public string Host { get; set; } = string.Empty;
    public int Port { get; set; } = 993;
    public bool Secure { get; set; } = true;
    public string SmtpHost { get; set; } = string.Empty;
    public int SmtpPort { get; set; } = 465;
    public bool SmtpSecure { get; set; } = true;
    public bool SmtpRequireTls { get; set; }
    public bool IsAggregate => Type == "aggregate";
    public string DisplayTitle => string.IsNullOrWhiteSpace(DisplayName) ? Address : DisplayName;
    public string GroupLabel => IsAggregate ? "智能视图" : string.IsNullOrWhiteSpace(Group) ? "未分组" : Group;
}
