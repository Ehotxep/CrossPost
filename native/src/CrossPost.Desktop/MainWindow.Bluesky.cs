using System.IO;
using System.Windows;
using System.Windows.Controls;
using CrossPost.Core;

namespace CrossPost.Desktop;

public partial class MainWindow
{
    private BlueskyOptions _bluesky = new();
    private readonly List<string> _alts = [];
    private bool _settingAlt;
    private string SelectedBlueskyLabel => BlueskyRating?.SelectedIndex switch { 1 => "erotic", 2 => "explicit", _ => "sfw" };
    private async Task InitializeBlueskyAsync(Draft draft)
    {
        _bluesky = await _store.ReadDocumentAsync<BlueskyOptions>("bluesky-options") ?? new();
        if (App.SmokeTest) _bluesky = _bluesky with { Demo = true };
        BlueskyDemoInput.IsChecked = _bluesky.Demo; BlueskyHandleInput.Text = _bluesky.Handle;
        BlueskyPasswordInput.Password = SecretVault.Load("bluesky");
        BlueskyRating.SelectedIndex = draft.ContentLabel switch { "erotic" => 1, "explicit" => 2, _ => 0 };
        BlueskyAccountLabel.Text = _bluesky.Did.Length > 0 ? $"{_bluesky.Handle} • {_bluesky.Did}" : "Аккаунт пока не проверен";
        UpdateBlueskyControls();
    }
    private void UpdateBlueskyControls()
    {
        if (BlueskyComposer is not null) BlueskyComposer.Visibility = CurrentOptions.Platform == "bluesky" ? Visibility.Visible : Visibility.Collapsed;
        UpdateAltInput();
    }
    private void UpdateAltInput()
    {
        if (ImageAltInput is null) return;
        _settingAlt = true;
        try { ImageAltInput.Text = ImageList.SelectedIndex is var index && index >= 0 && index < _alts.Count ? _alts[index] : ""; }
        finally { _settingAlt = false; }
    }
    private void ImageAltChanged(object sender, TextChangedEventArgs e)
    {
        if (_settingAlt || !_loaded || ImageList.SelectedIndex < 0) return;
        while (_alts.Count < _images.Count) _alts.Add("");
        _alts[ImageList.SelectedIndex] = ImageAltInput.Text; _draftTimer.Stop(); _draftTimer.Start();
    }
    private void BlueskyRatingChanged(object sender, SelectionChangedEventArgs e)
    { if (_loaded) { RefreshDestinations(); RenderTargetSummary(); _draftTimer.Stop(); _draftTimer.Start(); } }
    private async void PrepareBlueskyImages(object sender, RoutedEventArgs e) => await RunOperation(PrepareBlueskyDraftAsync);
    private async Task PrepareBlueskyDraftAsync(CancellationToken ct)
    {
        if (_images.Count == 0) throw new InvalidOperationException("Добавьте изображения для подготовки.");
        var draft = CurrentDraft; draft.Validate("bluesky", allowImagePreparation: true);
        StatusLabel.Text = "Подготавливаю отдельные копии изображений…";
        var files = await BlueskyMedia.PrepareAsync(draft.Images, ct);
        ct.ThrowIfCancellationRequested();
        if (!_images.SequenceEqual(draft.Images, StringComparer.OrdinalIgnoreCase))
            throw new InvalidOperationException("Список изображений изменился во время подготовки. Повторите подготовку для текущего списка.");
        _images.Clear(); _images.AddRange(files); RefreshImages(); UpdatePreview(); await SaveDraftAsync();
        StatusLabel.Text = "Копии готовы. Проверьте каждую в предпросмотре; оригиналы сохранены. Размеры: " + string.Join(", ", files.Select(path => $"{new FileInfo(path).Length:N0} байт"));
    }
    private async void SaveBlueskySettings(object sender, RoutedEventArgs e) => await RunOperation(async ct =>
    {
        var demo = BlueskyDemoInput.IsChecked != false;
        var handle = BlueskyHandleInput.Text.Trim(); var password = BlueskyPasswordInput.Password;
        BlueskyAccount? account = null;
        if (!demo)
        {
            handle = BlueskyClient.NormalizeHandle(handle);
            account = await new BlueskyClient(_http, handle, password, "").InspectAsync(ct);
        }
        SecretVault.Save(password, "bluesky");
        _bluesky = new(demo, account?.Handle ?? handle, account?.Did ?? "", account?.Pds ?? "", SelectedBlueskyLabel);
        await _store.SaveDocumentAsync("bluesky-options", _bluesky);
        BlueskyAccountLabel.Text = account is null ? "Деморежим: вход не выполнялся" : $"{account.Handle} • {account.Did}";
        ApplyMode(); UpdatePreview(); StatusLabel.Text = "Настройки Bluesky сохранены. Публичный пост не создавался.";
    });
    private async void CheckBluesky(object sender, RoutedEventArgs e) => await RunOperation(async ct =>
    {
        if (BlueskyDemoInput.IsChecked != false) { StatusLabel.Text = "ДЕМО Bluesky: вход и сетевые запросы отключены."; return; }
        var info = await new BlueskyClient(_http, BlueskyHandleInput.Text, BlueskyPasswordInput.Password, "").InspectAsync(ct);
        BlueskyAccountLabel.Text = $"{info.Handle} • {info.Did}";
        StatusLabel.Text = "Вход подтверждён без создания поста. Сохраните настройки для публикации.";
    });
}
