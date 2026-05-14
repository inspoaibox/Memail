namespace Memail.Desktop.Models;

public sealed class SecurityStatus
{
    public bool TotpEnabled { get; set; }
    public string CurrentSessionId { get; set; } = string.Empty;
    public List<SecuritySessionInfo> Sessions { get; set; } = [];
}
