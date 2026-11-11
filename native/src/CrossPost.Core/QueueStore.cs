using System.Text.Json;
using Microsoft.Data.Sqlite;

namespace CrossPost.Core;

public sealed partial class Store
{
    private async Task InitializeQueueAsync()
    {
        using var connection = await OpenAsync(); using var command = connection.CreateCommand();
        command.CommandText = """
            CREATE TABLE IF NOT EXISTS queue(id TEXT PRIMARY KEY, state INTEGER NOT NULL, due INTEGER NOT NULL, json TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS queue_due ON queue(state, due);
            """;
        await command.ExecuteNonQueryAsync();
    }
    public async Task InsertQueueAsync(ScheduledPost job)
    {
        await InsertQueueBatchAsync([job]);
    }
    public async Task InsertQueueBatchAsync(IEnumerable<ScheduledPost> jobs)
    {
        using var connection = await OpenAsync(); using var transaction = connection.BeginTransaction(deferred: false);
        foreach (var job in jobs)
        {
            using var command = connection.CreateCommand(); command.Transaction = transaction;
            command.CommandText = "INSERT INTO queue(id,state,due,json) VALUES($id,$state,$due,$json)";
            QueueParameters(command, job); await command.ExecuteNonQueryAsync();
        }
        transaction.Commit();
    }
    private static void QueueParameters(SqliteCommand command, ScheduledPost job)
    {
        command.Parameters.AddWithValue("$id", job.Id.ToString());
        command.Parameters.AddWithValue("$state", (int)job.State);
        command.Parameters.AddWithValue("$due", job.Due.ToUnixTimeMilliseconds());
        command.Parameters.AddWithValue("$json", JsonSerializer.Serialize(job));
    }
    public async Task<IReadOnlyList<ScheduledPost>> QueueAsync()
    {
        using var connection = await OpenAsync(); using var command = connection.CreateCommand();
        command.CommandText = "SELECT json FROM queue ORDER BY due,id";
        using var reader = await command.ExecuteReaderAsync(); var jobs = new List<ScheduledPost>();
        while (await reader.ReadAsync()) jobs.Add(JsonSerializer.Deserialize<ScheduledPost>(reader.GetString(0))!);
        return jobs;
    }
    public async Task<ScheduledPost?> ClaimDueAsync(DateTimeOffset now)
    {
        using var connection = await OpenAsync(); using var transaction = connection.BeginTransaction(deferred: false);
        using var select = connection.CreateCommand(); select.Transaction = transaction;
        select.CommandText = "SELECT json FROM queue WHERE state=$pending AND due<=$now ORDER BY due,id LIMIT 1";
        select.Parameters.AddWithValue("$pending", (int)QueueState.Pending);
        select.Parameters.AddWithValue("$now", now.ToUnixTimeMilliseconds());
        if (await select.ExecuteScalarAsync() is not string json) { transaction.Commit(); return null; }
        var job = JsonSerializer.Deserialize<ScheduledPost>(json)! with { State = QueueState.Running, Error = "" };
        using var update = connection.CreateCommand(); update.Transaction = transaction;
        update.CommandText = "UPDATE queue SET state=$state,due=$due,json=$json WHERE id=$id AND state=$pending";
        QueueParameters(update, job); update.Parameters.AddWithValue("$pending", (int)QueueState.Pending);
        if (await update.ExecuteNonQueryAsync() != 1) throw new InvalidOperationException("Задание уже занято.");
        transaction.Commit(); return job;
    }
    public async Task FinishQueueAsync(ScheduledPost job)
    {
        using var connection = await OpenAsync(); using var command = connection.CreateCommand();
        command.CommandText = "UPDATE queue SET state=$state,due=$due,json=$json WHERE id=$id AND state=$running";
        QueueParameters(command, job); command.Parameters.AddWithValue("$running", (int)QueueState.Running);
        if (await command.ExecuteNonQueryAsync() != 1) throw new InvalidOperationException("Задание больше не выполняется.");
    }
    public async Task<bool> ChangeQueueAsync(Guid id, QueueState state, DateTimeOffset? due = null)
    {
        if (state is not (QueueState.Pending or QueueState.Paused or QueueState.Cancelled))
            throw new ArgumentOutOfRangeException(nameof(state));
        using var connection = await OpenAsync(); using var transaction = connection.BeginTransaction(deferred: false);
        using var select = connection.CreateCommand(); select.Transaction = transaction;
        select.CommandText = "SELECT json FROM queue WHERE id=$id"; select.Parameters.AddWithValue("$id", id.ToString());
        if (await select.ExecuteScalarAsync() is not string json) { transaction.Commit(); return false; }
        var job = JsonSerializer.Deserialize<ScheduledPost>(json)!;
        if (job.State is not (QueueState.Pending or QueueState.Paused)) { transaction.Commit(); return false; }
        job = job with { State = state, Due = due ?? job.Due, Error = "" };
        using var update = connection.CreateCommand(); update.Transaction = transaction;
        update.CommandText = "UPDATE queue SET state=$state,due=$due,json=$json WHERE id=$id";
        QueueParameters(update, job); await update.ExecuteNonQueryAsync(); transaction.Commit(); return true;
    }
    public async Task<Publication?> PublicationAsync(Guid id)
    {
        using var connection = await OpenAsync(); using var command = connection.CreateCommand();
        command.CommandText = "SELECT json FROM publications WHERE id=$id";
        command.Parameters.AddWithValue("$id", id.ToString());
        return await command.ExecuteScalarAsync() is string json ? JsonSerializer.Deserialize<Publication>(json) : null;
    }
    // Call only after acquiring the exclusive worker lock, never from window startup.
    public async Task RecoverQueueAsync()
    {
        foreach (var job in (await QueueAsync()).Where(job => job.State == QueueState.Running))
        {
            var publication = await PublicationAsync(job.Id);
            if (publication?.Delivery.State == DeliveryState.Sending)
            {
                publication = publication with { Delivery = publication.Delivery with { State = DeliveryState.Unknown,
                    Error = "Фоновый процесс прервался во время отправки. Проверьте канал." } };
                await SavePublicationAsync(publication);
            }
            await FinishQueueAsync(job with { State = publication is null ? QueueState.Unknown : QueueRunner.StateFor(publication.Delivery.State),
                Error = publication?.Delivery.Error ?? "Процесс прервался после начала задания. Проверьте канал перед повтором." });
        }
    }
}
