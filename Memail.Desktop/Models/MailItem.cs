using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace Memail.Desktop.Models;

public sealed class MailItem : INotifyPropertyChanged
{
    private bool _seen;
    private bool _favorite;
    private bool _pinned;

    public event PropertyChangedEventHandler? PropertyChanged;

    public string Id { get; set; } = string.Empty;
    public string AccountType { get; set; } = string.Empty;
    public string AccountId { get; set; } = string.Empty;
    public string AccountLabel { get; set; } = string.Empty;
    public string Folder { get; set; } = string.Empty;
    public string From { get; set; } = string.Empty;
    public string Subject { get; set; } = string.Empty;
    public string Intro { get; set; } = string.Empty;
    public DateTimeOffset? Date { get; set; }
    public bool Seen
    {
        get => _seen;
        set => SetField(ref _seen, value);
    }
    public bool Favorite
    {
        get => _favorite;
        set => SetField(ref _favorite, value);
    }
    public bool Pinned
    {
        get => _pinned;
        set => SetField(ref _pinned, value);
    }
    public string To { get; set; } = string.Empty;
    public string Cc { get; set; } = string.Empty;
    public string Bcc { get; set; } = string.Empty;
    public string Html { get; set; } = string.Empty;
    public string Text { get; set; } = string.Empty;
    public string Error { get; set; } = string.Empty;
    public List<MailAttachment> Attachments { get; set; } = [];

    private void SetField<T>(ref T field, T value, [CallerMemberName] string propertyName = "")
    {
        if (EqualityComparer<T>.Default.Equals(field, value))
        {
            return;
        }
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }
}
