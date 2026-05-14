using System.IO;
using System.Security.Cryptography;
using System.Text;
using Memail.Desktop.Helpers;
using Memail.Desktop.Models;

namespace Memail.Desktop.Services;

public sealed class MailCacheService
{
    private readonly string _cacheDir;

    public MailCacheService()
    {
        _cacheDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Memail",
            "mail-cache");
    }

    public MailPageCache? Load(string key, int page, int pageSize)
    {
        return JsonFileStore.Load<MailPageCache>(PathFor(key, page, pageSize), null!);
    }

    public void Save(string key, int page, int pageSize, int total, IEnumerable<MailItem> items)
    {
        JsonFileStore.Save(PathFor(key, page, pageSize), new MailPageCache
        {
            Key = key,
            Page = page,
            PageSize = pageSize,
            Total = total,
            CachedAt = DateTimeOffset.Now,
            Items = items.ToList(),
        });
    }

    private string PathFor(string key, int page, int pageSize)
    {
        Directory.CreateDirectory(_cacheDir);
        var raw = $"{key}|{page}|{pageSize}";
        var hash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(raw))).ToLowerInvariant();
        return Path.Combine(_cacheDir, $"{hash}.json");
    }
}
