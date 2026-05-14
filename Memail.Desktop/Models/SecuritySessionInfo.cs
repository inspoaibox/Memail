namespace Memail.Desktop.Models;

public sealed class SecuritySessionInfo
{
    public string Id { get; set; } = string.Empty;
    public string Username { get; set; } = string.Empty;
    public string Ip { get; set; } = string.Empty;
    public string UserAgent { get; set; } = string.Empty;
    public string CreatedAt { get; set; } = string.Empty;
    public string LastSeen { get; set; } = string.Empty;
    public bool Revoked { get; set; }
    public bool IsCurrent { get; set; }

    public string DisplayName => $"{(IsCurrent ? "当前设备 · " : string.Empty)}{Username}";
    public string Summary => $"{Ip}  {LastSeen}";
    public string DisplayStatus => Revoked ? "已踢出" : IsCurrent ? "当前" : "在线";
}
