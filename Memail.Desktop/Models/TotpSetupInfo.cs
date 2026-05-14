namespace Memail.Desktop.Models;

public sealed class TotpSetupInfo
{
    public string Secret { get; set; } = string.Empty;
    public string OtpAuthUri { get; set; } = string.Empty;
}
