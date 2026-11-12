using CrossPost.Core;

internal static class DestinationChecks
{
    private static void Check(bool result, string message) { if (!result) throw new Exception(message); }
    public static async Task RunAsync(Func<string, Func<Task>, Task> run)
    {
        await run("destination routing excludes disabled channels and enforces SFW/18+ without changing selected IDs", () =>
        {
            var safe = new DestinationProfile("safe", "Safe", new(true, "-1001", RatingRule: "sfw"), "");
            var mature = new DestinationProfile("mature", "18+", new(true, "-1002", RatingRule: "nsfw"), "");
            var all = new DestinationProfile("all", "All", new(true, "chan", "discord"), "");
            var disabled = new DestinationProfile("off", "Off", new(true, "-1003"), "", false);
            var selected = new[] { "safe", "mature", "all", "off" };
            Check(DestinationRouting.Select([safe, mature, all, disabled], selected, "sfw").Select(t => t.Options.DestinationId).SequenceEqual(new[] { "safe", "all" }), "SFW leaked to adult channel");
            foreach (var label in new[] { "erotic", "explicit" }) Check(DestinationRouting.Select([safe, mature, all, disabled], selected, label).Select(t => t.Options.DestinationId).SequenceEqual(new[] { "mature", "all" }), "Adult post leaked to SFW");
            var rejected = false; try { DestinationRouting.Accepts("typo", "sfw"); } catch (InvalidOperationException) { rejected = true; }
            Check(rejected, "Unknown rule allowed"); return Task.CompletedTask;
        });
        await run("queued destination changes pause rather than reroute; existing snapshots stay immutable", () =>
        {
            var options = new AppOptions(false, "123", "discord", DestinationId: "hook");
            var job = new ScheduledPost(Guid.NewGuid(), DateTimeOffset.UtcNow, DateTimeOffset.UtcNow, new("Title"), options, "webhook1");
            var original = new DestinationProfile("hook", "Art", options, "webhook1");
            DestinationRouting.RequireForJob(job, [original]);
            foreach (var profile in new[] { original with { Enabled = false }, original with { Options = options with { Chat = "456" } }, original with { CredentialId = "webhook2" }, original with { Options = options with { Demo = true } }, original with { Options = options with { RatingRule = "nsfw" } } })
            {
                var paused = false; try { DestinationRouting.RequireForJob(job, [profile]); } catch (QueuePausedException) { paused = true; }
                Check(paused, "Changed destination accepted");
            }
            Check(job.Options.Chat == "123" && job.BotId == "webhook1", "Snapshot mutated"); return Task.CompletedTask;
        });
        await run("multi-destination batch keeps exact channels and one work; invalid rating rolls back whole batch", async () =>
        {
            var folder = Path.Combine(Path.GetTempPath(), "crosspost-targets-" + Guid.NewGuid().ToString("N"));
            try
            {
                var store = new Store(folder); await store.InitializeAsync();
                using (var database = new Microsoft.Data.Sqlite.SqliteConnection(new Microsoft.Data.Sqlite.SqliteConnectionStringBuilder { DataSource = Path.Combine(folder, "prototype.db"), Pooling = false }.ToString()))
                {
                    await database.OpenAsync(); using var version = database.CreateCommand(); version.CommandText = "PRAGMA user_version";
                    Check(Convert.ToInt32(await version.ExecuteScalarAsync()) == Store.SchemaVersion, "Database schema guard not persisted");
                }
                var planner = new QueuePlanner(store); var draft = new Draft("Art", ContentLabel: "explicit");
                var good = new PublishTarget(new(true, "-1001", DestinationId: "one", RatingRule: "nsfw"), "demo");
                var second = new PublishTarget(new(true, "-1002", DestinationId: "two", RatingRule: "all"), "demo");
                var bad = new PublishTarget(new(true, "-1003", DestinationId: "three", RatingRule: "sfw"), "demo");
                var refused = false; try { await planner.AddBatchAsync(draft, [good, bad], DateTimeOffset.UtcNow); } catch (InvalidOperationException) { refused = true; }
                Check(refused && (await store.QueueAsync()).Count == 0, "Part of invalid batch queued");
                var jobs = await planner.AddBatchAsync(draft, [good, second], DateTimeOffset.UtcNow);
                Check(jobs.Length == 2 && jobs[0].WorkId == jobs[1].WorkId && jobs.Select(j => j.Options.Chat).SequenceEqual(new[] { "-1001", "-1002" }), "Targets or linkage lost");
                Check((await store.QueueAsync()).Select(j => j.Options.DestinationId).ToHashSet().SetEquals(["one", "two"]), "Destination IDs lost on restart");
            }
            finally { Directory.Delete(folder, true); }
        });
    }
}
