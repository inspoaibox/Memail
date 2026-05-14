namespace Memail.Desktop.Models;

public sealed class MailAttachment
{
    public int Index { get; set; }
    public string Id { get; set; } = string.Empty;
    public string Filename { get; set; } = string.Empty;
    public string Path { get; set; } = string.Empty;
    public string ContentBase64 { get; set; } = string.Empty;
    public long Size { get; set; }
    public string ContentType { get; set; } = string.Empty;
}
