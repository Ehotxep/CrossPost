using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using CrossPost.Core;
using Microsoft.Win32;

namespace CrossPost.Desktop;

public record HistoryRow(Publication Record)
{
    public string CreatedText => Record.Created.ToLocalTime().ToString("dd.MM.yyyy HH:mm");
    public string Title => Record.EditText.Split('\n')[0] is { Length: > 0 } title ? title : "Изображение";
    public string Mode => (Record.Demo ? "ДЕМО • " : "") + (Record.Platform == "discord" ? "Discord" : "Telegram");
    public string Status => Record.Delivery.State switch
    {
        DeliveryState.Sent => "Отправлено", DeliveryState.Refused => "Не отправлено",
        DeliveryState.Partial => "Отправлено частично", DeliveryState.Unknown => "Результат неизвестен", _ => "Отправляется"
    };
}
public record ImageRow(string Path)
{
    public string Label => System.IO.Path.GetFileName(Path);
}
public partial class MainWindow : Window
{
    private readonly Store _store = new(App.DataFolder);
    private readonly HttpClient _http = new(new HttpClientHandler { AllowAutoRedirect = false }) { Timeout = TimeSpan.FromSeconds(90) };
    private readonly DispatcherTimer _draftTimer = new() { Interval = TimeSpan.FromMilliseconds(750) };
    private readonly SemaphoreSlim _draftLock = new(1, 1);
    private AppOptions _options = new();
    private readonly List<string> _images = [];
    private bool _loaded, _busy, _closing;
    private CancellationTokenSource? _operation;
    private Publication? _selected;
    public ObservableCollection<HistoryRow> History { get; } = [];

