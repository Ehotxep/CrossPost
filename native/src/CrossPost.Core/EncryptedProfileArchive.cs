using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace CrossPost.Core;

public record ProfileFile(string Path, string Id, long Length);

// File names and contents are encrypted. Only the platform-specific wrapper sees the archive key.
public sealed class EncryptedProfileArchive : IDisposable
{
    private const int ChunkSize = 1024 * 1024;
    private readonly byte[] _key;
    public string Folder { get; }
    public ProfileFile[] Files { get; }
    public string Source { get; }
    private record Manifest(string Source, ProfileFile[] Files);
    private EncryptedProfileArchive(string folder, byte[] key, ProfileFile[] files, string source)
    { Folder = folder; _key = key; Files = files; Source = source; }

    public static async Task<EncryptedProfileArchive> CreateAsync(string source, string folder,
        Func<byte[], byte[]> wrapKey, CancellationToken ct = default, IProgress<string>? progress = null)
    {
        source = Path.GetFullPath(source); folder = Path.GetFullPath(folder);
        if (IsInside(folder, source) || IsInside(source, folder))
            throw new InvalidOperationException("Папки источника и архива не должны содержать друг друга.");
        var inventory = Inventory(source, ct);
        if (Directory.Exists(folder)) throw new IOException("Папка архива уже существует.");
        Directory.CreateDirectory(folder);
        var key = RandomNumberGenerator.GetBytes(32);
        try
        {
            await File.WriteAllBytesAsync(Path.Combine(folder, "key.dpapi"), wrapKey(key), ct);
            var files = new List<ProfileFile>();
            for (var index = 0; index < inventory.Length; index++)
            {
                ct.ThrowIfCancellationRequested();
                var item = inventory[index]; var id = index.ToString("D8");
                progress?.Report($"Защищённая копия: {index + 1} / {inventory.Length}");
                using var input = new FileStream(item.Full, FileMode.Open, FileAccess.Read, FileShare.Read);
                using var output = File.Create(Path.Combine(folder, id + ".bin"));
                await TransformAsync(input, output, key, id, true, ct);
                files.Add(new(item.Relative, id, item.Length));
            }
            // Refuse a moving profile, including new/deleted files. Never change the source.
            if (!inventory.SequenceEqual(Inventory(source, ct)))
                throw new IOException("Данные Python изменились во время копирования. Закройте Python-версию и её браузеры, затем повторите.");
            using var manifest = new MemoryStream(JsonSerializer.SerializeToUtf8Bytes(new Manifest(source, files.ToArray())));
            using (var output = File.Create(Path.Combine(folder, "manifest.bin")))
                await TransformAsync(manifest, output, key, "manifest", true, ct);
            return new(folder, key, files.ToArray(), source);
        }
        catch { CryptographicOperations.ZeroMemory(key); Directory.Delete(folder, true); throw; }
    }
    private record SourceFile(string Full, string Relative, long Length, DateTime Modified);
    private static SourceFile[] Inventory(string root, CancellationToken ct)
    {
        var files = new List<SourceFile>();
        void Visit(string path)
        {
            ct.ThrowIfCancellationRequested();
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("Профиль содержит ссылку или junction. Выберите папку без перенаправлений.");
            if (Directory.Exists(path)) foreach (var entry in Directory.EnumerateFileSystemEntries(path).Order(StringComparer.Ordinal)) Visit(entry);
            else
            {
                var info = new FileInfo(path);
                files.Add(new(path, Path.GetRelativePath(root, path).Replace('\\', '/'), info.Length, info.LastWriteTimeUtc));
            }
        }
        Visit(root); return files.ToArray();
    }
    internal static bool IsInside(string path, string root) => path.Equals(root, StringComparison.OrdinalIgnoreCase)
        || path.StartsWith(Path.TrimEndingDirectorySeparator(root) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);

    public static async Task<EncryptedProfileArchive> OpenAsync(string folder, Func<byte[], byte[]> unwrapKey, CancellationToken ct = default)
    {
        var key = unwrapKey(await File.ReadAllBytesAsync(Path.Combine(folder, "key.dpapi"), ct));
        try
        {
            using var input = File.OpenRead(Path.Combine(folder, "manifest.bin"));
            using var output = new MemoryStream();
            await TransformAsync(input, output, key, "manifest", false, ct, 32 * 1024 * 1024);
            var manifest = JsonSerializer.Deserialize<Manifest>(output.ToArray()) ?? throw new IOException("Нет списка файлов архива.");
            var files = manifest.Files;
            if (files.Any(f => !SafePath(f.Path) || f.Id.Length != 8 || !f.Id.All(char.IsAsciiDigit) || f.Length < 0)
                || files.Select(f => f.Path).Distinct(StringComparer.OrdinalIgnoreCase).Count() != files.Length
                || files.Select(f => f.Id).Distinct().Count() != files.Length) throw new IOException("Некорректный список файлов архива.");
            return new(folder, key, files, manifest.Source);
        }
        catch { CryptographicOperations.ZeroMemory(key); throw; }
    }
    private static bool SafePath(string path) => !string.IsNullOrWhiteSpace(path) && !Path.IsPathRooted(path)
        && !path.Contains('\\') && !path.Contains(':') && path.Split('/').All(p => p.Length > 0 && p is not ("." or ".."));

