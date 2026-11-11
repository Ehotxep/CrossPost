using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace CrossPost.Update;

public record PackageFile(string Path, long Length, string Sha256);
public record PackageManifest(string Product, string Version, string Rid, PackageFile[] Files);
public static class UpdatePackage
{
    public const string ManifestName = "native-package.json";
    public const long MaxArchive = 512L * 1024 * 1024;
    public const long MaxExpanded = 1536L * 1024 * 1024;
    public static bool SafePath(string path) => path.Length is > 0 and < 220 && !path.Contains('\\')
        && !path.Contains(':') && !path.StartsWith('/') && path.Split('/').All(part => part.Length > 0 && part is not "." and not ".."
            && !part.EndsWith('.') && !part.EndsWith(' ') && !part.Any(c => c < 32 || "<>\"|?*".Contains(c))
            && !Regex.IsMatch(part, @"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)", RegexOptions.IgnoreCase));
    public static async Task<string> HashAsync(string path, CancellationToken ct = default)
    { await using var stream = File.OpenRead(path); return Convert.ToHexString(await SHA256.HashDataAsync(stream, ct)); }
    public static async Task<PackageManifest> VerifyAsync(string archive, string sha256, string version, string? extractTo = null, CancellationToken ct = default)
    {
        if (!Regex.IsMatch(sha256, "^[a-fA-F0-9]{64}$") || new FileInfo(archive).Length > MaxArchive
            || !string.Equals(await HashAsync(archive, ct), sha256, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("Контрольная сумма пакета не совпадает.");
        using var zip = ZipFile.OpenRead(archive);
        if (zip.Entries.Count is < 2 or > 3000) throw new InvalidOperationException("Неверный состав пакета.");
        var entries = new Dictionary<string, ZipArchiveEntry>(StringComparer.OrdinalIgnoreCase);
        foreach (var entry in zip.Entries)
        {
            if (!SafePath(entry.FullName) || ((entry.ExternalAttributes >> 16) & 0xF000) == 0xA000
                || (entry.ExternalAttributes & 0x400) != 0 || !entries.TryAdd(entry.FullName, entry))
                throw new InvalidOperationException("Небезопасный или повторяющийся путь в пакете.");
        }
        if (!entries.TryGetValue(ManifestName, out var manifestEntry) || manifestEntry.Length > 1024 * 1024)
            throw new InvalidOperationException("Нет манифеста нативной версии.");
        PackageManifest manifest;
        using (var input = manifestEntry.Open()) manifest = await JsonSerializer.DeserializeAsync<PackageManifest>(input, cancellationToken: ct) ?? throw new InvalidOperationException("Пустой манифест.");
        if (manifest.Product != "CrossPost.Native" || manifest.Rid != "win-x64" || manifest.Version != version || manifest.Files is null
            || manifest.Files.Length != entries.Count - 1 || manifest.Files.Select(file => file.Path).Distinct(StringComparer.OrdinalIgnoreCase).Count() != manifest.Files.Length
            || !manifest.Files.Any(file => file.Path == "CrossPost.Native.exe") || !manifest.Files.Any(file => file.Path == "CrossPost.Updater.exe"))
            throw new InvalidOperationException("Пакет не соответствует выбранной версии CrossPost Native x64.");
        long expanded = 0;
        foreach (var file in manifest.Files)
        {
            ct.ThrowIfCancellationRequested();
            if (!SafePath(file.Path) || file.Path.Equals(ManifestName, StringComparison.OrdinalIgnoreCase) || file.Length < 0
                || file.Length > MaxExpanded || (expanded += file.Length) > MaxExpanded || !Regex.IsMatch(file.Sha256, "^[a-fA-F0-9]{64}$")
                || !entries.TryGetValue(file.Path, out var entry) || entry.Length != file.Length)
                throw new InvalidOperationException("Состав или размер файлов пакета не совпадает.");
            using var input = entry.Open();
            using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
            Stream? output = null;
            try
            {
                if (extractTo is not null)
                {
                    var target = Path.Combine(extractTo, file.Path.Replace('/', Path.DirectorySeparatorChar));
                    Directory.CreateDirectory(Path.GetDirectoryName(target)!); output = new FileStream(target, FileMode.CreateNew, FileAccess.Write, FileShare.None);
                }
                var buffer = new byte[81920]; long count = 0; int read;
                while ((read = await input.ReadAsync(buffer, ct)) > 0)
                {
                    count += read; if (count > file.Length) throw new InvalidOperationException("Размер распакованного файла превышен.");
                    hash.AppendData(buffer, 0, read); if (output is not null) await output.WriteAsync(buffer.AsMemory(0, read), ct);
                }
                if (count != file.Length || !Convert.ToHexString(hash.GetHashAndReset()).Equals(file.Sha256, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Повреждён файл пакета: " + file.Path);
            }
            finally { if (output is not null) await output.DisposeAsync(); }
        }
        if (extractTo is not null) await File.WriteAllTextAsync(Path.Combine(extractTo, ManifestName), JsonSerializer.Serialize(manifest), ct);
        return manifest;
    }
}
public static class UpdateGate
{
    public static FileStream Acquire(string folder)
    { Directory.CreateDirectory(folder); return new(Path.Combine(folder, "update-install.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None); }
    public static bool IsHeld(string folder)
    { try { using var gate = Acquire(folder); return false; } catch (IOException) { return true; } }
}
