namespace Memail.Desktop.Models;

public sealed class AppConfig
{
    public string BaseUrl { get; set; } = "https://mail.aboen.co.uk";
    public string Username { get; set; } = string.Empty;
    public string AuthMode { get; set; } = "password";
    public string ProtectedDeviceToken { get; set; } = string.Empty;
    public bool RememberMe { get; set; } = true;
    public int PageSize { get; set; } = 50;
    public bool AutoRefreshEnabled { get; set; } = true;
    public int AutoRefreshSeconds { get; set; } = 60;
}
