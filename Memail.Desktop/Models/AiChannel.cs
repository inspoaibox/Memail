namespace Memail.Desktop.Models;

public sealed class AiChannel
{
    public string Id { get; set; } = string.Empty;
    public string Name { get; set; } = string.Empty;
    public string Provider { get; set; } = string.Empty;
    public string BaseUrl { get; set; } = string.Empty;
    public List<string> Models { get; set; } = [];
    public string UpdatedAt { get; set; } = string.Empty;
    public string DisplayName => $"{Name} ({Provider})";
}
