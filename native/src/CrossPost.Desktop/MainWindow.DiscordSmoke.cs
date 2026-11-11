using System.IO;
using System.Text;
using System.Windows;
using CrossPost.Core;

namespace CrossPost.Desktop;

public partial class MainWindow
{
    private async Task RunDiscordSmokeAsync()
    {
        const string secret = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        const string hook = "https://discord.com/api/webhooks/123/" + secret;
        var telegramBefore = SecretVault.Load();
        SecretVault.Save(hook, "discord");
        if (SecretVault.Load("discord") != hook || SecretVault.Load() != telegramBefore
            || Encoding.UTF8.GetString(await File.ReadAllBytesAsync(Path.Combine(App.DataFolder, "discord-webhook.dpapi"))).Contains(secret))
            throw new InvalidOperationException("Discord credential protection or Telegram secret isolation failed");
        _discord = new(Demo: true, Spoiler: true); await _store.SaveDocumentAsync("discord-options", _discord);
        PlatformPicker.SelectedIndex = 1; UpdateLayout();
        if (!ModeLabel.Text.Contains("Discord") || !CountLabel.Text.Contains("2000"))
            throw new InvalidOperationException("Discord composer selection/limits failed");
        var sent = await new Workflow(_store).SendAsync(Client(true, "discord"), "demo", CurrentOptions, CurrentDraft, CancellationToken.None);
        await LoadHistoryAsync(); HistoryList.SelectedItem = History.Single(row => row.Record.Id == sent.Id);
        if (!EditButton.IsEnabled || _selected?.Platform != "discord" || _selected.Delivery.AttachmentIds?.Length != 2)
            throw new InvalidOperationException("Discord history/edit controls failed");
        var edit = await new Workflow(_store).EditAsync(Client(true, "discord"), sent, "Демо Discord изменено", CancellationToken.None);
        if (!edit.Result.Confirmed || (await _store.LibraryAsync()).Posts.Single(post => post.NativeId == sent.Id).Platform != "demo.discord")
            throw new InvalidOperationException("Discord edit/library projection failed");
        App.TraceSmoke("legacy protocol guard");
        await WorkerProcess.VerifyLegacyRefusalAsync();
        App.TraceSmoke("start current Worker");
        await WorkerProcess.EnsureStartedAsync();
        App.TraceSmoke("current Worker ready");
        var queued = await new QueuePlanner(_store).AddAsync(CurrentDraft, CurrentOptions, "demo", DateTimeOffset.UtcNow.AddSeconds(20));
        await _store.SaveDocumentAsync("discord-worker-smoke-id", queued.Id);
        PlatformPicker.SelectedIndex = 0; ApplyMode(); UpdatePreview();
    }
}
