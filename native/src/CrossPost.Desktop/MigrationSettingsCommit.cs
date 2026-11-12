using System.IO;
using System.Text.Json;
using CrossPost.Core;

namespace CrossPost.Desktop;

internal static class MigrationSettingsCommit
{
    private record Journal(Guid Id, Dictionary<string, byte[]?> Secrets);
    private static string JournalPath => Path.Combine(App.DataFolder, "migration-settings-journal.json");
    public static void CleanTemporaryDatabases()
    {
        static IEnumerable<string> Children(string root) => Directory.Exists(root)
            ? Directory.EnumerateDirectories(root).Where(p => (File.GetAttributes(p) & FileAttributes.ReparsePoint) == 0) : [];
        foreach (var batch in Children(Path.Combine(App.DataFolder, "python-migrations")))
        {
            var archive = Path.Combine(batch, "archive");
            if (Directory.Exists(archive) && (File.GetAttributes(archive) & FileAttributes.ReparsePoint) == 0)
                foreach (var temporary in Children(archive).Where(p => Path.GetFileName(p).StartsWith("reading-", StringComparison.Ordinal)
                    || Path.GetFileName(p).StartsWith("history-", StringComparison.Ordinal))) Directory.Delete(temporary, true);
        }
        foreach (var folder in Children(Path.Combine(App.DataFolder, "imports")))
            if (File.Exists(Path.Combine(folder, "protected-profile-import.marker"))) Directory.Delete(folder, true);
    }
    public static async Task RecoverAsync(Store store)
    {
        if (!File.Exists(JournalPath)) return;
        var journal = JsonSerializer.Deserialize<Journal>(await File.ReadAllTextAsync(JournalPath))
            ?? throw new IOException("Не удалось прочитать журнал переноса настроек.");
        if (await store.ReadDocumentAsync<Guid>("migration-settings-commit") != journal.Id) SecretVault.Restore(journal.Secrets);
        File.Delete(JournalPath);
    }
    public static async Task ApplyAsync(Store store, Dictionary<string, object> documents, Dictionary<string, string> secrets)
    {
        await RecoverAsync(store);
        var journal = new Journal(Guid.NewGuid(), SecretVault.Capture(secrets.Keys));
        documents["migration-settings-commit"] = journal.Id;
        await File.WriteAllTextAsync(JournalPath + ".tmp", JsonSerializer.Serialize(journal));
        File.Move(JournalPath + ".tmp", JournalPath, true);
        await store.SaveMigrationSettingsAsync(documents,
            () => { foreach (var (platform, value) in secrets) SecretVault.Save(value, platform); },
            () => SecretVault.Restore(journal.Secrets));
        File.Delete(JournalPath);
    }
}
