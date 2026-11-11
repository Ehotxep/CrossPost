using System.Diagnostics;
using System.IO;
using System.Text.Json;
using CrossPost.Core;
using CrossPost.Update;

namespace CrossPost.Desktop;
internal static class UpdateSmoke
{
    public static bool Enabled => Environment.GetEnvironmentVariable("CROSSPOST_UPDATE_SMOKE") == "1"
        && !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("CROSSPOST_NATIVE_DATA"));
    public static async Task PrepareParentAsync(string package)
    {
        var version = MainWindow.NativeAppVersion; var token = Guid.NewGuid().ToString("N");
        var transaction = Path.Combine(App.DataFolder, "updates", "transactions", token); Directory.CreateDirectory(transaction);
        using var parent = Process.GetCurrentProcess();
        var request = new UpdateRequest(Path.TrimEndingDirectorySeparator(AppContext.BaseDirectory), App.DataFolder, package,
            await UpdatePackage.HashAsync(package), version, Environment.ProcessId, parent.StartTime.ToUniversalTime().Ticks, false, token);
        var path = Path.Combine(transaction, "request.json"); await File.WriteAllTextAsync(path, JsonSerializer.Serialize(request));
        var helper = Path.Combine(transaction, "CrossPost.Updater.exe"); File.Copy(Path.Combine(AppContext.BaseDirectory, "CrossPost.Updater.exe"), helper);
        var start = new ProcessStartInfo(helper) { UseShellExecute = false, WorkingDirectory = transaction }; start.ArgumentList.Add(path);
        using var process = Process.Start(start) ?? throw new InvalidOperationException("Smoke updater did not start");
        for (var attempt = 0; attempt < 100 && !process.HasExited; attempt++)
        { if (File.Exists(Path.Combine(transaction, "accepted"))) return; await Task.Delay(100); }
        throw new InvalidOperationException("Smoke updater never accepted parent handoff");
    }
    public static async Task<int> VerifyAsync(string package)
    {
        var root = Path.Combine(Path.GetDirectoryName(App.DataFolder)!, "CrossPostUpdate-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(root);
        try
        {
            foreach (var fail in new[] { false, true })
            {
                var scenario = Path.Combine(root, fail ? "rollback" : "success"); var install = Path.Combine(scenario, "install"); var data = Path.Combine(scenario, "data");
                Directory.CreateDirectory(install);
                foreach (var file in Directory.GetFiles(AppContext.BaseDirectory, "*", SearchOption.AllDirectories))
                {
                    var target = Path.Combine(install, Path.GetRelativePath(AppContext.BaseDirectory, file)); Directory.CreateDirectory(Path.GetDirectoryName(target)!); File.Copy(file, target);
                }
                await File.WriteAllTextAsync(Path.Combine(install, "old-install-marker.txt"), "old");
                var store = new Store(data); await store.InitializeAsync(); await store.SaveDocumentAsync("retained", "settings-and-history");
                var publication = new Publication(Guid.NewGuid(), DateTimeOffset.UtcNow, new("Existing post"), true, "demo", new(DeliveryState.Sent,"demo",100),"Existing post");
                await store.SavePublicationAsync(publication);
                var databaseBefore = await UpdatePackage.HashAsync(Path.Combine(data, "prototype.db"));
                var start = new ProcessStartInfo(Path.Combine(install, "CrossPost.Native.exe")) { UseShellExecute = false, WorkingDirectory = install };
                start.Environment["CROSSPOST_NATIVE_DATA"] = data; start.Environment["CROSSPOST_UPDATE_SMOKE"] = "1"; start.Environment["CROSSPOST_UPDATE_SMOKE_FAIL"] = fail ? "1" : "0";
                start.ArgumentList.Add("--update-smoke-parent"); start.ArgumentList.Add(Path.GetFullPath(package));
                using var parent = Process.Start(start) ?? throw new InvalidOperationException("Update test parent failed to start");
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(150)); await parent.WaitForExitAsync(timeout.Token);
                if (parent.ExitCode != 0) throw new InvalidOperationException("Update test parent failed: " + parent.ExitCode);
                var transaction = Directory.GetDirectories(Path.Combine(data,"updates","transactions")).Single();
                UpdateResult? result = null;
                for (var attempt = 0; attempt < 1500; attempt++)
                {
                    var path = Path.Combine(transaction,"result.json");
                    if (File.Exists(path)) result = JsonSerializer.Deserialize<UpdateResult>(await File.ReadAllTextAsync(path));
                    if (result?.State is "committed" or "rolled-back") break; await Task.Delay(100, timeout.Token);
                }
                for (var attempt = 0; attempt < 300 && UpdateGate.IsHeld(data); attempt++) await Task.Delay(100, timeout.Token);
                if (result?.State != (fail ? "rolled-back" : "committed"))
                    throw new InvalidOperationException("Update lifecycle failed: " + result + " " + (File.Exists(Path.Combine(transaction,"updater-error.log")) ? await File.ReadAllTextAsync(Path.Combine(transaction,"updater-error.log")) : ""));
                if (await store.ReadDocumentAsync<string>("retained") != "settings-and-history" || (await store.HistoryAsync()).Single().Id != publication.Id)
                    throw new InvalidOperationException("Update lost settings or publication history");
                if (fail && (!File.Exists(Path.Combine(install,"old-install-marker.txt")) || await UpdatePackage.HashAsync(Path.Combine(data,"prototype.db")) != databaseBefore))
                    throw new InvalidOperationException("Failed startup did not restore original install and database");
                if (!fail && (File.Exists(Path.Combine(install,"old-install-marker.txt")) || !File.Exists(Path.Combine(transaction,"commit"))))
                    throw new InvalidOperationException("New installation or readiness acknowledgement missing");
            }
            await File.WriteAllTextAsync(Path.Combine(App.DataFolder,"update-smoke-ready.txt"), "Real updater: parent handoff, WPF startup, exact readiness, settings/history retained, failed-start rollback");
            return 0;
        }
        catch (Exception error) { await File.WriteAllTextAsync(Path.Combine(App.DataFolder,"update-smoke-error.log"), error.ToString()); return 1; }
    }
}
