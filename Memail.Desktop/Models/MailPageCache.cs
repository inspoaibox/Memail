namespace Memail.Desktop.Models;

public sealed class MailPageCache
{
    public string Key { get; set; } = string.Empty;
    public int Page { get; set; }
    public int PageSize { get; set; }
    public int Total { get; set; }
    public DateTimeOffset CachedAt { get; set; } = DateTimeOffset.Now;
    public List<MailItem> Items { get; set; } = [];
}
