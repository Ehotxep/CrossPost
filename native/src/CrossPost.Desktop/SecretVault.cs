using System.IO;
using System.Security.Cryptography;
using System.Text;

namespace CrossPost.Desktop;

internal static class SecretVault
{
    private static byte[] Entropy(string platform) => Encoding.UTF8.GetBytes(platform switch { "discord" => "CrossPost.Native.Prototype.Discord.v1", "bluesky" => "CrossPost.Native.Prototype.Bluesky.v1", _ => "CrossPost.Native.Prototype.Telegram.v1" });
    private static string PathFor(string platform) => Path.Combine(App.DataFolder, platform switch
    { "telegram" => "telegram-token.dpapi", "discord" => "discord-webhook.dpapi", "bluesky" => "bluesky-password.dpapi", _ => throw new ArgumentOutOfRangeException(nameof(platform)) });
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
        Directory.CreateDirectory(App.DataFolder);
        if (string.IsNullOrWhiteSpace(token)) { File.Delete(FilePath); return; }
        var bytes = ProtectedData.Protect(Encoding.UTF8.GetBytes(token), Entropy(platform), DataProtectionScope.CurrentUser);
        var temp = FilePath + ".tmp";
        File.WriteAllBytes(temp, bytes);
        File.Move(temp, FilePath, true);
    }
}
