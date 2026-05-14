using System.Net;

namespace Memail.Desktop.Services;

public sealed class MemailApiException : Exception
{
    public MemailApiException(string message, HttpStatusCode statusCode, bool requireConfirmation = false, string action = "")
        : base(message)
    {
        StatusCode = statusCode;
        RequireConfirmation = requireConfirmation;
        Action = action;
    }

    public HttpStatusCode StatusCode { get; }
    public bool RequireConfirmation { get; }
    public string Action { get; }
}
