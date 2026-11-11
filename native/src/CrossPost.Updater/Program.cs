using System.Diagnostics;
using System.IO;
using System.Text.Json;
using System.Windows;
using CrossPost.Update;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args) => RunAsync(args).GetAwaiter().GetResult();
    private static async Task<int> RunAsync(string[] args)
    {
        string? transaction = null; UpdateRequest? request = null;
        try
        {
            if (args.Length != 1) throw new InvalidOperationException("Запускайте обновление из настроек CrossPost.");
            var path = Path.GetFullPath(args[0]);
            request = JsonSerializer.Deserialize<UpdateRequest>(await File.ReadAllTextAsync(path)) ?? throw new InvalidOperationException("Нет параметров обновления.");
            UpdateTransaction.Validate(request);
            transaction = Path.Combine(Path.GetFullPath(request.DataDirectory), "updates", "transactions", request.Token);
            if (!path.Equals(Path.Combine(transaction, "request.json"), StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("Неверное расположение задания обновления.");
            using var gate = UpdateGate.Acquire(request.DataDirectory);
            using var parent = Process.GetProcessById(request.ParentId);
            if (parent.StartTime.ToUniversalTime().Ticks != request.ParentStarted
                || !string.Equals(parent.MainModule?.FileName, Path.Combine(request.InstallDirectory, "CrossPost.Native.exe"), StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Обновление запущено другим экземпляром программы.");
            await File.WriteAllTextAsync(Path.Combine(transaction, "accepted"), request.Token);
            async Task WaitForParent()
            {
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(90));
                await parent.WaitForExitAsync(timeout.Token);
                // The window drained the queue first. Never replace files while a Worker owns the database.
                using var worker = new FileStream(Path.Combine(request.DataDirectory, "worker.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
            }
            async Task<bool> Launch(string exe)
            {
                var start = new ProcessStartInfo(exe) { WorkingDirectory = request.InstallDirectory, UseShellExecute = false };
                start.ArgumentList.Add("--updated"); start.ArgumentList.Add(path);
                using var child = Process.Start(start) ?? throw new InvalidOperationException("Не удалось запустить новую версию.");
                try
                {
                    for (var attempt = 0; attempt < 900 && !child.HasExited; attempt++)
                    {
                        var ready = Path.Combine(transaction, "ready");
                        if (File.Exists(ready) && await File.ReadAllTextAsync(ready) == $"{request.Token}|{child.Id}|{request.Version}")
                        {
                            await File.WriteAllTextAsync(Path.Combine(transaction, "commit"), request.Token); return true;
                        }
                        await Task.Delay(100);
                    }
                    return false;
                }
                finally { if (!File.Exists(Path.Combine(transaction, "commit")) && !child.HasExited) { child.Kill(entireProcessTree: true); await child.WaitForExitAsync(); } }
            }
            await UpdateTransaction.ApplyAsync(request, transaction, WaitForParent, Launch);
            return 0;
        }
        catch (Exception error)
        {
            if (transaction is not null && Directory.Exists(transaction))
                await File.WriteAllTextAsync(Path.Combine(transaction, "updater-error.log"), error.ToString());
            if (Environment.GetEnvironmentVariable("CROSSPOST_UPDATE_SMOKE") != "1")
                MessageBox.Show(error.Message + "\nЖурнал: " + transaction, "CrossPost — обновление", MessageBoxButton.OK, MessageBoxImage.Error);
            if (request is not null && transaction is not null && File.Exists(Path.Combine(transaction, "accepted"))
                && File.Exists(Path.Combine(request.InstallDirectory, "CrossPost.Native.exe")))
            {
                // Release the gate (using scope above) before restoring the old UI.
                var start = new ProcessStartInfo(Path.Combine(request.InstallDirectory, "CrossPost.Native.exe")) { UseShellExecute = false, WorkingDirectory = request.InstallDirectory };
                start.ArgumentList.Add("--update-failed"); start.ArgumentList.Add(transaction); using var previous = Process.Start(start);
            }
            return 1;
        }
    }
}