    public MainWindow()
    {
        InitializeComponent();
        DataContext = this;
        InitializeQueueUi();
        var queueTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
        queueTimer.Tick += async (_, _) => { if (_loaded && QueueView.Visibility == Visibility.Visible) await Guard(LoadQueueAsync); };
        queueTimer.Start();
        Closed += (_, _) => queueTimer.Stop();
        _draftTimer.Tick += async (_, _) => { _draftTimer.Stop(); await Guard(SaveDraftAsync); };
        Loaded += async (_, _) =>
        {
            if (!App.SmokeTest) { await Guard(InitializeAsync); return; }
            var exit = 0;
            try
            {
                App.TraceSmoke("initialize");
                await InitializeAsync();
                App.TraceSmoke("telegram demo");
                await RunSmokeChecksAsync();
                await Dispatcher.InvokeAsync(UpdateLayout, DispatcherPriority.Render);
                App.TraceSmoke("window ready");
                await File.WriteAllTextAsync(Path.Combine(App.DataFolder, "smoke-ready.txt"), "WPF initialized; demo album, reordering, persistence and editing checked");
            }
            catch (Exception error)
            {
                exit = 1;
                await File.AppendAllTextAsync(Path.Combine(App.DataFolder, "error.log"), error + Environment.NewLine);
            }
            Closing -= OnClosing;
            _http.Dispose();
            Application.Current.Shutdown(exit);
        };
        Closing += OnClosing;
    }
    private Draft CurrentDraft => new(TitleInput.Text, BodyInput.Text, _images.FirstOrDefault(), _images.Skip(1).ToArray());
    private async Task RunSmokeChecksAsync()
    {
        // Executed only with --smoke-test and an explicitly isolated data directory.
        var paths = new[] { Path.Combine(App.DataFolder, "first.png"), Path.Combine(App.DataFolder, "second.png") };
        var bitmap = BitmapSource.Create(2, 2, 96, 96, System.Windows.Media.PixelFormats.Bgra32, null, new byte[16], 8);
        foreach (var path in paths)
        {
            var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap));
            using var stream = File.Create(path); encoder.Save(stream);
        }
        TitleInput.Text = "Демо-альбом"; BodyInput.Text = "Проверка окна без публикации";
        _images.Clear(); _images.AddRange(paths); RefreshImages(); UpdatePreview();
        ValidateImages(CurrentDraft);
        await MoveImage(1);
        if (CurrentDraft.Images[0] != paths[1] || PreviewImage.Source is null)
            throw new InvalidOperationException("WPF album ordering or preview failed");
        var restored = await _store.ReadDocumentAsync<Draft>("draft");
        if (restored is null || !restored.SameContent(CurrentDraft))
            throw new InvalidOperationException("WPF draft persistence failed");
        var workflow = new Workflow(_store);
        var sent = await workflow.SendAsync(new DemoTelegram(), "demo", new(), CurrentDraft, CancellationToken.None);
        await LoadHistoryAsync(); HistoryList.SelectedItem = History.Single(row => row.Record.Id == sent.Id);
        if (!EditButton.IsEnabled || _selected?.Delivery.AllMediaMessageIds.Length != 2)
            throw new InvalidOperationException("WPF album history selection failed");
        var edited = await workflow.EditAsync(new DemoTelegram(), sent, "Изменённая подпись", CancellationToken.None);
        if (!edited.Result.Confirmed) throw new InvalidOperationException("WPF demo edit failed");
        SwitchView(HistoryView); UpdateLayout(); SwitchView(SettingsView); UpdateLayout(); SwitchView(QueueView); UpdateLayout(); SwitchView(ComposeView);
        App.TraceSmoke("discord scenario");
        await RunDiscordSmokeAsync();
        App.TraceSmoke("library scenario");
        await RunLibrarySmokeAsync();
        App.TraceSmoke("schedule Telegram after window exit");
        var queued = await new QueuePlanner(_store).AddAsync(CurrentDraft, new(), "demo", DateTimeOffset.UtcNow.AddSeconds(20));
        await _store.SaveDocumentAsync("worker-smoke-id", queued.Id);
        WorkerProcess.Start();
        for (var attempt = 0; attempt < 100 && !WorkerProcess.IsRunning; attempt++) await Task.Delay(100);
        if (!WorkerProcess.IsRunning) throw new InvalidOperationException("Background worker failed to start");
        _draftTimer.Stop();
    }
    private async Task InitializeAsync()
    {
        await _store.InitializeAsync();
        _options = await _store.ReadDocumentAsync<AppOptions>("options") ?? new();
        if (App.SmokeTest) _options = _options with { Demo = true };
        var draft = await _store.ReadDocumentAsync<Draft>("draft") ?? new();
        TitleInput.Text = draft.Title; BodyInput.Text = draft.Text; _images.AddRange(draft.Images);
        RefreshImages();
        DemoInput.IsChecked = _options.Demo; ChatInput.Text = _options.Chat;
        TokenInput.Password = SecretVault.Load();
        await InitializeDiscordAsync();
        _loaded = true;
        UpdatePreview(); ApplyMode();
        await LoadHistoryAsync();
        await LoadQueueAsync();
        if (!App.SmokeTest && (await _store.QueueAsync()).Any(job => job.State is QueueState.Pending or QueueState.Running)) await WorkerProcess.EnsureStartedAsync();
        StatusLabel.Text = "Готово. Черновик сохраняется автоматически.";
    }
    private async Task Guard(Func<Task> action)
    {
        try { await action(); }
        catch (Exception error) { StatusLabel.Text = error.Message; }
    }
    private void ApplyMode()
    {
        var options = CurrentOptions;
        ModeLabel.Text = options.Demo ? $"{CurrentPlatformLabel} • ДЕМО" : $"{CurrentPlatformLabel} • реальная отправка";
        SendButton.Content = options.Demo ? "Выполнить демо-отправку" : "Отправить в " + CurrentPlatformLabel;
        DestinationLabel.Text = options.Demo ? "Назначение: демонстрационный канал" : "Назначение: " + options.Chat;
        PreviewHeading.Text = "Предпросмотр текста " + CurrentPlatformLabel;
    }
    private void UpdatePreview()
    {
        var draft = CurrentDraft;
        PreviewText.Text = draft.PlainText.Length > 0 ? draft.PlainText : "Без текста";
        CountLabel.Text = $"{draft.PlainText.Length} / {PublishLimits.Text(CurrentOptions.Platform)} символов" +
            (CurrentOptions.Platform == "telegram" && _images.Count > 0 && draft.PlainText.Length > 1024 ? " • Текст уйдёт отдельным сообщением перед фото" : "");
        ImageLabel.Text = _images.Count == 0 ? "Изображения не выбраны"
            : $"{_images.Count} / 10 • " + (CurrentOptions.Platform == "discord" ? "вложения Discord" : _images.Count == 1 ? "одно фото" : "альбом, подпись под первым фото");
    }
    private void RefreshImages(int selection = 0)
    {
        ImageList.ItemsSource = _images.Select(path => new ImageRow(path)).ToArray();
        ImageList.SelectedIndex = _images.Count == 0 ? -1 : Math.Clamp(selection, 0, _images.Count - 1);
        UpdateImagePreview();
    }
    private void ImageSelected(object sender, SelectionChangedEventArgs e) => UpdateImagePreview();
    private void UpdateImagePreview()
    {
        if (PreviewImage is null) return;
        PreviewImage.Source = null;
        if (ImageList.SelectedItem is not ImageRow row || !File.Exists(row.Path)) return;
        try
        {
            var bitmap = new BitmapImage();
            bitmap.BeginInit(); bitmap.CacheOption = BitmapCacheOption.OnLoad;
            bitmap.DecodePixelWidth = 400; bitmap.UriSource = new Uri(Path.GetFullPath(row.Path)); bitmap.EndInit();
            bitmap.Freeze(); PreviewImage.Source = bitmap;
        }
        catch (Exception error) when (error is IOException or NotSupportedException or FileFormatException)
        { StatusLabel.Text = "Предпросмотр выбранного изображения недоступен."; }
    }
    private void DraftChanged(object sender, TextChangedEventArgs e)
    {
        if (!_loaded) return;
        UpdatePreview(); _draftTimer.Stop(); _draftTimer.Start();
    }
    private async Task SaveDraftAsync()
    {
        var draft = CurrentDraft;
        await _draftLock.WaitAsync();
        try { await _store.SaveDocumentAsync("draft", draft); }
        finally { _draftLock.Release(); }
    }
    private async void SaveDraftClick(object sender, RoutedEventArgs e) => await Guard(async () =>
    { await SaveDraftAsync(); StatusLabel.Text = "Черновик сохранён."; });
    private async void ClearDraft(object sender, RoutedEventArgs e)
    {
        if (_busy) return;
        if (MessageBox.Show("Очистить текущий черновик?", "CrossPost", MessageBoxButton.YesNo) != MessageBoxResult.Yes) return;
        TitleInput.Clear(); BodyInput.Clear(); _images.Clear(); RefreshImages(); UpdatePreview();
        await Guard(SaveDraftAsync);
    }
    private void ValidateImages(Draft candidate)
    {
        candidate.Validate(CurrentOptions.Platform);
        foreach (var path in candidate.Images)
        {
            using var stream = File.OpenRead(path);
            var frame = BitmapDecoder.Create(stream, BitmapCreateOptions.DelayCreation, BitmapCacheOption.None).Frames[0];
            if (CurrentOptions.Platform == "telegram" && (frame.PixelWidth + frame.PixelHeight > 10000 || (double)Math.Max(frame.PixelWidth, frame.PixelHeight) / Math.Min(frame.PixelWidth, frame.PixelHeight) > 20))
                throw new InvalidOperationException("Telegram: сумма сторон не больше 10000 пикселей, соотношение сторон не больше 20.");
        }
    }
    private async void PickImage(object sender, RoutedEventArgs e)
    {
        if (_busy) return;
        var picker = new OpenFileDialog { Filter = "Изображения|*.jpg;*.jpeg;*.png", Multiselect = true };
        if (picker.ShowDialog(this) != true) return;
        await Guard(async () =>
        {
            var paths = _images.Concat(picker.FileNames).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
            var candidate = CurrentDraft with { ImagePath = paths.FirstOrDefault(), AdditionalImagePaths = paths.Skip(1).ToArray() };
            ValidateImages(candidate);
            _images.Clear(); _images.AddRange(paths); RefreshImages(paths.Length - 1); UpdatePreview(); await SaveDraftAsync();
        });
    }
    private async void RemoveImage(object sender, RoutedEventArgs e)
    {
        if (_busy || ImageList.SelectedIndex < 0) return;
        var index = ImageList.SelectedIndex;
        _images.RemoveAt(index); RefreshImages(index); UpdatePreview(); await Guard(SaveDraftAsync);
    }
    private async Task MoveImage(int offset)
    {
        var index = ImageList.SelectedIndex;
        if (_busy || index < 0 || index + offset < 0 || index + offset >= _images.Count) return;
        (_images[index], _images[index + offset]) = (_images[index + offset], _images[index]);
        RefreshImages(index + offset); await SaveDraftAsync();
    }
    private async void MoveImageUp(object sender, RoutedEventArgs e) => await Guard(() => MoveImage(-1));
    private async void MoveImageDown(object sender, RoutedEventArgs e) => await Guard(() => MoveImage(1));
    private void ShowCompose(object sender, RoutedEventArgs e) => SwitchView(ComposeView);
    private async void ShowHistory(object sender, RoutedEventArgs e)
    { SwitchView(HistoryView); await Guard(LoadHistoryAsync); }
    private void ShowSettings(object sender, RoutedEventArgs e) => SwitchView(SettingsView);
    private void SwitchView(UIElement view)
    {
        ComposeView.Visibility = view == ComposeView ? Visibility.Visible : Visibility.Collapsed;
        HistoryView.Visibility = view == HistoryView ? Visibility.Visible : Visibility.Collapsed;
        LibraryView.Visibility = view == LibraryView ? Visibility.Visible : Visibility.Collapsed;
        LibraryStatsView.Visibility = view == LibraryStatsView ? Visibility.Visible : Visibility.Collapsed;
        QueueView.Visibility = view == QueueView ? Visibility.Visible : Visibility.Collapsed;
        SettingsView.Visibility = view == SettingsView ? Visibility.Visible : Visibility.Collapsed;
    }
    private async void SaveSettings(object sender, RoutedEventArgs e)
    {
        if (_busy) return;
        await Guard(async () =>
        {
            var options = new AppOptions(DemoInput.IsChecked != false, ChatInput.Text.Trim());
            if (!options.Demo)
            {
                TelegramClient.BotId(TokenInput.Password.Trim());
                if (string.IsNullOrWhiteSpace(options.Chat)) throw new InvalidOperationException("Укажите канал Telegram.");
            }
            SecretVault.Save(TokenInput.Password.Trim());
            await _store.SaveDocumentAsync("options", options);
            _options = options; ApplyMode(); StatusLabel.Text = "Настройки сохранены.";
        });
    }
    private IPublisher Client(bool demo, string platform = "telegram", string? channel = null) => platform == "discord"
        ? demo ? new DemoDiscord() : new DiscordClient(_http, DiscordEndpoint.Parse(SecretVault.Load("discord")), channel ?? _discord.ChannelId, _discord.Spoiler, _discord.SuppressEmbeds)
        : demo ? new DemoTelegram() : new TelegramClient(_http, SecretVault.Load());
    private async Task RunOperation(Func<CancellationToken, Task> action)
    {
        if (_busy) return;
        _busy = true; _operation = new CancellationTokenSource();
        SendButton.IsEnabled = EditButton.IsEnabled = false;
        BusyBar.Visibility = CancelButton.Visibility = Visibility.Visible;
        try { await action(_operation.Token); }
        catch (OperationCanceledException) { StatusLabel.Text = "Операция отменена."; }
        catch (Exception error) { StatusLabel.Text = error.Message; }
        finally
        {
            _busy = false; _operation.Dispose(); _operation = null;
            SendButton.IsEnabled = true;
            EditButton.IsEnabled = CanEdit(_selected);
            BusyBar.Visibility = CancelButton.Visibility = Visibility.Collapsed;
        }
    }
    private async void CheckBot(object sender, RoutedEventArgs e) => await RunOperation(async ct =>
    {
        StatusLabel.Text = _options.Demo ? "Проверка демонстрационного режима…" : "Проверка Telegram…";
        StatusLabel.Text = "Бот доступен: " + await Client(_options.Demo).CheckAsync(ct);
    });
    private async void SendPost(object sender, RoutedEventArgs e) => await RunOperation(async ct =>
    {
        var options = CurrentOptions;
        var draft = CurrentDraft; ValidateImages(draft); await SaveDraftAsync();
        var uncertain = (await _store.HistoryAsync()).Any(p => p.Draft.SameContent(draft) && p.Demo == options.Demo && p.Platform == options.Platform
            && p.Delivery.State is DeliveryState.Sending or DeliveryState.Unknown or DeliveryState.Partial);
        if (uncertain && MessageBox.Show("У этого черновика есть неподтверждённая или частичная отправка. Сначала проверьте канал. Отправить ещё раз с риском дубликата?", "CrossPost", MessageBoxButton.YesNo, MessageBoxImage.Warning) != MessageBoxResult.Yes) return;
        if (!options.Demo && MessageBox.Show("Отправить пост в " + options.Chat + "?", "Реальная публикация", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        StatusLabel.Text = options.Demo ? "Демонстрационная отправка…" : "Отправляю в " + CurrentPlatformLabel + "…";
        var botId = options.Demo ? "demo" : CurrentCredentialId;
        var sent = await new Workflow(_store).SendAsync(Client(options.Demo, options.Platform, options.Chat), botId, options, draft, ct);
        await LoadHistoryAsync();
        StatusLabel.Text = (sent.Demo ? "ДЕМО: " : "") + new HistoryRow(sent).Status +
            (sent.Delivery.Error.Length > 0 ? ". " + sent.Delivery.Error : "");
    });
    private async Task LoadHistoryAsync()
    {
        var selectedId = _selected?.Id;
        History.Clear();
        foreach (var publication in await _store.HistoryAsync()) History.Add(new(publication));
        if (selectedId is not null) HistoryList.SelectedItem = History.FirstOrDefault(row => row.Record.Id == selectedId);
    }
    private async void RefreshHistory(object sender, RoutedEventArgs e) => await Guard(LoadHistoryAsync);
    private void HistorySelected(object sender, SelectionChangedEventArgs e)
    {
        _selected = (HistoryList.SelectedItem as HistoryRow)?.Record;
        EditInput.Text = _selected?.EditText ?? "";
        EditButton.IsEnabled = !_busy && CanEdit(_selected);
        HistoryDetails.Text = _selected is null ? "Выберите запись." :
            $"{new HistoryRow(_selected).Mode} • {_selected.Delivery.ChatId} • {new HistoryRow(_selected).Status}\nИзображений: {(_selected.Platform == "discord" ? _selected.Delivery.AttachmentIds?.Length ?? 0 : _selected.Delivery.AllMediaMessageIds.Length)} • ID сообщения: {_selected.Delivery.RemoteMessageId ?? _selected.Delivery.EditMessageId?.ToString()}\n{_selected.Delivery.Error}\n{_selected.EditState}";
    }
    private async void EditPost(object sender, RoutedEventArgs e) => await RunOperation(async ct =>
    {
        if (_selected is not { } selected) return;
        if (!selected.Demo && MessageBox.Show("Изменить это сообщение в " + (selected.Platform == "discord" ? "Discord" : "Telegram") + "?", "Редактирование", MessageBoxButton.YesNo) != MessageBoxResult.Yes) return;
        StatusLabel.Text = selected.Demo ? "Демонстрационное изменение…" : "Сохраняю на площадке…";
        var updated = await new Workflow(_store).EditAsync(Client(selected.Demo, selected.Platform, selected.Delivery.ChatId), selected, EditInput.Text, ct);
        await LoadHistoryAsync();
        StatusLabel.Text = updated.Result.Confirmed ? (selected.Demo ? "ДЕМО: изменение сохранено." : "Изменение сохранено на площадке.") : updated.Result.Error;
    });
    private void CancelOperation(object sender, RoutedEventArgs e) => _operation?.Cancel();
    private void OpenPost(object sender, RoutedEventArgs e)
    {
        if (_selected?.Delivery.Link is { } link && Uri.TryCreate(link, UriKind.Absolute, out var uri) && uri.Scheme == "https" && (uri.Host == "t.me" || uri.Host == "discord.com"))
            Process.Start(new ProcessStartInfo(link) { UseShellExecute = true });
    }
    private void OpenData(object sender, RoutedEventArgs e) => Process.Start(new ProcessStartInfo(App.DataFolder) { UseShellExecute = true });
    private async void OnClosing(object? sender, CancelEventArgs e)
    {
        e.Cancel = true;
        if (_closing) return;
        if (_busy)
        { MessageBox.Show("Дождитесь завершения операции или отмените её. Отмена отправки может оставить неизвестный результат."); return; }
        _closing = true;
        try
        {
            _draftTimer.Stop();
            if (_loaded) await SaveDraftAsync();
            _http.Dispose();
            Closing -= OnClosing; Close();
        }
        catch (Exception error) { _closing = false; StatusLabel.Text = "Не удалось сохранить черновик: " + error.Message; }
    }
}
