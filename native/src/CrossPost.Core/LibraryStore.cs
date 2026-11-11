using System.Text.Json;
using Microsoft.Data.Sqlite;

namespace CrossPost.Core;

public sealed partial class Store
{
    public const int SchemaVersion = 1;
    internal Task<SqliteConnection> OpenForImportAsync() => OpenAsync();
    public async Task<string> BackupAsync(string reason)
    {
        var folder = Path.Combine(Folder, "backups"); Directory.CreateDirectory(folder);
        var path = Path.Combine(folder, $"prototype-{reason}-{DateTimeOffset.UtcNow:yyyyMMddHHmmss}-{Guid.NewGuid():N}.db");
        using var source = await OpenAsync();
        using var backup = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = path, Pooling = false }.ToString());
        await backup.OpenAsync(); source.BackupDatabase(backup); return path;
    }
    private async Task InitializeLibraryAsync()
    {
        using var connection = await OpenAsync(); using var version = connection.CreateCommand();
        version.CommandText = "PRAGMA user_version";
        var current = Convert.ToInt32(await version.ExecuteScalarAsync());
        if (current > SchemaVersion) throw new InvalidOperationException("Эта база создана более новой версией CrossPost. Обновите приложение.");
        if (current == SchemaVersion) return;
        using var exists = connection.CreateCommand(); exists.CommandText = "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'";
        if (Convert.ToInt32(await exists.ExecuteScalarAsync()) > 0) await BackupAsync("before-library");
        using var transaction = connection.BeginTransaction(deferred: false);
        using var create = connection.CreateCommand(); create.Transaction = transaction;
        create.CommandText = """
            CREATE TABLE IF NOT EXISTS native_projection(native_id TEXT PRIMARY KEY,json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS library_posts(id TEXT PRIMARY KEY,work_id TEXT NOT NULL,json TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS library_work ON library_posts(work_id);
            CREATE TABLE IF NOT EXISTS library_refs(platform TEXT,target TEXT,external_id TEXT,post_id TEXT NOT NULL,PRIMARY KEY(platform,target,external_id));
            CREATE TABLE IF NOT EXISTS library_metrics(id TEXT PRIMARY KEY,post_id TEXT NOT NULL,json TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS library_metric_post ON library_metrics(post_id);
            CREATE TABLE IF NOT EXISTS library_manual(work_id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS library_group_events(id TEXT PRIMARY KEY,at TEXT,kind TEXT,posts TEXT);
            CREATE TABLE IF NOT EXISTS legacy_rules_applied(id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS legacy_imports(id TEXT PRIMARY KEY,json TEXT NOT NULL);
            PRAGMA user_version=1;
            """;
        await create.ExecuteNonQueryAsync(); transaction.Commit();
    }
    internal static async Task<LibraryPost> UpsertLibraryAsync(SqliteConnection connection, SqliteTransaction transaction, LibraryPost incoming)
    {
        var matches = new HashSet<string>();
        foreach (var externalId in incoming.ExternalIds)
        {
            using var lookup = connection.CreateCommand(); lookup.Transaction = transaction;
            lookup.CommandText = "SELECT post_id FROM library_refs WHERE platform=$platform AND target=$target AND external_id=$external";
            lookup.Parameters.AddWithValue("$platform", incoming.Platform); lookup.Parameters.AddWithValue("$target", incoming.Target);
            lookup.Parameters.AddWithValue("$external", externalId);
            if (await lookup.ExecuteScalarAsync() is string id) matches.Add(id);
        }
        if (matches.Count > 1) throw new InvalidOperationException("Идентификаторы альбома относятся к разным записям. Импорт отменён для проверки данных.");
        var chosen = matches.SingleOrDefault() ?? incoming.Id.ToString();
        using var read = connection.CreateCommand(); read.Transaction = transaction;
        read.CommandText = "SELECT work_id,json FROM library_posts WHERE id=$id"; read.Parameters.AddWithValue("$id", chosen);
        LibraryPost? old = null;
        using (var reader = await read.ExecuteReaderAsync())
            if (await reader.ReadAsync()) old = JsonSerializer.Deserialize<LibraryPost>(reader.GetString(1))! with { WorkId = Guid.Parse(reader.GetString(0)) };
        var post = incoming with { Id = Guid.Parse(chosen) };
        if (old is not null)
        {
            post = post with { WorkId = old.WorkId, ExternalIds = old.ExternalIds.Concat(post.ExternalIds).Distinct().ToArray(),
                NativeId = post.NativeId ?? old.NativeId,
                OriginalJson = post.OriginalJson.Length > 0 ? post.OriginalJson : old.OriginalJson };
            if (incoming.NativeId is not null) post = post with { Excluded = old.Excluded, Source = old.Source };
            if (old.NativeId is not null && incoming.NativeId is null)
                post = post with { Title = old.Title, Text = old.Text, State = old.State, Published = old.Published };
        }
        using var save = connection.CreateCommand(); save.Transaction = transaction;
        save.CommandText = "INSERT INTO library_posts(id,work_id,json) VALUES($id,$work,$json) ON CONFLICT(id) DO UPDATE SET json=excluded.json";
        save.Parameters.AddWithValue("$id", post.Id.ToString()); save.Parameters.AddWithValue("$work", post.WorkId.ToString());
        save.Parameters.AddWithValue("$json", JsonSerializer.Serialize(post)); await save.ExecuteNonQueryAsync();
        foreach (var externalId in post.ExternalIds)
        {
            using var reference = connection.CreateCommand(); reference.Transaction = transaction;
            reference.CommandText = "INSERT OR IGNORE INTO library_refs(platform,target,external_id,post_id) VALUES($platform,$target,$external,$post)";
            reference.Parameters.AddWithValue("$platform", post.Platform); reference.Parameters.AddWithValue("$target", post.Target);
            reference.Parameters.AddWithValue("$external", externalId); reference.Parameters.AddWithValue("$post", post.Id.ToString());
            await reference.ExecuteNonQueryAsync();
        }
        return post;
    }
    internal static async Task AddMetricAsync(SqliteConnection connection, SqliteTransaction transaction, Guid postId,
        DateTimeOffset? observed, MetricValues values, string source)
    {
        if (!values.HasData) return;
        var id = Library.Key($"metric|{postId}|{observed:O}|{JsonSerializer.Serialize(values)}");
        var sample = new MetricSample(id, postId, observed, values, source, DateTimeOffset.UtcNow);
        using var command = connection.CreateCommand(); command.Transaction = transaction;
        command.CommandText = "INSERT OR IGNORE INTO library_metrics(id,post_id,json) VALUES($id,$post,$json)";
        command.Parameters.AddWithValue("$id", id.ToString()); command.Parameters.AddWithValue("$post", postId.ToString());
        command.Parameters.AddWithValue("$json", JsonSerializer.Serialize(sample)); await command.ExecuteNonQueryAsync();
    }
    private static async Task ProjectNativeAsync(SqliteConnection connection, SqliteTransaction transaction, Publication publication, string? sourceJson = null)
    {
        var ids = publication.Delivery.AllMediaMessageIds.Concat(publication.Delivery.EditMessageId is { } id ? [id] : Array.Empty<long>())
            .Distinct().Select(value => value.ToString(System.Globalization.CultureInfo.InvariantCulture)).ToArray();
        await UpsertLibraryAsync(connection, transaction, new(publication.Id, publication.Id,
            publication.Demo ? "demo.telegram" : "telegram", publication.Delivery.ChatId, ids,
            publication.Created, publication.EditText.Split('\n')[0], publication.EditText, publication.Delivery.State,
            publication.Delivery.Link ?? "", publication.Demo, publication.Id, "native"));
        using var marker = connection.CreateCommand(); marker.Transaction = transaction;
        marker.CommandText = "INSERT INTO native_projection(native_id,json) VALUES($id,$json) ON CONFLICT(native_id) DO UPDATE SET json=excluded.json";
        marker.Parameters.AddWithValue("$id", publication.Id.ToString()); marker.Parameters.AddWithValue("$json", sourceJson ?? JsonSerializer.Serialize(publication));
        await marker.ExecuteNonQueryAsync();
    }
    public async Task<LibraryData> LibraryAsync()
    {
        using var connection = await OpenAsync();
        // Only reconcile changed records from an older still-running Worker.
        var changed = new List<string>();
        using (var pending = connection.CreateCommand())
        {
            pending.CommandText = "SELECT p.id FROM publications p LEFT JOIN native_projection n ON n.native_id=p.id WHERE n.json IS NULL OR n.json!=p.json";
            using var reader = await pending.ExecuteReaderAsync();
            while (await reader.ReadAsync()) changed.Add(reader.GetString(0));
        }
        if (changed.Count > 0)
        {
            using var transaction = connection.BeginTransaction(deferred: false);
            foreach (var id in changed)
            {
                using var latest = connection.CreateCommand(); latest.Transaction = transaction;
                latest.CommandText = "SELECT json FROM publications WHERE id=$id"; latest.Parameters.AddWithValue("$id", id);
                // Re-read under the write lock so an older captured Sending record cannot overwrite Sent.
                if (await latest.ExecuteScalarAsync() is string json)
                    await ProjectNativeAsync(connection, transaction, JsonSerializer.Deserialize<Publication>(json)!, json);
            }
            transaction.Commit();
        }
        using var readTransaction = connection.BeginTransaction(deferred: true);
        using var posts = connection.CreateCommand(); posts.Transaction = readTransaction; posts.CommandText = "SELECT work_id,json FROM library_posts";
        var entries = new List<LibraryPost>();
        using (var reader = await posts.ExecuteReaderAsync())
            while (await reader.ReadAsync()) entries.Add(JsonSerializer.Deserialize<LibraryPost>(reader.GetString(1))! with { WorkId = Guid.Parse(reader.GetString(0)) });
        using var metrics = connection.CreateCommand(); metrics.Transaction = readTransaction; metrics.CommandText = "SELECT json FROM library_metrics";
        var samples = new List<MetricSample>();
        using (var reader = await metrics.ExecuteReaderAsync())
            while (await reader.ReadAsync()) samples.Add(JsonSerializer.Deserialize<MetricSample>(reader.GetString(0))!);
        using var manual = connection.CreateCommand(); manual.Transaction = readTransaction; manual.CommandText = "SELECT work_id FROM library_manual"; var groups = new HashSet<Guid>();
        using (var reader = await manual.ExecuteReaderAsync()) while (await reader.ReadAsync()) groups.Add(Guid.Parse(reader.GetString(0)));
        readTransaction.Commit(); return new(entries, samples, groups);
    }
    internal static async Task GroupPostsAsync(SqliteConnection connection, SqliteTransaction transaction, Guid[] ids, bool split)
    {
        if (ids.Distinct().Count() < 2) return;
        if (!split)
        {
            var expanded = new HashSet<Guid>(ids);
            foreach (var id in ids)
            {
                using var members = connection.CreateCommand(); members.Transaction = transaction;
                members.CommandText = "SELECT id FROM library_posts WHERE work_id=(SELECT work_id FROM library_posts WHERE id=$id)";
                members.Parameters.AddWithValue("$id", id.ToString()); using var reader = await members.ExecuteReaderAsync();
                while (await reader.ReadAsync()) expanded.Add(Guid.Parse(reader.GetString(0)));
            }
            ids = expanded.ToArray();
        }
        var target = Library.Key("manual|" + string.Join('|', ids.Order()));
        foreach (var id in ids.Distinct())
        {
            using var command = connection.CreateCommand(); command.Transaction = transaction;
            command.CommandText = "UPDATE library_posts SET work_id=$work WHERE id=$id";
            command.Parameters.AddWithValue("$work", (split ? Library.Key("split|" + id) : target).ToString());
            command.Parameters.AddWithValue("$id", id.ToString()); await command.ExecuteNonQueryAsync();
        }
        using var log = connection.CreateCommand(); log.Transaction = transaction;
        log.CommandText = "INSERT INTO library_group_events(id,at,kind,posts) VALUES($id,$at,$kind,$posts)";
        log.Parameters.AddWithValue("$id", Guid.NewGuid().ToString()); log.Parameters.AddWithValue("$at", DateTimeOffset.UtcNow.ToString("O"));
        log.Parameters.AddWithValue("$kind", split ? "split" : "join"); log.Parameters.AddWithValue("$posts", JsonSerializer.Serialize(ids)); await log.ExecuteNonQueryAsync();
        if (!split)
        {
            using var flag = connection.CreateCommand(); flag.Transaction = transaction;
            flag.CommandText = "INSERT OR IGNORE INTO library_manual(work_id) VALUES($work)"; flag.Parameters.AddWithValue("$work", target.ToString());
            await flag.ExecuteNonQueryAsync();
        }
    }
    public async Task GroupWorksAsync(Guid[] works, bool split)
    {
        var data = await LibraryAsync();
        var ids = data.Posts.Where(post => works.Contains(post.WorkId)).Select(post => post.Id).ToArray();
        if (ids.Length < 2) throw new InvalidOperationException("Выберите хотя бы две публикации.");
        using var connection = await OpenAsync(); using var transaction = connection.BeginTransaction(deferred: false);
        await GroupPostsAsync(connection, transaction, ids, split); transaction.Commit();
    }
}
