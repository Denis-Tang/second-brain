using System;
using System.IO;
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
        Environment.SetEnvironmentVariable("WEBVIEW2_DEFAULT_BACKGROUND_COLOR", "0");
        var assets = args[0];
        var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        var window = new Window
        {
            Title = "Shared Brain", Width = 1160, Height = 860,
            MinWidth = 680, MinHeight = 500, WindowStartupLocation = WindowStartupLocation.CenterScreen,
            WindowStyle = WindowStyle.None, ResizeMode = ResizeMode.CanResize, ShowActivated = false,
            Background = Brushes.Transparent,
            Icon = BitmapDecoder.Create(new Uri(Path.Combine(assets, "icon.ico")), BitmapCreateOptions.None, BitmapCacheOption.OnLoad)
                .Frames.MaxBy(frame => frame.PixelWidth)
        };
        app.MainWindow = window;
        WindowChrome.SetWindowChrome(window, new WindowChrome
        {
            CaptionHeight = 0, ResizeBorderThickness = new Thickness(6),
            CornerRadius = new CornerRadius(8), GlassFrameThickness = new Thickness(-1),
            UseAeroCaptionButtons = false
        });
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
        void Quit() { quitting = true; app.Shutdown(); }
        tray.ContextMenuStrip.Items.Add("退出", null, (_, _) => Quit());
        window.Closing += (_, e) => { if (!quitting) { e.Cancel = true; window.Hide(); } };
        window.SourceInitialized += (_, _) =>
        {
            var handle = new WindowInteropHelper(window).Handle;
            HwndSource.FromHwnd(handle).AddHook(ConstrainMaximizedWindow);
            HwndSource.FromHwnd(handle).AddHook((nint hwnd, int message, nint wParam, nint lParam, ref bool handled) =>
            {
                if (message == 0x18 && wParam != 0 && window.Visibility != Visibility.Visible) window.Show();
                return 0;
            });
            var round = 2; var light = 0; var acrylic = 3; var cloaked = 1;
            Marshal.ThrowExceptionForHR(DwmSetWindowAttribute(handle, 13, ref cloaked, 4));
            var margins = new Margins { Left = -1, Right = -1, Top = -1, Bottom = -1 };
            Marshal.ThrowExceptionForHR(DwmSetWindowAttribute(handle, 20, ref light, 4));
            Marshal.ThrowExceptionForHR(DwmSetWindowAttribute(handle, 33, ref round, 4));
            Marshal.ThrowExceptionForHR(DwmExtendFrameIntoClientArea(handle, ref margins));
            Marshal.ThrowExceptionForHR(DwmSetWindowAttribute(handle, 38, ref acrylic, 4));
        };
        window.StateChanged += (_, _) => browser.Visibility = window.WindowState == WindowState.Minimized ? Visibility.Collapsed : Visibility.Visible;
        window.ContentRendered += InitializeBrowser;
        async void InitializeBrowser(object? sender, EventArgs e)
        {
            window.ContentRendered -= InitializeBrowser;
            var environment = await CoreWebView2Environment.CreateAsync(null, args[1]);
            await browser.EnsureCoreWebView2Async(environment);
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
                    }
                    return;
                }
                if (value.GetProperty("method").GetString() == "choose_vault")
                {
                    var picker = new Microsoft.Win32.OpenFolderDialog { Title = "选择文件夹" };
                    var path = picker.ShowDialog(window) == true ? picker.FolderName : "";
                    browser.CoreWebView2.PostWebMessageAsJson(JsonSerializer.Serialize(new { id = value.GetProperty("id").GetInt32(), result = new { path } }));
                }
                else output.WriteLine(e.WebMessageAsJson);
            };
            _ = Task.Run(async () =>
            {
                while (await input.ReadLineAsync() is { } line)
                    await app.Dispatcher.InvokeAsync(() => browser.CoreWebView2.PostWebMessageAsJson(line));
                await app.Dispatcher.InvokeAsync(Quit);
            });
            browser.CoreWebView2.NavigationCompleted += async (_, _) =>
            {
                // Render the first frame before revealing the acrylic window.
                await browser.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, Stream.Null);
                var cloaked = 0;
                Marshal.ThrowExceptionForHR(DwmSetWindowAttribute(new WindowInteropHelper(window).Handle, 13, ref cloaked, 4));
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
