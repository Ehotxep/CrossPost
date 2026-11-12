using System.IO;
using System.Security.Cryptography;
using System.Text;

namespace CrossPost.Desktop;

internal static class SecretVault
{
    private static byte[] Entropy(string platform) => Encoding.UTF8.GetBytes(platform switch { "discord" => "CrossPost.Native.Prototype.Discord.v1", "bluesky" => "CrossPost.Native.Prototype.Bluesky.v1", _ when platform.StartsWith("destination:", StringComparison.Ordinal) => "CrossPost.Native.Discord." + platform[12..], _ => "CrossPost.Native.Prototype.Telegram.v1" });
    private static string PathFor(string platform) => Path.Combine(App.DataFolder, platform switch
    { "telegram" => "telegram-token.dpapi", "discord" => "discord-webhook.dpapi", "bluesky" => "bluesky-password.dpapi", _ when platform.StartsWith("destination:", StringComparison.Ordinal) => Path.GetRelativePath(App.DataFolder, DestinationPath("discord", platform[12..])), _ => throw new ArgumentOutOfRangeException(nameof(platform)) });
    public static bool Exists(string platform) => File.Exists(PathFor(platform));
    public static Dictionary<string, byte[]?> Capture(IEnumerable<string> platforms) => platforms.ToDictionary(platform => platform,
        platform => File.Exists(PathFor(platform)) ? File.ReadAllBytes(PathFor(platform)) : null);
    public static void Restore(Dictionary<string, byte[]?> snapshot)
    {
        foreach (var (platform, bytes) in snapshot)
        {
            var path = PathFor(platform);
            if (bytes is null) File.Delete(path);
            else { Directory.CreateDirectory(Path.GetDirectoryName(path)!); File.WriteAllBytes(path + ".tmp", bytes); File.Move(path + ".tmp", path, true); }
        }
    }
    private static string DestinationPath(string platform, string id)
    {
        if (platform != "discord" || !Guid.TryParseExact(id, "N", out _)) throw new ArgumentException("Некорректный идентификатор webhook.");
        return Path.Combine(App.DataFolder, "destination-secrets", id + ".dpapi");
    }
    public static string LoadDestination(string id) => id.Length == 0 ? Load("discord") : Load("destination:" + id);
    public static void DeleteDestination(string id) => File.Delete(DestinationPath("discord", id));
    public static string Load(string platform = "telegram")
    {
        var FilePath = PathFor(platform);
        if (!File.Exists(FilePath)) return "";
        try { return Encoding.UTF8.GetString(ProtectedData.Unprotect(File.ReadAllBytes(FilePath), Entropy(platform), DataProtectionScope.CurrentUser)); }
        catch (CryptographicException) { return ""; }
    }
    public static void Save(string token, string platform = "telegram")
    {
        var FilePath = PathFor(platform);
        Directory.CreateDirectory(Path.GetDirectoryName(FilePath)!);
        if (string.IsNullOrWhiteSpace(token)) { File.Delete(FilePath); return; }
        var bytes = ProtectedData.Protect(Encoding.UTF8.GetBytes(token), Entropy(platform), DataProtectionScope.CurrentUser);
        var temp = FilePath + ".tmp";
        File.WriteAllBytes(temp, bytes);
        File.Move(temp, FilePath, true);
    }
}
