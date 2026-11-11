using System.Net;
using System.Text;
using System.Text.Json;
using CrossPost.Core;

var failures = 0;
const string token = "123:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
static void Check(bool condition, string message) { if (!condition) throw new Exception(message); }
async Task Run(string name, Func<Task> test)
{
    try { await test(); Console.WriteLine("PASS " + name); }
    catch (Exception error) { failures++; Console.Error.WriteLine("FAIL " + name + ": " + error); }
}
static HttpResponseMessage Message(long id) => new(HttpStatusCode.OK)
{
    Content = new StringContent(JsonSerializer.Serialize(new { ok = true, result = new { message_id = id, chat = new { id = -100777L, username = "test_channel" } } }))
};
static HttpResponseMessage Refused() => new(HttpStatusCode.Forbidden)
{ Content = new StringContent("""{"ok":false,"description":"Forbidden: bot cannot post"}""") };
static string TemporaryFolder()
{
    var path = Path.Combine(Path.GetTempPath(), "crosspost-native-" + Guid.NewGuid());
    Directory.CreateDirectory(path); return path;
}
await Run("draft and history survive restart; interrupted send becomes unknown", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var store = new Store(folder); await store.InitializeAsync();
        var draft = new Draft("Русский / English", "Текст & <tags>");
        await store.SaveDocumentAsync("draft", draft);
        var publication = new Publication(Guid.NewGuid(), DateTimeOffset.UtcNow, draft, true, "demo", new(DeliveryState.Sending, "demo"), draft.PlainText);
        await store.SavePublicationAsync(publication);
        var reopened = new Store(folder); await reopened.InitializeAsync();
        Check(await reopened.ReadDocumentAsync<Draft>("draft") == draft, "Draft changed on restart");
        Check((await reopened.HistoryAsync()).Single().Delivery.State == DeliveryState.Unknown, "Interrupted submission lost");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("demo makes no network requests and supports edits", async () =>
{
    var demo = new DemoTelegram(); var draft = new Draft("Demo", "Body");
    var delivery = await demo.SendAsync("ignored", draft, CancellationToken.None);
    Check(delivery.State == DeliveryState.Sent && delivery.ChatId == "demo-channel", "Wrong demo destination");
    var publication = new Publication(Guid.NewGuid(), DateTimeOffset.UtcNow, draft, true, "demo", delivery, draft.PlainText);
    Check((await demo.EditAsync(publication, "Edited", CancellationToken.None)).Confirmed, "Demo edit failed");
});
await Run("text is HTML escaped and confirmed message ID is recorded", async () =>
{
    var handler = new ScriptedHandler(async (request, ct) =>
    {
        Check(request.RequestUri!.AbsolutePath.EndsWith("/sendMessage"), "Wrong method");
        var body = WebUtility.UrlDecode(await request.Content!.ReadAsStringAsync(ct));
        Check(body.Contains("&lt;b&gt;") && body.Contains("parse_mode=HTML"), "Unescaped input");
        return Message(42);
    });
    using var http = new HttpClient(handler);
    var delivery = await new TelegramClient(http, token).SendAsync("@test_channel", new Draft("<b>", "Body"), CancellationToken.None);
    Check(delivery.State == DeliveryState.Sent && delivery.EditMessageId == 42 && delivery.ChatId == "-100777", "Confirmation lost");
    Check(handler.Calls == 1, "Unexpected retry");
});
await Run("long photo caption retains separate editable text ID", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var image = Path.Combine(folder, "test.png"); await File.WriteAllTextAsync(image, "test");
        var handler = new ScriptedHandler(
            (request, ct) => { Check(request.RequestUri!.AbsolutePath.EndsWith("/sendMessage"), "Text must be first"); return Task.FromResult(Message(51)); },
            (request, ct) => { Check(request.RequestUri!.AbsolutePath.EndsWith("/sendPhoto"), "Photo must be second"); return Task.FromResult(Message(52)); });
        using var http = new HttpClient(handler);
        var delivery = await new TelegramClient(http, token).SendAsync("@test_channel", new Draft("", new string('я', 1100), image), CancellationToken.None);
        Check(delivery.State == DeliveryState.Sent && delivery.EditMessageId == 51 && delivery.MediaMessageId == 52 && delivery.Kind == "text", "Wrong editable message");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("photo refusal after confirmed text is partial, not total refusal", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var image = Path.Combine(folder, "test.png"); await File.WriteAllTextAsync(image, "test");
        var handler = new ScriptedHandler((_, _) => Task.FromResult(Message(60)), (_, _) => Task.FromResult(Refused()));
        using var http = new HttpClient(handler);
        var delivery = await new TelegramClient(http, token).SendAsync("@test_channel", new Draft("", new string('a', 1100), image), CancellationToken.None);
        Check(delivery.State == DeliveryState.Partial && delivery.EditMessageId == 60, "Partial success was lost");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("network failure is unknown and is never retried", async () =>
{
    var handler = new ScriptedHandler((_, _) => throw new HttpRequestException("connection lost"));
    using var http = new HttpClient(handler);
    var delivery = await new TelegramClient(http, token).SendAsync("@test_channel", new Draft("Title"), CancellationToken.None);
    Check(delivery.State == DeliveryState.Unknown && handler.Calls == 1, "Ambiguous request retried");
    Check(!delivery.Error.Contains(token), "Token leaked");
});
await Run("explicit Telegram refusal is recorded as refused", async () =>
{
    using var http = new HttpClient(new ScriptedHandler((_, _) => Task.FromResult(Refused())));
    var delivery = await new TelegramClient(http, token).SendAsync("@test_channel", new Draft("Title"), CancellationToken.None);
    Check(delivery.State == DeliveryState.Refused, "Explicit refusal classified incorrectly");
});
await Run("editing targets recorded caption and prevents switching bots", async () =>
{
    var publication = new Publication(Guid.NewGuid(), DateTimeOffset.UtcNow, new Draft("Old"), false, "123",
        new(DeliveryState.Sent, "-100777", 77, 77, "caption"), "Old");
    var handler = new ScriptedHandler(async (request, ct) =>
    {
        var body = WebUtility.UrlDecode(await request.Content!.ReadAsStringAsync(ct));
        Check(request.RequestUri!.AbsolutePath.EndsWith("/editMessageCaption") && body.Contains("message_id=77") && body.Contains("caption=New"), "Wrong edit target");
        return Message(77);
    });
    using var http = new HttpClient(handler);
    var client = new TelegramClient(http, token);
    Check((await client.EditAsync(publication, "New", CancellationToken.None)).Confirmed, "Edit failed");
    var blocked = false;
    try { await client.EditAsync(publication with { BotId = "999" }, "New", CancellationToken.None); }
    catch (InvalidOperationException) { blocked = true; }
    Check(blocked && handler.Calls == 1, "Wrong bot was allowed");
});
await Run("failed edits preserve recorded text and successful edit updates history", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var store = new Store(folder); await store.InitializeAsync();
        var publication = new Publication(Guid.NewGuid(), DateTimeOffset.UtcNow, new Draft("Old"), false, "123",
            new(DeliveryState.Sent, "-100777", 80, Kind: "text"), "Old");
        await store.SavePublicationAsync(publication);
        var handler = new ScriptedHandler((_, _) => Task.FromResult(Refused()), (_, _) => Task.FromResult(Message(80)));
        using var http = new HttpClient(handler);
        var workflow = new Workflow(store); var client = new TelegramClient(http, token);
        var failed = await workflow.EditAsync(client, publication, "New", CancellationToken.None);
        Check(!failed.Result.Confirmed && (await store.HistoryAsync()).Single().EditText == "Old", "Refused edit changed text");
        var edited = await workflow.EditAsync(client, failed.Publication, "New", CancellationToken.None);
        Check(edited.Result.Confirmed && (await store.HistoryAsync()).Single().EditText == "New", "Successful edit not stored");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("workflow stores uncertain delivery for recovery", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var store = new Store(folder); await store.InitializeAsync();
        var handler = new ScriptedHandler((_, _) => throw new HttpRequestException());
        using var http = new HttpClient(handler);
        await new Workflow(store).SendAsync(new TelegramClient(http, token), "123", new(false, "@test_channel"), new Draft("Test"), CancellationToken.None);
        Check((await store.HistoryAsync()).Single().Delivery.State == DeliveryState.Unknown, "Uncertain result not persisted");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("album multipart keeps image order, one escaped caption and every confirmed message ID", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var paths = await MakeImages(folder, 3);
        var handler = new ScriptedHandler(async (request, ct) =>
        {
            Check(request.RequestUri!.AbsolutePath.EndsWith("/sendMediaGroup"), "Album must use sendMediaGroup");
            var parts = ((MultipartFormDataContent)request.Content!).ToDictionary(part => part.Headers.ContentDisposition!.Name!.Trim('"'));
            using var json = JsonDocument.Parse(await parts["media"].ReadAsStringAsync(ct));
            var items = json.RootElement.EnumerateArray().ToArray();
            Check(items.Length == 3 && items[0].GetProperty("caption").GetString() == "&lt;Album&gt;", "Caption is missing or unsafe");
            for (var index = 0; index < items.Length; index++)
            {
                Check(items[index].GetProperty("media").GetString() == "attach://photo" + index, "Wrong attachment order");
                Check(await parts["photo" + index].ReadAsStringAsync(ct) == "image " + index, "Wrong image data");
                if (index > 0) Check(!items[index].TryGetProperty("caption", out _), "Caption duplicated on every image");
            }
            return Album(100, 101, 102);
        });
        using var http = new HttpClient(handler);
        var result = await new TelegramClient(http, token).SendAsync("@test_channel", new("<Album>", ImagePath: paths[0], AdditionalImagePaths: paths[1..]), CancellationToken.None);
        Check(result.State == DeliveryState.Sent && result.AllMediaMessageIds.SequenceEqual(new long[] { 100, 101, 102 }), "Album IDs lost");
        Check(result.EditMessageId == 100 && result.Kind == "caption" && handler.Calls == 1, "Wrong album edit target");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("confirmed text is on disk before album request and survives a lost response", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var paths = await MakeImages(folder, 2);
        var store = new Store(folder); await store.InitializeAsync();
        var handler = new ScriptedHandler((_, _) => Task.FromResult(Message(150)), async (_, _) =>
        {
            var checkpoint = (await store.HistoryAsync()).Single();
            Check(checkpoint.Delivery.State == DeliveryState.Sending && checkpoint.Delivery.EditMessageId == 150, "Text was not durably checkpointed");
            throw new HttpRequestException("response lost");
        });
        using var http = new HttpClient(handler);
        var draft = new Draft(Text: new string('a', 1100), ImagePath: paths[0], AdditionalImagePaths: paths[1..]);
        await new Workflow(store).SendAsync(new TelegramClient(http, token), "123", new(false, "@test_channel"), draft, CancellationToken.None);
        var reopened = new Store(folder); await reopened.InitializeAsync();
        var saved = (await reopened.HistoryAsync()).Single();
        Check(saved.Delivery.State == DeliveryState.Unknown && saved.Delivery.EditMessageId == 150, "Confirmed text lost on failure/restart");
        Check(handler.Calls == 2 && saved.Draft.SameContent(draft), "Unexpected retry or album order changed");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("album with long text edits the separate message and persists all media IDs", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var paths = await MakeImages(folder, 2);
        var store = new Store(folder); await store.InitializeAsync();
        var handler = new ScriptedHandler((_, _) => Task.FromResult(Message(200)), async (request, ct) =>
        {
            var parts = ((MultipartFormDataContent)request.Content!).ToDictionary(part => part.Headers.ContentDisposition!.Name!.Trim('"'));
            using var json = JsonDocument.Parse(await parts["media"].ReadAsStringAsync(ct));
            Check(json.RootElement.EnumerateArray().All(item => !item.TryGetProperty("caption", out _)), "Long text duplicated into album captions");
            return Album(201, 202);
        }, async (request, ct) =>
        {
            var body = WebUtility.UrlDecode(await request.Content!.ReadAsStringAsync(ct));
            Check(request.RequestUri!.AbsolutePath.EndsWith("/editMessageText") && body.Contains("message_id=200"), "Editing targeted a photo");
            return Message(200);
        });
        using var http = new HttpClient(handler); var client = new TelegramClient(http, token); var workflow = new Workflow(store);
        var sent = await workflow.SendAsync(client, "123", new(false, "@test_channel"), new(Text: new string('a', 1100), ImagePath: paths[0], AdditionalImagePaths: paths[1..]), CancellationToken.None);
        Check(sent.Delivery.EditMessageId == 200 && sent.Delivery.AllMediaMessageIds.SequenceEqual(new long[] { 201, 202 }), "Wrong message identities");
        var updated = await workflow.EditAsync(client, sent, "Edited", CancellationToken.None);
        Check(updated.Result.Confirmed && (await store.HistoryAsync()).Single().Delivery.AllMediaMessageIds.SequenceEqual(new long[] { 201, 202 }), "Edit lost album IDs");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("an incomplete album confirmation is unknown and is not retried", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var paths = await MakeImages(folder, 2);
        var handler = new ScriptedHandler((_, _) => Task.FromResult(Album(300)));
        using var http = new HttpClient(handler);
        var result = await new TelegramClient(http, token).SendAsync("@test_channel", new("Album", ImagePath: paths[0], AdditionalImagePaths: paths[1..]), CancellationToken.None);
        Check(result.State == DeliveryState.Unknown && handler.Calls == 1 && result.AllMediaMessageIds.SequenceEqual(new long[] { 300 }), "Incomplete album was treated as confirmed/retried or returned ID lost");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("invalid image counts and duplicates fail before network", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var paths = await MakeImages(folder, 11);
        var handler = new ScriptedHandler(); using var http = new HttpClient(handler);
        foreach (var extra in new[] { paths[1..], new[] { paths[0] } })
        {
            var rejected = false;
            try { await new TelegramClient(http, token).SendAsync("@test_channel", new("Album", ImagePath: paths[0], AdditionalImagePaths: extra), CancellationToken.None); }
            catch (InvalidOperationException) { rejected = true; }
            Check(rejected && handler.Calls == 0, "Invalid album reached network");
        }
    }
    finally { Directory.Delete(folder, true); }
});
await Run("old single-image documents load and new album order survives restart", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var legacy = JsonSerializer.Deserialize<Draft>("""{"Title":"Old","Text":"Body","ImagePath":"old.png"}""")!;
        Check(legacy.Images.SequenceEqual(new[] { "old.png" }), "Legacy draft broken");
        var delivery = JsonSerializer.Deserialize<Delivery>("""{"State":1,"ChatId":"-100777","MediaMessageId":7}""")!;
        Check(delivery.AllMediaMessageIds.SequenceEqual(new long[] { 7 }), "Legacy media ID lost");
        var paths = await MakeImages(folder, 3);
        var draft = new Draft("Ordered", ImagePath: paths[2], AdditionalImagePaths: new[] { paths[0], paths[1] });
        var store = new Store(folder); await store.InitializeAsync(); await store.SaveDocumentAsync("draft", draft);
        var reopened = new Store(folder); await reopened.InitializeAsync();
        Check((await reopened.ReadDocumentAsync<Draft>("draft"))!.SameContent(draft), "Album order not retained");
        Check(!draft.SameContent(draft with { AdditionalImagePaths = new[] { paths[1], paths[0] } }), "Order ignored by duplicate-send guard");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("failed checkpoint stops before the next Telegram request", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var paths = await MakeImages(folder, 2);
        var handler = new ScriptedHandler((_, _) => Task.FromResult(Message(400)));
        using var http = new HttpClient(handler); var stopped = false;
        try
        {
            await new TelegramClient(http, token).SendAsync("@test_channel", new(Text: new string('a', 1100), ImagePath: paths[0], AdditionalImagePaths: paths[1..]), CancellationToken.None,
                _ => throw new ApplicationException("storage unavailable"));
        }
        catch (ApplicationException) { stopped = true; }
        Check(stopped && handler.Calls == 1, "Network continued after failed checkpoint");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("interrupted checkpoint retains separate text identity on recovery", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var store = new Store(folder); await store.InitializeAsync();
        var publication = new Publication(Guid.NewGuid(), DateTimeOffset.UtcNow, new("Album"), false, "123",
            new(DeliveryState.Sending, "-100777", 500, Kind: "text"), "Album");
        await store.SavePublicationAsync(publication);
        var reopened = new Store(folder); await reopened.InitializeAsync();
        var delivery = (await reopened.HistoryAsync()).Single().Delivery;
        Check(delivery.State == DeliveryState.Unknown && delivery.EditMessageId == 500, "Recovery dropped checkpoint identity");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("cancellation after confirmed text stops before album and keeps editable identity", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var paths = await MakeImages(folder, 2);
        var handler = new ScriptedHandler((_, _) => Task.FromResult(Message(600)));
        using var http = new HttpClient(handler); using var cancellation = new CancellationTokenSource();
        var result = await new TelegramClient(http, token).SendAsync("@test_channel",
            new(Text: new string('a', 1100), ImagePath: paths[0], AdditionalImagePaths: paths[1..]), cancellation.Token,
            _ => { cancellation.Cancel(); return Task.CompletedTask; });
        Check(result.State == DeliveryState.Partial && result.EditMessageId == 600 && handler.Calls == 1, "Cancellation lost text or uploaded album");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("queue snapshots media and destination independently of the current draft/settings", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var paths = await MakeImages(folder, 2); var store = new Store(folder); await store.InitializeAsync();
        var draft = new Draft("Scheduled", "Body", paths[1], [paths[0]]);
        var job = await new QueuePlanner(store).AddAsync(draft, new(false, "@original_channel"), "123", DateTimeOffset.UtcNow.AddHours(1));
        await File.WriteAllTextAsync(paths[1], "changed"); File.Delete(paths[0]);
        await store.SaveDocumentAsync("options", new AppOptions(false, "@different_channel"));
        var restored = (await new Store(folder).QueueAsync()).Single();
        Check(restored.Draft.Images.All(File.Exists) && await File.ReadAllTextAsync(restored.Draft.Images[0]) == "image 1", "Queued media changed with originals");
        Check(restored.Options.Chat == "@original_channel" && restored.BotId == "123" && restored.Draft.Title == "Scheduled", "Destination snapshot changed");
        Check(job.Id == restored.Id && restored.Due.Offset == TimeSpan.Zero, "Queue identity/time changed");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("atomic claiming allows only one owner and never claims a future job", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var store = new Store(folder); await store.InitializeAsync(); var now = DateTimeOffset.UtcNow;
        var job = await new QueuePlanner(store).AddAsync(new("Due"), new(), "demo", now);
        var future = await new QueuePlanner(store).AddAsync(new("Future"), new(), "demo", now.AddHours(1));
        var claims = await Task.WhenAll(Enumerable.Range(0, 4).Select(_ => Task.Run(() => new Store(folder).ClaimDueAsync(now))));
        Check(claims.Count(value => value?.Id == job.Id) == 1 && claims.Count(value => value is not null) == 1, "Job claimed twice or too early");
        Check((await store.QueueAsync()).Single(value => value.Id == future.Id).State == QueueState.Pending, "Future job changed state");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("paused and cancelled jobs do not run; terminal jobs cannot be resumed", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var store = new Store(folder); await store.InitializeAsync(); var now = DateTimeOffset.UtcNow;
        var planner = new QueuePlanner(store);
        var paused = await planner.AddAsync(new("Pause"), new(), "demo", now);
        var cancelled = await planner.AddAsync(new("Cancel"), new(), "demo", now);
        Check(await store.ChangeQueueAsync(paused.Id, QueueState.Paused), "Pause failed");
        Check(await store.ChangeQueueAsync(cancelled.Id, QueueState.Cancelled), "Cancel failed");
        Check(await store.ClaimDueAsync(now) is null, "Paused/cancelled job was claimed");
        Check(!await store.ChangeQueueAsync(cancelled.Id, QueueState.Pending), "Cancelled job could be silently retried");
        Check(await store.ChangeQueueAsync(paused.Id, QueueState.Pending, now.AddMinutes(5)), "Resume failed");
        Check(await store.ClaimDueAsync(now) is null, "Resumed job ignored new time");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("queue runner executes once and links the result to publication history", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var store = new Store(folder); await store.InitializeAsync(); var now = DateTimeOffset.UtcNow;
        var job = await new QueuePlanner(store).AddAsync(new("Queue demo"), new(), "demo", now);
        var runner = new QueueRunner(store); var calls = 0;
        Task<IPublisher> Client(ScheduledPost _) { calls++; return Task.FromResult<IPublisher>(new DemoTelegram()); }
        Check(!await runner.RunNextAsync(now.AddSeconds(-1), Client, CancellationToken.None) && calls == 0, "Job ran early");
        Check(await runner.RunNextAsync(now, Client, CancellationToken.None), "Due job did not run");
        Check(!await runner.RunNextAsync(now.AddSeconds(1), Client, CancellationToken.None) && calls == 1, "Job repeated");
        var publication = (await store.HistoryAsync()).Single();
        Check(publication.Id == job.Id && publication.QueueId == job.Id && publication.Delivery.State == DeliveryState.Sent, "Queue/history link lost");
        Check((await store.QueueAsync()).Single().State == QueueState.Completed, "Queue completion not stored");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("opening the window does not recover an active worker publication", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var store = new Store(folder); await store.InitializeAsync(); var now = DateTimeOffset.UtcNow;
        var job = await new QueuePlanner(store).AddAsync(new("Active"), new(), "demo", now);
        await store.ClaimDueAsync(now);
        var publication = new Publication(job.Id, now, job.Draft, true, "demo", new(DeliveryState.Sending, "demo", 700), "Active", QueueId: job.Id);
        await store.SavePublicationAsync(publication);
        await new Store(folder).InitializeAsync();
        Check((await store.PublicationAsync(job.Id))!.Delivery.State == DeliveryState.Sending, "Window modified active worker publication");
        await store.RecoverQueueAsync();
        Check((await store.PublicationAsync(job.Id))!.Delivery is { State: DeliveryState.Unknown, EditMessageId: 700 }, "Worker recovery lost confirmed identity");
        Check((await store.QueueAsync()).Single().State == QueueState.Unknown && await store.ClaimDueAsync(now) is null, "Interrupted queue was retried");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("recovery recognizes confirmed sends even if queue completion was interrupted", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var store = new Store(folder); await store.InitializeAsync(); var now = DateTimeOffset.UtcNow;
        var job = await new QueuePlanner(store).AddAsync(new("Confirmed"), new(), "demo", now);
        await store.ClaimDueAsync(now);
        await new Workflow(store).SendAsync(new DemoTelegram(), "demo", new(), job.Draft, CancellationToken.None, job.Id);
        await store.RecoverQueueAsync();
        Check((await store.QueueAsync()).Single().State == QueueState.Completed && (await store.HistoryAsync()).Count == 1, "Confirmed send not recovered");
        Check(await store.ClaimDueAsync(now) is null, "Confirmed send retried");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("interruption after claim without a publication is never silently retried", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var store = new Store(folder); await store.InitializeAsync(); var now = DateTimeOffset.UtcNow;
        await new QueuePlanner(store).AddAsync(new("Claimed"), new(), "demo", now); await store.ClaimDueAsync(now);
        await store.RecoverQueueAsync();
        Check((await store.QueueAsync()).Single().State == QueueState.Unknown && await store.ClaimDueAsync(now) is null, "Claim-only interruption retried");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("long overdue jobs pause before any client/network creation", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var store = new Store(folder); await store.InitializeAsync(); var now = DateTimeOffset.UtcNow;
        await new QueuePlanner(store).AddAsync(new("Missed"), new(), "demo", now.AddHours(-1));
        await new QueueRunner(store).RunNextAsync(now, _ => throw new Exception("Client must not be created"), CancellationToken.None);
        Check((await store.QueueAsync()).Single().State == QueueState.Paused && (await store.HistoryAsync()).Count == 0, "Overdue job did not pause");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("changed bot or enabled demo pauses a real job before sending", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var store = new Store(folder); await store.InitializeAsync(); var now = DateTimeOffset.UtcNow;
        await new QueuePlanner(store).AddAsync(new("Real"), new(false, "@saved_target"), "123", now);
        await new QueueRunner(store).RunNextAsync(now, _ => throw new QueuePausedException("Different bot"), CancellationToken.None);
        Check((await store.QueueAsync()).Single().State == QueueState.Paused && (await store.HistoryAsync()).Count == 0, "Preflight pause failed");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("an unknown queued send remains unknown across restart without a retry", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var store = new Store(folder); await store.InitializeAsync(); var now = DateTimeOffset.UtcNow;
        var handler = new ScriptedHandler((_, _) => throw new HttpRequestException()); using var http = new HttpClient(handler);
        await new QueuePlanner(store).AddAsync(new("Uncertain"), new(false, "@saved_target"), "123", now);
        await new QueueRunner(store).RunNextAsync(now, _ => Task.FromResult<IPublisher>(new TelegramClient(http, token)), CancellationToken.None);
        await new Store(folder).InitializeAsync(); await store.RecoverQueueAsync();
        Check((await store.QueueAsync()).Single().State == QueueState.Unknown && handler.Calls == 1, "Unknown queued send retried");
        Check(await store.ClaimDueAsync(now) is null && (await store.HistoryAsync()).Single().Delivery.State == DeliveryState.Unknown, "Unknown result lost");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("invalid queued media is refused before constructing a client", async () =>
{
    var folder = TemporaryFolder();
    try
    {
        var store = new Store(folder); await store.InitializeAsync(); var now = DateTimeOffset.UtcNow;
        var paths = await MakeImages(folder, 1);
        var job = await new QueuePlanner(store).AddAsync(new("Missing", ImagePath: paths[0]), new(), "demo", now);
        File.Delete(job.Draft.Images[0]);
        await new QueueRunner(store).RunNextAsync(now, _ => throw new Exception("Unexpected client creation"), CancellationToken.None);
        Check((await store.QueueAsync()).Single().State == QueueState.Refused, "Missing queued media not refused");
    }
    finally { Directory.Delete(folder, true); }
});
await Run("schedule converts local time to UTC and rejects invalid or ambiguous clock times", () =>
{
    var zone = TimeZoneInfo.CreateCustomTimeZone("test+10", TimeSpan.FromHours(10), "test", "test");
    var due = ScheduleTime.FromLocal(new DateTime(2026, 11, 11), "12:30", zone);
    Check(due == new DateTimeOffset(2026, 11, 11, 2, 30, 0, TimeSpan.Zero), "Local timezone ignored");
    var rule = TimeZoneInfo.AdjustmentRule.CreateAdjustmentRule(new DateTime(2020, 1, 1), new DateTime(2040, 12, 31), TimeSpan.FromHours(1),
        TimeZoneInfo.TransitionTime.CreateFloatingDateRule(new DateTime(1, 1, 1, 2, 0, 0), 3, 2, DayOfWeek.Sunday),
        TimeZoneInfo.TransitionTime.CreateFloatingDateRule(new DateTime(1, 1, 1, 2, 0, 0), 11, 1, DayOfWeek.Sunday));
    var dst = TimeZoneInfo.CreateCustomTimeZone("test-dst", TimeSpan.FromHours(-5), "dst", "std", "summer", [rule]);
    foreach (var (date, time) in new[] { (new DateTime(2026, 11, 11), "24:00"), (new DateTime(2026, 3, 8), "02:30"), (new DateTime(2026, 11, 1), "01:30") })
    {
        var rejected = false; try { ScheduleTime.FromLocal(date, time, dst); } catch (InvalidOperationException) { rejected = true; }
        Check(rejected, "Invalid/ambiguous local time accepted");
    }
    return Task.CompletedTask;
});
await ImportChecks.RunAsync(Run);
await DiscordChecks.RunAsync(Run);
await BlueskyChecks.RunAsync(Run);
Console.WriteLine($"Checks completed; failures: {failures}");
return failures == 0 ? 0 : 1;

static HttpResponseMessage Album(params long[] ids) => new(HttpStatusCode.OK)
{
    Content = new StringContent(JsonSerializer.Serialize(new { ok = true, result = ids.Select(id => new { message_id = id, chat = new { id = -100777L, username = "test_channel" } }) }))
};
static async Task<string[]> MakeImages(string folder, int count)
{
    var paths = Enumerable.Range(0, count).Select(index => Path.Combine(folder, index + ".png")).ToArray();
    for (var index = 0; index < count; index++) await File.WriteAllTextAsync(paths[index], "image " + index);
    return paths;
}

sealed class ScriptedHandler(params Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>>[] responses) : HttpMessageHandler
{
    private readonly Queue<Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>>> _responses = new(responses);
    public int Calls { get; private set; }
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        Calls++;
        if (_responses.Count == 0) throw new Exception("Unexpected network request");
        return _responses.Dequeue()(request, cancellationToken);
    }
}
