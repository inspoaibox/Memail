namespace Memail.Desktop.Models;

public sealed class KeywordRule
{
    public string Id { get; set; } = string.Empty;
    public string Name { get; set; } = string.Empty;
    public List<string> Keywords { get; set; } = [];
    public string MatchMode { get; set; } = "any";
    public List<string> Fields { get; set; } = ["subject", "from", "intro"];
    public string ScopeType { get; set; } = "all";
    public string ScopeGroup { get; set; } = string.Empty;
    public List<string> ScopeAccounts { get; set; } = [];
    public bool Enabled { get; set; } = true;
    public string CreatedAt { get; set; } = string.Empty;
    public string UpdatedAt { get; set; } = string.Empty;

    public string KeywordText
    {
        get => string.Join(Environment.NewLine, Keywords);
        set => Keywords = ParseList(value);
    }

    public string FieldText
    {
        get => string.Join(",", Fields);
        set => Fields = ParseList(value);
    }

    public string ScopeAccountText
    {
        get => string.Join(",", ScopeAccounts);
        set => ScopeAccounts = ParseList(value);
    }

    public string Summary
    {
        get
        {
            var scope = ScopeType == "group" ? $"分组: {ScopeGroup}" : ScopeType == "accounts" ? $"指定账号: {ScopeAccounts.Count}" : "全部账号";
            var mode = MatchMode == "all" ? "全部关键词" : "任意关键词";
            return $"{scope} · {mode}";
        }
    }

    public string DisplayStatus => Enabled ? "启用" : "停用";

    private static List<string> ParseList(string value)
    {
        return value
            .Split(['\r', '\n', ',', '，'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();
    }
}
