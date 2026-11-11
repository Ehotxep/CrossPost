using System.Net;
using System.Text.Json;
using CrossPost.Core;

internal static class DiscordChecks
{
    private const string Secret = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    private const string Hook = "https://discord.com/api/webhooks/123/" + Secret;
    private static DiscordEndpoint Endpoint => DiscordEndpoint.Parse(Hook);
    private static HttpResponseMessage Json(string content, HttpStatusCode status = HttpStatusCode.OK) => new(status) { Content = new StringContent(content) };
    private static HttpResponseMessage Info(string channel = "456") => Json("{\"id\":\"123\",\"type\":1,\"channel_id\":\"" + channel + "\",\"guild_id\":\"789\",\"name\":\"Test\"}");
    private static HttpResponseMessage Message(int attachments = 0, string channel = "456") => Json(JsonSerializer.Serialize(new
    { id = "9007199254740993123", channel_id = channel, webhook_id = "123", attachments = Enumerable.Range(0, attachments).Select(index => new { id = "9007199254740994" + index }) }));
    private static void Check(bool condition, string message) { if (!condition) throw new Exception(message); }
    private static string Folder() { var path = Path.Combine(Path.GetTempPath(), "crosspost-discord-" + Guid.NewGuid()); Directory.CreateDirectory(path); return path; }
    private static async Task<string[]> Images(string folder, int count)
    {
        var paths = Enumerable.Range(0, count).Select(index => Path.Combine(folder, "image" + index + ".png")).ToArray();
        for (var index = 0; index < count; index++) await File.WriteAllTextAsync(paths[index], "image " + index);
        return paths;
    }
    public static async Task RunAsync(Func<string, Func<Task>, Task> run)
    {
        await run("Discord webhook parser restricts credentials to official HTTPS endpoints", () =>
        {
            Check(Endpoint.Id == "123" && !Endpoint.ToString().Contains(Secret), "Endpoint diagnostics leak token");
            foreach (var url in new[] { Hook.Replace("https:", "http:"), Hook.Replace("discord.com", "discord.com.evil.test"), Hook + "?thread_id=1", Hook + "#fragment", Hook.Replace("discord.com", "user@discord.com"), Hook.Replace("discord.com", "discord.com:444") })
            {
                var rejected = false; try { DiscordEndpoint.Parse(url); } catch (InvalidOperationException) { rejected = true; }
                Check(rejected, "Unsupported credential destination accepted");
            }
            return Task.CompletedTask;
        });
        await run("Discord connection check reads metadata without publishing", async () =>
        {
            var handler = new ScriptedHandler((request, _) =>
            {
                Check(request.Method == HttpMethod.Get && request.RequestUri!.Host == "discord.com" && request.RequestUri.AbsolutePath.Contains("/api/v10/webhooks/123/"), "Connection check mutated or used wrong host");
                return Task.FromResult(Info());
            });
            using var http = new HttpClient(handler);
            var info = await new DiscordClient(http, Endpoint, "456").InspectAsync(CancellationToken.None);
            Check(info is { Id: "123", ChannelId: "456", GuildId: "789" } && handler.Calls == 1, "Metadata not retained");
        });
        await run("Discord preserves full text, spoiler order and attachment IDs and disables mention notifications", async () =>
        {
            var folder = Folder();
            try
            {
                var images = await Images(folder, 2);
                var text = "@everyone " + new string('x', 1990);
                var handler = new ScriptedHandler((_, _) => Task.FromResult(Info()), async (request, ct) =>
                {
                    Check(request.Method == HttpMethod.Post && request.RequestUri!.Query == "?wait=true", "Send did not request confirmation");
                    var parts = ((MultipartFormDataContent)request.Content!).ToDictionary(part => part.Headers.ContentDisposition!.Name!.Trim('"'));
                    using var json = JsonDocument.Parse(await parts["payload_json"].ReadAsStringAsync(ct)); var body = json.RootElement;
                    Check(body.GetProperty("content").GetString() == text && body.GetProperty("flags").GetInt32() == 4, "Text truncated or previews flag lost");
                    Check(body.GetProperty("allowed_mentions").GetProperty("parse").GetArrayLength() == 0, "Broadcast mentions enabled");
                    var files = body.GetProperty("attachments").EnumerateArray().ToArray();
                    for (var index = 0; index < 2; index++)
                    {
                        Check(files[index].GetProperty("filename").GetString() == "SPOILER_image" + index + ".png", "Spoiler filename/order changed");
                        Check(await parts[$"files[{index}]"] .ReadAsStringAsync(ct) == "image " + index, "Wrong file content");
                        Check(parts[$"files[{index}]"] .Headers.ContentType?.MediaType == "image/png", "Wrong image MIME");
                    }
                    return Message(2);
                });
                using var http = new HttpClient(handler);
                var sent = await new DiscordClient(http, Endpoint, "456", true).SendAsync("456", new(Text: text, ImagePath: images[0], AdditionalImagePaths: images[1..]), CancellationToken.None);
                Check(sent is { State: DeliveryState.Sent, RemoteMessageId: "9007199254740993123" } && sent.AttachmentIds?.Length == 2, "Confirmation/IDs lost");
                Check(sent.Link == "https://discord.com/channels/789/456/9007199254740993123", "Wrong message link");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("Discord rejects text over 2000 characters before any network request", async () =>
        {
            var handler = new ScriptedHandler(); using var http = new HttpClient(handler); var rejected = false;
            var draft = new Draft(Text: new string('x', 2001)); draft.Validate("telegram");
            try { await new DiscordClient(http, Endpoint, "456").SendAsync("456", draft, CancellationToken.None); } catch (InvalidOperationException) { rejected = true; }
            Check(rejected && handler.Calls == 0, "Oversized text was truncated or reached Discord");
        });
        await run("moving a webhook to a different channel refuses before POST", async () =>
        {
            var handler = new ScriptedHandler((_, _) => Task.FromResult(Info("999"))); using var http = new HttpClient(handler);
            var sent = await new DiscordClient(http, Endpoint, "456").SendAsync("456", new("Do not redirect"), CancellationToken.None);
            Check(sent.State == DeliveryState.Refused && handler.Calls == 1, "Moved webhook received a post");
        });
        await run("failed Discord GET is a known refusal and does not leak the webhook URL", async () =>
        {
            var handler = new ScriptedHandler((_, _) => throw new HttpRequestException(Hook)); using var http = new HttpClient(handler);
            var sent = await new DiscordClient(http, Endpoint, "456").SendAsync("456", new("Title"), CancellationToken.None);
            Check(sent.State == DeliveryState.Refused && !sent.Error.Contains(Secret) && handler.Calls == 1, "Read-only failure classified as a send or leaked credentials");
        });
        await run("explicit Discord refusal and rate limit are never retried", async () =>
        {
            foreach (var status in new[] { 400, 401, 403, 404, 413, 415, 422, 429 })
            {
                var handler = new ScriptedHandler((_, _) => Task.FromResult(Info()), (_, _) => Task.FromResult(Json("{\"message\":\"" + Hook + "\"}", (HttpStatusCode)status)));
                using var http = new HttpClient(handler);
                var sent = await new DiscordClient(http, Endpoint, "456").SendAsync("456", new("Title"), CancellationToken.None);
                Check(sent.State == DeliveryState.Refused && handler.Calls == 2 && !sent.Error.Contains(Secret), "Refusal retried, misclassified or leaked token");
            }
        });
        await run("Discord lost send response is unknown and is not retried", async () =>
        {
            var handler = new ScriptedHandler((_, _) => Task.FromResult(Info()), (_, _) => throw new HttpRequestException(Hook)); using var http = new HttpClient(handler);
            var sent = await new DiscordClient(http, Endpoint, "456").SendAsync("456", new("Title"), CancellationToken.None);
            Check(sent.State == DeliveryState.Unknown && handler.Calls == 2 && !sent.Error.Contains(Secret), "Ambiguous send retried or leaked credentials");
        });
        await run("incomplete Discord attachments preserve the confirmed message as partial", async () =>
        {
            var folder = Folder();
            try
            {
                var images = await Images(folder, 2); var handler = new ScriptedHandler((_, _) => Task.FromResult(Info()), (_, _) => Task.FromResult(Message(1)));
                using var http = new HttpClient(handler);
                var sent = await new DiscordClient(http, Endpoint, "456").SendAsync("456", new("Title", ImagePath: images[0], AdditionalImagePaths: images[1..]), CancellationToken.None);
                Check(sent is { State: DeliveryState.Partial, RemoteMessageId: "9007199254740993123" } && sent.AttachmentIds?.Length == 1, "Partial confirmation lost");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("Discord queue records platform and settings without storing credentials", async () =>
        {
            var folder = Folder();
            try
            {
                var store = new Store(folder); await store.InitializeAsync(); var now = DateTimeOffset.UtcNow;
                var options = new AppOptions(false, "456", "discord", true, false);
                var job = await new QueuePlanner(store).AddAsync(new("Queued Discord"), options, "123", now);
                var handler = new ScriptedHandler((_, _) => Task.FromResult(Info()), (_, _) => Task.FromResult(Message())); using var http = new HttpClient(handler);
                await new QueueRunner(store).RunNextAsync(now, queued =>
                {
                    Check(queued.Options is { Platform: "discord", Spoiler: true, SuppressEmbeds: false } && queued.BotId == "123", "Destination options changed");
                    return Task.FromResult<IPublisher>(new DiscordClient(http, Endpoint, queued.Options.Chat, queued.Options.Spoiler, queued.Options.SuppressEmbeds));
                }, CancellationToken.None);
                var publication = (await store.HistoryAsync()).Single(); var library = await store.LibraryAsync();
                Check(publication.Platform == "discord" && publication.QueueId == job.Id && library.Posts.Single().Platform == "discord", "Discord routed into Telegram history");
                Check(library.Posts.Single().ExternalIds.Contains("9007199254740993123"), "String ID changed");
                var stored = JsonSerializer.Serialize(publication) + JsonSerializer.Serialize(await store.QueueAsync());
                Check(!stored.Contains(Secret) && !stored.Contains("/webhooks/"), "Webhook credential stored in queue/history");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("Discord edits keep attachments and update the recorded exact text", async () =>
        {
            var folder = Folder();
            try
            {
                var store = new Store(folder); await store.InitializeAsync();
                var publication = new Publication(Guid.NewGuid(), DateTimeOffset.UtcNow, new("Old"), false, "123",
                    new(DeliveryState.Sent, "456", Kind: "discord", RemoteMessageId: "9007199254740993123", AttachmentIds: ["100"]), "Old", Platform: "discord");
                await store.SavePublicationAsync(publication);
                var handler = new ScriptedHandler((_, _) => Task.FromResult(Info()), async (request, ct) =>
                {
                    Check(request.Method == HttpMethod.Patch && request.RequestUri!.AbsolutePath.EndsWith("/messages/9007199254740993123"), "Wrong edit method/target");
                    using var body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(ct));
                    Check(!body.RootElement.TryGetProperty("attachments", out _) && body.RootElement.GetProperty("content").GetString() == "Edited", "Edit replaced attachments or text");
                    return Message(1);
                });
                using var http = new HttpClient(handler);
                var edit = await new Workflow(store).EditAsync(new DiscordClient(http, Endpoint, "456"), publication, "Edited", CancellationToken.None);
                Check(edit.Result.Confirmed && (await store.HistoryAsync()).Single().EditText == "Edited", "Confirmed edit not stored");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("Discord refuses edits made through another webhook before network", async () =>
        {
            var handler = new ScriptedHandler(); using var http = new HttpClient(handler);
            var publication = new Publication(Guid.NewGuid(), DateTimeOffset.UtcNow, new("Old"), false, "999",
                new(DeliveryState.Sent, "456", Kind: "discord", RemoteMessageId: "1234"), "Old", Platform: "discord");
            var edit = await new DiscordClient(http, Endpoint, "456").EditAsync(publication, "New", CancellationToken.None);
            Check(!edit.Confirmed && !edit.Unknown && handler.Calls == 0, "Wrong webhook reached network");
        });
        await run("Discord server errors and malformed confirmations stay unknown without retries", async () =>
        {
            foreach (var response in new Func<HttpResponseMessage>[] { () => Json("{}", HttpStatusCode.InternalServerError), () => Json("{}", HttpStatusCode.ServiceUnavailable), () => Json("{\"id\":\"invalid\"}") })
            {
                var handler = new ScriptedHandler((_, _) => Task.FromResult(Info()), (_, _) => Task.FromResult(response())); using var http = new HttpClient(handler);
                var sent = await new DiscordClient(http, Endpoint, "456").SendAsync("456", new("Title"), CancellationToken.None);
                Check(sent.State == DeliveryState.Unknown && handler.Calls == 2, "Server error/incomplete confirmation was treated as safely retryable");
            }
        });
        await run("refused or uncertain Discord edits preserve the last confirmed text", async () =>
        {
            var folder = Folder();
            try
            {
                var store = new Store(folder); await store.InitializeAsync();
                var publication = new Publication(Guid.NewGuid(), DateTimeOffset.UtcNow, new("Old"), false, "123",
                    new(DeliveryState.Sent, "456", Kind: "discord", RemoteMessageId: "9007199254740993123"), "Old", Platform: "discord");
                await store.SavePublicationAsync(publication);
                var handler = new ScriptedHandler((_, _) => Task.FromResult(Info()), (_, _) => Task.FromResult(Json("{}", HttpStatusCode.NotFound)),
                    (_, _) => Task.FromResult(Info()), (_, _) => throw new HttpRequestException(Hook));
                using var http = new HttpClient(handler); var client = new DiscordClient(http, Endpoint, "456"); var workflow = new Workflow(store);
                var refused = await workflow.EditAsync(client, publication, "New", CancellationToken.None);
                Check(!refused.Result.Confirmed && !refused.Result.Unknown && (await store.HistoryAsync()).Single().EditText == "Old", "Refused edit changed recorded text");
                var uncertain = await workflow.EditAsync(client, refused.Publication, "New", CancellationToken.None);
                Check(uncertain.Result.Unknown && (await store.HistoryAsync()).Single().EditText == "Old" && !uncertain.Result.Error.Contains(Secret), "Uncertain edit changed recorded text or leaked credential");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("Discord validates per-file and total upload limits before contacting the webhook", async () =>
        {
            var folder = Folder();
            try
            {
                var files = await Images(folder, 3);
                foreach (var file in files) { using var stream = File.OpenWrite(file); stream.SetLength(12L * 1024 * 1024); }
                new Draft(ImagePath: files[0], AdditionalImagePaths: [files[1]]).Validate("discord");
                var handler = new ScriptedHandler(); using var http = new HttpClient(handler); var rejected = false;
                try { await new DiscordClient(http, Endpoint, "456").SendAsync("456", new(ImagePath: files[0], AdditionalImagePaths: files[1..]), CancellationToken.None); }
                catch (InvalidOperationException) { rejected = true; }
                Check(rejected && handler.Calls == 0, "Oversized aggregate upload reached Discord");
                using (var stream = File.OpenWrite(files[0])) stream.SetLength(PublishLimits.DiscordImage + 1);
                rejected = false; try { new Draft(ImagePath: files[0]).Validate("discord"); } catch (InvalidOperationException) { rejected = true; }
                Check(rejected, "Oversized individual attachment accepted");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("demo message identities reused by a restarted process do not merge separate posts", async () =>
        {
            var folder = Folder();
            try
            {
                var store = new Store(folder); await store.InitializeAsync();
                for (var index = 0; index < 2; index++)
                    await store.SavePublicationAsync(new(Guid.NewGuid(), DateTimeOffset.UtcNow, new("Demo " + index), true, "demo",
                        new(DeliveryState.Sent, "demo-discord-channel", Kind: "discord", RemoteMessageId: "100"), "Demo " + index, Platform: "discord"));
                var data = await store.LibraryAsync();
                Check(data.Posts.Count == 2 && data.Posts.Select(post => post.NativeId).Distinct().Count() == 2, "Demo IDs collapsed real history entries");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("schema 1 upgrade preserves old Telegram records and paused scheduling data", async () =>
        {
            var folder = Folder();
            try
            {
                var store = new Store(folder); await store.InitializeAsync();
                var publication = new Publication(Guid.NewGuid(), DateTimeOffset.UtcNow, new("Old Telegram"), true, "demo",
                    new(DeliveryState.Sent, "demo-channel", 100), "Old Telegram");
                await store.SavePublicationAsync(publication);
                var job = await new QueuePlanner(store).AddAsync(new("Old schedule"), new(), "demo", DateTimeOffset.UtcNow.AddDays(1));
                await store.ChangeQueueAsync(job.Id, QueueState.Paused);
                var oldJson = System.Text.Json.Nodes.JsonNode.Parse(JsonSerializer.Serialize(publication))!.AsObject();
                oldJson.Remove("Platform"); oldJson.Remove("Options");
                using (var database = new Microsoft.Data.Sqlite.SqliteConnection(new Microsoft.Data.Sqlite.SqliteConnectionStringBuilder { DataSource = Path.Combine(folder, "prototype.db"), Pooling = false }.ToString()))
                {
                    await database.OpenAsync(); using var command = database.CreateCommand();
                    command.CommandText = "UPDATE publications SET json=$json WHERE id=$id; PRAGMA user_version=1";
                    command.Parameters.AddWithValue("$json", oldJson.ToJsonString()); command.Parameters.AddWithValue("$id", publication.Id.ToString());
                    await command.ExecuteNonQueryAsync();
                }
                await store.InitializeAsync(); var history = (await store.HistoryAsync()).Single();
                Check(history.Platform == "telegram" && history.Delivery.EditMessageId == 100 && history.EditText == "Old Telegram", "Old Telegram data changed on upgrade");
                Check((await store.QueueAsync()).Single().State == QueueState.Paused && (await store.LibraryAsync()).Posts.Single().Platform == "demo.telegram", "Queue or projection changed platform");
                Check(Directory.GetFiles(Path.Combine(folder, "backups"), "*.db").Length == 1, "Upgrade backup missing");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("Discord demo and old native settings remain compatible without credentials", async () =>
        {
            var old = JsonSerializer.Deserialize<AppOptions>("""{"Demo":true,"Chat":"@old"}""")!;
            Check(old.Platform == "telegram", "Old Telegram options changed platform");
            var demo = new DemoDiscord(); var sent = await demo.SendAsync("ignored", new("Demo"), CancellationToken.None);
            Check(sent.State == DeliveryState.Sent && sent.Kind == "discord" && sent.RemoteMessageId is not null, "Demo Discord missing");
        });
    }
}
