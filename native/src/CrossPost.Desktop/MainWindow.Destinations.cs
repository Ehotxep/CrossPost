using System.IO;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Controls;
using CrossPost.Core;

namespace CrossPost.Desktop;

public record DestinationChoice(DestinationProfile Profile, bool Selected, bool Eligible)
{
    public string Label => Profile.ToString() + (Eligible ? "" : " · не подходит рейтингу / отключено");
}
public partial class MainWindow
{
    private DestinationProfile[] _destinations = [];
    private HashSet<string> _extraSelected = [];
    private bool _refreshingDestinations;
    private string? _editingDestination;
    private static string RatingRule(ComboBox box) => box.SelectedIndex switch { 1 => "sfw", 2 => "nsfw", _ => "all" };
    private static int RatingIndex(string rule) => rule switch { "sfw" => 1, "nsfw" => 2, _ => 0 };
    private async Task InitializeDestinationsAsync()
    {
        _destinations = await _store.ReadDocumentAsync<DestinationProfile[]>("destinations") ?? [];
        _extraSelected = (await _store.ReadDocumentAsync<string[]>("selected-destinations") ?? []).ToHashSet();
        PrimaryTelegramRating.SelectedIndex = RatingIndex(_options.RatingRule); PrimaryDiscordRating.SelectedIndex = RatingIndex(_discord.RatingRule);
        RefreshDestinations(); ResetDestinationEditor();
    }
    private void RefreshDestinations()
    {
        if (ExtraDestinations is null) return;
        _refreshingDestinations = true;
        try
        {
            ExtraDestinations.ItemsSource = _destinations.Select(p => new DestinationChoice(p, _extraSelected.Contains(p.Id), p.Enabled && DestinationRouting.Accepts(p.Options.RatingRule, SelectedBlueskyLabel))).ToArray();
            DestinationSettingsList.ItemsSource = _destinations;
            ExtraDestinationHeading.Visibility = _destinations.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        }
        finally { _refreshingDestinations = false; }
    }
    private void ExtraDestinationChanged(object sender, RoutedEventArgs e)
    {
        if (_refreshingDestinations || !_loaded || sender is not CheckBox { DataContext: DestinationChoice choice } box) return;
        if (!ExtraDestinations.Items.Cast<DestinationChoice>().Any(item => ReferenceEquals(item, choice))) return;
        if ((box.IsChecked == true) == _extraSelected.Contains(choice.Profile.Id)) return;
        if (box.IsChecked == true) _extraSelected.Add(choice.Profile.Id); else _extraSelected.Remove(choice.Profile.Id);
        PlatformPicker.SelectedIndex = choice.Profile.Options.Platform == "discord" ? 1 : 0;
        RenderTargetSummary(); _draftTimer.Stop(); _draftTimer.Start();
    }
    private void ShowDestinations(object sender, RoutedEventArgs e) { SelectSettingsCategory("destinations"); SwitchView(SettingsView); }
    private void NewDestination(object sender, RoutedEventArgs e) { if (!_busy) ResetDestinationEditor(); }
    private void ResetDestinationEditor()
    {
        _editingDestination = null; DestinationSettingsList.SelectedItem = null; DestinationName.Clear(); DestinationAddress.Clear(); DestinationSecret.Clear();
        DestinationPlatform.SelectedIndex = 0; DestinationDemo.IsChecked = DestinationEnabled.IsChecked = true;
        DestinationSpoiler.IsChecked = false; DestinationEmbeds.IsChecked = true; DestinationRating.SelectedIndex = 0;
        DestinationEditorHeading.Text = "Новое назначение"; UpdateDestinationEditorVisibility();
    }
    private void DestinationPlatformChanged(object sender, SelectionChangedEventArgs e) => UpdateDestinationEditorVisibility();
    private void UpdateDestinationEditorVisibility()
    {
        if (DestinationSecretPanel is null) return;
        var discord = DestinationPlatform.SelectedIndex == 1;
        DestinationSecretPanel.Visibility = discord ? Visibility.Visible : Visibility.Collapsed;
        DestinationTelegramPanel.Visibility = discord ? Visibility.Collapsed : Visibility.Visible;
        DestinationDiscordOptions.Visibility = discord ? Visibility.Visible : Visibility.Collapsed;
    }
    private void DestinationSettingsSelected(object sender, SelectionChangedEventArgs e)
    {
        if (_busy || DestinationSettingsList.SelectedItem is not DestinationProfile profile) return;
        _editingDestination = profile.Id; DestinationEditorHeading.Text = "Изменить назначение";
        DestinationName.Text = profile.Name; DestinationPlatform.SelectedIndex = profile.Options.Platform == "discord" ? 1 : 0;
        DestinationAddress.Text = profile.Options.Platform == "telegram" ? profile.Options.Chat : "";
        DestinationSecret.Password = profile.Options.Platform == "discord" ? SecretVault.LoadDestination(profile.Id) : "";
        DestinationDemo.IsChecked = profile.Options.Demo; DestinationEnabled.IsChecked = profile.Enabled;
        DestinationSpoiler.IsChecked = profile.Options.Spoiler; DestinationEmbeds.IsChecked = profile.Options.SuppressEmbeds;
        DestinationRating.SelectedIndex = RatingIndex(profile.Options.RatingRule); UpdateDestinationEditorVisibility();
    }
    private async void SaveDestination(object sender, RoutedEventArgs e) => await RunOperation(SaveDestinationAsync);
    private async Task SaveDestinationAsync(CancellationToken ct)
    {
        var id = _editingDestination ?? Guid.NewGuid().ToString("N");
        if ((await _store.QueueAsync()).Any(j => j.Options.DestinationId == id && j.State == QueueState.Running)) throw new InvalidOperationException("Назначение сейчас отправляет пост. Дождитесь завершения перед изменением.");
        var name = DestinationName.Text.Trim(); if (name.Length is 0 or > 100) throw new InvalidOperationException("Введите название назначения до 100 символов.");
        var platform = DestinationPlatform.SelectedIndex == 1 ? "discord" : "telegram";
        var demo = DestinationDemo.IsChecked != false; var rating = RatingRule(DestinationRating);
        var chat = DestinationAddress.Text.Trim(); var credential = ""; var secret = DestinationSecret.Password.Trim();
        if (platform == "telegram")
        {
            if (demo && chat.Length == 0) chat = "demo-" + id;
            else if (!Regex.IsMatch(chat, @"^(?:@[A-Za-z][A-Za-z0-9_]{3,}|-?[1-9]\d{0,30})$")) throw new InvalidOperationException("Укажите @имя или числовой ID канала Telegram.");
            if (!demo) credential = TelegramClient.BotId(SecretVault.Load());
        }
        else if (secret.Length > 0)
        {
            var endpoint = DiscordEndpoint.Parse(secret); credential = endpoint.Id;
            if (!demo) chat = (await new DiscordClient(_http, endpoint, "").InspectAsync(ct)).ChannelId;
            else chat = "demo-" + id;
        }
        else if (!demo) throw new InvalidOperationException("Укажите webhook Discord.");
        else chat = "demo-" + id;
        var options = new AppOptions(demo, chat, platform, DestinationSpoiler.IsChecked == true, DestinationEmbeds.IsChecked != false, DestinationId: id, RatingRule: rating);
        if (_destinations.Any(p => p.Id != id && p.Options.Platform == platform && p.Options.Chat == chat && p.Options.Demo == demo)) throw new InvalidOperationException("Такое назначение уже есть в списке.");
        if (!demo && ((platform == "telegram" && !_options.Demo && _options.Chat == chat) || (platform == "discord" && !_discord.Demo && _discord.ChannelId == chat))) throw new InvalidOperationException("Это назначение уже используется как основное подключение.");
        var profile = new DestinationProfile(id, name, options, credential, DestinationEnabled.IsChecked != false);
        var updated = _destinations.Where(p => p.Id != id).Append(profile).ToArray();
        ct.ThrowIfCancellationRequested(); MainContent.IsEnabled = false;
        await MigrationSettingsCommit.ApplyAsync(_store, new() { ["destinations"] = updated },
            new() { ["destination:" + id] = platform == "discord" ? secret : "" });
        _destinations = updated; RefreshDestinations(); ResetDestinationEditor(); RenderTargetSummary();
        StatusLabel.Text = "Назначение сохранено. Проверка Discord только читает канал; посты не публиковались.";
    }
    private async void DeleteDestination(object sender, RoutedEventArgs e)
    {
        if (_busy || _editingDestination is not { } id) return;
        if (MessageBox.Show(this, "Удалить назначение и его webhook? Редактирование старых сообщений через это назначение станет недоступно. Чтобы сохранить его, вместо удаления снимите флажок «Назначение включено».", "Удаление назначения", MessageBoxButton.YesNo) != MessageBoxResult.Yes) return;
        await RunOperation(async _ =>
        {
            if ((await _store.QueueAsync()).Any(j => j.Options.DestinationId == id && j.State is QueueState.Pending or QueueState.Paused or QueueState.Running)) throw new InvalidOperationException("Сначала завершите или отмените задания этого назначения.");
            MainContent.IsEnabled = false;
            var updated = _destinations.Where(p => p.Id != id).ToArray();
            await _store.SaveDocumentAsync("destinations", updated); SecretVault.DeleteDestination(id);
            _destinations = updated; _extraSelected.Remove(id); await _store.SaveDocumentAsync("selected-destinations", _extraSelected.ToArray());
            RefreshDestinations(); ResetDestinationEditor(); RenderTargetSummary(); StatusLabel.Text = "Назначение удалено.";
        });
    }
}
