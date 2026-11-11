using System.Diagnostics;
using System.Windows;
using CrossPost.Core;
using Microsoft.Win32;

namespace CrossPost.Desktop;

public record WorkRow(Guid Id, string Title, DateTimeOffset? Date, string Platforms, string GroupLabel, string StateLabel, bool Confirmed)
{ public string DateLabel => Date?.ToLocalTime().ToString("dd.MM.yyyy HH:mm") ?? "Дата неизвестна"; }
public record CalendarCell(DateTime Date, string Label, string Detail, string Background);
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
    private bool _loadingLibrary;
    private static string StateLabel(DeliveryState state) => state switch
    {
        DeliveryState.Sent => "Отправлено", DeliveryState.Refused => "Не отправлено", DeliveryState.Partial => "Частично",
        DeliveryState.Sending => "Отправляется", _ => "Проверьте площадку"
    };
    private async Task LoadLibraryAsync()
    {
        if (_loadingLibrary) return;
        _loadingLibrary = true;
        try { _library = await Task.Run(_store.LibraryAsync); RenderLibrary(); }
        finally { _loadingLibrary = false; }
    }
    private void RenderLibrary()
    {
        var posts = _library.Posts.Where(post => IncludeDemo.IsChecked == true || !post.Platform.StartsWith("demo.", StringComparison.Ordinal)).ToArray();
        var works = posts.GroupBy(post => post.WorkId).Select(group =>
        {
            var newest = group.OrderByDescending(post => post.Published).First();
            var first = group.Where(post => post.Published.HasValue).MinBy(post => post.Published)?.Published;
            var title = newest.Title.Length > 0 ? newest.Title : newest.Text.Split('\n')[0];
            return new WorkRow(group.Key, title.Length == 0 ? "Без заголовка" : title, first,
                string.Join(", ", group.Select(post => post.Platform).Distinct()),
                _library.ManualWorks.Contains(group.Key) && group.Count() > 1 ? "Объединено вручную" : group.Count() > 1 ? "Одна работа" : "",
                string.Join(", ", group.Select(post => StateLabel(post.State)).Distinct()), group.Any(post => post.State == DeliveryState.Sent));
        }).ToArray();
        var jobs = Queue.Select(row => row.Record).Where(job => job.State is QueueState.Pending or QueueState.Paused or QueueState.Running).ToArray();
        var start = _libraryMonth.AddDays(-((int)_libraryMonth.DayOfWeek + 6) % 7);
        CalendarDays.ItemsSource = Enumerable.Range(0, 42).Select(index =>
        {
            var date = start.AddDays(index); var count = works.Count(work => work.Confirmed && work.Date?.ToLocalTime().Date == date);
            var attempts = works.Count(work => !work.Confirmed && work.Date?.ToLocalTime().Date == date);
            var scheduled = jobs.Count(job => job.Due.ToLocalTime().Date == date);
            return new CalendarCell(date, date.Day + (count > 0 ? $" · {count}" : "") + (attempts > 0 ? " !" : "") + (scheduled > 0 ? $"\n◷ {scheduled}" : ""),
                $"{date:d}: подтверждённых работ {count}, неподтверждённых попыток {attempts}, заданий {scheduled}", date.Month == _libraryMonth.Month ? "#30384A" : "#1A1E27");
        }).ToArray();
        LibraryMonthLabel.Text = _libraryMonth.ToString("MMMM yyyy");
        WorkList.ItemsSource = works.Where(work => work.Date is null || (work.Date.Value.ToLocalTime().Year == _libraryMonth.Year && work.Date.Value.ToLocalTime().Month == _libraryMonth.Month
            && (_libraryDay is null || work.Date.Value.ToLocalTime().Date == _libraryDay))).OrderByDescending(work => work.Date).ToArray();
        LibrarySummary.Text = $"Работ: {works.Length}\nПубликаций на площадках: {posts.Length}\nЗамеров: {_library.Metrics.Count}\nБез даты: {works.Count(work => work.Date is null)}\n\nВыберите день или несколько работ в списке (Ctrl/Shift).";
        TotalsList.ItemsSource = Library.Totals(_library).Select(row => new TotalsRow(row)).ToArray();
    }
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
    {
        var ids = WorkList.SelectedItems.Cast<WorkRow>().Select(row => row.Id).ToHashSet();
        WorkDetails.Text = string.Join("\n", _library.Posts.Where(post => ids.Contains(post.WorkId)).Select(post =>
        {
            var sample = Library.Latest(_library, post.Id);
            string Number(long? value) => value?.ToString("N0") ?? "—";
            return (post.Excluded ? "Не учитывается в статистике • " : "") + $"{post.Platform} · {post.Target} · {StateLabel(post.State)} · {post.Published?.ToLocalTime():dd.MM.yyyy HH:mm}\nПросмотры: {Number(sample?.Values.Views)}, реакции: {Number(sample?.Values.Reactions)} • замер: {sample?.Observed?.ToLocalTime().ToString("g") ?? "дата неизвестна"}";
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
