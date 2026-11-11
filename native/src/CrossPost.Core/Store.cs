using System.Text.Json;
using Microsoft.Data.Sqlite;

namespace CrossPost.Core;

public sealed partial class Store
{
    private readonly string _connectionString;
    public string Folder { get; }
    public Store(string folder)
    {
        Folder = Path.GetFullPath(folder);
        Directory.CreateDirectory(Folder);
        _connectionString = new SqliteConnectionStringBuilder
        {
            DataSource = Path.Combine(folder, "prototype.db"), Pooling = false, DefaultTimeout = 5
        }.ToString();
    }
    private async Task<SqliteConnection> OpenAsync()
    {
        var connection = new SqliteConnection(_connectionString);
        try { await connection.OpenAsync(); return connection; }
        catch { connection.Dispose(); throw; }
    }
    public async Task InitializeAsync(bool recoverUnfinished = true)
    {
        await InitializeLibraryAsync();
        using var connection = await OpenAsync();
        using var command = connection.CreateCommand();
        command.CommandText = """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS documents(key TEXT PRIMARY KEY, json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS publications(id TEXT PRIMARY KEY, created TEXT NOT NULL, json TEXT NOT NULL, revision INTEGER NOT NULL);
            """;
        await command.ExecuteNonQueryAsync();
        await InitializeQueueAsync();
        if (!recoverUnfinished) return;
        // Queue-owned publications are recovered only by the exclusive worker owner.
        foreach (var publication in await HistoryAsync())
            if (publication.QueueId is null && publication.Delivery.State == DeliveryState.Sending)
                await SavePublicationAsync(publication with
                {
                    Delivery = publication.Delivery with { State = DeliveryState.Unknown,
                        Error = "Приложение завершилось во время отправки. Проверьте канал перед повтором." }
                });
    }
    public async Task SaveDocumentAsync<T>(string key, T value)
    {
        using var connection = await OpenAsync();
        using var command = connection.CreateCommand();
        command.CommandText = "INSERT INTO documents(key,json) VALUES($key,$json) ON CONFLICT(key) DO UPDATE SET json=excluded.json";
        command.Parameters.AddWithValue("$key", key);
        command.Parameters.AddWithValue("$json", JsonSerializer.Serialize(value));
        await command.ExecuteNonQueryAsync();
    }
    public async Task<T?> ReadDocumentAsync<T>(string key)
    {
        using var connection = await OpenAsync();
        using var command = connection.CreateCommand();
        command.CommandText = "SELECT json FROM documents WHERE key=$key";
        command.Parameters.AddWithValue("$key", key);
        return await command.ExecuteScalarAsync() is string value ? JsonSerializer.Deserialize<T>(value) : default;
    }
    public async Task SavePublicationAsync(Publication publication)
    {
        using var connection = await OpenAsync();
        using var transaction = connection.BeginTransaction(deferred: false);
        using var command = connection.CreateCommand(); command.Transaction = transaction;
        command.CommandText = """
            INSERT INTO publications(id,created,json,revision) VALUES($id,$created,$json,$revision)
            ON CONFLICT(id) DO UPDATE SET json=excluded.json,revision=excluded.revision
            """;
        command.Parameters.AddWithValue("$id", publication.Id.ToString());
        command.Parameters.AddWithValue("$created", publication.Created.ToString("O"));
        command.Parameters.AddWithValue("$json", JsonSerializer.Serialize(publication));
        command.Parameters.AddWithValue("$revision", publication.Revision);
        await command.ExecuteNonQueryAsync();
        await ProjectNativeAsync(connection, transaction, publication);
        transaction.Commit();
    }
    public async Task<IReadOnlyList<Publication>> HistoryAsync()
    {
        using var connection = await OpenAsync();
        using var command = connection.CreateCommand();
        command.CommandText = "SELECT json FROM publications ORDER BY created DESC";
        using var reader = await command.ExecuteReaderAsync();
        var list = new List<Publication>();
        while (await reader.ReadAsync())
            if (JsonSerializer.Deserialize<Publication>(reader.GetString(0)) is { } publication) list.Add(publication);
        return list;
    }
}
