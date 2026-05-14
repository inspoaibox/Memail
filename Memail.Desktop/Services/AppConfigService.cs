using System.IO;
using Memail.Desktop.Helpers;
using Memail.Desktop.Models;

namespace Memail.Desktop.Services;

public sealed class AppConfigService
{
    private readonly string _configPath;

    public AppConfigService()
    {
        var appDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Memail");
        _configPath = Path.Combine(appDir, "desktop-config.json");
    }

    public AppConfig Load() => JsonFileStore.Load(_configPath, new AppConfig());

    public void Save(AppConfig config) => JsonFileStore.Save(_configPath, config);
}
