using System.Security.Cryptography;
using System.Text.Json;
using CrossPost.Core;
using Microsoft.Data.Sqlite;

internal static class ImportChecks
{
    private static void Check(bool value, string message) { if (!value) throw new Exception(message); }
    private static SqliteConnection Open(string path)
    {
        var connection = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = path, Pooling = false }.ToString());
        connection.Open(); return connection;
    }
    private static void Exec(SqliteConnection connection, string sql)
    { using var command = connection.CreateCommand(); command.CommandText = sql; command.ExecuteNonQuery(); }
    private static string Hash(string path) => Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path)));
    private static string Folder()
    { var path = Path.Combine(Path.GetTempPath(), "crosspost-import-" + Guid.NewGuid()); Directory.CreateDirectory(path); return path; }
    private static SqliteConnection Fixture(string path)
    {
        var connection = Open(path);
        Exec(connection, """
            PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;
            CREATE TABLE posts(id INTEGER PRIMARY KEY,platform TEXT,chat_id TEXT,msg_id INTEGER,msg_ids TEXT,date_utc TEXT,text TEXT,views INTEGER,reactions INTEGER,forwards INTEGER,replies INTEGER,updated_at TEXT,excluded INTEGER DEFAULT 0,is_forward INTEGER DEFAULT 0,link TEXT,custom_future TEXT);
            CREATE TABLE snapshots(post_id INTEGER,taken_at TEXT,views INTEGER,reactions INTEGER,forwards INTEGER,replies INTEGER);
            CREATE TABLE publications(id INTEGER PRIMARY KEY,created TEXT,title TEXT,text TEXT,dests TEXT,files TEXT,draft TEXT);
            CREATE TABLE cal_rules(a TEXT,b TEXT,kind TEXT);
            CREATE TABLE scheduled(id INTEGER PRIMARY KEY,run_at TEXT,draft TEXT,state TEXT);
            INSERT INTO posts(id,platform,chat_id,msg_id,msg_ids,date_utc,text,views,updated_at,custom_future) VALUES(1,'telegram','-100777',42,'42,43','2026-11-01T00:00:00Z','Old title',100,'2026-11-03T00:00:00Z','keep unknown fields');
            INSERT INTO snapshots(post_id,taken_at,views) VALUES(1,'2026-11-02T00:00:00Z',90),(1,'2026-11-04T00:00:00Z',80);
            INSERT INTO publications VALUES(1,'2026-11-01T00:00:00Z','Old title','Body','[{"key":"telegram:0","platform":"telegram","chat_id":"-100777","message_ids":[42,43],"state":"done"}]','[]','{}');
            INSERT INTO scheduled VALUES(1,'2027-01-01T00:00:00Z','{"files":["upload-id"]}','pending');
            CREATE TABLE future_table(id TEXT,payload BLOB); INSERT INTO future_table VALUES('future',X'0123ABCD');
            """);
        return connection;
    }
    public static async Task RunAsync(Func<string, Func<Task>, Task> run)
    {
        await run("Python import reads committed WAL, preserves unknown fields and does not change the source", async () =>
        {
            var folder = Folder();
            try
            {
                using var source = Fixture(Path.Combine(folder, "posts.db"));
                var before = Hash(Path.Combine(folder, "posts.db")); var wal = Hash(Path.Combine(folder, "posts.db-wal"));
                var store = new Store(Path.Combine(folder, "native")); await store.InitializeAsync();
                var report = await new LegacyImporter(store).ImportAsync(Path.Combine(folder, "posts.db"));
                Check(before == Hash(Path.Combine(folder, "posts.db")) && wal == Hash(Path.Combine(folder, "posts.db-wal")), "Source database/WAL changed");
                Check(File.Exists(report.Snapshot) && File.Exists(report.Backup) && report.ArchivedSchedules == 1, "Backup/archive missing");
                using var snapshot = Open(report.Snapshot); using var command = snapshot.CreateCommand();
                command.CommandText = "SELECT custom_future FROM posts";
                Check((string?)command.ExecuteScalar() == "keep unknown fields", "Unknown field lost");
                command.CommandText = "SELECT hex(payload) FROM future_table"; Check((string?)command.ExecuteScalar() == "0123ABCD", "Unknown table lost");
                var data = await store.LibraryAsync(); Check(data.Posts.Count == 1 && data.Posts[0].ExternalIds.Contains("43"), "Application history duplicated channel history or lost album IDs");
                Check(Library.Latest(data, data.Posts[0].Id)?.Values is { Views: 80, Reactions: null }, "Latest measurement replaced by maximum/zero");
                Check((await store.QueueAsync()).Count == 0, "Old scheduled job activated");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("repeated imports are idempotent and external identities survive moving the source", async () =>
        {
            var folder = Folder();
            try
            {
                var path = Path.Combine(folder, "posts.db"); using (var fixture = Fixture(path)) { }
                var store = new Store(Path.Combine(folder, "native")); await store.InitializeAsync();
                var importer = new LegacyImporter(store); await importer.ImportAsync(path);
                var repeated = await importer.ImportAsync(path); Check(repeated.Publications == 0 && repeated.Measurements == 0, "Repeat import duplicated records");
                var moved = Path.Combine(folder, "moved.db"); File.Copy(path, moved);
                Check((await importer.ImportAsync(moved)).Publications == 0, "Copied source duplicated external post identities");
                using (var correction = Open(path)) Exec(correction, "UPDATE snapshots SET views=60 WHERE taken_at='2026-11-04T00:00:00Z'");
                await importer.ImportAsync(path); var corrected = await store.LibraryAsync();
                Check(Library.Latest(corrected, corrected.Posts[0].Id)?.Values.Views == 60, "Correction at the same observation time was ignored");
                using (var update = Open(path)) Exec(update, "INSERT INTO snapshots(post_id,taken_at,views) VALUES(1,'2026-11-05T00:00:00Z',70)");
                await importer.ImportAsync(path); var data = await store.LibraryAsync();
                Check(data.Posts.Count == 1 && Library.Latest(data, data.Posts[0].Id)?.Values.Views == 70, "New smaller measurement ignored");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("matching numeric message IDs in different channels remain separate", async () =>
        {
            var folder = Folder();
            try
            {
                var path = Path.Combine(folder, "posts.db");
                using (var source = Fixture(path)) Exec(source, "INSERT INTO posts(id,platform,chat_id,msg_id,date_utc,text,views,updated_at) VALUES(2,'telegram','-100888',42,'2026-11-02T00:00:00Z','Other',30,'2026-11-04T00:00:00Z')");
                var store = new Store(Path.Combine(folder, "native")); await store.InitializeAsync(); await new LegacyImporter(store).ImportAsync(path);
                var data = await store.LibraryAsync(); Check(data.Posts.Count == 2 && Library.Totals(data).Length == 2, "Channel identity discarded");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("merge uses the newer publication per destination and split restores original statistics", async () =>
        {
            var folder = Folder();
            try
            {
                var path = Path.Combine(folder, "posts.db");
                using (var source = Fixture(path)) Exec(source, "INSERT INTO posts(id,platform,chat_id,msg_id,date_utc,text,views,reactions,updated_at) VALUES(2,'telegram','-100777',99,'2026-11-02T00:00:00Z','New',30,2,'2026-11-04T00:00:00Z')");
                var store = new Store(Path.Combine(folder, "native")); await store.InitializeAsync(); await new LegacyImporter(store).ImportAsync(path);
                var before = await store.LibraryAsync(); var metrics = before.Metrics.Count;
                await store.GroupWorksAsync(before.Posts.Select(post => post.WorkId).ToArray(), false);
                var joined = await store.LibraryAsync(); var total = Library.Totals(joined).Single();
                Check(joined.Posts.Select(post => post.WorkId).Distinct().Count() == 1 && total.Views == 30 && total.Publications == 1, "Old/max/summed publication statistics used after merge");
                await store.GroupWorksAsync([joined.Posts[0].WorkId], true); var split = await store.LibraryAsync();
                Check(split.Posts.Select(post => post.WorkId).Distinct().Count() == 2 && split.Metrics.Count == metrics && Library.Totals(split).Single().Views == 110, "Split lost original measurements");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("legacy transitive joins are imported once and do not override a later native split", async () =>
        {
            var folder = Folder();
            try
            {
                var path = Path.Combine(folder, "posts.db");
                using (var source = Fixture(path)) Exec(source, """
                    INSERT INTO posts(id,platform,chat_id,msg_id,date_utc,text) VALUES(2,'telegram','-100777',50,'2026-11-02T00:00:00Z','Two'),(3,'telegram','-100777',60,'2026-11-03T00:00:00Z','Three');
                    INSERT INTO cal_rules VALUES('p1','p2','join'),('p2','p3','join');
                    """);
                var store = new Store(Path.Combine(folder, "native")); await store.InitializeAsync(); var importer = new LegacyImporter(store);
                await importer.ImportAsync(path); var joined = await store.LibraryAsync(); Check(joined.Posts.Select(post => post.WorkId).Distinct().Count() == 1, "Transitive legacy grouping lost");
                await store.GroupWorksAsync([joined.Posts[0].WorkId], true); await importer.ImportAsync(path);
                Check((await store.LibraryAsync()).Posts.Select(post => post.WorkId).Distinct().Count() == 3, "Reimport overrode manual split");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("imported history links to a native post without overwriting its exact sent text", async () =>
        {
            var folder = Folder();
            try
            {
                var path = Path.Combine(folder, "posts.db"); using (var fixture = Fixture(path)) { }
                var store = new Store(Path.Combine(folder, "native")); await store.InitializeAsync();
                var publication = new Publication(Guid.NewGuid(), DateTimeOffset.UtcNow, new("Exact"), false, "123",
                    new(DeliveryState.Sent, "-100777", 42, 42, "caption", MediaMessageIds: [42, 43]), "Exact sent text with signature");
                await store.SavePublicationAsync(publication); await new LegacyImporter(store).ImportAsync(path);
                var data = await store.LibraryAsync(); Check(data.Posts.Count == 1 && data.Posts[0].NativeId == publication.Id && data.Posts[0].Text == publication.EditText, "Native identity/text lost on import");
                Check(data.Metrics.Count > 0 && data.Posts[0].OriginalJson.Contains("custom_future"), "Imported measurements/provenance lost");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("cancelling import rolls back all normalized changes", async () =>
        {
            var folder = Folder();
            try
            {
                var path = Path.Combine(folder, "posts.db"); using (var fixture = Fixture(path)) { }
                var store = new Store(Path.Combine(folder, "native")); await store.InitializeAsync(); using var cancellation = new CancellationTokenSource();
                var progress = new InlineProgress(text => { if (text.Contains("внешние")) cancellation.Cancel(); });
                var cancelled = false;
                try { await new LegacyImporter(store).ImportAsync(path, cancellation.Token, progress); } catch (OperationCanceledException) { cancelled = true; }
                Check(cancelled && (await store.LibraryAsync()).Posts.Count == 0 && (await store.QueueAsync()).Count == 0, "Cancelled import partially applied");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("schema migration backs up old native data and rejects newer schema versions", async () =>
        {
            var folder = Folder();
            try
            {
                var old = Path.Combine(folder, "old"); Directory.CreateDirectory(old);
                using (var database = Open(Path.Combine(old, "prototype.db"))) Exec(database, "CREATE TABLE documents(key TEXT PRIMARY KEY,json TEXT NOT NULL); INSERT INTO documents VALUES('draft','{\"Title\":\"Keep\"}')");
                var store = new Store(old); await store.InitializeAsync();
                Check((await store.ReadDocumentAsync<Draft>("draft"))?.Title == "Keep" && Directory.GetFiles(Path.Combine(old, "backups")).Length == 1, "Migration lost old data or backup");
                using (var database = Open(Path.Combine(old, "prototype.db"))) Exec(database, "PRAGMA user_version=2");
                var rejected = false; try { await store.InitializeAsync(); } catch (InvalidOperationException) { rejected = true; }
                Check(rejected, "Newer schema was modified by an old binary");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("malformed dates and JSON stay archived without fabricated timestamps or runnable jobs", async () =>
        {
            var folder = Folder();
            try
            {
                var path = Path.Combine(folder, "posts.db"); using (var source = Fixture(path)) Exec(source, "UPDATE posts SET date_utc='invalid'; UPDATE publications SET dests='invalid'");
                var store = new Store(Path.Combine(folder, "native")); await store.InitializeAsync();
                var report = await new LegacyImporter(store).ImportAsync(path); var data = await store.LibraryAsync();
                Check(data.Posts.Single().Published is null && report.Notes.Length >= 3 && File.Exists(report.Snapshot), "Malformed input hidden or assigned current time");
                Check((await store.QueueAsync()).Count == 0, "Import activated a job");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("VK mirror and VK history deduplicate while preserving reach and latest actual counters", async () =>
        {
            var folder = Folder();
            try
            {
                var path = Path.Combine(folder, "posts.db");
                using (var source = Fixture(path)) Exec(source, """
                    CREATE TABLE ext_posts(key TEXT PRIMARY KEY,platform TEXT,date INTEGER,text TEXT,link TEXT,views INTEGER,likes INTEGER,reposts INTEGER,replies INTEGER,updated_at INTEGER);
                    INSERT INTO ext_posts VALUES('vk:-77_12','vk',1793491200,'VK','https://vk.com/wall-77_12',100,3,1,2,1793664000);
                    CREATE TABLE vk_posts(key TEXT PRIMARY KEY,owner INTEGER,post INTEGER,date INTEGER,title TEXT,text TEXT,href TEXT);
                    INSERT INTO vk_posts VALUES('-77_12',-77,12,1793491200,'VK','body','https://vk.com/wall-77_12');
                    CREATE TABLE vk_snap(key TEXT,at INTEGER,views INTEGER,likes INTEGER,reposts INTEGER,comments INTEGER,reach INTEGER,bookmarks INTEGER);
                    INSERT INTO vk_snap VALUES('-77_12',1793750400,90,4,2,3,80,1);
                    """);
                var store = new Store(Path.Combine(folder, "native")); await store.InitializeAsync(); await new LegacyImporter(store).ImportAsync(path);
                var data = await store.LibraryAsync(); var vk = data.Posts.Single(post => post.Platform == "vk");
                Check(vk.Target == "-77" && Library.Latest(data, vk.Id)?.Values is { Views: 90, Reach: 80, Replies: 3 }, "VK identity or measurements lost");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("unknown source schemas are rejected without changing native history", async () =>
        {
            var folder = Folder();
            try
            {
                var store = new Store(Path.Combine(folder, "native")); await store.InitializeAsync();
                foreach (var (name, schema) in new[] { ("wrong-posts.db", "CREATE TABLE posts(other TEXT)"), ("wrong-publications.db", "CREATE TABLE publications(id TEXT,json TEXT)") })
                {
                    var path = Path.Combine(folder, name); using (var source = Open(path)) Exec(source, schema);
                    var rejected = false; try { await new LegacyImporter(store).ImportAsync(path); } catch (InvalidOperationException) { rejected = true; }
                    Check(rejected && (await store.LibraryAsync()).Posts.Count == 0, "Unknown schema was silently interpreted");
                }
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("external IDs above JavaScript integer precision remain exact strings", async () =>
        {
            var folder = Folder();
            try
            {
                var path = Path.Combine(folder, "posts.db");
                using (var source = Fixture(path)) Exec(source, "INSERT INTO posts(id,platform,chat_id,msg_id,msg_ids,date_utc,text) VALUES(2,'telegram','-100777',9007199254740993,'9007199254740993,9007199254740994','2026-11-02T00:00:00Z','Large IDs')");
                var store = new Store(Path.Combine(folder, "native")); await store.InitializeAsync(); await new LegacyImporter(store).ImportAsync(path);
                var data = await store.LibraryAsync(); var post = data.Posts.Single(value => value.Text == "Large IDs");
                Check(post.ExternalIds.Contains("9007199254740993") && post.ExternalIds.Contains("9007199254740994"), "External IDs rounded or truncated");
            }
            finally { Directory.Delete(folder, true); }
        });
    }
    private sealed class InlineProgress(Action<string> action) : IProgress<string> { public void Report(string value) => action(value); }
}
