using System;
using System.IO;
using System.Diagnostics;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Shell;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;
using Forms = System.Windows.Forms;

internal static class Program
{
    [STAThread]
    public static void Main(string[] args)
    {
        // Taskbar pins launch the window host without the parent's bridge arguments.
        if (args.Length == 0)
        {
            var launcher = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "shared-brain.exe"));
            Process.Start(new ProcessStartInfo(launcher) { UseShellExecute = true, WorkingDirectory = Path.GetDirectoryName(launcher)! });
            return;
        }
        var assets = args[0];
        var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        var window = new Window
        {
            Title = "Shared Brain", Width = 1160, Height = 860,
            MinWidth = 680, MinHeight = 500, WindowStartupLocation = WindowStartupLocation.CenterScreen,
            WindowStyle = WindowStyle.None, ResizeMode = ResizeMode.CanResize, ShowActivated = false,
            Background = new SolidColorBrush(Color.FromRgb(241, 246, 253)),
            Icon = BitmapDecoder.Create(new Uri(Path.Combine(assets, "icon.ico")), BitmapCreateOptions.None, BitmapCacheOption.OnLoad)
                .Frames.MaxBy(frame => frame.PixelWidth)
        };
        app.MainWindow = window;
        var chrome = new WindowChrome
        {
            CaptionHeight = 0, ResizeBorderThickness = new Thickness(6),
            CornerRadius = new CornerRadius(0), GlassFrameThickness = new Thickness(0),
            UseAeroCaptionButtons = false
        };
        WindowChrome.SetWindowChrome(window, chrome);
        var browser = new WebView2CompositionControl { AllowExternalDrop = false };
        window.Content = browser;
        using var output = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true };
        using var input = new StreamReader(Console.OpenStandardInput(), Encoding.UTF8);
        using var tray = new Forms.NotifyIcon
        {
            Icon = new System.Drawing.Icon(Path.Combine(assets, "icon.ico")),
            Text = "Shared Brain", Visible = true,
            ContextMenuStrip = new Forms.ContextMenuStrip()
        };
        void Show()
        {
            window.Show();
            if (window.WindowState == WindowState.Minimized) window.WindowState = WindowState.Normal;
            window.Activate();
        }
        tray.DoubleClick += (_, _) => Show();
        tray.ContextMenuStrip.Items.Add("显示 Shared Brain", null, (_, _) => Show());
        var quitting = false;
        var usesBackdrop = false;
        var resumePending = false;
        void Quit() { quitting = true; app.Shutdown(); }
        void RestoreBrowser()
        {
            if (!resumePending || !window.IsVisible || window.WindowState == WindowState.Minimized) return;
            resumePending = false;
            var cloaked = 1;
            DwmSetWindowAttribute(new WindowInteropHelper(window).Handle, 13, ref cloaked, 4);
            // Recreate the graphics capture surface after sleep/hibernation.
            browser.Dispose();
            browser = new WebView2CompositionControl { AllowExternalDrop = false };
            window.ContentRendered += InitializeBrowser;
            window.Content = browser;
            ApplyTheme(Microsoft.Win32.Registry.GetValue(@"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "AppsUseLightTheme", 1) is int mode && mode == 0);
        }
        window.Activated += (_, _) => RestoreBrowser();
        void ApplyTheme(bool dark)
        {
            var color = dark ? Color.FromRgb(20, 24, 32) : Color.FromRgb(241, 246, 253);
            window.Background = usesBackdrop ? Brushes.Transparent : new SolidColorBrush(color);
            browser.DefaultBackgroundColor = usesBackdrop ? System.Drawing.Color.Transparent
                : System.Drawing.Color.FromArgb(color.R, color.G, color.B);
            if (OperatingSystem.IsWindowsVersionAtLeast(10, 0, 22000))
            {
                var value = dark ? 1 : 0;
                DwmSetWindowAttribute(new WindowInteropHelper(window).Handle, 20, ref value, 4);
            }
        }
        tray.ContextMenuStrip.Items.Add("退出", null, (_, _) => Quit());
        window.Closing += (_, e) => { if (!quitting) { e.Cancel = true; window.Hide(); } };
        window.SourceInitialized += (_, _) =>
        {
            var handle = new WindowInteropHelper(window).Handle;
            HwndSource.FromHwnd(handle).AddHook(ConstrainMaximizedWindow);
            HwndSource.FromHwnd(handle).AddHook((nint hwnd, int message, nint wParam, nint lParam, ref bool handled) =>
            {
                if (message == 0x18 && wParam != 0 && window.Visibility != Visibility.Visible) window.Show();
                if (message == 0x218 && wParam == 0x12) // PBT_APMRESUMEAUTOMATIC
                {
                    resumePending = true;
                    if (window.IsVisible && window.WindowState != WindowState.Minimized)
                        app.Dispatcher.BeginInvoke(new Action(RestoreBrowser));
                }
                return 0;
            });
            var cloaked = 1;
            DwmSetWindowAttribute(handle, 13, ref cloaked, 4);
            // Decorations are optional; unsupported Windows versions keep an opaque surface.
            if (OperatingSystem.IsWindowsVersionAtLeast(10, 0, 22000))
            {
                var round = 2;
                DwmSetWindowAttribute(handle, 33, ref round, 4);
            }
            if (OperatingSystem.IsWindowsVersionAtLeast(10, 0, 22621))
            {
                var acrylic = 3;
                var margins = new Margins { Left = -1, Right = -1, Top = -1, Bottom = -1 };
                usesBackdrop = DwmSetWindowAttribute(handle, 38, ref acrylic, 4) >= 0
                    && DwmExtendFrameIntoClientArea(handle, ref margins) >= 0;
                if (usesBackdrop) chrome.GlassFrameThickness = new Thickness(-1);
            }
            ApplyTheme(Microsoft.Win32.Registry.GetValue(@"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "AppsUseLightTheme", 1) is int mode && mode == 0);
        };
        window.StateChanged += (_, _) =>
        {
            browser.Visibility = window.WindowState == WindowState.Minimized ? Visibility.Collapsed : Visibility.Visible;
            RestoreBrowser();
        };
        _ = Task.Run(async () =>
        {
            while (await input.ReadLineAsync() is { } line)
                await app.Dispatcher.InvokeAsync(() => browser.CoreWebView2?.PostWebMessageAsJson(line));
            await app.Dispatcher.InvokeAsync(Quit);
        });
        window.ContentRendered += InitializeBrowser;
        async void InitializeBrowser(object? sender, EventArgs e)
        {
            window.ContentRendered -= InitializeBrowser;
            try
            {
                // Reject file/directory conflicts before Edge opens its own blocking dialog.
                Directory.CreateDirectory(Path.Combine(args[1], "EBWebView"));
                var environment = await CoreWebView2Environment.CreateAsync(null, args[1]);
                await browser.EnsureCoreWebView2Async(environment);
            }
            catch (Exception error)
            {
                var reason = error is IOException or UnauthorizedAccessException
                    ? "无法创建或访问界面数据目录。请检查该位置是否被同名文件占用，以及文件夹的读写权限。"
                    : "无法初始化 WebView2。请确认 Microsoft Edge WebView2 Runtime 已安装，且应用数据目录可写。";
                MessageBox.Show($"{reason}\n\n数据目录：{args[1]}\n\n{error.Message} (0x{error.HResult:X8})",
                    "Shared Brain 启动失败", MessageBoxButton.OK, MessageBoxImage.Error);
                Environment.ExitCode = 1;
                Quit();
                return;
            }
            browser.ZoomFactor = 1.5;
            browser.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
            browser.CoreWebView2.WebMessageReceived += (_, e) =>
            {
                using var message = JsonDocument.Parse(e.WebMessageAsJson);
                var value = message.RootElement;
                if (value.ValueKind == JsonValueKind.String)
                {
                    switch (value.GetString())
                    {
                        case "drag": ReleaseCapture(); SendMessage(new WindowInteropHelper(window).Handle, 0xA1, (nint)2, 0); break;
                        case "minimize": window.WindowState = WindowState.Minimized; break;
                        case "maximize": window.WindowState = window.WindowState == WindowState.Maximized ? WindowState.Normal : WindowState.Maximized; break;
                        case "close": window.Close(); break;
                        case "theme:dark": ApplyTheme(true); break;
                        case "theme:light": ApplyTheme(false); break;
                    }
                    return;
                }
                if (value.GetProperty("method").GetString() == "choose_vault")
                {
                    var requestId = value.GetProperty("id").GetString();
                    // Native modal UI must open after the WebView2 event handler returns.
                    app.Dispatcher.BeginInvoke(new Action(() =>
                    {
                        var picker = new Microsoft.Win32.OpenFolderDialog { Title = "选择文件夹" };
                        var path = picker.ShowDialog(window) == true ? picker.FolderName : "";
                        browser.CoreWebView2.PostWebMessageAsJson(JsonSerializer.Serialize(new { id = requestId, result = new { path } }));
                    }));
                }
                else output.WriteLine(e.WebMessageAsJson);
            };
            browser.CoreWebView2.NavigationCompleted += async (_, _) =>
            {
                // Navigation can finish before the composition surface has its first frame.
                await browser.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, Stream.Null);
                var cloaked = 0;
                DwmSetWindowAttribute(new WindowInteropHelper(window).Handle, 13, ref cloaked, 4);
                window.Activate();
            };
            browser.Source = new Uri(Path.Combine(assets, "index.html"));
        }
        app.DispatcherUnhandledException += (_, e) =>
        {
            MessageBox.Show(e.Exception.Message, "Shared Brain", MessageBoxButton.OK, MessageBoxImage.Error);
            e.Handled = true; Quit();
        };
        app.Exit += (_, _) => { tray.Visible = false; browser.Dispose(); };
        app.Run(window);
    }
    private static nint ConstrainMaximizedWindow(nint handle, int message, nint wParam, nint lParam, ref bool handled)
    {
        if (message != 0x24) return 0;
        var screen = Forms.Screen.FromHandle(handle);
        var info = Marshal.PtrToStructure<MinMaxInfo>(lParam);
        info.MaxPosition = new Point { X = screen.WorkingArea.Left - screen.Bounds.Left, Y = screen.WorkingArea.Top - screen.Bounds.Top };
        info.MaxSize = new Point { X = screen.WorkingArea.Width, Y = screen.WorkingArea.Height };
        Marshal.StructureToPtr(info, lParam, false);
        handled = true;
        return 0;
    }
    [StructLayout(LayoutKind.Sequential)] private struct Point { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)] private struct MinMaxInfo { public Point Reserved, MaxSize, MaxPosition, MinTrackSize, MaxTrackSize; }
    [StructLayout(LayoutKind.Sequential)] private struct Margins { public int Left, Right, Top, Bottom; }
    [DllImport("dwmapi.dll")] private static extern int DwmSetWindowAttribute(nint handle, int attribute, ref int value, int size);
    [DllImport("dwmapi.dll")] private static extern int DwmExtendFrameIntoClientArea(nint handle, ref Margins margins);
    [DllImport("user32.dll")] private static extern bool ReleaseCapture();
    [DllImport("user32.dll")] private static extern nint SendMessage(nint handle, int message, nint wParam, nint lParam);
}
