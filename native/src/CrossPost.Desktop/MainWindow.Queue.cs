using System.Collections.ObjectModel;
using System.Windows;
using System.Windows.Controls;
using CrossPost.Core;

namespace CrossPost.Desktop;

public record QueueRow(ScheduledPost Record)
{
    public string DueText => Record.Due.ToLocalTime().ToString("dd.MM.yyyy HH:mm");
    public string Title => Record.Draft.Title.Length > 0 ? Record.Draft.Title : (Record.Draft.PlainText.Length > 0 ? Record.Draft.PlainText.Split('\n')[0] : "Изображения");
    public string Destination => Record.Options.Demo ? "ДЕМО" : Record.Options.Chat;
    public string Status => Record.State switch
    {
        QueueState.Pending => "Ожидает", QueueState.Paused => "Приостановлено", QueueState.Running => "Отправляется",
        QueueState.Completed => "Отправлено", QueueState.Refused => "Не отправлено", QueueState.Partial => "Частично отправлено",
        QueueState.Cancelled => "Отменено", _ => "Проверьте канал"
    };
}
public partial class MainWindow
{
    public ObservableCollection<QueueRow> Queue { get; } = [];
    private ScheduledPost? SelectedJob => (QueueList.SelectedItem as QueueRow)?.Record;
    private bool _refreshingQueue;
    private void InitializeQueueUi()
    {
        var initial = DateTime.Now.AddHours(1);
        ScheduleDate.SelectedDate = initial.Date; ScheduleClock.Text = initial.ToString("HH:mm");
        ScheduleZoneLabel.Text = "По расписанию • часовой пояс компьютера: " + TimeZoneInfo.Local.DisplayName;
    }
    private async Task LoadQueueAsync()
    {
        if (_refreshingQueue) return;
        _refreshingQueue = true;
        try
        {
            var selected = SelectedJob?.Id;
            Queue.Clear(); foreach (var job in await _store.QueueAsync()) Queue.Add(new(job));
            if (selected is not null) QueueList.SelectedItem = Queue.FirstOrDefault(row => row.Record.Id == selected);
            WorkerLabel.Text = WorkerProcess.IsRunning ? "Очередь работает в отдельном процессе и продолжит работу после закрытия окна."
                : "Фоновая очередь остановлена. Задания сохраняются; для выполнения нажмите «Запустить очередь».";
        }
        finally { _refreshingQueue = false; }
    }
    private async void ShowQueue(object sender, RoutedEventArgs e)
    { SwitchView(QueueView); await Guard(LoadQueueAsync); }
    private async void RefreshQueue(object sender, RoutedEventArgs e) => await Guard(LoadQueueAsync);
    private async void StartWorker(object sender, RoutedEventArgs e) => await Guard(async () =>
    { WorkerProcess.Start(); StatusLabel.Text = "Запущен фоновый процесс очереди."; await LoadQueueAsync(); });
    private async void StopWorker(object sender, RoutedEventArgs e) => await Guard(async () =>
    {
        if (WorkerProcess.IsRunning && MessageBox.Show(
            "Остановка может прервать отправку с неизвестным результатом. Остановить очередь?", "Очередь", MessageBoxButton.YesNo) != MessageBoxResult.Yes) return;
        StatusLabel.Text = await WorkerProcess.CommandAsync("stop") ? "Очередь останавливается. Ожидающие задания сохранены."
            : "Процесс недоступен. Если он прервался, при запуске результат будет проверен по истории.";
        await LoadQueueAsync();
    });
    private async void QueueNow(object sender, RoutedEventArgs e) => await EnqueueAsync(DateTimeOffset.UtcNow);
    private async void SchedulePost(object sender, RoutedEventArgs e) => await Guard(async () =>
    {
        if (ScheduleDate.SelectedDate is not { } date) throw new InvalidOperationException("Выберите дату публикации.");
        var due = ScheduleTime.FromLocal(date, ScheduleClock.Text, TimeZoneInfo.Local);
        if (due <= DateTimeOffset.UtcNow) throw new InvalidOperationException("Выберите время в будущем.");
        await EnqueueAsync(due);
    });
    private async Task EnqueueAsync(DateTimeOffset due) => await RunOperation(async _ =>
    {
        var draft = CurrentDraft; ValidateImages(draft);
        if (!_options.Demo && MessageBox.Show($"Разрешить автоматическую отправку в {_options.Chat} в {due.ToLocalTime():dd.MM.yyyy HH:mm}? Очередь продолжит работу после закрытия окна.",
            "Запланированная публикация", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        var botId = _options.Demo ? "demo" : TelegramClient.BotId(SecretVault.Load());
        await SaveDraftAsync();
        await new QueuePlanner(_store).AddAsync(draft, _options, botId, due);
        WorkerProcess.Start(); SwitchView(QueueView); await LoadQueueAsync();
        StatusLabel.Text = "Задание сохранено. Текст и файлы скопированы; дальнейшие правки черновика его не изменят.";
    });
    private void QueueSelected(object sender, SelectionChangedEventArgs e)
    {
        var job = SelectedJob;
        PauseJobButton.IsEnabled = job?.State == QueueState.Pending;
        ResumeJobButton.IsEnabled = job?.State is QueueState.Pending or QueueState.Paused;
        CancelJobButton.IsEnabled = job?.State is QueueState.Pending or QueueState.Paused or QueueState.Running;
        JobHistoryButton.IsEnabled = job?.State is QueueState.Completed or QueueState.Refused or QueueState.Partial or QueueState.Unknown;
        QueueDetails.Text = job is null ? "Выберите задание." : $"{new QueueRow(job).Title} • {new QueueRow(job).Destination}\n{job.Error}";
        if (job is not null && !_refreshingQueue)
        { var due = job.Due.ToLocalTime(); QueueDate.SelectedDate = due.Date; QueueClock.Text = due.ToString("HH:mm"); }
    }
    private async void PauseJob(object sender, RoutedEventArgs e) => await Guard(async () =>
    {
        if (SelectedJob is not { } job) return;
        if (!await _store.ChangeQueueAsync(job.Id, QueueState.Paused)) StatusLabel.Text = "Задание уже начало выполняться.";
        await LoadQueueAsync();
    });
    private async void ResumeJob(object sender, RoutedEventArgs e) => await Guard(async () =>
    {
        if (SelectedJob is not { } job || QueueDate.SelectedDate is not { } date) return;
        var due = ScheduleTime.FromLocal(date, QueueClock.Text, TimeZoneInfo.Local);
        if (due <= DateTimeOffset.UtcNow) throw new InvalidOperationException("Для продолжения выберите новое время в будущем.");
        if (await _store.ChangeQueueAsync(job.Id, QueueState.Pending, due)) WorkerProcess.Start();
        await LoadQueueAsync();
    });
    private async void CancelJob(object sender, RoutedEventArgs e) => await Guard(async () =>
    {
        if (SelectedJob is not { } job) return;
        if (job.State == QueueState.Running && MessageBox.Show("Отменить выполняемую отправку? Уже опубликованные сообщения останутся в канале; результат может быть неизвестен.",
            "Отмена", MessageBoxButton.YesNo) != MessageBoxResult.Yes) return;
        var cancelled = await _store.ChangeQueueAsync(job.Id, QueueState.Cancelled);
        if (!cancelled) cancelled = await WorkerProcess.CommandAsync("cancel|" + job.Id);
        StatusLabel.Text = cancelled ? "Запрошена отмена задания." : "Задание уже завершилось или процесс недоступен. Обновите список.";
        await LoadQueueAsync();
    });
    private async void OpenJobHistory(object sender, RoutedEventArgs e) => await Guard(async () =>
    {
        if (SelectedJob is not { } job) return;
        await LoadHistoryAsync(); SwitchView(HistoryView);
        HistoryList.SelectedItem = History.FirstOrDefault(row => row.Record.Id == job.Id);
        if (HistoryList.SelectedItem is null) StatusLabel.Text = "Отправка не подтверждена: записи в истории нет. Проверьте канал перед повтором.";
    });
}
