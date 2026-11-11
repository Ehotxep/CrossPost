using System.IO;
using System.Threading;
using System.Windows;

namespace CrossPost.Desktop;

public partial class App : Application
{
    private Mutex? _mutex;
    public static bool UpdateLaunch => Environment.GetCommandLineArgs().Contains("--updated");
    public static bool SmokeTest => Environment.GetCommandLineArgs().Contains("--smoke-test");
    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        if (e.Args.Contains("--verify-update-smoke") && !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("CROSSPOST_NATIVE_DATA")))
        {
            ShutdownMode = ShutdownMode.OnExplicitShutdown;
            Shutdown(await UpdateSmoke.VerifyAsync(e.Args[^1])); return;
        }
        if (UpdateSmoke.Enabled && e.Args.Contains("--update-smoke-parent"))
        {
            ShutdownMode = ShutdownMode.OnExplicitShutdown;
            try { await UpdateSmoke.PrepareParentAsync(e.Args[^1]); Shutdown(); }
            catch (Exception error) { await File.WriteAllTextAsync(Path.Combine(DataFolder,"update-parent-error.log"),error.ToString()); Shutdown(1); }
            return;
        }
        if (UpdateSmoke.Enabled && ((UpdateLaunch && Environment.GetEnvironmentVariable("CROSSPOST_UPDATE_SMOKE_FAIL") == "1") || e.Args.Contains("--update-failed")))
        { Shutdown(1); return; }
        if (!UpdateLaunch && CrossPost.Update.UpdateGate.IsHeld(DataFolder))
        { if (!SmokeTest) MessageBox.Show("CrossPost устанавливает обновление. Дождитесь автоматического перезапуска."); Shutdown(1); return; }
        if (e.Args.Contains("--verify-worker-smoke"))
        {
            ShutdownMode = ShutdownMode.OnExplicitShutdown;
            if (string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("CROSSPOST_NATIVE_DATA"))) { Shutdown(1); return; }
            Shutdown(await WorkerProcess.VerifySmokeAsync());
            return;
        }
        if (e.Args.Contains("--worker"))
        {
            ShutdownMode = ShutdownMode.OnExplicitShutdown;
            try { Shutdown(await WorkerProcess.RunAsync()); }
            catch (Exception error)
            {
                try
                {
                    Directory.CreateDirectory(DataFolder);
                    await File.AppendAllTextAsync(Path.Combine(DataFolder, "worker-error.log"),
                        $"{DateTimeOffset.UtcNow:O} {error.GetType().Name}\n");
                }
                catch (Exception logError) when (logError is IOException or UnauthorizedAccessException) { }
                Shutdown(1);
            }
            return;
        }
        if (SmokeTest && string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("CROSSPOST_NATIVE_DATA")))
        { Shutdown(1); return; }
        _mutex = new Mutex(true, @"Local\CrossPost.Native.Prototype", out var created);
        if (!created) { MessageBox.Show("Прототип CrossPost уже запущен."); Shutdown(); return; }
        DispatcherUnhandledException += (_, args) =>
        {
            try
            {
                Directory.CreateDirectory(DataFolder);
                File.AppendAllText(Path.Combine(DataFolder, "error.log"), $"{DateTimeOffset.Now:O} {(SmokeTest ? args.Exception.ToString() : args.Exception.GetType().Name)}\n");
            }
            catch (IOException) { }
            if (SmokeTest) { args.Handled = true; Shutdown(1); return; }
            MessageBox.Show("Не удалось выполнить операцию. Данные Python-версии не затронуты. Перезапустите прототип.", "CrossPost");
            args.Handled = true;
        };
        MainWindow = new MainWindow();
        MainWindow.Show();
    }
    public static void TraceSmoke(string stage)
    {
        if (SmokeTest) File.AppendAllText(Path.Combine(DataFolder, "smoke-stage.log"), $"{DateTimeOffset.UtcNow:O} {stage}\n");
    }
    public static string DataFolder => Environment.GetEnvironmentVariable("CROSSPOST_NATIVE_DATA") is { Length: > 0 } folder
        ? Path.GetFullPath(folder)
        : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "CrossPostNativePrototype");
    protected override void OnExit(ExitEventArgs e) { _mutex?.Dispose(); base.OnExit(e); }
}