    public async Task<byte[]> ReadAsync(string relative, int limit = 8 * 1024 * 1024, CancellationToken ct = default)
    {
        var file = Files.SingleOrDefault(f => f.Path == relative) ?? throw new FileNotFoundException("Файл отсутствует в копии профиля.");
        using var input = File.OpenRead(Path.Combine(Folder, file.Id + ".bin"));
        using var output = new MemoryStream();
        await TransformAsync(input, output, _key, file.Id, false, ct, limit);
        if (output.Length != file.Length) throw new IOException("Размер файла не совпадает со списком архива.");
        return output.ToArray();
    }
    public async Task CopyAsync(string relative, string destination, CancellationToken ct = default)
    {
        var file = Files.Single(f => f.Path == relative);
        var temporary = destination + "." + Guid.NewGuid().ToString("N") + ".tmp";
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(destination))!);
        try
        {
            using (var input = File.OpenRead(Path.Combine(Folder, file.Id + ".bin")))
            using (var output = File.Create(temporary))
            {
                await TransformAsync(input, output, _key, file.Id, false, ct);
                if (output.Length != file.Length) throw new IOException("Размер файла не совпадает со списком архива.");
            }
            File.Move(temporary, destination, false);
        }
        finally { File.Delete(temporary); }
    }
    public async Task RestoreAsync(string destination, CancellationToken ct = default)
    {
        destination = Path.GetFullPath(destination);
        if (Directory.Exists(destination) || File.Exists(destination)) throw new IOException("Для восстановления нужна новая папка.");
        Directory.CreateDirectory(destination);
        try
        {
            foreach (var file in Files)
            {
                var path = Path.GetFullPath(Path.Combine(destination, file.Path.Replace('/', Path.DirectorySeparatorChar)));
                if (!SafePath(file.Path) || !IsInside(path, destination)) throw new IOException("Недопустимый путь в архиве.");
                await CopyAsync(file.Path, path, ct);
            }
        }
        catch { Directory.Delete(destination, true); throw; }
    }
    private static async Task TransformAsync(Stream input, Stream output, byte[] key, string id, bool encrypt,
        CancellationToken ct, long limit = long.MaxValue)
    {
        var header = new byte[8];
        if (encrypt) { BinaryPrimitives.WriteInt64LittleEndian(header, input.Length); await output.WriteAsync(header, ct); }
        else await input.ReadExactlyAsync(header, ct);
        var length = BinaryPrimitives.ReadInt64LittleEndian(header);
        if (length < 0 || length > limit) throw new IOException("Файл превышает допустимый размер.");
        using var aes = new AesGcm(key, 16);
        var plain = new byte[ChunkSize]; var cipher = new byte[ChunkSize];
        var nonce = new byte[12]; var tag = new byte[16];
        try
        {
            // Even empty files have an authenticated block. Length/index/id are authenticated too.
            long offset = 0, index = 0;
            do
            {
                ct.ThrowIfCancellationRequested(); var count = (int)Math.Min(ChunkSize, length - offset);
                var associated = Encoding.UTF8.GetBytes($"CrossPost.Profile.v1|{id}|{length}|{index}");
                if (encrypt)
                {
                    await input.ReadExactlyAsync(plain.AsMemory(0, count), ct); RandomNumberGenerator.Fill(nonce);
                    aes.Encrypt(nonce, plain.AsSpan(0, count), cipher.AsSpan(0, count), tag, associated);
                    await output.WriteAsync(nonce, ct); await output.WriteAsync(tag, ct); await output.WriteAsync(cipher.AsMemory(0, count), ct);
                }
                else
                {
                    await input.ReadExactlyAsync(nonce, ct); await input.ReadExactlyAsync(tag, ct);
                    await input.ReadExactlyAsync(cipher.AsMemory(0, count), ct);
                    aes.Decrypt(nonce, cipher.AsSpan(0, count), tag, plain.AsSpan(0, count), associated);
                    await output.WriteAsync(plain.AsMemory(0, count), ct);
                }
                offset += count; index++;
            } while (offset < length);
            if (input.ReadByte() != -1) throw new IOException("Лишние данные после конца файла.");
        }
        finally { CryptographicOperations.ZeroMemory(plain); }
    }
    public void Dispose() => CryptographicOperations.ZeroMemory(_key);
}
