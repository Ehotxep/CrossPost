using System.Globalization;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.Data.Sqlite;

namespace CrossPost.Core;

public sealed class LegacyImporter(Store store)
{
    private static string S(Dictionary<string, object?> row, string key) => row.TryGetValue(key, out var value)
        ? Convert.ToString(value, CultureInfo.InvariantCulture) ?? "" : "";
    private static long? N(Dictionary<string, object?> row, string key) => long.TryParse(S(row, key), out var value) && value >= 0 ? value : null;
    private static DateTimeOffset? Date(string text)
    {
        if (long.TryParse(text, out var unix))
        { if (unix == 0) return null; try { return DateTimeOffset.FromUnixTimeSeconds(unix); } catch (ArgumentOutOfRangeException) { return null; } }
        return DateTimeOffset.TryParse(text, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var date) ? date : null;
    }
    private static MetricValues Metrics(Dictionary<string, object?> row, bool external = false) => new(N(row, "views"),
        N(row, external ? "likes" : "reactions"), N(row, external ? "reposts" : "forwards"), N(row, external ? "comments" : "replies"), N(row, "reach"), N(row, "bookmarks"));
    private static async IAsyncEnumerable<Dictionary<string, object?>> Rows(SqliteConnection database, HashSet<string> tables, string table,
        [System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken ct)
    {
        if (!tables.Contains(table)) yield break;
        using var command = database.CreateCommand(); command.CommandText = "SELECT * FROM \"" + table + "\"";
        using var reader = await command.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            var row = new Dictionary<string, object?>(StringComparer.OrdinalIgnoreCase);
            for (var index = 0; index < reader.FieldCount; index++) row[reader.GetName(index)] = reader.IsDBNull(index) ? null : reader.GetValue(index);
            yield return row;
        }
    }
    private static string[] Ids(string text, string primary = "")
    {
        var result = new List<string>();
        try
        {
            using var json = JsonDocument.Parse(text);
            if (json.RootElement.ValueKind == JsonValueKind.Array)
                result.AddRange(json.RootElement.EnumerateArray().Select(value => value.ToString()));
        }
        catch (JsonException) { result.AddRange(text.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)); }
        if (primary.Length > 0) result.Add(primary);
        return result.Where(value => value.Length > 0 && value != "0").Distinct().ToArray();
    }
    private static (string Target, string[] Ids) Identity(string platform, string target, string key, string link, string[] ids)
    {
        var references = ids.ToList();
        if (platform == "vk")
        {
            var match = Regex.Match(link + " " + key, @"(?:wall)?(-?\d+)_(\d+)");
            if (match.Success) { target = match.Groups[1].Value; references.Add(match.Groups[2].Value); }
        }
        if (Uri.TryCreate(link, UriKind.Absolute, out var uri) && uri.Scheme == "https")
        {
            if (platform == "bluesky")
            {
                var parts = uri.AbsolutePath.Split('/', StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length >= 4 && parts[0] == "profile" && parts[2] == "post")
                { target = parts[1]; references.Add(parts[3]); }
            }
            if (platform == "discord")
            {
                var parts = uri.AbsolutePath.Split('/', StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length >= 4 && parts[0] == "channels")
                { target = parts[1] + "/" + parts[2]; references.Add(parts[3]); }
            }
            references.Add("url:" + uri.GetLeftPart(UriPartial.Path).TrimEnd('/'));
            if (string.IsNullOrWhiteSpace(target)) target = uri.Host;
        }
        if (string.IsNullOrWhiteSpace(target)) target = platform;
        // A destination slot such as discord:0 is not an external message identity.
        if (references.Count == 0 && key.Length > 0) references.Add("legacy-key:" + key);
        return (target, references.Distinct().ToArray());
    }
    public async Task<ImportReport> ImportAsync(string sourcePath, CancellationToken ct = default, IProgress<string>? progress = null)
    {
        sourcePath = Path.GetFullPath(sourcePath);
        if (sourcePath.Equals(Path.Combine(store.Folder, "prototype.db"), StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("Выберите posts.db Python-версии, а не базу нативного приложения.");
        var batch = Guid.NewGuid(); var folder = Path.Combine(store.Folder, "imports", batch.ToString("N")); Directory.CreateDirectory(folder);
        var snapshot = Path.Combine(folder, "source.db");
        progress?.Report("Создаю согласованный снимок Python-базы…");
        using (var source = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = sourcePath, Mode = SqliteOpenMode.ReadOnly, Pooling = false, DefaultTimeout = 10 }.ToString()))
        using (var destination = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = snapshot, Pooling = false }.ToString()))
        {
            await source.OpenAsync(ct); await destination.OpenAsync(ct); ct.ThrowIfCancellationRequested();
            source.BackupDatabase(destination);
        }
        using var database = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = snapshot, Mode = SqliteOpenMode.ReadOnly, Pooling = false }.ToString());
        await database.OpenAsync(ct);
        var tables = new HashSet<string>();
        using (var schema = database.CreateCommand())
        {
            schema.CommandText = "SELECT name FROM sqlite_master WHERE type='table'";
            using var reader = await schema.ExecuteReaderAsync(ct); while (await reader.ReadAsync(ct)) tables.Add(reader.GetString(0));
        }
        if (!tables.Contains("posts") && !tables.Contains("ext_posts") && !tables.Contains("publications"))
            throw new InvalidOperationException("В выбранной базе нет истории CrossPost.");
        // Distinguish the Python publications table from the native JSON table.
        if (tables.Contains("publications"))
        {
            using var columns = database.CreateCommand(); columns.CommandText = "SELECT name FROM pragma_table_info('publications')";
            var names = new HashSet<string>(); using var reader = await columns.ExecuteReaderAsync(ct);
            while (await reader.ReadAsync(ct)) names.Add(reader.GetString(0));
            if (!names.Contains("dests")) throw new InvalidOperationException("Это не Python-база CrossPost: отсутствуют назначения публикаций.");
        }
        foreach (var (table, required) in new[]
        {
            ("posts", new[] { "id", "platform", "chat_id", "msg_id", "date_utc" }),
            ("ext_posts", new[] { "key", "platform", "date" })
        })
        {
            if (!tables.Contains(table)) continue;
            using var columns = database.CreateCommand(); columns.CommandText = "SELECT name FROM pragma_table_info('" + table + "')";
            var names = new HashSet<string>(); using var reader = await columns.ExecuteReaderAsync(ct);
            while (await reader.ReadAsync(ct)) names.Add(reader.GetString(0));
            if (required.Any(name => !names.Contains(name))) throw new InvalidOperationException("Неизвестный формат таблицы " + table + ". Импорт не применён.");
        }
        var backup = await store.BackupAsync("before-import");
        var sourceKey = OperatingSystem.IsWindows() ? sourcePath.ToUpperInvariant() : sourcePath;
        Guid Key(string value) => Library.Key("python|" + sourceKey + "|" + value);
        var aliases = new Dictionary<string, List<Guid>>(); var notes = new List<string>(); var badDates = 0; var badJson = 0;
        var channels = new Dictionary<string, bool>();
        await foreach (var row in Rows(database, tables, "channels", ct)) channels[S(row, "platform") + "|" + S(row, "chat_id")] = S(row, "selected") != "0";
        using var connection = await store.OpenForImportAsync(); using var transaction = connection.BeginTransaction(deferred: false);
        async Task<int> Count(string table)
        { using var command = connection.CreateCommand(); command.Transaction = transaction; command.CommandText = "SELECT COUNT(*) FROM " + table; return Convert.ToInt32(await command.ExecuteScalarAsync(ct)); }
        var beforePosts = await Count("library_posts"); var beforeMetrics = await Count("library_metrics");
        progress?.Report("Импортирую публикации и их внешние идентификаторы…");
        await foreach (var row in Rows(database, tables, "publications", ct))
        {
            var date = Date(S(row, "created")); if (date is null) badDates++;
            var alias = "u" + S(row, "id"); var work = Key("work|" + alias); var mapped = new List<Guid>();
            JsonDocument document;
            try { document = JsonDocument.Parse(S(row, "dests")); }
            catch (JsonException) { badJson++; continue; }
            using (document)
            {
                if (document.RootElement.ValueKind != JsonValueKind.Array) { badJson++; continue; }
                var index = 0;
                foreach (var dest in document.RootElement.EnumerateArray())
                {
                    if (dest.ValueKind != JsonValueKind.Object) { badJson++; continue; }
                    string D(string name) => dest.TryGetProperty(name, out var value) ? value.ToString() : "";
                    var platform = Library.Platform(D("platform").Length > 0 ? D("platform") : D("key").Split(':')[0]);
                    if (platform.Length == 0) platform = "unknown";
                    var identity = Identity(platform, D("chat_id"), "", D("link"), Ids(D("message_ids")));
                    var state = D("state") == "done" ? DeliveryState.Sent : D("outcome") == "not_sent" ? DeliveryState.Refused : DeliveryState.Unknown;
                    var entry = new LibraryPost(Key(alias + "|" + index++), work, platform, identity.Target, identity.Ids, date,
                        S(row, "title"), S(row, "text"), state, D("link"), Source: alias, OriginalJson: JsonSerializer.Serialize(row));
                    var saved = await Store.UpsertLibraryAsync(connection, transaction, entry); mapped.Add(saved.Id);
                }
            }
            aliases[alias] = mapped;
        }
        await foreach (var row in Rows(database, tables, "posts", ct))
        {
            var platform = Library.Platform(S(row, "platform")); var alias = "p" + S(row, "id"); var date = Date(S(row, "date_utc"));
            if (date is null) badDates++;
            var identity = Identity(platform, S(row, "chat_id"), "", S(row, "link"), Ids(S(row, "msg_ids"), S(row, "msg_id")));
            var excluded = S(row, "excluded") == "1" || S(row, "is_forward") == "1" || channels.GetValueOrDefault(S(row, "platform") + "|" + S(row, "chat_id"), true) == false;
            var entry = new LibraryPost(Key(alias), Key("work|" + alias), platform, identity.Target, identity.Ids, date,
                S(row, "text").Split('\n')[0], S(row, "text"), DeliveryState.Sent, S(row, "link"), excluded, Source: alias, OriginalJson: JsonSerializer.Serialize(row));
            var saved = await Store.UpsertLibraryAsync(connection, transaction, entry); aliases[alias] = [saved.Id];
            await Store.AddMetricAsync(connection, transaction, saved.Id, Date(S(row, "updated_at")), Metrics(row), "posts:" + S(row, "id"));
        }
        progress?.Report("Импортирую внешние посты и историю замеров…");
        await foreach (var row in Rows(database, tables, "ext_posts", ct))
        {
            var key = S(row, "key"); var platform = Library.Platform(S(row, "platform"));
            var identity = Identity(platform, "", key, S(row, "link"), []); var date = Date(S(row, "date")); if (date is null) badDates++;
            var entry = new LibraryPost(Key("ext|" + key), Key("work|ext|" + key), platform, identity.Target, identity.Ids, date,
                S(row, "text").Split('\n')[0], S(row, "text"), DeliveryState.Sent, S(row, "link"), Source: key, OriginalJson: JsonSerializer.Serialize(row));
            var saved = await Store.UpsertLibraryAsync(connection, transaction, entry); aliases[key] = [saved.Id];
            // ext_posts uses replies, whereas vk_snap uses comments.
            var values = Metrics(row, true) with { Replies = N(row, "replies") };
            await Store.AddMetricAsync(connection, transaction, saved.Id, Date(S(row, "updated_at")), values, "ext_posts:" + key);
        }
        await foreach (var row in Rows(database, tables, "snapshots", ct))
        {
            if (!aliases.TryGetValue("p" + S(row, "post_id"), out var mapped)) continue;
            foreach (var id in mapped) await Store.AddMetricAsync(connection, transaction, id, Date(S(row, "taken_at")), Metrics(row), "snapshots");
        }
        // VK rows may exist even if the external-post mirror was never populated.
        await foreach (var row in Rows(database, tables, "vk_posts", ct))
        {
            var key = "vk:" + S(row, "key"); var identity = Identity("vk", S(row, "owner"), S(row, "key"), S(row, "href"), []);
            var entry = new LibraryPost(Key(key), Key("work|" + key), "vk", identity.Target, identity.Ids, Date(S(row, "date")),
                S(row, "title"), S(row, "text"), DeliveryState.Sent, S(row, "href"), Source: key, OriginalJson: JsonSerializer.Serialize(row));
            var saved = await Store.UpsertLibraryAsync(connection, transaction, entry); aliases[key] = [saved.Id];
        }
        await foreach (var row in Rows(database, tables, "vk_snap", ct))
        {
            if (!aliases.TryGetValue("vk:" + S(row, "key"), out var mapped)) continue;
            foreach (var id in mapped) await Store.AddMetricAsync(connection, transaction, id, Date(S(row, "at")), Metrics(row, true), "vk_snap");
        }
        var rules = new List<Dictionary<string, object?>>(); await foreach (var row in Rows(database, tables, "cal_rules", ct)) rules.Add(row);
        foreach (var row in rules.OrderBy(row => S(row, "kind") == "join" ? 0 : 1))
        {
            if (!aliases.TryGetValue(S(row, "a"), out var first) || !aliases.TryGetValue(S(row, "b"), out var second)) continue;
            var ids = first.Concat(second).Distinct().Order().ToArray();
            var ruleId = Library.Key(S(row, "kind") + "|" + string.Join('|', ids)).ToString();
            using var rule = connection.CreateCommand(); rule.Transaction = transaction;
            rule.CommandText = "INSERT OR IGNORE INTO legacy_rules_applied(id) VALUES($id)"; rule.Parameters.AddWithValue("$id", ruleId);
            if (await rule.ExecuteNonQueryAsync(ct) == 1)
                await Store.GroupPostsAsync(connection, transaction, ids, S(row, "kind") != "join");
        }
        var schedules = 0; await foreach (var _ in Rows(database, tables, "scheduled", ct)) schedules++;
        if (badDates > 0) notes.Add($"Не распознаны даты в {badDates} записях; они сохранены без выдуманной даты.");
        if (badJson > 0) notes.Add($"Не разобраны назначения в {badJson} записях. Оригиналы доступны в снимке source.db.");
        notes.Add("Полная исходная база сохранена, включая неизвестные поля, черновики и расписание. Авторизация и задания в рабочую очередь не переносятся. Файлы превью и медиа не копируются.");
        var report = new ImportReport(batch, snapshot, backup, await Count("library_posts") - beforePosts,
            await Count("library_metrics") - beforeMetrics, schedules, notes.ToArray());
        using var saveReport = connection.CreateCommand(); saveReport.Transaction = transaction;
        saveReport.CommandText = "INSERT INTO legacy_imports(id,json) VALUES($id,$json)";
        saveReport.Parameters.AddWithValue("$id", batch.ToString()); saveReport.Parameters.AddWithValue("$json", JsonSerializer.Serialize(report));
        await saveReport.ExecuteNonQueryAsync(ct); ct.ThrowIfCancellationRequested(); transaction.Commit(); return report;
    }
}
