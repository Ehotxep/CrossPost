using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using CrossPost.Core;

namespace CrossPost.Desktop;
public partial class MainWindow
{
    private readonly Dictionary<string,(DateTime Modified,long Length,BitmapSource Bitmap)> _thumbnails = new(StringComparer.OrdinalIgnoreCase);
    private void InitializePythonLayout()
    {
        SizeChanged += (_,_) => UpdateResponsiveLayout();
        TextCard.SizeChanged += (_,_) => UpdateResponsiveLayout();
        SelectSettingsCategory("general"); UpdateResponsiveLayout(); RefreshWorkerBadge();
    }
    private void UpdateResponsiveLayout()
    {
        if (MainContent is null || TextCard is null) return;
        var width=MainContent.ActualWidth;
        var sidebar = width>0 && width<1000 ? 280 : width>0 && width<1180 ? 320 : 380;
        PublicationColumn.Width = new(sidebar); HistoryDetailsColumn.Width = new(sidebar);
        var narrow = TextCard.ActualWidth > 0 && TextCard.ActualWidth < 660;
        EnglishColumn.Width = narrow ? new(0) : new(1,GridUnitType.Star); BilingualGap.Width = new(narrow ? 0 : 24);
        Grid.SetRow(EnglishEditor,narrow ? 1 : 0); Grid.SetColumn(EnglishEditor,narrow ? 0 : 2);
        Grid.SetColumnSpan(EnglishEditor,narrow ? 3 : 1); EnglishEditor.Margin = new(0,narrow ? 14 : 0,0,0);
    }
    private void SelectSettingsCategory(string category)
    {
        var pages = new[] { ("general",SettingsGeneralPanel,SettingsGeneralNav), ("texts",SettingsTextsPanel,SettingsTextsNav), ("migration",SettingsMigrationPanel,SettingsMigrationNav), ("destinations",SettingsDestinationsPanel,SettingsDestinationsNav),
            ("telegram",SettingsTelegramPanel,SettingsTelegramNav), ("discord",SettingsDiscordPanel,SettingsDiscordNav), ("bluesky",SettingsBlueskyPanel,SettingsBlueskyNav) };
        foreach (var (key,panel,button) in pages)
        {
            panel.Visibility = category==key ? Visibility.Visible : Visibility.Collapsed;
            button.Style = (Style)FindResource(category==key ? "SettingsSelectedNavigationStyle" : "SettingsNavigationStyle");
        }
        SettingsContentScroll.ScrollToTop();
    }
    private void ShowSettingsCategory(object sender,RoutedEventArgs e)
    { if (sender is Button { Tag: string category }) SelectSettingsCategory(category); }
    private void ShowTextSettings(object sender,RoutedEventArgs e)
    { SelectSettingsCategory("texts"); SwitchView(SettingsView); }
    private void RefreshWorkerBadge()
    {
        if (WorkerTopText is null) return;
        var running = WorkerProcess.IsRunning;
        WorkerTopText.Text = running ? "Фоновая отправка" : "Очередь остановлена";
        WorkerTopLamp.Fill = (Brush)FindResource(running ? "GoodBrush" : "MutedBrush");
        WorkerTopButton.ToolTip = running ? "Очередь работает после закрытия окна. Нажмите, чтобы открыть её." : "Открыть очередь и управление фоновым процессом";
    }
    private void UpdateDestinationRows()
    {
        var rows = new[] { (TargetTelegram,TargetTelegramInfo,_options), (TargetDiscord,TargetDiscordInfo,_discord.PublishOptions), (TargetBluesky,TargetBlueskyInfo,_bluesky.PublishOptions) };
        var count = rows.Count(row=>row.Item1.IsChecked==true && DestinationRouting.Accepts(row.Item3.RatingRule, SelectedBlueskyLabel))
            + DestinationRouting.Select(_destinations, _extraSelected, SelectedBlueskyLabel).Length;
        var selected = rows.Count(row=>row.Item1.IsChecked==true) + _extraSelected.Count;
        SelectedDestinationCount.Text = count==0 ? "Не выбраны" : count==1 ? "1 место" : $"{count} места";
        foreach (var (_,label,options) in rows)
        {
            var destination = options.Demo ? "ДЕМО" : options.Chat.Length>0 ? options.Chat : "Не настроено";
            label.Text = destination+" · "+Draft.LanguageName(CurrentDraft.LanguageFor(options.Platform))
                + (DestinationRouting.Accepts(options.RatingRule, SelectedBlueskyLabel) ? "" : " · не подходит рейтингу");
            label.ToolTip = label.Text;
        }
        TargetSummary.Text = count==0 ? "Выберите, куда отправить пост." : "У каждого назначения будет свой результат в истории.";
        if (selected > count) TargetSummary.Text += $" Исключено по рейтингу или отключено: {selected-count}.";
    }
    private BitmapSource? ThumbnailFor(string path)
    {
        try
        {
            var file = new FileInfo(path);
            if (!file.Exists) return null;
            if (_thumbnails.TryGetValue(path,out var cached) && cached.Modified==file.LastWriteTimeUtc && cached.Length==file.Length) return cached.Bitmap;
            var image = new BitmapImage(); image.BeginInit(); image.CacheOption=BitmapCacheOption.OnLoad;
            image.DecodePixelWidth=128; image.UriSource=new Uri(file.FullName); image.EndInit(); image.Freeze();
            _thumbnails[path]=(file.LastWriteTimeUtc,file.Length,image); return image;
        }
        catch (Exception error) when (error is IOException or NotSupportedException or FileFormatException) { return null; }
    }
    private void ImageFilesDragOver(object sender,DragEventArgs e)
    {
        e.Effects = !_busy && e.Data.GetData(DataFormats.FileDrop) is string[] { Length: >0 } paths
            && paths.All(path=>new[]{".jpg",".jpeg",".png"}.Contains(Path.GetExtension(path).ToLowerInvariant())) ? DragDropEffects.Copy : DragDropEffects.None;
        e.Handled=true;
    }
    private async void ImageFilesDrop(object sender,DragEventArgs e)
    {
        e.Handled=true;
        if (!_busy && e.Data.GetData(DataFormats.FileDrop) is string[] paths) await Guard(()=>AddImagePathsAsync(paths));
    }
    private async Task AddImagePathsAsync(IEnumerable<string> additions)
    {
        if (_busy) return;
        var paths=_images.Concat(additions).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        var candidate=CurrentDraft with { ImagePath=paths.FirstOrDefault(),AdditionalImagePaths=paths.Skip(1).ToArray() };
        // File selection validates media independently of an unfinished translation.
        ValidateImages(candidate with { Title="",Text="",EnglishTitle="",EnglishText="",Language="ru",PlatformTexts=null },allowPreparation:CurrentOptions.Platform=="bluesky");
        _images.Clear();_images.AddRange(paths);RefreshImages(paths.Length-1);UpdatePreview();await SaveDraftAsync();
    }
}
