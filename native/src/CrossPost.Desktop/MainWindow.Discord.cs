using System.Windows;
using System.Windows.Controls;
using CrossPost.Core;

namespace CrossPost.Desktop;

public partial class MainWindow
{
    private DiscordOptions _discord = new();
    private AppOptions CurrentOptions => PlatformPicker?.SelectedIndex switch { 1 => _discord.PublishOptions, 2 => _bluesky.PublishOptions with { ContentLabel = SelectedBlueskyLabel }, _ => _options };
    private string CurrentPlatformLabel => PublishLimits.Name(CurrentOptions.Platform);
    private string CurrentCredentialId => CurrentOptions.Platform switch { "discord" => DiscordEndpoint.Parse(SecretVault.Load("discord")).Id, "bluesky" => _bluesky.Did, _ => TelegramClient.BotId(SecretVault.Load()) };
    private static bool CanEdit(Publication? publication) => publication?.Platform != "bluesky" && (publication?.Delivery.EditMessageId is not null || publication?.Delivery.RemoteMessageId is not null);
    private async Task InitializeDiscordAsync()
    {
        _discord = await _store.ReadDocumentAsync<DiscordOptions>("discord-options") ?? new();
        if (App.SmokeTest) _discord = _discord with { Demo = true };
        DiscordDemoInput.IsChecked = _discord.Demo; DiscordWebhookInput.Password = SecretVault.Load("discord");
        DiscordSpoilerInput.IsChecked = _discord.Spoiler; DiscordEmbedsInput.IsChecked = _discord.SuppressEmbeds;
        DiscordTargetLabel.Text = _discord.ChannelId.Length > 0 ? $"{_discord.Name} • канал {_discord.ChannelId}" : "Канал пока не проверен";
    }
    private void PlatformChanged(object sender, SelectionChangedEventArgs e)
    { if (_loaded) { ApplyMode(); UpdatePreview(); UpdateBlueskyControls(); } }
    private async void SaveDiscordSettings(object sender, RoutedEventArgs e) => await RunOperation(async ct =>
    {
        var demo = DiscordDemoInput.IsChecked != false;
        var secret = DiscordWebhookInput.Password.Trim();
        DiscordWebhookInfo? info = null;
        if (secret.Length > 0)
        {
            var endpoint = DiscordEndpoint.Parse(secret);
            // Saving in demo mode never contacts Discord. A real destination must be read and confirmed.
            if (!demo) info = await new DiscordClient(_http, endpoint, "").InspectAsync(ct);
        }
        else if (!demo) throw new InvalidOperationException("Укажите ссылку webhook Discord.");
        SecretVault.Save(secret, "discord");
        _discord = new(demo, info?.Id ?? "", info?.ChannelId ?? "", info?.GuildId ?? "", info?.Name ?? "Discord", DiscordSpoilerInput.IsChecked == true, DiscordEmbedsInput.IsChecked != false);
        await _store.SaveDocumentAsync("discord-options", _discord);
        DiscordTargetLabel.Text = info is null ? "Деморежим: канал не проверялся" : $"{info.Name} • канал {info.ChannelId}";
        ApplyMode(); UpdatePreview(); StatusLabel.Text = "Настройки Discord сохранены. Проверка не публиковала сообщения.";
    });
    private async void CheckDiscord(object sender, RoutedEventArgs e) => await RunOperation(async ct =>
    {
        if (DiscordDemoInput.IsChecked != false) { StatusLabel.Text = "ДЕМО Discord: сетевые запросы отключены."; return; }
        var endpoint = DiscordEndpoint.Parse(DiscordWebhookInput.Password.Trim());
        var info = await new DiscordClient(_http, endpoint, "").InspectAsync(ct);
        DiscordTargetLabel.Text = $"{info.Name} • канал {info.ChannelId}";
        StatusLabel.Text = "Webhook доступен. Сообщения не публиковались; сохраните настройки для отправки.";
    });
}
