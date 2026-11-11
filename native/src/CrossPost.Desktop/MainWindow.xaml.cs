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
    public string Mode => Record.Demo ? "ДЕМО" : "Telegram";
    public string Status => Record.Delivery.State switch
    {
        DeliveryState.Sent => "Отправлено", DeliveryState.Refused => "Не отправлено",
        DeliveryState.Partial => "Отправлено частично", DeliveryState.Unknown => "Результат неизвестен", _ => "Отправляется"
    };
}
public partial class MainWindow : Window
{
    private readonly Store _store = new(App.DataFolder);
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(90) };
    private readonly DispatcherTimer _draftTimer = new() { Interval = TimeSpan.FromMilliseconds(750) };
    private readonly SemaphoreSlim _draftLock = new(1, 1);
    private AppOptions _options = new();
    private string? _imagePath;
    private bool _loaded, _busy, _closing;
    private CancellationTokenSource? _operation;
    private Publication? _selected;
    public ObservableCollection<HistoryRow> History { get; } = [];

    public MainWindow()
    {
        InitializeComponent();
        DataContext = this;
        _draftTimer.Tick += async (_, _) => { _draftTimer.Stop(); await Guard(SaveDraftAsync); };
        Loaded += async (_, _) =>
        {
            if (!App.SmokeTest) { await Guard(InitializeAsync); return; }
            var exit = 0;
            try
            {
                await InitializeAsync();
                await Dispatcher.InvokeAsync(UpdateLayout, DispatcherPriority.Render);
                await File.WriteAllTextAsync(Path.Combine(App.DataFolder, "smoke-ready.txt"), "WPF initialized; demo mode; SQLite ready");
            }
            catch (Exception) { exit = 1; }
            Closing -= OnClosing;
            _http.Dispose();
            Application.Current.Shutdown(exit);
        };
        Closing += OnClosing;
    }
    private Draft CurrentDraft => new(TitleInput.Text, BodyInput.Text, _imagePath);
    private async Task InitializeAsync()
    {
        await _store.InitializeAsync();
        _options = await _store.ReadDocumentAsync<AppOptions>("options") ?? new();
        if (App.SmokeTest) _options = _options with { Demo = true };
        var draft = await _store.ReadDocumentAsync<Draft>("draft") ?? new();
        TitleInput.Text = draft.Title; BodyInput.Text = draft.Text; _imagePath = draft.ImagePath;
        DemoInput.IsChecked = _options.Demo; ChatInput.Text = _options.Chat;
        TokenInput.Password = SecretVault.Load();
        _loaded = true;
        UpdatePreview(); ApplyMode();
        await LoadHistoryAsync();
        StatusLabel.Text = "Готово. Черновик сохраняется автоматически.";
    }
    private async Task Guard(Func<Task> action)
    {
        try { await action(); }
        catch (Exception error) { StatusLabel.Text = error.Message; }
    }
    private void ApplyMode()
    {
        ModeLabel.Text = _options.Demo ? "ДЕМО • ничего не публикуется" : "TELEGRAM • реальная отправка";
        SendButton.Content = _options.Demo ? "Выполнить демо-отправку" : "Отправить в Telegram";
        DestinationLabel.Text = _options.Demo ? "Назначение: демонстрационный канал" : "Назначение: " + _options.Chat;
    }
    private void UpdatePreview()
    {
        var draft = CurrentDraft;
        PreviewText.Text = draft.PlainText.Length > 0 ? draft.PlainText : "Без текста";
        CountLabel.Text = $"{draft.PlainText.Length} / 4096 символов" +
            (_imagePath is not null && draft.PlainText.Length > 1024 ? " • Текст уйдёт отдельным сообщением перед фото" : "");
        ImageLabel.Text = _imagePath is null ? "Изображение не выбрано" : Path.GetFileName(_imagePath);
        PreviewImage.Source = null;
        if (_imagePath is not null && File.Exists(_imagePath))
        {
            try
            {
                var bitmap = new BitmapImage();
                bitmap.BeginInit(); bitmap.CacheOption = BitmapCacheOption.OnLoad;
                bitmap.DecodePixelWidth = 400; bitmap.UriSource = new Uri(Path.GetFullPath(_imagePath)); bitmap.EndInit();
                bitmap.Freeze(); PreviewImage.Source = bitmap;
            }
            catch (Exception error) when (error is IOException or NotSupportedException or FileFormatException)
            { ImageLabel.Text += " — предпросмотр недоступен"; }
        }
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
        TitleInput.Clear(); BodyInput.Clear(); _imagePath = null; UpdatePreview();
        await Guard(SaveDraftAsync);
    }
    private async void PickImage(object sender, RoutedEventArgs e)
    {
        if (_busy) return;
        var picker = new OpenFileDialog { Filter = "Изображения|*.jpg;*.jpeg;*.png", Multiselect = false };
        if (picker.ShowDialog(this) != true) return;
        await Guard(async () =>
        {
            var candidate = CurrentDraft with { ImagePath = picker.FileName }; candidate.Validate();
            using var stream = File.OpenRead(picker.FileName);
            var frame = BitmapDecoder.Create(stream, BitmapCreateOptions.DelayCreation, BitmapCacheOption.None).Frames[0];
            if (frame.PixelWidth + frame.PixelHeight > 10000 || (double)Math.Max(frame.PixelWidth, frame.PixelHeight) / Math.Min(frame.PixelWidth, frame.PixelHeight) > 20)
                throw new InvalidOperationException("Telegram: сумма сторон не больше 10000 пикселей, соотношение сторон не больше 20.");
            _imagePath = picker.FileName; UpdatePreview(); await SaveDraftAsync();
        });
    }
    private async void RemoveImage(object sender, RoutedEventArgs e)
    { if (_busy) return; _imagePath = null; UpdatePreview(); await Guard(SaveDraftAsync); }
    private void ShowCompose(object sender, RoutedEventArgs e) => SwitchView(ComposeView);
    private async void ShowHistory(object sender, RoutedEventArgs e)
    { SwitchView(HistoryView); await Guard(LoadHistoryAsync); }
    private void ShowSettings(object sender, RoutedEventArgs e) => SwitchView(SettingsView);
    private void SwitchView(UIElement view)
    {
        ComposeView.Visibility = view == ComposeView ? Visibility.Visible : Visibility.Collapsed;
        HistoryView.Visibility = view == HistoryView ? Visibility.Visible : Visibility.Collapsed;
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
    private ITelegram Client(bool demo) => demo ? new DemoTelegram() : new TelegramClient(_http, SecretVault.Load());
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
            EditButton.IsEnabled = _selected?.Delivery.EditMessageId is not null;
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
        var draft = CurrentDraft; draft.Validate(); await SaveDraftAsync();
        var uncertain = (await _store.HistoryAsync()).Any(p => p.Draft == draft && p.Demo == _options.Demo
            && p.Delivery.State is DeliveryState.Sending or DeliveryState.Unknown or DeliveryState.Partial);
        if (uncertain && MessageBox.Show("У этого черновика есть неподтверждённая или частичная отправка. Сначала проверьте канал. Отправить ещё раз с риском дубликата?", "CrossPost", MessageBoxButton.YesNo, MessageBoxImage.Warning) != MessageBoxResult.Yes) return;
        if (!_options.Demo && MessageBox.Show("Отправить пост в " + _options.Chat + "?", "Реальная публикация", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        StatusLabel.Text = _options.Demo ? "Демонстрационная отправка…" : "Отправляю в Telegram…";
        var botId = _options.Demo ? "demo" : TelegramClient.BotId(SecretVault.Load());
        var sent = await new Workflow(_store).SendAsync(Client(_options.Demo), botId, _options, draft, ct);
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
        EditButton.IsEnabled = !_busy && _selected?.Delivery.EditMessageId is not null;
        HistoryDetails.Text = _selected is null ? "Выберите запись." :
            $"{new HistoryRow(_selected).Mode} • {_selected.Delivery.ChatId} • {new HistoryRow(_selected).Status}\n{_selected.Delivery.Error}\n{_selected.EditState}";
    }
    private async void EditPost(object sender, RoutedEventArgs e) => await RunOperation(async ct =>
    {
        if (_selected is not { } selected) return;
        if (!selected.Demo && MessageBox.Show("Изменить это сообщение в Telegram?", "Редактирование", MessageBoxButton.YesNo) != MessageBoxResult.Yes) return;
        StatusLabel.Text = selected.Demo ? "Демонстрационное изменение…" : "Сохраняю в Telegram…";
        var updated = await new Workflow(_store).EditAsync(Client(selected.Demo), selected, EditInput.Text, ct);
        await LoadHistoryAsync();
        StatusLabel.Text = updated.Result.Confirmed ? (selected.Demo ? "ДЕМО: изменение сохранено." : "Изменение сохранено в Telegram.") : updated.Result.Error;
    });
    private void CancelOperation(object sender, RoutedEventArgs e) => _operation?.Cancel();
    private void OpenPost(object sender, RoutedEventArgs e)
    {
        if (_selected?.Delivery.Link is { } link && Uri.TryCreate(link, UriKind.Absolute, out var uri) && uri.Scheme == "https" && uri.Host == "t.me")
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
