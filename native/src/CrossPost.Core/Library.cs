using System.Security.Cryptography;
using System.Text;

namespace CrossPost.Core;

public record LibraryPost(Guid Id, Guid WorkId, string Platform, string Target, string[] ExternalIds,
    DateTimeOffset? Published, string Title, string Text, DeliveryState State, string Link = "",
    bool Excluded = false, Guid? NativeId = null, string Source = "", string OriginalJson = "");
public record MetricValues(long? Views = null, long? Reactions = null, long? Forwards = null,
    long? Replies = null, long? Reach = null, long? Bookmarks = null)
{
    public bool HasData => new[] { Views, Reactions, Forwards, Replies, Reach, Bookmarks }.Any(value => value.HasValue);
}
public record MetricSample(Guid Id, Guid PostId, DateTimeOffset? Observed, MetricValues Values, string Source, DateTimeOffset Recorded = default);
public sealed class LibraryData(IReadOnlyList<LibraryPost> posts, IReadOnlyList<MetricSample> metrics, IReadOnlySet<Guid> manualWorks)
{
    public IReadOnlyList<LibraryPost> Posts { get; } = posts;
    public IReadOnlyList<MetricSample> Metrics { get; } = metrics;
    public IReadOnlySet<Guid> ManualWorks { get; } = manualWorks;
    public IReadOnlyDictionary<Guid, MetricSample> LatestMetrics { get; } = metrics.GroupBy(sample => sample.PostId)
        .ToDictionary(group => group.Key, group => group.OrderByDescending(sample => sample.Observed)
            .ThenByDescending(sample => sample.Recorded).ThenByDescending(sample => sample.Id).First());
}
public record PlatformTotals(string Platform, string Target, int Publications, long? Views, long? Reactions, int WithViews, int WithReactions);
public record ImportReport(Guid BatchId, string Snapshot, string Backup, int Publications, int Measurements, int ArchivedSchedules, string[] Notes);

public static class Library
{
    public static Guid Key(string value) => new(SHA256.HashData(Encoding.UTF8.GetBytes(value)).AsSpan(0, 16));
    public static string Platform(string value) => value.ToLowerInvariant() switch
    { "bsky" => "bluesky", "da" => "deviantart", "e6ai" => "blep.art", _ => value.ToLowerInvariant() };
    public static MetricSample? Latest(LibraryData data, Guid postId) => data.LatestMetrics.GetValueOrDefault(postId);
    // Only successful publications count. A later failed attempt does not replace a live post.
    public static LibraryPost[] Representatives(LibraryData data) => data.Posts
        .Where(post => post.State == DeliveryState.Sent && !post.Excluded && !post.Platform.StartsWith("demo.", StringComparison.Ordinal))
        .GroupBy(post => (post.WorkId, post.Platform, post.Target))
        .Select(group => group.OrderByDescending(post => post.Published).ThenByDescending(post => post.Id).First()).ToArray();
    public static PlatformTotals[] Totals(LibraryData data) => Representatives(data)
        .GroupBy(post => (post.Platform, post.Target)).Select(group =>
        {
            var metrics = group.Select(post => Latest(data, post.Id)?.Values).ToArray();
            var views = metrics.Where(value => value?.Views is not null).Select(value => value!.Views!.Value).ToArray();
            var reactions = metrics.Where(value => value?.Reactions is not null).Select(value => value!.Reactions!.Value).ToArray();
            return new PlatformTotals(group.Key.Platform, group.Key.Target, group.Count(),
                views.Length == 0 ? null : views.Sum(), reactions.Length == 0 ? null : reactions.Sum(), views.Length, reactions.Length);
        }).OrderBy(row => row.Platform).ThenBy(row => row.Target).ToArray();
}
