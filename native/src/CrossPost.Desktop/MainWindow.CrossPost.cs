using System.Windows;
using CrossPost.Core;
namespace CrossPost.Desktop;

public partial class MainWindow
{
    private void TargetSelectionChanged(object sender, RoutedEventArgs e)
    {
        if (!_loaded) return;
        if (sender is System.Windows.Controls.CheckBox { IsChecked: true } choice)
            PlatformPicker.SelectedIndex = choice == TargetDiscord ? 1 : choice == TargetBluesky ? 2 : 0;
        RenderTargetSummary(); _draftTimer.Stop(); _draftTimer.Start();
    }
    private void RenderTargetSummary()
    {
        if (TargetSummary is null) return;
        var targets = new[] { (TargetTelegram.IsChecked == true, _options), (TargetDiscord.IsChecked == true, _discord.PublishOptions), (TargetBluesky.IsChecked == true, _bluesky.PublishOptions) };
        TargetSummary.Text = string.Join(" • ", targets.Where(value => value.Item1).Select(value => PublishLimits.Name(value.Item2.Platform) + ": "
            + (value.Item2.Demo ? "ДЕМО" : value.Item2.Chat.Length > 0 ? value.Item2.Chat : "не настроено")));
        if (TargetSummary.Text.Length == 0) TargetSummary.Text = "Выберите хотя бы одно назначение.";
    }
    private string[] SelectedPlatformNames() => new[] { (TargetTelegram.IsChecked == true,"telegram"), (TargetDiscord.IsChecked == true,"discord"), (TargetBluesky.IsChecked == true,"bluesky") }
        .Where(value => value.Item1).Select(value => value.Item2).ToArray();
    private PublishTarget[] SelectedTargets()
    {
        var result = new List<PublishTarget>();
        if (TargetTelegram.IsChecked == true) result.Add(new(_options, _options.Demo ? "demo" : TelegramClient.BotId(SecretVault.Load())));
        if (TargetDiscord.IsChecked == true) result.Add(new(_discord.PublishOptions, _discord.Demo ? "demo" : DiscordEndpoint.Parse(SecretVault.Load("discord")).Id));
        if (TargetBluesky.IsChecked == true) result.Add(new(_bluesky.PublishOptions with { ContentLabel = SelectedBlueskyLabel }, _bluesky.Demo ? "demo" : _bluesky.Did));
        return result.ToArray();
    }
    private async Task EnqueueTargetsAsync(DateTimeOffset due, bool immediate) => await RunOperation(async ct =>
    {
        var targets = SelectedTargets(); var draft = CurrentDraft;
        if (targets.Length == 0) throw new InvalidOperationException("Отметьте хотя бы одно назначение публикации.");
        foreach (var target in targets) draft.Validate(target.Options.Platform);
        var details = string.Join("\n", targets.Select(target => $"{PublishLimits.Name(target.Options.Platform)} • {(target.Options.Demo ? "ДЕМО" : target.Options.Chat)}"));
        var uncertain = (await _store.HistoryAsync()).Where(post => post.Delivery.State is DeliveryState.Sending or DeliveryState.Unknown or DeliveryState.Partial)
            .Any(post => post.Draft.PlainText == draft.PlainText && targets.Any(target => target.Options.Platform == post.Platform && target.Options.Demo == post.Demo && (post.Options?.Chat ?? post.Delivery.ChatId) == target.Options.Chat));
        if (uncertain && MessageBox.Show("Есть неподтверждённая отправка этого текста. Проверьте историю и площадки. Повторить с риском дубликата?", "Проверка перед отправкой", MessageBoxButton.YesNo, MessageBoxImage.Warning) != MessageBoxResult.Yes) return;
        if (targets.Any(target => !target.Options.Demo) && MessageBox.Show((immediate ? "Опубликовать сейчас?" : $"Запланировать на {due.ToLocalTime():dd.MM.yyyy HH:mm}?") + "\n\n" + details,
            "Публикация", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        await SaveDraftAsync(); await WorkerProcess.EnsureStartedAsync();
        var jobs = await new QueuePlanner(_store).AddBatchAsync(draft, targets, due, ct);
        await LoadQueueAsync(); SwitchView(QueueView);
        QueueList.SelectedItem = Queue.FirstOrDefault(row => row.Record.Id == jobs[0].Id);
        StatusLabel.Text = $"Сохранено назначений: {jobs.Length}. Результат каждого появится в очереди и истории; отправки связаны в одну работу.";
    });
}
