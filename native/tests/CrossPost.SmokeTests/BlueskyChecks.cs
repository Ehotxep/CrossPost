using System.Net;
using System.Text;
using System.Text.Json;
using CrossPost.Core;

internal static class BlueskyChecks
{
    private const string Did = "did:plc:aaaaaaaaaaaaaaaaaaaaaaaa";
    private const string Other = "did:plc:bbbbbbbbbbbbbbbbbbbbbbbb";
    private const string Password = "test-app-password-not-real";
    private const string Jwt = "testAccessJwtNotReal";
    private const string Pds = "https://test.us-east.host.bsky.network";
    private static HttpResponseMessage Json(object value, HttpStatusCode status = HttpStatusCode.OK) => new(status) { Content = new StringContent(JsonSerializer.Serialize(value)) };
    private static HttpResponseMessage Session(string did = Did, string home = Pds) => Json(new { did, handle = "test.bsky.social", accessJwt = Jwt,
        refreshJwt = "notStored", didDoc = new { service = new[] { new { id = did + "#atproto_pds", serviceEndpoint = home } } } });
    private static HttpResponseMessage Blob(string cid = "bafkimage", long size = 4) => Json(new { blob = new Dictionary<string, object>
    { ["$type"] = "blob", ["ref"] = new Dictionary<string, string> { ["$link"] = cid }, ["mimeType"] = "image/png", ["size"] = size } });
    private static async Task<HttpResponseMessage> Confirm(HttpRequestMessage request, CancellationToken ct)
    {
        using var body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(ct));
        return Json(new { uri = $"at://{Did}/app.bsky.feed.post/" + body.RootElement.GetProperty("rkey").GetString(), cid = "bafyconfirmed" });
    }
    private static void Check(bool value, string message) { if (!value) throw new Exception(message); }
    private static string Folder() { var path = Path.Combine(Path.GetTempPath(), "crosspost-bsky-" + Guid.NewGuid()); Directory.CreateDirectory(path); return path; }
    private static async Task<string[]> Images(string folder, int count)
    {
        var files = Enumerable.Range(0, count).Select(index => Path.Combine(folder, index + ".png")).ToArray();
        foreach (var file in files) await File.WriteAllTextAsync(file, "test"); return files;
    }
    public static async Task RunAsync(Func<string, Func<Task>, Task> run)
    {
        await run("schema 2 upgrade preserves Discord queue and legacy drafts with a backup", async () =>
        {
            var folder = Folder();
            try
            {
                var store = new Store(folder); await store.InitializeAsync();
                var job = await new QueuePlanner(store).AddAsync(new("Existing Discord"), new(Platform: "discord"), "demo", DateTimeOffset.UtcNow.AddDays(1));
                await store.SaveDocumentAsync("draft", new Draft("Legacy title"));
                using (var database = new Microsoft.Data.Sqlite.SqliteConnection(new Microsoft.Data.Sqlite.SqliteConnectionStringBuilder { DataSource = Path.Combine(folder, "prototype.db"), Pooling = false }.ToString()))
                {
                    await database.OpenAsync(); using var command = database.CreateCommand(); command.CommandText = "PRAGMA user_version=2"; await command.ExecuteNonQueryAsync();
                }
                await store.InitializeAsync();
                var saved = (await store.QueueAsync()).Single(); var draft = await store.ReadDocumentAsync<Draft>("draft");
                Check(saved.Id == job.Id && saved.Options.Platform == "discord" && saved.State == QueueState.Pending && draft?.Title == "Legacy title", "Schema upgrade changed existing queue/draft");
                Check(Directory.GetFiles(Path.Combine(folder, "backups"), "*.db").Length == 1, "Schema 2 upgrade did not retain backup");
                var legacy = JsonSerializer.Deserialize<Draft>("""{"Title":"Legacy","Text":"","AltTexts":[null]}""")!;
                Check(legacy.ContentLabel == "sfw" && legacy.AltFor(0) == "", "Legacy/default ALT compatibility failed");
                legacy.Validate("bluesky");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("Bluesky counts graphemes and rejects overlong text without truncation", () =>
        {
            var emoji = string.Concat(Enumerable.Repeat("👍🏽", 300));
            Check(PublishLimits.Count(emoji, "bluesky") == 300 && emoji.Length > 300, "UTF-16 code units counted as characters");
            new Draft(Text: emoji).Validate("bluesky"); var rejected = false;
            try { new Draft(Text: emoji + "x").Validate("bluesky"); } catch (InvalidOperationException) { rejected = true; }
            Check(rejected, "Overlong text accepted");
            rejected = false; try { new Draft(Text: string.Concat(Enumerable.Repeat("👨‍👩‍👧‍👦", 300))).Validate("bluesky"); } catch (InvalidOperationException) { rejected = true; }
            Check(rejected, "UTF-8 byte limit ignored"); return Task.CompletedTask;
        });
        await run("Bluesky facets use UTF-8 byte offsets for links and Unicode hashtags", () =>
        {
            var rich = BlueskyText.Build("Кот 🐈 [сайт](https://example.com) #арт"); var bytes = Encoding.UTF8.GetBytes(rich.Text);
            Check(!rich.Text.Contains("https://") && rich.Text.Contains("сайт"), "Markdown link not converted");
            using var facets = JsonDocument.Parse(JsonSerializer.Serialize(rich.Facets)); var slices = new List<string>();
            foreach (var facet in facets.RootElement.EnumerateArray())
            {
                var index = facet.GetProperty("index"); var start = index.GetProperty("byteStart").GetInt32(); var end = index.GetProperty("byteEnd").GetInt32();
                slices.Add(Encoding.UTF8.GetString(bytes.AsSpan(start, end - start)));
            }
            Check(slices.Contains("сайт") && slices.Contains("#арт"), "Unicode byte spans are wrong");
            Check(BlueskyText.Build("https://example.com/#tag").Facets.Length == 1, "URL fragment also became a hashtag");
            return Task.CompletedTask;
        });
        await run("Bluesky account inspection authenticates without creating a post", async () =>
        {
            var handler = new ScriptedHandler(async (request, ct) =>
            {
                Check(request.RequestUri!.Host == "bsky.social" && request.RequestUri.AbsolutePath.EndsWith("createSession") && request.Headers.Authorization is null, "Wrong auth destination");
                using var body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(ct));
                Check(body.RootElement.GetProperty("identifier").GetString() == "test.bsky.social", "Handle normalization failed"); return Session();
            });
            using var http = new HttpClient(handler); var info = await new BlueskyClient(http, "@test", Password, "").InspectAsync(CancellationToken.None);
            Check(info.Did == Did && info.Pds == Pds && handler.Calls == 1, "Inspection mutated or lost identity");
        });
        await run("Bluesky refuses unexpected PDS and changed account before upload or createRecord", async () =>
        {
            foreach (var (did, home) in new[] { (Other, Pds), (Did, "https://evil.test"), (Did, "https://test.host.bsky.network.evil.test"), (Did, "http://test.host.bsky.network") })
            {
                var handler = new ScriptedHandler((_, _) => Task.FromResult(Session(did, home))); using var http = new HttpClient(handler);
                var sent = await new BlueskyClient(http, "test", Password, Did).SendAsync(Did, new("Title"), CancellationToken.None);
                Check(sent.State == DeliveryState.Refused && handler.Calls == 1, "Account or token destination changed silently");
            }
        });
        await run("Bluesky checkpoints record URI before creation and stores the exact transformed text", async () =>
        {
            var folder = Folder();
            try
            {
                var store = new Store(folder); await store.InitializeAsync();
                var handler = new ScriptedHandler((_, _) => Task.FromResult(Session()), async (request, ct) =>
                {
                    Check(request.RequestUri!.Host == "test.us-east.host.bsky.network" && request.Headers.Authorization?.Parameter == Jwt, "Bearer sent to wrong PDS");
                    using var body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(ct));
                    var pending = (await store.HistoryAsync()).Single();
                    Check(pending.Delivery.State == DeliveryState.Sending && pending.Delivery.RemoteMessageId!.EndsWith(body.RootElement.GetProperty("rkey").GetString()!), "URI not persisted before mutation");
                    Check(body.RootElement.GetProperty("record").GetProperty("text").GetString() == "Сайт", "Wrong rendered text"); return await Confirm(request, ct);
                });
                using var http = new HttpClient(handler);
                var sent = await new Workflow(store).SendAsync(new BlueskyClient(http, "test", Password, Did), Did,
                    new(false, Did, "bluesky"), new(Text: "[Сайт](https://example.com)"), CancellationToken.None);
                Check(sent.EditText == "Сайт" && sent.Delivery is { State: DeliveryState.Sent, ContentId: "bafyconfirmed" }, "Confirmed text/CID not retained");
                var data = await store.LibraryAsync(); Check(data.Posts.Single().Platform == "bluesky" && data.Posts.Single().ExternalIds.Contains(sent.Delivery.RemoteMessageId!), "AT URI identity lost");
                var persisted = JsonSerializer.Serialize(sent); Check(!persisted.Contains(Password) && !persisted.Contains(Jwt), "Credential stored in history");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("Bluesky uploads four-or-fewer images, preserving ALT order and explicit content label", async () =>
        {
            var folder = Folder();
            try
            {
                var files = await Images(folder, 2); var uploads = 0;
                Task<HttpResponseMessage> Upload(HttpRequestMessage request, CancellationToken _)
                { Check(request.RequestUri!.AbsolutePath.EndsWith("uploadBlob") && request.Content!.Headers.ContentType!.MediaType == "image/png", "Wrong upload type"); return Task.FromResult(Blob("bafkimage" + uploads++)); }
                var handler = new ScriptedHandler((_, _) => Task.FromResult(Session()), Upload, Upload, async (request, ct) =>
                {
                    using var body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(ct)); var record = body.RootElement.GetProperty("record");
                    var images = record.GetProperty("embed").GetProperty("images");
                    Check(images[0].GetProperty("alt").GetString() == "Первое" && images[1].GetProperty("alt").GetString() == "Second", "ALT order lost");
                    Check(record.GetProperty("labels").GetProperty("values")[0].GetProperty("val").GetString() == "porn", "Explicit self-label missing");
                    return await Confirm(request, ct);
                });
                using var http = new HttpClient(handler);
                var sent = await new BlueskyClient(http, "test", Password, Did, "explicit").SendAsync(Did,
                    new("Title", ImagePath: files[0], AdditionalImagePaths: files[1..], AltTexts: ["Первое", "Second"]), CancellationToken.None);
                Check(sent.State == DeliveryState.Sent && sent.AttachmentIds?.Length == 2 && handler.Calls == 4, "Images/confirmation lost or retried");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("failed Bluesky upload never proceeds to createRecord and never retries forever", async () =>
        {
            var folder = Folder();
            try
            {
                var files = await Images(folder, 1);
                var handler = new ScriptedHandler((_, _) => Task.FromResult(Session()), (_, _) => throw new HttpRequestException(Password + Jwt)); using var http = new HttpClient(handler);
                var sent = await new BlueskyClient(http, "test", Password, Did).SendAsync(Did, new(ImagePath: files[0]), CancellationToken.None);
                Check(sent.State == DeliveryState.Refused && handler.Calls == 2 && !sent.Error.Contains(Password), "Upload failure became a post/retry or leaked credentials");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("Bluesky unknown creation preserves its chosen URI across restart without a second POST", async () =>
        {
            var folder = Folder();
            try
            {
                var store = new Store(folder); await store.InitializeAsync();
                var handler = new ScriptedHandler((_, _) => Task.FromResult(Session()), (_, _) => throw new HttpRequestException(Jwt)); using var http = new HttpClient(handler);
                var sent = await new Workflow(store).SendAsync(new BlueskyClient(http, "test", Password, Did), Did, new(false, Did, "bluesky"), new("Title"), CancellationToken.None);
                await new Store(folder).InitializeAsync(); var saved = (await store.HistoryAsync()).Single();
                Check(saved.Delivery.State == DeliveryState.Unknown && saved.Delivery.RemoteMessageId!.StartsWith("at://" + Did) && saved.Delivery.Link!.Contains("bsky.app") && handler.Calls == 2, "Unknown URI lost or create repeated");
                Check(!saved.Delivery.Error.Contains(Jwt), "Bearer leaked");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("Bluesky explicit refusal differs from transient or malformed record confirmation", async () =>
        {
            foreach (var (status, payload, state) in new[]
            {
                (400, new { error = "InvalidRecord" }, DeliveryState.Refused), (401, new { error = "ExpiredToken" }, DeliveryState.Refused),
                (429, new { error = "RateLimitExceeded" }, DeliveryState.Refused), (503, new { error = "ServiceUnavailable" }, DeliveryState.Unknown),
                (400, new { error = "UpstreamFailure" }, DeliveryState.Unknown), (200, new { error = "malformed" }, DeliveryState.Unknown)
            })
            {
                var handler = new ScriptedHandler((_, _) => Task.FromResult(Session()), (_, _) => Task.FromResult(Json(payload, (HttpStatusCode)status))); using var http = new HttpClient(handler);
                var sent = await new BlueskyClient(http, "test", Password, Did).SendAsync(Did, new("Title"), CancellationToken.None);
                Check(sent.State == state && handler.Calls == 2, "Outcome classification or no-retry contract failed");
            }
        });
        await run("Bluesky media limit and image count fail before login", async () =>
        {
            var folder = Folder();
            try
            {
                var files = await Images(folder, 5); var handler = new ScriptedHandler(); using var http = new HttpClient(handler);
                var refused = false; try { await new BlueskyClient(http, "test", Password, Did).SendAsync(Did, new(ImagePath: files[0], AdditionalImagePaths: files[1..]), CancellationToken.None); } catch (InvalidOperationException) { refused = true; }
                Check(refused && handler.Calls == 0, "Too many images reached login");
                using (var file = File.OpenWrite(files[0])) file.SetLength(PublishLimits.BlueskyImage + 1);
                new Draft(ImagePath: files[0]).Validate("bluesky", true); refused = false;
                try { new Draft(ImagePath: files[0]).Validate("bluesky"); } catch (InvalidOperationException) { refused = true; }
                Check(refused, "Oversized original skipped explicit preparation");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("Bluesky checkpoint failure prevents public post creation", async () =>
        {
            var handler = new ScriptedHandler((_, _) => Task.FromResult(Session())); using var http = new HttpClient(handler); var stopped = false;
            try { await new BlueskyClient(http, "test", Password, Did).SendAsync(Did, new("Title"), CancellationToken.None, _ => throw new ApplicationException("disk unavailable")); }
            catch (ApplicationException) { stopped = true; }
            Check(stopped && handler.Calls == 1, "Post created without durable record URI");
        });
        await run("Bluesky queue snapshots ALT and rating, and unsupported edits make no requests", async () =>
        {
            var folder = Folder();
            try
            {
                var store = new Store(folder); await store.InitializeAsync(); var paths = await Images(folder, 1); var now = DateTimeOffset.UtcNow;
                var job = await new QueuePlanner(store).AddAsync(new("Demo", ImagePath: paths[0], AltTexts: ["Описание"], ContentLabel: "erotic"),
                    new(true, DemoBluesky.Did, "bluesky", ContentLabel: "erotic"), "demo", now);
                await new QueueRunner(store).RunNextAsync(now, value => { Check(value.Options.ContentLabel == "erotic" && value.Draft.AltFor(0) == "Описание", "Snapshot metadata lost"); return Task.FromResult<IPublisher>(new DemoBluesky()); }, CancellationToken.None);
                var post = (await store.HistoryAsync()).Single(); var handler = new ScriptedHandler(); using var http = new HttpClient(handler);
                var edit = await new BlueskyClient(http, "test", Password, Did).EditAsync(post, "Changed", CancellationToken.None);
                Check(post.Platform == "bluesky" && !edit.Confirmed && !edit.Unknown && handler.Calls == 0, "Bluesky edit attempted replacement/deletion");
            }
            finally { Directory.Delete(folder, true); }
        });
    }
}
