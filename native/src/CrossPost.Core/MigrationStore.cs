using System.Text.Json;

namespace CrossPost.Core;

public sealed partial class Store
{
    // The caller protects/stages secrets first, replaces them here, and restores them if this transaction fails.
    public async Task SaveMigrationSettingsAsync(Dictionary<string, object> documents, Action replaceSecrets, Action restoreSecrets)
    {
        using var connection = await OpenAsync(); using var transaction = connection.BeginTransaction(deferred: false);
        try
        {
            foreach (var (key, value) in documents)
            {
                using var command = connection.CreateCommand(); command.Transaction = transaction;
                command.CommandText = "INSERT INTO documents(key,json) VALUES($key,$json) ON CONFLICT(key) DO UPDATE SET json=excluded.json";
                command.Parameters.AddWithValue("$key", key); command.Parameters.AddWithValue("$json", JsonSerializer.Serialize(value, value.GetType()));
                await command.ExecuteNonQueryAsync();
            }
            replaceSecrets(); transaction.Commit();
        }
        catch { restoreSecrets(); throw; }
    }
}
