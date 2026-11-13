using System.Diagnostics;
using System.Windows;
using System.Windows.Media.Imaging;
using CrossPost.Core;
using Microsoft.Win32;

namespace CrossPost.Desktop;

public record PublicationBadge(string Code,string Name,string Color)
{
    public static PublicationBadge For(string platform) => platform.Replace("demo.", "", StringComparison.Ordinal) switch
    {
        "telegram" => new("TG","Telegram","#2CA5D0"), "discord" => new("DS","Discord","#6673F2"), "bluesky" => new("BS","Bluesky","#208BFE"),
        "vk" => new("VK","ВКонтакте","#2879F0"), "deviantart" => new("DA","DeviantArt","#008F59"),
        "blep.art" or "e621" => new("BA",platform,"#5867A4"), "reddit" => new("R","Reddit","#D94217"),
        "joyreactor" => new("JR","JoyReactor","#B64B13"), _ => new("•",platform,"#586174")
    };
}
public record WorkRow(Guid Id, string Title, DateTimeOffset? Date, string Platforms, string GroupLabel, string StateLabel, bool Confirmed)
{
    public string DateLabel => Date?.ToLocalTime().ToString("dd.MM.yyyy HH:mm") ?? "Дата неизвестна";
    public Func<BitmapSource?>? ThumbnailLoader { get; init; }
    public BitmapSource? Thumbnail => ThumbnailLoader?.Invoke();
    public PublicationBadge[] Badges { get; init; } = [];
    public string ShortPlatforms => string.Join(" · ",Badges.Select(b=>b.Code));
    public string ViewsLabel { get; init; } = "Просмотры: —";
    public string ReactionsLabel { get; init; } = "Реакции: —";
    public string MeasurementHint { get; init; } = "";
}
public record CalendarCell(DateTime Date, string Day, string Detail, string Background)
{
    public WorkRow[] Works { get; init; } = [];
    public int TileColumns => Works.Length <= 1 ? 1 : 2;
    public int TileRows => Works.Length <= 2 ? 1 : 2;
    public string CountText { get; init; } = "";
    public string PlanText { get; init; } = "";
    public string MoreText { get; init; } = "";
    public string Border { get; init; } = "#262A34";
    public double Opacity { get; init; } = 1;
}
public record TotalsRow(PlatformTotals Value)
{
    public string Platform => Value.Platform; public string Target => Value.Target; public int Publications => Value.Publications;
    public string ViewsLabel => Value.Views is { } value ? $"{value:N0} ({Value.WithViews}/{Publications})" : "—";
    public string ReactionsLabel => Value.Reactions is { } value ? $"{value:N0} ({Value.WithReactions}/{Publications})" : "—";
}
public partial class MainWindow
{
    private LibraryData _library = new([], [], new HashSet<Guid>());
    private DateTime _libraryMonth = new(DateTime.Today.Year, DateTime.Today.Month, 1);
    private DateTime? _libraryDay;
    private static readonly System.Globalization.CultureInfo LibraryCulture=System.Globalization.CultureInfo.GetCultureInfo("ru-RU");
    private bool _loadingLibrary, _renderingLibrary;
    private readonly Dictionary<Guid,string> _libraryMedia = [];
    private WorkRow[] _visibleLibraryWorks = [];
    private static string StateLabel(DeliveryState state) => state switch
    {
        DeliveryState.Sent => "Отправлено", DeliveryState.Refused => "Не отправлено", DeliveryState.Partial => "Частично",
        DeliveryState.Sending => "Отправляется", _ => "Проверьте площадку"
    };
    private async Task LoadLibraryAsync()
    {
        if (_loadingLibrary) return;
        _loadingLibrary = true;
        try
        {
            var loaded = await Task.Run(async () => (Data: await _store.LibraryAsync(), History: await _store.HistoryAsync()));
            _library=loaded.Data;_libraryMedia.Clear();
            foreach(var post in loaded.History) if(post.Draft.Images.FirstOrDefault() is { } path) _libraryMedia[post.Id]=path;
            RenderLibrary();
        }
        finally { _loadingLibrary = false; }
    }
    private void RenderLibrary()
    {
        if (_renderingLibrary || WorkList is null) return;
        _renderingLibrary = true;
        try
        {
            var selected = WorkList.SelectedItems.Cast<WorkRow>().Select(r => r.Id).ToHashSet();
            var posts = _library.Posts.Where(post => IncludeDemo.IsChecked == true || !post.Platform.StartsWith("demo.", StringComparison.Ordinal)).ToArray();
            var start = _libraryMonth.AddDays(-((int)_libraryMonth.DayOfWeek + 6) % 7);
            var days = (int)Math.Ceiling((_libraryMonth.AddMonths(1)-start).TotalDays/7)*7;
            var end = start.AddDays(days);
            var representativeGroups = Library.Representatives(_library).GroupBy(p=>p.WorkId).ToDictionary(g=>g.Key,g=>g.ToArray());
            var works = posts.GroupBy(post => post.WorkId).Select(group =>
            {
                var newest = group.OrderByDescending(post => post.Published).First();
                var first = group.Where(post => post.Published.HasValue).MinBy(post => post.Published)?.Published;
                var title = newest.Title.Length > 0 ? newest.Title : newest.Text.Split('\n')[0];
                var badges = group.Select(p=>p.Platform).Distinct().Select(PublicationBadge.For).ToArray();
                var representatives = representativeGroups.GetValueOrDefault(group.Key) ?? [];
                var values = representatives.Select(p=>Library.Latest(_library,p.Id)?.Values).ToArray();
                var views = values.Where(v=>v?.Views is not null).Select(v=>v!.Views!.Value).ToArray();
                var reactions = values.Where(v=>v?.Reactions is not null).Select(v=>v!.Reactions!.Value).ToArray();
                var imagePaths = group.OrderByDescending(p=>p.Published).Where(p=>p.NativeId.HasValue && _libraryMedia.ContainsKey(p.NativeId.Value))
                    .Select(p=>_libraryMedia[p.NativeId!.Value]).Distinct().ToArray();
                var showThumbnail = first?.ToLocalTime().Date is { } date && date >= start && date < end;
                return new WorkRow(group.Key, title.Length == 0 ? "Без заголовка" : title, first,
                    string.Join(", ", badges.Select(b=>b.Name)),
                    _library.ManualWorks.Contains(group.Key) && group.Count() > 1 ? "Объединено вручную" : group.Count() > 1 ? "Одна работа" : "",
                    string.Join(", ", group.Select(post => StateLabel(post.State)).Distinct()), group.Any(post => post.State == DeliveryState.Sent))
                {
                    ThumbnailLoader = showThumbnail ? () => imagePaths.Select(ThumbnailFor).FirstOrDefault(image=>image is not null) : null, Badges = badges,
                    ViewsLabel = views.Length > 0 ? $"Просмотры: {views.Sum():N0}" : "Просмотры: —",
                    ReactionsLabel = reactions.Length > 0 ? $"Реакции: {reactions.Sum():N0}" : "Реакции: —",
                    MeasurementHint = $"Данные просмотров: {views.Length} из {representatives.Length} назначений. Реакции: {reactions.Length} из {representatives.Length}. При объединении учитывается последняя успешная публикация в каждом назначении."
                };
            }).ToArray();
            _visibleLibraryWorks = works;
            var byDay = works.Where(w=>w.Date.HasValue).GroupBy(w=>w.Date!.Value.ToLocalTime().Date).ToDictionary(g=>g.Key,g=>g.ToArray());
            var jobs = Queue.Select(row=>row.Record).Where(job=>job.State is QueueState.Pending or QueueState.Paused or QueueState.Running).ToArray();
            CalendarDays.ItemsSource = Enumerable.Range(0,days).Select(index=>
            {
                var date = start.AddDays(index); var dayWorks = byDay.GetValueOrDefault(date) ?? [];
                var scheduled = jobs.Count(job=>job.Due.ToLocalTime().Date == date);
                return new CalendarCell(date, date.Day.ToString(), $"{date:d}: работ {dayWorks.Length}, заданий {scheduled}", "#21252E")
                {
                    Works = dayWorks.Take(4).ToArray(), CountText = dayWorks.Length > 1 ? dayWorks.Length.ToString() : "",
                    PlanText = scheduled > 0 ? $"◷ {scheduled} в очереди" : "", MoreText = dayWorks.Length > 4 ? $"+{dayWorks.Length-4}" : "",
                    Border = _libraryDay == date || date == DateTime.Today ? "#8B97FF" : "#262A34", Opacity = date.Month == _libraryMonth.Month ? 1 : .42
                };
            }).ToArray();
            LibraryMonthLabel.Text = LibraryCulture.TextInfo.ToTitleCase(_libraryMonth.ToString("MMMM yyyy",LibraryCulture));
            var monthly = works.Where(w=>w.Date?.ToLocalTime().Year == _libraryMonth.Year && w.Date?.ToLocalTime().Month == _libraryMonth.Month).ToArray();
            LibraryMonthCount.Text = $"Работ: {monthly.Length}";
            var rows = works.Where(work=>work.Date is null || (work.Date.Value.ToLocalTime().Year == _libraryMonth.Year && work.Date.Value.ToLocalTime().Month == _libraryMonth.Month
                && (_libraryDay is null || work.Date.Value.ToLocalTime().Date == _libraryDay))).OrderByDescending(work=>work.Date).ToArray();
            WorkList.ItemsSource = rows;
            foreach (var row in rows.Where(r=>selected.Contains(r.Id))) WorkList.SelectedItems.Add(row);
            LibraryEmpty.Visibility = rows.Length == 0 ? Visibility.Visible : Visibility.Collapsed;
            LibraryDayHeading.Text = _libraryDay is { } chosen ? chosen.ToString("d MMMM · dddd",LibraryCulture) : "Публикации за месяц";
            LibrarySummary.Text = $"Работ: {works.Length} · публикаций: {posts.Length} · замеров: {_library.Metrics.Count} · без даты: {works.Count(w=>w.Date is null)}";
            TotalsList.ItemsSource = Library.Totals(_library).Select(row=>new TotalsRow(row)).ToArray();
            UpdateCalendarSize();
        }
        finally { _renderingLibrary = false; }
        UpdateWorkInspector();
    }
    private void CalendarSizeChanged(object sender, SizeChangedEventArgs e) => UpdateCalendarSize();
    private void UpdateCalendarSize()
    {
        if (CalendarDays is null || CalendarCard is null) return;
        var rows = (int)Math.Ceiling(CalendarDays.Items.Count/7d);
        var width = CalendarCard.ActualWidth > 0 ? CalendarCard.ActualWidth : Math.Max(350,ActualWidth-96);
        var height = Math.Clamp((width-40)/7+18,94,174)*rows;
        if (Math.Abs(CalendarDays.Height-height)>.5 || double.IsNaN(CalendarDays.Height)) CalendarDays.Height=height;
    }
    private void TodayLibraryMonth(object sender,RoutedEventArgs e)
    { _libraryMonth=new(DateTime.Today.Year,DateTime.Today.Month,1);_libraryDay=null;RenderLibrary();LibraryScroll.ScrollToTop(); }
    private void SelectCalendarWork(object sender,RoutedEventArgs e)
    {
        e.Handled=true;
        if ((sender as FrameworkElement)?.Tag is not Guid id) return;
        var work=_visibleLibraryWorks.FirstOrDefault(w=>w.Id==id);
        if (work?.Date is { } date) { var local=date.ToLocalTime();_libraryMonth=new(local.Year,local.Month,1);_libraryDay=System.Windows.Input.Keyboard.Modifiers.HasFlag(System.Windows.Input.ModifierKeys.Control)?null:local.Date; }
        RenderLibrary();
        if (WorkList.Items.Cast<WorkRow>().FirstOrDefault(w=>w.Id==id) is { } row)
        {
            if (System.Windows.Input.Keyboard.Modifiers.HasFlag(System.Windows.Input.ModifierKeys.Control))
            { if (WorkList.SelectedItems.Contains(row)) WorkList.SelectedItems.Remove(row);else WorkList.SelectedItems.Add(row); }
            else WorkList.SelectedItem=row;
        }
    }
    private void CloseWorkInspector(object sender,RoutedEventArgs e) => WorkList.SelectedItems.Clear();
    private async void ShowLibrary(object sender, RoutedEventArgs e)
    { SwitchView(LibraryView); await Guard(async () => { await LoadQueueAsync(); await LoadLibraryAsync(); }); }
    private async void ShowLibraryStats(object sender, RoutedEventArgs e)
    { SwitchView(LibraryStatsView); await Guard(LoadLibraryAsync); }
    private async void RefreshLibrary(object sender, RoutedEventArgs e) => await Guard(async () => { await LoadQueueAsync(); await LoadLibraryAsync(); });
    private void LibraryFilterChanged(object sender, RoutedEventArgs e) { if (_loaded) RenderLibrary(); }
    private void PreviousLibraryMonth(object sender, RoutedEventArgs e) { _libraryMonth = _libraryMonth.AddMonths(-1); _libraryDay = null; RenderLibrary(); }
    private void NextLibraryMonth(object sender, RoutedEventArgs e) { _libraryMonth = _libraryMonth.AddMonths(1); _libraryDay = null; RenderLibrary(); }
    private void ClearLibraryDay(object sender, RoutedEventArgs e) { _libraryDay = null; RenderLibrary(); }
    private void SelectLibraryDay(object sender, RoutedEventArgs e)
    {
        if ((sender as FrameworkElement)?.Tag is DateTime date)
        { _libraryMonth = new(date.Year, date.Month, 1); _libraryDay = date; RenderLibrary(); }
    }
    private void WorkSelected(object sender, System.Windows.Controls.SelectionChangedEventArgs e)
    { if (!_renderingLibrary) UpdateWorkInspector(); }
    private void UpdateWorkInspector()
    {
        var rows=WorkList.SelectedItems.Cast<WorkRow>().ToArray();var ids=rows.Select(row=>row.Id).ToHashSet();
        JoinWorksButton.IsEnabled=rows.Length>1;SplitWorksButton.IsEnabled=rows.Any(r=>_library.ManualWorks.Contains(r.Id));OpenLibraryButton.IsEnabled=rows.Length==1;
        WorkInspector.Visibility=rows.Length>0?Visibility.Visible:Visibility.Collapsed;
        WorkInspectorTitle.Text=rows.Length==1?rows[0].Title:$"Выбрано работ: {rows.Length}";
        WorkInspectorImage.Source=rows.Length==1?rows[0].Thumbnail:null;WorkInspectorImage.Visibility=WorkInspectorImage.Source is null?Visibility.Collapsed:Visibility.Visible;
        var posts=Library.Representatives(_library).Where(p=>ids.Contains(p.WorkId)).ToArray();
        if(posts.Length==0) posts=_library.Posts.Where(p=>ids.Contains(p.WorkId)).ToArray();
        WorkDetails.Text=string.Join("\n\n",posts.Select(post=>
        {
            var sample=Library.Latest(_library,post.Id);string Number(long? value)=>value?.ToString("N0")??"—";
            return (post.Excluded?"Не учитывается в статистике · ":"")+$"{PublicationBadge.For(post.Platform).Name} · {post.Target}\n{StateLabel(post.State)} · {post.Published?.ToLocalTime():dd.MM.yyyy HH:mm}\nПросмотры: {Number(sample?.Values.Views)} · Реакции: {Number(sample?.Values.Reactions)}\nЗамер: {sample?.Observed?.ToLocalTime().ToString("g")??"дата неизвестна"}";
        }));
    }
    private async void ImportLegacy(object sender, RoutedEventArgs e)
    {
        if (_busy) return;
        var picker = new OpenFileDialog { Title = "Выберите posts.db Python-версии CrossPost", Filter = "SQLite база|*.db|Все файлы|*.*" };
        if (picker.ShowDialog(this) != true) return;
        await RunOperation(async ct =>
        {
            var progress = new Progress<string>(text => StatusLabel.Text = text);
            var report = await Task.Run(() => new LegacyImporter(_store).ImportAsync(picker.FileName, ct, progress));
            await LoadLibraryAsync();
            var latest = _library.Posts.Where(post => post.Published.HasValue).MaxBy(post => post.Published)?.Published?.ToLocalTime();
            if (latest is { } date) { _libraryMonth = new(date.Year, date.Month, 1); _libraryDay = null; RenderLibrary(); }
            ImportSummary.Text = $"Добавлено публикаций: {report.Publications}, замеров: {report.Measurements}.\nАрхив заданий: {report.ArchivedSchedules}.\n" + string.Join("\n", report.Notes);
            StatusLabel.Text = "Импорт завершён. Исходный снимок и резервная копия сохранены в папке данных.";
        });
    }
    private async void JoinWorks(object sender, RoutedEventArgs e) => await Guard(async () =>
    {
        var works = WorkList.SelectedItems.Cast<WorkRow>().Select(row => row.Id).ToArray();
        if (works.Length < 2) throw new InvalidOperationException("Выберите несколько работ через Ctrl/Shift.");
        await _store.GroupWorksAsync(works, false); await LoadLibraryAsync();
    });
    private async void SplitWorks(object sender, RoutedEventArgs e) => await Guard(async () =>
    {
        var works = WorkList.SelectedItems.Cast<WorkRow>().Select(row => row.Id).ToArray();
        await _store.GroupWorksAsync(works, true); await LoadLibraryAsync();
    });
    private void OpenLibraryPost(object sender, RoutedEventArgs e)
    {
        if (WorkList.SelectedItem is not WorkRow row) return;
        var post = _library.Posts.Where(post => post.WorkId == row.Id).OrderByDescending(post => post.Published).First();
        if (Uri.TryCreate(post.Link, UriKind.Absolute, out var uri) && uri.Scheme == "https")
            Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
    }
}
