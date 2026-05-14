namespace Memail.Desktop.Models;

public sealed class MailFolder
{
    public string Key { get; set; } = string.Empty;
    public string Title { get; set; } = string.Empty;
    public int Count { get; set; }
}
