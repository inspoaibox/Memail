namespace Memail.Desktop.Models;

public sealed class AiSettings
{
    public List<AiChannel> Channels { get; set; } = [];
    public AiDefaultModel DefaultModel { get; set; } = new();
}
