using System.Windows;

namespace Memail.Desktop.Views;

public partial class SensitiveConfirmWindow : Window
{
    public SensitiveConfirmWindow(string action, string fallbackAction)
    {
        InitializeComponent();
        ActionTextBlock.Text = $"敏感操作确认：{(string.IsNullOrWhiteSpace(action) ? fallbackAction : action)}";
    }

    public string AdminPassword => PasswordBox.Password;
    public string TotpCode => TotpTextBox.Text.Trim();

    private void ConfirmButton_OnClick(object sender, RoutedEventArgs e)
    {
        if (string.IsNullOrWhiteSpace(AdminPassword))
        {
            StatusTextBlock.Text = "请输入后台登录密码";
            return;
        }
        DialogResult = true;
        Close();
    }

    private void CancelButton_OnClick(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
        Close();
    }
}
