using System.IO;
using System.Security.Cryptography;
using System.Text.Json;
using System.Windows;
using CrossPost.Core;

namespace CrossPost.Desktop;

public partial class MainWindow
{
    private async Task RunMigrationSmokeAsync()
    {
        var source = Path.Combine(Path.GetTempPath(), "crosspost-python-ui-" + Guid.NewGuid().ToString("N"));
        var credentials = SecretVault.Capture(["telegram", "discord", "bluesky"]);
        var originalTelegram = _options; var originalDiscord = _discord; var originalBluesky = _bluesky;
        Directory.CreateDirectory(Path.Combine(source, "uploads", "0123456789abcdef"));
        try
        {
            File.Copy(Path.Combine(App.DataFolder, "first.png"), Path.Combine(source, "uploads", "0123456789abcdef", "original.png"));
            await File.WriteAllTextAsync(Path.Combine(source, "publish.json"), """
                {"telegram":{"bot_token":"123:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","channels":[{"id":"-100123","title":"Imported demo"}]},"discord":{"webhooks":[{"name":"Imported art","url":"https://discord.com/api/webhooks/123456789/abcdefghijklmnopqrstuvwxyz123456789"}]},"bluesky":{"handle":"imported.bsky.social"},"unknown_key":"archive-secret"}
                """);
            await File.WriteAllTextAsync(Path.Combine(source, "ui.json"), """
                {"draft":{"title":"Из Python","text":"Проверка переноса","files":["0123456789abcdef"],"variants":{"en":{"title":"Imported","text":"English body"}}}}
                """);
            SelectSettingsCategory("migration"); SwitchView(SettingsView);
            if (!SettingsMigrationPanel.IsVisible || SettingsGeneralPanel.IsVisible) throw new InvalidOperationException("Migration settings navigation failed");
            MigrationSource.Text = source; MigrationPythonClosed.IsChecked = true;
            await CreateMigrationAsync(CancellationToken.None);
            if (_migration?.Items.Length != 1 || MigrationItems.Items.Count != 1 || MigrationChannels.SelectedIndex != 0 || MigrationWebhooks.SelectedIndex != 0)
                throw new InvalidOperationException("Migration inventory or account selection failed");
            using (var archive = await EncryptedProfileArchive.OpenAsync(_migration.Archive, UnwrapMigrationKey))
            {
                var settings = await PythonProfile.SettingsAsync(archive);
                if (settings.TelegramToken != "123:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") throw new InvalidOperationException("DPAPI archive did not recover API token");
                var refused = false;
                try { ProtectedData.Unprotect(File.ReadAllBytes(Path.Combine(archive.Folder, "key.dpapi")), [1, 2, 3], DataProtectionScope.CurrentUser); }
                catch (CryptographicException) { refused = true; }
                if (!refused) throw new InvalidOperationException("Wrong DPAPI archive context accepted");
            }
            await CaptureWindowAsync("09-python-migration");
            MigrationReplace.IsChecked = true; MigrationTelegram.IsChecked = MigrationDiscord.IsChecked = MigrationBluesky.IsChecked = true;
            await ApplyMigrationSettingsAsync(CancellationToken.None);
            if (!_options.Demo || _options.Chat != "-100123" || !_discord.Demo || !_bluesky.Demo || _bluesky.Handle != "imported.bsky.social"
                || SecretVault.Load() != "123:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" || !SecretVault.Load("discord").Contains("abcdefghijklmnopqrstuvwxyz")
                || TargetTelegram.IsChecked == true || TargetDiscord.IsChecked == true || TargetBluesky.IsChecked == true)
                throw new InvalidOperationException("Migration settings activation or demo guard failed");
            // Simulate a process exit between vault replacement and SQLite commit.
            var before = SecretVault.Capture(["telegram"]); var journalPath = Path.Combine(App.DataFolder, "migration-settings-journal.json");
            await File.WriteAllTextAsync(journalPath, JsonSerializer.Serialize(new { Id = Guid.NewGuid(), Secrets = before }));
            SecretVault.Save("uncommitted-token");
            await MigrationSettingsCommit.RecoverAsync(_store);
            if (SecretVault.Load() != "123:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" || File.Exists(journalPath)) throw new InvalidOperationException("Interrupted credential migration did not recover");
            var committed = Guid.NewGuid(); await _store.SaveDocumentAsync("migration-settings-commit", committed);
            await File.WriteAllTextAsync(journalPath, JsonSerializer.Serialize(new { Id = committed, Secrets = before }));
            SecretVault.Save("committed-token"); await MigrationSettingsCommit.RecoverAsync(_store);
            if (SecretVault.Load() != "committed-token") throw new InvalidOperationException("Committed credentials were rolled back");
            await File.WriteAllTextAsync(Path.Combine(App.DataFolder, "migration-smoke-ready.txt"), "DPAPI profile archive, account choices, demo settings and crash recovery checked without network requests");
        }
        finally
        {
            SecretVault.Restore(credentials);
            await _store.SaveDocumentAsync("options", originalTelegram); await _store.SaveDocumentAsync("discord-options", originalDiscord); await _store.SaveDocumentAsync("bluesky-options", originalBluesky);
            _options = originalTelegram; DemoInput.IsChecked = _options.Demo; ChatInput.Text = _options.Chat; TokenInput.Password = SecretVault.Load();
            await InitializeDiscordAsync(); await InitializeBlueskyAsync(CurrentDraft);
            TargetTelegram.IsChecked = true; TargetDiscord.IsChecked = TargetBluesky.IsChecked = false;
            MigrationTelegram.IsChecked = MigrationDiscord.IsChecked = MigrationBluesky.IsChecked = false;
            ApplyMode(); SwitchView(ComposeView); Directory.Delete(source, true);
            MainContent.IsEnabled = true; SelectSettingsCategory("general");
        }
    }
}
