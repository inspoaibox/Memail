using System.Windows;
using Memail.Desktop.Views;

namespace Memail.Desktop;

public partial class App : Application
{
    private void OnStartup(object sender, StartupEventArgs e)
    {
        var login = new LoginWindow();
        login.Show();
    }
}
