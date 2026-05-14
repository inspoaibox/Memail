namespace Memail.Desktop.Models;

public sealed class AuthResult
{
    public bool Success { get; init; }
    public string Message { get; init; } = string.Empty;
}
