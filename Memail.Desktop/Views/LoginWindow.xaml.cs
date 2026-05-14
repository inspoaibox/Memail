using System.Windows;
using Memail.Desktop.Models;
using Memail.Desktop.Services;

namespace Memail.Desktop.Views;

public partial class LoginWindow : Window
{
    private readonly AppConfigService _configService = new();
    private readonly MemailApiClient _apiClient = new();

    public LoginWindow()
    {
        InitializeComponent();
        var config = _configService.Load();
        BaseUrlTextBox.Text = config.BaseUrl;
        UsernameTextBox.Text = config.RememberMe ? config.Username : string.Empty;
        DeviceTokenBox.Password = config.RememberMe ? SecretProtector.Unprotect(config.ProtectedDeviceToken) : string.Empty;
        AuthTabControl.SelectedIndex = config.RememberMe && config.AuthMode == "device-token" ? 1 : 0;
        RememberCheckBox.IsChecked = config.RememberMe;
    }

    private async void OnLoginClick(object sender, RoutedEventArgs e)
    {
        StatusTextBlock.Text = string.Empty;
        var baseUrl = BaseUrlTextBox.Text.Trim();
        var username = UsernameTextBox.Text.Trim();
        var password = PasswordBox.Password;
        var useDeviceToken = AuthTabControl.SelectedIndex == 1;
        if (string.IsNullOrWhiteSpace(baseUrl))
        {
            StatusTextBlock.Text = "请填写服务端地址。";
            return;
        }
        if (!useDeviceToken && (string.IsNullOrWhiteSpace(username) || string.IsNullOrWhiteSpace(password)))
        {
            StatusTextBlock.Text = "请完整填写账号和密码。";
            return;
        }
        if (useDeviceToken && string.IsNullOrWhiteSpace(DeviceTokenBox.Password))
        {
            StatusTextBlock.Text = "请填写设备 Token。";
            return;
        }

        try
        {
            _apiClient.Configure(baseUrl);
            var result = useDeviceToken
                ? await _apiClient.LoginWithDeviceTokenAsync(DeviceTokenBox.Password, CancellationToken.None)
                : await _apiClient.LoginAsync(username, password, TotpTextBox.Text.Trim(), CancellationToken.None);
            if (!result.Success)
            {
                StatusTextBlock.Text = result.Message;
                return;
            }

            if (RememberCheckBox.IsChecked == true)
            {
                _configService.Save(new AppConfig
                {
                    BaseUrl = baseUrl,
                    Username = username,
                    AuthMode = useDeviceToken ? "device-token" : "password",
                    ProtectedDeviceToken = useDeviceToken ? SecretProtector.Protect(DeviceTokenBox.Password.Trim()) : string.Empty,
                    RememberMe = true,
                });
            }
            else
            {
                _configService.Save(new AppConfig
                {
                    BaseUrl = baseUrl,
                    Username = string.Empty,
                    AuthMode = "password",
                    ProtectedDeviceToken = string.Empty,
                    RememberMe = false,
                });
            }

            var main = new MainWindow(_apiClient);
            main.Show();
            Close();
        }
        catch (Exception ex)
        {
            StatusTextBlock.Text = ex.Message;
        }
    }
}
