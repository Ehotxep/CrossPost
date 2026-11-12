using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Windows;
using CrossPost.Core;
using Microsoft.Win32;

namespace CrossPost.Desktop;

public partial class MainWindow
{
    private MigrationRecord? _migration;
    private static readonly byte[] MigrationEntropy = Encoding.UTF8.GetBytes("CrossPost.Native.PythonProfile.v1");
    private static byte[] WrapMigrationKey(byte[] key) => ProtectedData.Protect(key, MigrationEntropy, DataProtectionScope.CurrentUser);
    private static byte[] UnwrapMigrationKey(byte[] key) => ProtectedData.Unprotect(key, MigrationEntropy, DataProtectionScope.CurrentUser);
    private async Task InitializeMigrationAsync()
    {
        MigrationSource.Text = Environment.GetEnvironmentVariable("CROSSPOST_DATA") is { Length: > 0 } configured
            ? configured : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Crosspost");
        _migration = await _store.ReadDocumentAsync<MigrationRecord>("python-migration");
        if (_migration is not null) await ShowMigrationAsync();
    }
    private void ChooseMigrationSource(object sender, RoutedEventArgs e)
    {
        if (_busy) return;
        var picker = new OpenFolderDialog { Title = "Папка данных Python-версии CrossPost" };
        if (picker.ShowDialog(this) == true) { MigrationSource.Text = picker.FolderName; MigrationPythonClosed.IsChecked = false; }
    }
    private async void ImportPythonProfile(object sender, RoutedEventArgs e) => await RunOperation(CreateMigrationAsync);
    private async Task CreateMigrationAsync(CancellationToken ct)
    {
        if (MigrationPythonClosed.IsChecked != true) throw new InvalidOperationException("Сначала закройте Python-версию, её очередь и браузеры; подтвердите это флажком.");
        var source = Path.GetFullPath(MigrationSource.Text);
        var nativeRoot = Path.GetFullPath(App.DataFolder);
        bool Contains(string a, string b) => a.Equals(b, StringComparison.OrdinalIgnoreCase) || a.StartsWith(Path.TrimEndingDirectorySeparator(b) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
        if (Contains(source, nativeRoot) || Contains(nativeRoot, source)) throw new InvalidOperationException("Выберите отдельную папку Python-версии.");
        if (!new[] { "publish.json", "ui.json", "posts.db" }.Any(name => File.Exists(Path.Combine(source, name))))
            throw new InvalidOperationException("Не найдены данные CrossPost. Нужна папка данных, а не папка исходного кода.");
        await SaveDraftAsync(); _draftTimer.Stop();
        MainContent.IsEnabled = false;
        var batch = Path.Combine(App.DataFolder, "python-migrations", Guid.NewGuid().ToString("N"));
        var progress = new Progress<string>(text => StatusLabel.Text = text);
        var record = await Task.Run(async () =>
        {
            var locks = new List<FileStream>();
            try
            {
                foreach (var name in new[] { "app.lock", "worker.lock" })
                {
                    var path = Path.Combine(source, name);
                    if (File.Exists(path))
                    {
                        try { locks.Add(new(path, FileMode.Open, FileAccess.Read, FileShare.Read)); }
                        catch (IOException) { throw new IOException("Python-версия или её очередь ещё работает. Закройте её перед импортом."); }
                    }
                }
                Directory.CreateDirectory(batch);
                using var archive = await EncryptedProfileArchive.CreateAsync(source, Path.Combine(batch, "archive"), WrapMigrationKey, ct, progress);
                try { return await PythonProfile.PrepareAsync(archive, source, Path.Combine(batch, "media"), ct); }
                catch (System.Text.Json.JsonException)
                { return new MigrationRecord(archive.Folder, source, [], ["Формат одного из JSON-файлов не распознан. Исходники сохранены; доступно восстановление файлов из копии."]); }
            }
            finally { foreach (var file in locks) file.Dispose(); }
        }, ct);
        // A failed/cancelled analysis leaves the protected source copy recoverable, never an active queue.
        await _store.SaveDocumentAsync("python-migration", record); _migration = record;
        await ShowMigrationAsync();
        StatusLabel.Text = "Копия профиля создана. Выберите настройки для переноса и проверьте черновики.";
    }
    private async Task ShowMigrationAsync()
    {
        if (_migration is null) return;
        MigrationItems.ItemsSource = _migration.Items;
        MigrationSummary.Text = $"Записей для проверки: {_migration.Items.Length}.\n" + string.Join("\n", _migration.Notes);
        try
        {
            using var archive = await EncryptedProfileArchive.OpenAsync(_migration.Archive, UnwrapMigrationKey);
            var settings = await PythonProfile.SettingsAsync(archive);
            MigrationChannels.ItemsSource = settings.Channels; MigrationWebhooks.ItemsSource = settings.DiscordDestinations;
            MigrationChannels.SelectedIndex = settings.Channels.Length == 1 ? 0 : -1;
            MigrationWebhooks.SelectedIndex = settings.DiscordDestinations.Length == 1 ? 0 : -1;
            MigrationTelegram.IsChecked = MigrationDiscord.IsChecked = MigrationBluesky.IsChecked = false;
        }
        catch (Exception error) when (error is IOException or CryptographicException or System.Text.Json.JsonException)
        { MigrationSummary.Text += "\nКопия недоступна для чтения. Настройки не изменены; выберите исходную папку и повторите перенос."; }
    }
    private async void ApplyMigrationSettings(object sender, RoutedEventArgs e) => await RunOperation(ApplyMigrationSettingsAsync);
    private async Task ApplyMigrationSettingsAsync(CancellationToken ct)
    {
        if (_migration is null) throw new InvalidOperationException("Сначала создайте копию Python-профиля.");
        if (!(MigrationTelegram.IsChecked == true || MigrationDiscord.IsChecked == true || MigrationBluesky.IsChecked == true))
            throw new InvalidOperationException("Выберите площадки для переноса.");
        if (WorkerProcess.IsRunning || (await _store.QueueAsync()).Any(j => j.State is QueueState.Pending or QueueState.Running or QueueState.Paused))
            throw new InvalidOperationException("Перед заменой подключений остановите нативную очередь и завершите или отмените её незавершённые задания.");
        using var archive = await EncryptedProfileArchive.OpenAsync(_migration.Archive, UnwrapMigrationKey, ct);
        var settings = await PythonProfile.SettingsAsync(archive, ct);
        var documents = new Dictionary<string, object>(); var secrets = new Dictionary<string, string>();
        var replace = MigrationReplace.IsChecked == true;
        if (MigrationTelegram.IsChecked == true)
        {
            if (MigrationChannels.SelectedItem is not MigrationDestination channel) throw new InvalidOperationException("Выберите канал Telegram.");
            if (!replace && (_options.Chat.Length > 0 || SecretVault.Exists("telegram"))) throw new InvalidOperationException("Telegram уже настроен. Для замены установите флажок разрешения.");
            _ = TelegramClient.BotId(settings.TelegramToken);
            documents["options"] = settings.Telegram with { Chat = channel.Id, RatingRule = channel.RatingRule }; secrets["telegram"] = settings.TelegramToken;
        }
        if (MigrationDiscord.IsChecked == true)
        {
            if (MigrationWebhooks.SelectedItem is not MigrationDestination selected || !int.TryParse(selected.Id, out var index)) throw new InvalidOperationException("Выберите webhook Discord.");
            if (!replace && (_discord.WebhookId.Length > 0 || SecretVault.Exists("discord"))) throw new InvalidOperationException("Discord уже настроен. Для замены установите флажок разрешения.");
            var secret = settings.Webhooks[index]; var endpoint = DiscordEndpoint.Parse(secret);
            documents["discord-options"] = settings.Discord with { WebhookId = endpoint.Id, Name = selected.Name, RatingRule = selected.RatingRule }; secrets["discord"] = secret;
        }
        if (MigrationBluesky.IsChecked == true)
        {
            if (!replace && (_bluesky.Handle.Length > 0 || SecretVault.Exists("bluesky"))) throw new InvalidOperationException("Bluesky уже настроен. Для замены установите флажок разрешения.");
            documents["bluesky-options"] = settings.Bluesky; secrets["bluesky"] = "";
        }
        ct.ThrowIfCancellationRequested();
        MainContent.IsEnabled = false;
        await _store.BackupAsync("before-python-settings");
        await MigrationSettingsCommit.ApplyAsync(_store, documents, secrets);
        _options = await _store.ReadDocumentAsync<AppOptions>("options") ?? new();
        PrimaryTelegramRating.SelectedIndex = RatingIndex(_options.RatingRule);
        DemoInput.IsChecked = _options.Demo; ChatInput.Text = _options.Chat; TokenInput.Password = SecretVault.Load();
        await InitializeDiscordAsync(); await InitializeBlueskyAsync(CurrentDraft);
        TargetTelegram.IsChecked = TargetDiscord.IsChecked = TargetBluesky.IsChecked = false;
        _extraSelected.Clear(); RefreshDestinations();
        ApplyMode(); UpdatePreview();
        MigrationReplace.IsChecked = false;
        StatusLabel.Text = "Выбранные настройки перенесены в деморежиме. Проверьте подключения в настройках площадок перед реальной отправкой.";
    }
    private async void OpenMigrationDraft(object sender, RoutedEventArgs e)
    {
        if (_busy || MigrationItems.SelectedItem is not MigrationItem item) return;
        var explanation = string.Join("\n\n", item.Warnings) + "\n\nТекущий черновик будет заменён. Открыть выбранную запись?";
        if (MessageBox.Show(this, explanation, "Проверка перенесённого черновика", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        await RunOperation(async _ =>
        {
            MainContent.IsEnabled = false; _draftTimer.Stop(); _loaded = false;
            try
            {
                var draft = item.Draft; TitleInput.Text = draft.Title; BodyInput.Text = draft.Text;
                _images.Clear(); _images.AddRange(draft.Images); _alts.Clear(); _alts.AddRange(Enumerable.Range(0, draft.Images.Length).Select(draft.AltFor));
                RefreshImages(); await InitializeBlueskyAsync(draft); await InitializeTextsAsync(draft);
                TargetTelegram.IsChecked = TargetDiscord.IsChecked = TargetBluesky.IsChecked = false;
        _extraSelected.Clear(); RefreshDestinations();
            }
            finally { _loaded = true; }
            await SaveDraftAsync(); ApplyMode(); UpdatePreview(); SwitchView(ComposeView);
            StatusLabel.Text = "Черновик открыт для проверки. Выберите площадки и проверьте предпросмотр.";
        });
    }
    private async void ImportMigrationHistory(object sender, RoutedEventArgs e) => await RunOperation(async ct =>
    {
        if (_migration is null) throw new InvalidOperationException("Сначала создайте копию профиля.");
        MainContent.IsEnabled = false;
        var migration = _migration;
        var progress = new Progress<string>(text => StatusLabel.Text = text);
        var report = await Task.Run(async () =>
        {
            using var archive = await EncryptedProfileArchive.OpenAsync(migration.Archive, UnwrapMigrationKey, ct);
            var temporary = Path.Combine(archive.Folder, "history-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(temporary);
            try
            {
                foreach (var name in new[] { "posts.db", "posts.db-wal" })
                    if (archive.Files.Any(f => f.Path == name)) await archive.CopyAsync(name, Path.Combine(temporary, name), ct);
                return await new LegacyImporter(_store).ImportAsync(Path.Combine(temporary, "posts.db"), ct, progress,
                    sourceIdentity: Path.Combine(migration.Source, "posts.db"), retainSource: false);
            }
            finally { Directory.Delete(temporary, true); }
        }, ct);
        await LoadLibraryAsync();
        MigrationSummary.Text += $"\nИстория: добавлено публикаций {report.Publications}, замеров {report.Measurements}. Повторный импорт не создаёт дубликатов.";
        StatusLabel.Text = "История и статистика перенесены. Исходная база остаётся в защищённой копии.";
    });
    private async void OpenMigrationArchive(object sender, RoutedEventArgs e)
    {
        if (_busy) return;
        var picker = new OpenFolderDialog { Title = "Выберите папку archive с key.dpapi и manifest.bin" };
        if (picker.ShowDialog(this) != true) return;
        await RunOperation(async ct =>
        {
            MainContent.IsEnabled = false;
            using var archive = await EncryptedProfileArchive.OpenAsync(picker.FolderName, UnwrapMigrationKey, ct);
            var media = Path.Combine(App.DataFolder, "python-migrations", Guid.NewGuid().ToString("N"), "media");
            MigrationRecord record;
            try { record = await Task.Run(() => PythonProfile.PrepareAsync(archive, archive.Source, media, ct), ct); }
            catch (System.Text.Json.JsonException)
            { record = new(archive.Folder, archive.Source, [], ["Формат настроек не распознан. Копия доступна для восстановления исходных файлов."]); }
            await _store.SaveDocumentAsync("python-migration", record); _migration = record;
            await ShowMigrationAsync(); StatusLabel.Text = "Защищённая копия открыта.";
        });
    }
    private async void RestoreMigrationArchive(object sender, RoutedEventArgs e)
    {
        if (_busy || _migration is null) return;
        var picker = new OpenFolderDialog { Title = "Выберите родительскую папку для восстановления исходных файлов" };
        if (picker.ShowDialog(this) != true) return;
        var target = Path.Combine(picker.FolderName, "CrossPost-Python-recovered-" + Guid.NewGuid().ToString("N"));
        if (MessageBox.Show(this, "Будут восстановлены все исходные файлы, в том числе API-ключи и сессии, в папку:\n" + target + "\n\nПродолжить?", "Восстановление профиля", MessageBoxButton.YesNo, MessageBoxImage.Warning) != MessageBoxResult.Yes) return;
        await RunOperation(async ct =>
        {
            MainContent.IsEnabled = false;
            using var archive = await EncryptedProfileArchive.OpenAsync(_migration.Archive, UnwrapMigrationKey, ct);
            await Task.Run(() => archive.RestoreAsync(target, ct), ct);
            StatusLabel.Text = "Исходные файлы восстановлены в " + target;
        });
    }
}
