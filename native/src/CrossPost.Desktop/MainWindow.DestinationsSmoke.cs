using System.IO;
using CrossPost.Core;

namespace CrossPost.Desktop;

public partial class MainWindow
{
    private async Task RunDestinationsSmokeAsync()
    {
        SelectSettingsCategory("destinations"); SwitchView(SettingsView);
        if (!SettingsDestinationsPanel.IsVisible) throw new InvalidOperationException("Destination navigation failed");
        ResetDestinationEditor(); DestinationName.Text = "Telegram SFW"; DestinationAddress.Text = "-100111"; DestinationRating.SelectedIndex = 1;
        await SaveDestinationAsync(CancellationToken.None);
        ResetDestinationEditor(); DestinationName.Text = "Telegram 18+"; DestinationAddress.Text = "-100222"; DestinationRating.SelectedIndex = 2;
        await SaveDestinationAsync(CancellationToken.None);
        ResetDestinationEditor(); DestinationPlatform.SelectedIndex = 1; DestinationName.Text = "Discord Art";
        const string secret = "https://discord.com/api/webhooks/123456789/abcdefghijklmnopqrstuvwxyz123456789";
        DestinationSecret.Password = secret;
        await SaveDestinationAsync(CancellationToken.None);
        var discord = _destinations.Single(p => p.Options.Platform == "discord");
        if (SecretVault.LoadDestination(discord.Id) != secret || SecretVault.Load("discord").Length != 0)
            throw new InvalidOperationException("Webhook destination secret leaked into the primary connection");
        // Re-read settings to verify they survive a fresh window; secrets never go into documents.
        await InitializeDestinationsAsync();
        if (_destinations.Length != 3 || ExtraDestinations.Items.Count != 3) throw new InvalidOperationException("Saved destinations lost");
        var json = System.Text.Json.JsonSerializer.Serialize(_destinations);
        if (json.Contains("abcdefghijklmnopqrstuvwxyz")) throw new InvalidOperationException("Webhook secret present in settings JSON");
        foreach (var profile in _destinations) _extraSelected.Add(profile.Id);
        TargetTelegram.IsChecked = TargetDiscord.IsChecked = TargetBluesky.IsChecked = false;
        BlueskyRating.SelectedIndex = 2; RefreshDestinations();
        var targets = SelectedTargets();
        if (targets.Length != 2 || targets.Any(t => t.Options.Chat == "-100111")) throw new InvalidOperationException("Rating filter selected a wrong channel");
        var jobs = await new QueuePlanner(_store).AddBatchAsync(CurrentDraft, targets, DateTimeOffset.UtcNow.AddSeconds(20));
        await _store.SaveDocumentAsync("destination-worker-smoke-ids", jobs.Select(j => j.Id).ToArray());
        BlueskyRating.SelectedIndex = 0; RefreshDestinations();
        if (SelectedTargets().Length != 2 || SelectedTargets().Any(t => t.Options.Chat == "-100222")) throw new InvalidOperationException("SFW filter selected adult channel");
        MainContent.IsEnabled = true; await CaptureWindowAsync("10-destination-settings");
        _extraSelected.Clear(); RefreshDestinations(); ResetDestinationEditor(); TargetTelegram.IsChecked = true;
        await SaveDraftAsync(); ApplyMode(); SwitchView(ComposeView); SelectSettingsCategory("general");
        File.WriteAllText(Path.Combine(App.DataFolder, "destinations-smoke-ready.txt"), "Multiple destinations, ratings, DPAPI and persistence checked");
    }
}
