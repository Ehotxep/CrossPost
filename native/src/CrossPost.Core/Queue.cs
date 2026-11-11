using System.Globalization;

namespace CrossPost.Core;

public enum QueueState { Pending, Paused, Running, Completed, Refused, Partial, Unknown, Cancelled }
public record ScheduledPost(Guid Id, DateTimeOffset Created, DateTimeOffset Due, Draft Draft,
    AppOptions Options, string BotId, QueueState State = QueueState.Pending, string Error = "");
public sealed class QueuePausedException(string message) : Exception(message);

public static class ScheduleTime
{
    public static DateTimeOffset FromLocal(DateTime date, string time, TimeZoneInfo zone)
    {
        if (!TimeOnly.TryParseExact(time, "HH:mm", CultureInfo.InvariantCulture, DateTimeStyles.None, out var clock))
            throw new InvalidOperationException("Введите время в формате ЧЧ:ММ.");
        var local = DateTime.SpecifyKind(date.Date.Add(clock.ToTimeSpan()), DateTimeKind.Unspecified);
        if (zone.IsInvalidTime(local) || zone.IsAmbiguousTime(local))
            throw new InvalidOperationException("Время попало на перевод часов. Выберите другое время.");
        return new DateTimeOffset(TimeZoneInfo.ConvertTimeToUtc(local, zone));
    }
}

public sealed class QueuePlanner(Store store)
{
    public async Task<ScheduledPost> AddAsync(Draft draft, AppOptions options, string botId, DateTimeOffset due)
    {
        draft.Validate(options.Platform);
        if (!options.Demo && (string.IsNullOrWhiteSpace(options.Chat) || string.IsNullOrWhiteSpace(botId)))
            throw new InvalidOperationException("Для задания нужны канал и бот.");
        var id = Guid.NewGuid();
        var folder = Path.Combine(store.Folder, "queue-media", id.ToString("N"));
        try
        {
            var copies = new List<string>();
            for (var index = 0; index < draft.Images.Length; index++)
            {
                Directory.CreateDirectory(folder);
                var path = draft.Images[index];
                var copy = Path.Combine(folder, index + Path.GetExtension(path).ToLowerInvariant());
                await using (var input = File.OpenRead(path))
                await using (var output = new FileStream(copy, FileMode.CreateNew, FileAccess.Write, FileShare.None, 81920, true))
                    await input.CopyToAsync(output);
                copies.Add(copy);
            }
            var snapshot = draft with { ImagePath = copies.FirstOrDefault(), AdditionalImagePaths = copies.Skip(1).ToArray() };
            snapshot.Validate(options.Platform);
            var job = new ScheduledPost(id, DateTimeOffset.UtcNow, due.ToUniversalTime(), snapshot, options, botId);
            await store.InsertQueueAsync(job);
            return job;
        }
        catch
        {
            if (Directory.Exists(folder)) Directory.Delete(folder, true);
            throw;
        }
    }
}

public sealed class QueueRunner(Store store)
{
    public static readonly TimeSpan OverdueGrace = TimeSpan.FromMinutes(15);
    public async Task<bool> RunNextAsync(DateTimeOffset now, Func<ScheduledPost, Task<IPublisher>> client,
        CancellationToken ct, Action<Guid?>? active = null)
    {
        ct.ThrowIfCancellationRequested();
        var job = await store.ClaimDueAsync(now);
        if (job is null) return false;
        active?.Invoke(job.Id);
        try
        {
            if (now - job.Due > OverdueGrace)
            {
                await store.FinishQueueAsync(job with { State = QueueState.Paused, Error = "Время пропущено более чем на 15 минут. Перенесите задание и включите его вручную." });
                return true;
            }
            job.Draft.Validate(job.Options.Platform);
            var publisher = await client(job);
            ct.ThrowIfCancellationRequested();
            var sent = await new Workflow(store).SendAsync(publisher, job.BotId, job.Options, job.Draft, ct, job.Id);
            await store.FinishQueueAsync(job with { State = StateFor(sent.Delivery.State), Error = sent.Delivery.Error });
        }
        catch (QueuePausedException error)
        { await store.FinishQueueAsync(job with { State = QueueState.Paused, Error = error.Message }); }
        catch (OperationCanceledException)
        { await store.FinishQueueAsync(job with { State = QueueState.Cancelled, Error = "Отменено до начала отправки." }); }
        catch (Exception)
        {
            // If a publication exists, a request may have left this process. Never retry automatically.
            var sent = await store.PublicationAsync(job.Id);
            await store.FinishQueueAsync(job with { State = sent is null ? QueueState.Refused : QueueState.Unknown,
                Error = sent is null ? "Задание не отправлено: проверьте файлы, канал и токен бота." : "Результат не подтверждён. Проверьте канал перед повторной публикацией." });
        }
        finally { active?.Invoke(null); }
        return true;
    }
    public static QueueState StateFor(DeliveryState state) => state switch
    {
        DeliveryState.Sent => QueueState.Completed, DeliveryState.Refused => QueueState.Refused,
        DeliveryState.Partial => QueueState.Partial, _ => QueueState.Unknown
    };
}
