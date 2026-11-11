using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Threading;
using CrossPost.Update;
using Microsoft.Win32;

namespace CrossPost.Desktop;
public record UpdateOptions(bool Preview = true, bool CheckAtStartup = true);
public partial class MainWindow
{
    public static string NativeAppVersion => typeof(App).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()!.InformationalVersion.Split('+')[0];
    private NativeRelease? _availableUpdate;
    private int _updateCheckGeneration;
    private UpdateOptions _updateOptions = new();
    private async Task InitializeUpdatesAsync()
    {
        _updateOptions = await _store.ReadDocumentAsync<UpdateOptions>("update-options") ?? new();
        UpdateVersionLabel.Text = "Установлена версия " + NativeAppVersion;
        UpdateChannel.SelectedIndex = _updateOptions.Preview ? 0 : 1; AutoCheckUpdates.IsChecked = _updateOptions.CheckAtStartup;
        if (App.UpdateLaunch) await CompleteUpdateLaunchAsync();
        else if (!App.SmokeTest && _updateOptions.CheckAtStartup) _ = CheckUpdatesBackgroundAsync();
    }
    private async Task CheckUpdatesBackgroundAsync()
    {
        try { await CheckForUpdateAsync(CancellationToken.None); }
        catch (Exception error) { UpdateStatus.Text = "Проверка обновлений недоступна: " + error.Message; }
    }
    private async Task CheckForUpdateAsync(CancellationToken ct)
    {
        UpdateStatus.Text = "Проверяю нативные выпуски на GitHub…"; InstallUpdateButton.IsEnabled = false;
        var generation = Interlocked.Increment(ref _updateCheckGeneration); var preview = _updateOptions.Preview;
        var available = await new UpdateFeed(_http).CheckAsync(NativeAppVersion, preview, ct);
        if (generation != _updateCheckGeneration) return;
        _availableUpdate = available;
        UpdateStatus.Text = _availableUpdate is null ? "Более новой нативной версии в выбранном канале нет."
            : "Доступна " + _availableUpdate.Version + "\n" + _availableUpdate.Notes;
        InstallUpdateButton.IsEnabled = _availableUpdate is not null;
        UpdateNotice.Visibility = _availableUpdate is null ? Visibility.Collapsed : Visibility.Visible;
        UpdateNotice.Content = "Обновление " + _availableUpdate?.Version;
    }
    private async void CheckUpdates(object sender, RoutedEventArgs e) => await RunOperation(async ct =>
    {
        try { await CheckForUpdateAsync(ct); }
        catch (Exception error) { UpdateStatus.Text = "Проверка не завершена: " + error.Message; throw; }
    });
    private async void SaveUpdateOptions(object sender, RoutedEventArgs e) => await RunOperation(async _ =>
    {
        Interlocked.Increment(ref _updateCheckGeneration);
        _updateOptions = new(UpdateChannel.SelectedIndex == 0, AutoCheckUpdates.IsChecked == true);
        await _store.SaveDocumentAsync("update-options", _updateOptions); _availableUpdate = null; InstallUpdateButton.IsEnabled = false; UpdateNotice.Visibility = Visibility.Collapsed;
        UpdateStatus.Text = "Канал обновлений сохранён. Нажмите «Проверить обновления».";
    });
    private async void InstallUpdate(object sender, RoutedEventArgs e) => await RunOperation(async ct =>
    {
        var release = _availableUpdate ?? throw new InvalidOperationException("Сначала проверьте обновления.");
        var folder = Path.Combine(App.DataFolder, "updates", "downloads", Guid.NewGuid().ToString("N"));
        var progress = new Progress<double>(value => { UpdateProgress.Value = value; UpdateStatus.Text = $"Скачивание {release.Version}: {value:F0}%"; });
        UpdateProgress.Visibility = Visibility.Visible;
        using var downloadHttp = new System.Net.Http.HttpClient(new System.Net.Http.HttpClientHandler { AllowAutoRedirect = false }) { Timeout = TimeSpan.FromMinutes(15) };
        var package = await new UpdateFeed(downloadHttp).DownloadAsync(release, folder, progress, ct);
        await StartUpdateAsync(package.Path, package.Sha256, release.Version, ct);
    });
    private async void InstallLocalUpdate(object sender, RoutedEventArgs e)
    {
        if (_busy) return;
        var picker = new OpenFileDialog { Title = "ZIP нативного выпуска CrossPost с файлом .sha256 рядом", Filter = "CrossPost Native ZIP|*.zip" };
        if (picker.ShowDialog(this) != true) return;
        await RunOperation(async ct =>
        {
            using var zip = System.IO.Compression.ZipFile.OpenRead(picker.FileName);
            var entry = zip.GetEntry(UpdatePackage.ManifestName) ?? throw new InvalidOperationException("В ZIP нет манифеста нативной версии.");
            if (entry.Length > 1024 * 1024) throw new InvalidOperationException("Неверный манифест.");
            PackageManifest manifest;
            using (var input = entry.Open()) manifest = await JsonSerializer.DeserializeAsync<PackageManifest>(input, cancellationToken: ct) ?? throw new InvalidOperationException("Пустой манифест.");
            if (NativeVersion.Parse(manifest.Version).CompareTo(NativeVersion.Parse(NativeAppVersion)) < 0) throw new InvalidOperationException("Понижение версии отключено: старая программа может не прочитать новую базу.");
            if (MessageBox.Show($"Установить {manifest.Version} из выбранного ZIP? Используйте архив и .sha256 из доверенного выпуска CrossPost. Приложение будет перезапущено.", "Обновление из файла", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
            var sum = (await File.ReadAllTextAsync(picker.FileName + ".sha256", ct)).Trim().TrimStart('\uFEFF');
            await Task.Run(() => UpdatePackage.VerifyAsync(picker.FileName, sum, manifest.Version, ct: ct), ct);
            var folder = Path.Combine(App.DataFolder, "updates", "downloads", Guid.NewGuid().ToString("N")); Directory.CreateDirectory(folder);
            var copy = Path.Combine(folder, "package.zip");
            await using (var input = new FileStream(picker.FileName, FileMode.Open, FileAccess.Read, FileShare.Read, 81920, true))
            await using (var output = new FileStream(copy, FileMode.CreateNew, FileAccess.Write, FileShare.None, 81920, true))
                await input.CopyToAsync(output, ct);
            await StartUpdateAsync(copy, sum, manifest.Version, ct);
        });
    }
    private async Task StartUpdateAsync(string package, string sum, string version, CancellationToken ct)
    {
        var exe = Path.Combine(AppContext.BaseDirectory, "CrossPost.Updater.exe");
        if (!File.Exists(exe)) throw new InvalidOperationException("Обновлятор отсутствует. Распакуйте полный архив новой нативной сборки.");
        MainContent.IsEnabled = false;
        var resume = WorkerProcess.IsRunning;
        UpdateStatus.Text = "Ожидаю завершения текущей отправки и остановки очереди…";
        if (resume)
        {
            if (!await WorkerProcess.CommandAsync("drain")) throw new InvalidOperationException("Worker не поддерживает завершение перед обновлением. Остановите очередь вручную.");
            for (var attempt = 0; attempt < 1200 && WorkerProcess.IsRunning; attempt++) { ct.ThrowIfCancellationRequested(); await Task.Delay(100, ct); }
            if (WorkerProcess.IsRunning) throw new InvalidOperationException("Отправка ещё выполняется. Попробуйте обновление после её завершения.");
        }
        await SaveDraftAsync();
        var token = Guid.NewGuid().ToString("N"); var folder = Path.Combine(App.DataFolder, "updates", "transactions", token); Directory.CreateDirectory(folder);
        using var process = Process.GetCurrentProcess();
        var request = new UpdateRequest(Path.TrimEndingDirectorySeparator(AppContext.BaseDirectory), App.DataFolder, package, sum, version,
            Environment.ProcessId, process.StartTime.ToUniversalTime().Ticks, resume, token);
        UpdateTransaction.Validate(request);
        var requestPath = Path.Combine(folder, "request.json"); await File.WriteAllTextAsync(requestPath, JsonSerializer.Serialize(request), ct);
        var helper = Path.Combine(folder, "CrossPost.Updater.exe"); File.Copy(exe, helper);
        var start = new ProcessStartInfo(helper) { UseShellExecute = false, WorkingDirectory = folder }; start.ArgumentList.Add(requestPath);
        ct.ThrowIfCancellationRequested();
        using var updater = Process.Start(start) ?? throw new InvalidOperationException("Не удалось запустить обновлятор.");
        // After the helper owns the update gate it must be allowed to complete; no half-cancelled handoff.
        for (var attempt = 0; attempt < 100 && !updater.HasExited; attempt++)
        {
            if (File.Exists(Path.Combine(folder, "accepted")) && await File.ReadAllTextAsync(Path.Combine(folder, "accepted")) == token)
            {
                _draftTimer.Stop(); _closing = true; Closing -= OnClosing; _http.Dispose(); Application.Current.Shutdown(); return;
            }
            await Task.Delay(100);
        }
        throw new InvalidOperationException("Обновлятор не подтвердил запуск. Текущая версия продолжает работать. Журнал: " + folder);
    }
    private async Task CompleteUpdateLaunchAsync()
    {
        IsEnabled = false;
        var args = Environment.GetCommandLineArgs(); var index = Array.IndexOf(args, "--updated");
        if (index < 0 || index + 1 >= args.Length) throw new InvalidOperationException("Нет задания обновления.");
        var path = Path.GetFullPath(args[index + 1]);
        var request = JsonSerializer.Deserialize<UpdateRequest>(await File.ReadAllTextAsync(path)) ?? throw new InvalidOperationException("Нет задания обновления.");
        var folder = Path.Combine(App.DataFolder, "updates", "transactions", request.Token);
        if (!path.Equals(Path.Combine(folder, "request.json"), StringComparison.OrdinalIgnoreCase)
            || !Path.TrimEndingDirectorySeparator(request.InstallDirectory).Equals(Path.TrimEndingDirectorySeparator(AppContext.BaseDirectory), StringComparison.OrdinalIgnoreCase)
            || !request.DataDirectory.Equals(App.DataFolder, StringComparison.OrdinalIgnoreCase) || request.Version != NativeAppVersion)
            throw new InvalidOperationException("Новая версия не соответствует заданию обновления.");
        await Dispatcher.InvokeAsync(UpdateLayout, DispatcherPriority.ApplicationIdle);
        var ready = Path.Combine(folder, "ready");
        await File.WriteAllTextAsync(ready + ".tmp", $"{request.Token}|{Environment.ProcessId}|{NativeAppVersion}"); File.Move(ready + ".tmp", ready, true);
        for (var attempt = 0; attempt < 1000; attempt++)
        {
            var commit = Path.Combine(folder, "commit");
            if (File.Exists(commit) && await File.ReadAllTextAsync(commit) == request.Token && !UpdateGate.IsHeld(App.DataFolder))
            {
                IsEnabled = true; UpdateStatus.Text = "Обновление установлено. Настройки, история и очередь сохранены.";
                if (request.ResumeWorker) await WorkerProcess.EnsureStartedAsync();
                if (Environment.GetEnvironmentVariable("CROSSPOST_UPDATE_SMOKE") == "1") Close();
                return;
            }
            await Task.Delay(100);
        }
        throw new InvalidOperationException("Обновлятор не подтвердил завершение установки.");
    }
}
