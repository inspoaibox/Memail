namespace Memail.Desktop.Models;

public sealed class DeviceTokenInfo
{
    public string Id { get; set; } = string.Empty;
    public string Name { get; set; } = string.Empty;
    public string CreatedAt { get; set; } = string.Empty;
    public string LastSeen { get; set; } = string.Empty;
    public string LastIp { get; set; } = string.Empty;
    public bool Revoked { get; set; }

    public string DisplayStatus => Revoked ? "已撤销" : "可用";
    public string SeenSummary => string.IsNullOrWhiteSpace(LastSeen) ? "从未使用" : LastSeen;
}
