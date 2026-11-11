using System.Text.Json;
namespace CrossPost.Update;

public record UpdateRequest(string InstallDirectory, string DataDirectory, string PackagePath, string Sha256,
    string Version, int ParentId, long ParentStarted, bool ResumeWorker, string Token);
public record UpdateResult(string State, string Message, string? Backup = null);
public static class UpdateTransaction
{
    public static void Validate(UpdateRequest request)
    {
        _ = NativeVersion.Parse(request.Version);
        var install = Path.GetFullPath(request.InstallDirectory).TrimEnd(Path.DirectorySeparatorChar);
        var data = Path.GetFullPath(request.DataDirectory).TrimEnd(Path.DirectorySeparatorChar);
        if (install == Path.GetPathRoot(install) || data.Equals(install, StringComparison.OrdinalIgnoreCase)
            || data.StartsWith(install + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
            || install.StartsWith(data + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
            || !File.Exists(Path.Combine(install, "CrossPost.Native.exe")) || !Guid.TryParseExact(request.Token, "N", out _))
            throw new InvalidOperationException("Папки программы и данных должны быть отдельными.");
        for (var folder = new DirectoryInfo(install); folder is not null; folder = folder.Parent)
            if (folder.Attributes.HasFlag(FileAttributes.ReparsePoint)) throw new InvalidOperationException("Обновление через ссылку на папку не поддерживается.");
    }
    public static async Task WriteResultAsync(string folder, UpdateResult result)
    {
        var path = Path.Combine(folder, "result.json"); var temp = path + ".tmp";
        await File.WriteAllTextAsync(temp, JsonSerializer.Serialize(result));
        for (var attempt = 0; ; attempt++)
        {
            try { File.Move(temp, path, true); return; }
            catch (IOException) when (attempt < 10) { await Task.Delay(50); }
        }
    }
    public static async Task ApplyAsync(UpdateRequest request, string transactionFolder, Func<Task> waitForParent,
        Func<string, Task<bool>> launchAndVerify, Func<Task>? stopCandidate = null)
    {
        Validate(request);
        var parent = Path.GetDirectoryName(Path.GetFullPath(request.InstallDirectory))!;
        var stage = Path.Combine(parent, ".crosspost-stage-" + request.Token);
        var backup = Path.Combine(parent, ".crosspost-previous-" + request.Token);
        var databaseBackup = Path.Combine(transactionFolder, "database-before");
        var oldMoved = false; var newMoved = false; var snapshot = false;
        try
        {
            await WriteResultAsync(transactionFolder, new("preparing", "Проверка пакета"));
            await UpdatePackage.VerifyAsync(request.PackagePath, request.Sha256, request.Version, stage);
            await waitForParent();
            Directory.CreateDirectory(databaseBackup);
            foreach (var name in new[] { "prototype.db", "prototype.db-wal" })
            { var path = Path.Combine(request.DataDirectory, name); if (File.Exists(path)) File.Copy(path, Path.Combine(databaseBackup, name)); }
            snapshot = true;
            await WriteResultAsync(transactionFolder, new("installing", "Замена файлов", backup));
            Directory.Move(request.InstallDirectory, backup); oldMoved = true;
            Directory.Move(stage, request.InstallDirectory); newMoved = true;
            if (!await launchAndVerify(Path.Combine(request.InstallDirectory, "CrossPost.Native.exe")))
                throw new InvalidOperationException("Новая версия не подтвердила готовность. Восстановлена предыдущая версия.");
            await WriteResultAsync(transactionFolder, new("committed", "Обновление установлено", backup));
            var commit = Path.Combine(transactionFolder, "commit");
            await File.WriteAllTextAsync(commit + ".tmp", request.Token); File.Move(commit + ".tmp", commit);
        }
        catch (Exception error)
        {
            if (stopCandidate is not null) await stopCandidate();
            if (newMoved) Directory.Move(request.InstallDirectory, stage);
            if (oldMoved) Directory.Move(backup, request.InstallDirectory);
            if (snapshot)
            {
                foreach (var name in new[] { "prototype.db", "prototype.db-wal", "prototype.db-shm" })
                { var path = Path.Combine(request.DataDirectory, name); if (File.Exists(path)) File.Delete(path); }
                foreach (var path in Directory.GetFiles(databaseBackup)) File.Copy(path, Path.Combine(request.DataDirectory, Path.GetFileName(path)), true);
            }
            await WriteResultAsync(transactionFolder, new("rolled-back", error.Message, oldMoved ? null : backup));
            throw;
        }
        finally { if (Directory.Exists(stage)) { try { Directory.Delete(stage, true); } catch (IOException) { } catch (UnauthorizedAccessException) { } } }
    }
}
