using CrossPost.Core;
internal static class BatchChecks
{
    private static void Check(bool value, string message) { if (!value) throw new Exception(message); }
    public static async Task RunAsync(Func<string, Func<Task>, Task> run)
    {
        await run("batch queues immutable destinations and projects deliveries into one work", async () =>
        {
            var folder = Path.Combine(Path.GetTempPath(), "crosspost-batch-" + Guid.NewGuid());
            try
            {
                var store = new Store(folder); await store.InitializeAsync();
                var jobs = await new QueuePlanner(store).AddBatchAsync(new("One work"),
                    [new(new(), "demo"), new(new(Platform: "discord"), "demo"), new(new(Platform: "bluesky"), "demo")], DateTimeOffset.UtcNow);
                Check(jobs.Length == 3 && jobs.Select(job => job.WorkId).Distinct().Count() == 1, "Batch identity missing");
                var runner = new QueueRunner(store);
                for (var i = 0; i < 3; i++) await runner.RunNextAsync(DateTimeOffset.UtcNow, job => Task.FromResult<IPublisher>(job.Options.Platform switch
                    { "discord" => new DemoDiscord(), "bluesky" => new DemoBluesky(), _ => new DemoTelegram() }), CancellationToken.None);
                var library = await store.LibraryAsync();
                Check((await store.QueueAsync()).All(job => job.State == QueueState.Completed) && library.Posts.Count == 3
                    && library.Posts.Select(post => post.WorkId).Distinct().Count() == 1 && library.ManualWorks.Count == 0, "Crosspost split into unrelated works or marked manually merged");
                var posts = library.Posts.Select(post => post.WorkId).Distinct().ToArray(); await store.GroupWorksAsync(posts, true);
                var history = (await store.HistoryAsync())[0]; await store.SavePublicationAsync(history with { EditText = "Edited" });
                Check((await store.LibraryAsync()).Posts.Select(post => post.WorkId).Distinct().Count() == 3, "Edit undid manual splitting");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("batch validates every target before writing or publishing and rejects duplicates", async () =>
        {
            var folder = Path.Combine(Path.GetTempPath(), "crosspost-batch-" + Guid.NewGuid());
            try
            {
                var store = new Store(folder); await store.InitializeAsync(); var planner = new QueuePlanner(store);
                foreach (var targets in new PublishTarget[][] { [], [new(new(), "demo"), new(new(), "demo")], [new(new(), "demo"), new(new(Demo: false, Platform: "discord"), "")] })
                {
                    var refused = false; try { await planner.AddBatchAsync(new("Post"), targets, DateTimeOffset.UtcNow); } catch (InvalidOperationException) { refused = true; }
                    Check(refused && (await store.QueueAsync()).Count == 0, "Invalid batch left partial queue");
                }
                var invalid = false;
                try { await planner.AddBatchAsync(new(Text: new string('x', 301)), [new(new(), "demo"), new(new(Platform: "bluesky"), "demo")], DateTimeOffset.UtcNow); }
                catch (InvalidOperationException) { invalid = true; }
                Check(invalid && (await store.QueueAsync()).Count == 0 && !Directory.Exists(Path.Combine(folder, "queue-media")), "Bluesky limit checked after saving Telegram job");
                using var cancellation = new CancellationTokenSource(); cancellation.Cancel();
                try { await planner.AddBatchAsync(new("Post"), [new(new(), "demo")], DateTimeOffset.UtcNow, cancellation.Token); } catch (OperationCanceledException) { }
                Check((await store.QueueAsync()).Count == 0, "Cancelled batch persisted");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("batch refusal does not repeat successful destinations or block remaining destinations", async () =>
        {
            var folder = Path.Combine(Path.GetTempPath(), "crosspost-batch-" + Guid.NewGuid());
            try
            {
                var store = new Store(folder); await store.InitializeAsync();
                var jobs = await new QueuePlanner(store).AddBatchAsync(new("Post"), [new(new(), "demo"), new(new(Platform: "discord"), "demo")], DateTimeOffset.UtcNow);
                var calls = 0; var runner = new QueueRunner(store);
                async Task<IPublisher> Client(ScheduledPost job) { calls++; await Task.Yield(); if (job.Options.Platform == "telegram") throw new InvalidOperationException("Refused"); return new DemoDiscord(); }
                while (await runner.RunNextAsync(DateTimeOffset.UtcNow, Client, CancellationToken.None)) { }
                Check(calls == 2 && (await store.QueueAsync()).Any(job => job.State == QueueState.Completed) && (await store.QueueAsync()).Any(job => job.State == QueueState.Refused), "Failure blocked unrelated target or caused retry");
            }
            finally { Directory.Delete(folder, true); }
        });
    }
}
