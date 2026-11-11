using System.IO;
using System.Windows;
using CrossPost.Core;
using Microsoft.Data.Sqlite;

namespace CrossPost.Desktop;

public partial class MainWindow
{
    private async Task RunLibrarySmokeAsync()
    {
        var sourcePath = Path.Combine(App.DataFolder, "library-smoke-source.db");
        using (var database = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = sourcePath, Pooling = false }.ToString()))
        {
            await database.OpenAsync(); using var command = database.CreateCommand();
            command.CommandText = """
                CREATE TABLE posts(id INTEGER PRIMARY KEY,platform TEXT,chat_id TEXT,msg_id INTEGER,date_utc TEXT,text TEXT,views INTEGER,updated_at TEXT);
                INSERT INTO posts VALUES(1,'telegram','-100999',7001,'2026-11-01T00:00:00Z','Old',100,'2026-11-03T00:00:00Z'),
                                        (2,'telegram','-100999',7002,'2026-11-02T00:00:00Z','New',30,'2026-11-03T00:00:00Z');
                """;
            await command.ExecuteNonQueryAsync();
        }
        await Task.Run(() => new LegacyImporter(_store).ImportAsync(sourcePath));
        _libraryMonth = new(2026, 11, 1); await LoadLibraryAsync(); SwitchView(LibraryView);
        Width = 850; Height = 650; UpdateLayout();
        if (WorkList.Items.Count != 2 || CalendarDays.Items.Count != 42 || WorkList.ActualHeight < 60)
            throw new InvalidOperationException("Calendar/list layout or imported work visibility failed");
        var rows = WorkList.Items.Cast<WorkRow>().ToArray();
        foreach (var row in rows) WorkList.SelectedItems.Add(row);
        if (string.IsNullOrWhiteSpace(WorkDetails.Text)) throw new InvalidOperationException("Publication selection details missing");
        await _store.GroupWorksAsync(rows.Select(row => row.Id).ToArray(), false); await LoadLibraryAsync();
        SwitchView(LibraryStatsView); UpdateLayout();
        if (WorkList.Items.Count != 1 || TotalsList.Items.Count != 1 || TotalsList.Items[0] is not TotalsRow { Value.Views: 30, Publications: 1 })
            throw new InvalidOperationException("Merged work statistics use an older/summed publication");
        var joined = (WorkRow)WorkList.Items[0];
        await _store.GroupWorksAsync([joined.Id], true); await LoadLibraryAsync();
        if (WorkList.Items.Count != 2) throw new InvalidOperationException("Splitting did not restore work list");
        var repeat = await Task.Run(() => new LegacyImporter(_store).ImportAsync(sourcePath));
        if (repeat.Publications != 0 || repeat.Measurements != 0) throw new InvalidOperationException("Repeated UI import duplicated data");
        Width = 1100; Height = 800; SwitchView(ComposeView);
    }
}
