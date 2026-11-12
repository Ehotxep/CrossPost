using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using CrossPost.Core;
using Microsoft.Data.Sqlite;

internal static class MigrationChecks
{
    private static void Check(bool result, string message) { if (!result) throw new Exception(message); }
    private static string Root() { var path = Path.Combine(Path.GetTempPath(), "crosspost-migration-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(path); return path; }
    private static async Task Fails(Func<Task> action)
    {
        try { await action(); }
        catch (Exception error) when (error is IOException or CryptographicException or OperationCanceledException or InvalidOperationException) { return; }
        throw new Exception("Unsafe migration was accepted");
    }
    // Test wrapper only. Production uses current-user Windows DPAPI, exercised by the WPF smoke test.
    private static byte[] Wrap(byte[] key) => key.ToArray();
    public static async Task RunAsync(Func<string, Func<Task>, Task> run)
    {
        await run("profile archive streams large/empty files; encrypted names and data round-trip without touching source", async () =>
        {
            var root = Root();
            try
            {
                var source = Path.Combine(root, "python"); Directory.CreateDirectory(Path.Combine(source, "unknown"));
                var secret = Encoding.UTF8.GetBytes("unknown-api-secret-for-migration");
                await File.WriteAllBytesAsync(Path.Combine(source, "publish.json"), secret);
                await File.WriteAllBytesAsync(Path.Combine(source, "unknown", "large.bin"), RandomNumberGenerator.GetBytes(2_200_000));
                await File.WriteAllTextAsync(Path.Combine(source, "empty"), "");
                var before = Directory.GetFiles(source, "*", SearchOption.AllDirectories).ToDictionary(p => p, p => Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(p))));
                var archivePath = Path.Combine(root, "archive");
                using (var archive = await EncryptedProfileArchive.CreateAsync(source, archivePath, Wrap)) Check(archive.Files.Length == 3, "Files missing");
                foreach (var file in Directory.GetFiles(archivePath)) Check(!Encoding.UTF8.GetString(await File.ReadAllBytesAsync(file)).Contains(Encoding.UTF8.GetString(secret)), "Plaintext secret leaked");
                Check(!Encoding.UTF8.GetString(await File.ReadAllBytesAsync(Path.Combine(archivePath, "manifest.bin"))).Contains("publish.json"), "Manifest paths leaked");
                using var reopened = await EncryptedProfileArchive.OpenAsync(archivePath, Wrap);
                Check(reopened.Source == source, "Stable source identity missing");
                var restored = Path.Combine(root, "recovered"); await reopened.RestoreAsync(restored);
                foreach (var (file, hash) in before)
                {
                    Check(Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(file))) == hash, "Source changed");
                    var restoredHash = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(Path.Combine(restored, Path.GetRelativePath(source, file)))));
                    Check(restoredHash == hash, "Restored data changed");
                }
                await Fails(() => reopened.RestoreAsync(restored));
            }
            finally { Directory.Delete(root, true); }
        });
        await run("profile archive rejects altered headers, ciphertext, swapped files and truncation; failed restore is removed", async () =>
        {
            var root = Root();
            try
            {
                var source = Path.Combine(root, "python"); Directory.CreateDirectory(source);
                await File.WriteAllTextAsync(Path.Combine(source, "a"), "secret-a"); await File.WriteAllTextAsync(Path.Combine(source, "b"), "secret-b");
                using var archive = await EncryptedProfileArchive.CreateAsync(source, Path.Combine(root, "archive"), Wrap);
                var path = Path.Combine(archive.Folder, archive.Files[0].Id + ".bin"); var original = await File.ReadAllBytesAsync(path);
                foreach (var modified in new[] { original[..^1], original.Select((b, i) => i == 9 ? (byte)(b ^ 1) : b).ToArray(), original.Select((b, i) => i == 0 ? (byte)0xff : b).ToArray(), await File.ReadAllBytesAsync(Path.Combine(archive.Folder, archive.Files[1].Id + ".bin")) })
                {
                    await File.WriteAllBytesAsync(path, modified);
                    await Fails(async () => { await archive.ReadAsync("a"); });
                    var destination = Path.Combine(root, "failed");
                    await Fails(() => archive.RestoreAsync(destination)); Check(!Directory.Exists(destination), "Partial plaintext restore left behind");
                }
            }
            finally { Directory.Delete(root, true); }
        });
        await run("profile import rejects overlap, symlinks, cancellation and a profile changed during capture", async () =>
        {
            var root = Root();
            try
            {
                var source = Path.Combine(root, "python"); Directory.CreateDirectory(source); await File.WriteAllTextAsync(Path.Combine(source, "ui.json"), "{}");
                await Fails(async () => { using var bad = await EncryptedProfileArchive.CreateAsync(source, Path.Combine(source, "archive"), Wrap); });
                using var cancellation = new CancellationTokenSource(); cancellation.Cancel();
                await Fails(async () => { using var bad = await EncryptedProfileArchive.CreateAsync(source, Path.Combine(root, "cancelled"), Wrap, cancellation.Token); });
                await Fails(async () => { using var bad = await EncryptedProfileArchive.CreateAsync(source, Path.Combine(root, "changed"), key => { File.WriteAllText(Path.Combine(source, "new-file"), "changed"); return key.ToArray(); }); });
                Check(!Directory.Exists(Path.Combine(root, "changed")), "Invalid archive retained");
                if (!OperatingSystem.IsWindows())
                {
                    File.CreateSymbolicLink(Path.Combine(source, "linked"), Path.Combine(source, "ui.json"));
                    await Fails(async () => { using var bad = await EncryptedProfileArchive.CreateAsync(source, Path.Combine(root, "linked-archive"), Wrap); });
                }
            }
            finally { Directory.Delete(root, true); }
        });
        await run("Python profile imports account choices, bilingual drafts, templates, ALT and owned media; schedules remain review-only", async () =>
        {
            var root = Root();
            try
            {
                var source = Path.Combine(root, "python"); Directory.CreateDirectory(Path.Combine(source, "uploads", "0123456789abcdef"));
                await File.WriteAllBytesAsync(Path.Combine(source, "uploads", "0123456789abcdef", "original.png"), [1, 2, 3]);
                await File.WriteAllTextAsync(Path.Combine(source, "publish.json"), """
                    {"modules":{"translation":true},"telegram":{"bot_token":"123:private-token","spoiler_nsfw":true,"channels":[{"id":"-1001","title":"One"},{"id":"-1002","title":"Two"}]},"discord":{"webhooks":[{"url":"private-webhook","name":"Art"}]},"bluesky":{"handle":"test.bsky.social"},"text_lang":{"discord":"en"},"platform_texts_enabled":{"telegram":true,"discord":true},"unknown":{"api_key":"unknown-secret"}}
                    """);
                const string draft = """{"title":"Русское название","text":"Текст","rating":"explicit","variants":{"en":{"title":"English","text":"Body"}},"platform_texts":{"telegram":{"enabled":true,"title":"Telegram","text":"Custom"},"discord":{"enabled":true,"title":"Custom English","text":"Custom body"}},"files":["0123456789abcdef","fedcba9876543210"],"alts":{"0123456789abcdef":"Accessible description"}}""";
                await File.WriteAllTextAsync(Path.Combine(source, "ui.json"), "{\"draft\":" + draft + "}");
                await File.WriteAllTextAsync(Path.Combine(source, "templates.json"), "{\"items\":[{\"name\":\"Template\",\"draft\":" + draft + "}]}");
                using (var db = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = Path.Combine(source, "posts.db"), Pooling = false }.ToString()))
                {
                    await db.OpenAsync(); using var command = db.CreateCommand();
                    command.CommandText = "CREATE TABLE saved_drafts(id INTEGER,name TEXT,draft TEXT); CREATE TABLE scheduled(id INTEGER,draft TEXT,state TEXT); INSERT INTO saved_drafts VALUES(1,'Saved',$draft); INSERT INTO scheduled VALUES(1,$draft,'pending');";
                    command.Parameters.AddWithValue("$draft", draft); await command.ExecuteNonQueryAsync();
                }
                using var archive = await EncryptedProfileArchive.CreateAsync(source, Path.Combine(root, "archive"), Wrap);
                var settings = await PythonProfile.SettingsAsync(archive);
                Check(settings.Channels.Length == 2 && settings.TelegramToken == "123:private-token" && settings.Telegram.Demo && settings.Discord.Demo && settings.Bluesky.Demo, "Settings lost or real mode enabled");
                Check(settings.Webhooks.Single() == "private-webhook" && settings.DiscordDestinations.Single().Name == "Art", "Webhook selection lost");
                var record = await PythonProfile.PrepareAsync(archive, source, Path.Combine(root, "media"));
                Check(record.Items.Length == 4 && record.Items.Single(i => i.Kind == "Шаблон").Draft.Title == "Русское название", "Draft/template import incomplete");
                var current = record.Items.Single(i => i.Key == "current");
                Check(current.Draft.Images.Length == 1 && File.Exists(current.Draft.Images[0]) && !current.Draft.Images[0].StartsWith(source), "Media not owned by native app");
                Check(current.Draft.AltFor(0) == "Accessible description" && current.Draft.EnglishTitle == "English" && current.Draft.ContentLabel == "explicit", "Text/ALT/rating lost");
                Check(current.Draft.ForPlatform("telegram").Title == "Telegram" && current.Draft.LanguageFor("discord") == "en" && current.Draft.ForPlatform("discord").Title == "Custom English", "Per-platform texts lost");
                Check(current.Warnings.Any(w => w.Contains("отсутствует")) && record.Items.Single(i => i.Kind == "Расписание").Warnings.Any(w => w.Contains("паузе")), "Review warnings missing");
                var json = JsonSerializer.Serialize(record); Check(!json.Contains("private-token") && !json.Contains("private-webhook") && !json.Contains("unknown-secret"), "Secrets leaked into SQLite document");
                var store = new Store(Path.Combine(root, "native")); await store.InitializeAsync(); await store.SaveDocumentAsync("python-migration", record);
                Check((await store.QueueAsync()).Count == 0 && (await store.HistoryAsync()).Count == 0, "Import activated scheduled posts");
                Check(!Directory.GetDirectories(archive.Folder, "reading-*").Any(), "Plaintext database staging retained");
            }
            finally { Directory.Delete(root, true); }
        });
        await run("migration settings transaction rolls back documents and credentials together", async () =>
        {
            var root = Root();
            try
            {
                var store = new Store(root); await store.InitializeAsync(); await store.SaveDocumentAsync("options", new AppOptions(Chat: "old"));
                var secret = "old-secret";
                await Fails(() => store.SaveMigrationSettingsAsync(new() { ["options"] = new AppOptions(Chat: "new") },
                    () => { secret = "new-secret"; throw new IOException("Interrupted vault write"); }, () => secret = "old-secret"));
                Check((await store.ReadDocumentAsync<AppOptions>("options"))?.Chat == "old" && secret == "old-secret", "Partial settings migration committed");
                await store.SaveMigrationSettingsAsync(new() { ["options"] = new AppOptions(Chat: "new") }, () => secret = "new-secret", () => secret = "old-secret");
                Check((await store.ReadDocumentAsync<AppOptions>("options")) is { Chat: "new", Demo: true } && secret == "new-secret", "Valid settings migration failed");
            }
            finally { Directory.Delete(root, true); }
        });
        await run("encrypted-profile history import is idempotent across temporary paths and retains no raw database or JSON", async () =>
        {
            var root = Root();
            try
            {
                var native = new Store(Path.Combine(root, "native")); await native.InitializeAsync();
                var source = Path.Combine(root, "source.db");
                using (var db = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = source, Pooling = false }.ToString()))
                {
                    await db.OpenAsync(); using var command = db.CreateCommand(); command.CommandText = """
                        CREATE TABLE posts(id INTEGER PRIMARY KEY,platform TEXT,chat_id TEXT,msg_id INTEGER,date_utc TEXT,text TEXT,private_extra TEXT);
                        INSERT INTO posts VALUES(1,'telegram','-100777',42,'2026-11-01T00:00:00Z','Title','should-stay-only-in-encrypted-copy');
                        """; await command.ExecuteNonQueryAsync();
                }
                for (var index = 0; index < 2; index++)
                {
                    var copy = Path.Combine(root, "staged-" + index + ".db"); File.Copy(source, copy);
                    var report = await new LegacyImporter(native).ImportAsync(copy, sourceIdentity: source, retainSource: false);
                    Check(report.Publications == (index == 0 ? 1 : 0) && report.Snapshot == "", "Stable identity or protected import report wrong");
                }
                var library = await native.LibraryAsync(); Check(library.Posts.Single().OriginalJson == "", "Raw source JSON leaked");
                Check(!Directory.GetDirectories(Path.Combine(native.Folder, "imports")).Any(), "Raw snapshot retained");
            }
            finally { Directory.Delete(root, true); }
        });
    }
}
