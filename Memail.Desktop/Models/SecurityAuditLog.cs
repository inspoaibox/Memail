namespace Memail.Desktop.Models;

public sealed class SecurityAuditLog
{
    public string Id { get; set; } = string.Empty;
    public string Action { get; set; } = string.Empty;
    public string Ip { get; set; } = string.Empty;
    public string Username { get; set; } = string.Empty;
    public string CreatedAt { get; set; } = string.Empty;
    public bool Success { get; set; }

    public string Summary => $"{CreatedAt}  {Ip}";
    public string DisplayStatus => Success ? "成功" : "失败";
}
