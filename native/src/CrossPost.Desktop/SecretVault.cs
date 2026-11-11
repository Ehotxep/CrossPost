using System.IO;
using System.Security.Cryptography;
using System.Text;

namespace CrossPost.Desktop;

internal static class SecretVault
{
    private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("CrossPost.Native.Prototype.Telegram.v1");
    private static string FilePath => Path.Combine(App.DataFolder, "telegram-token.dpapi");
    public static string Load()
    {
        if (!File.Exists(FilePath)) return "";
        try { return Encoding.UTF8.GetString(ProtectedData.Unprotect(File.ReadAllBytes(FilePath), Entropy, DataProtectionScope.CurrentUser)); }
        catch (CryptographicException) { return ""; }
    }
    public static void Save(string token)
    {
        Directory.CreateDirectory(App.DataFolder);
        if (string.IsNullOrWhiteSpace(token)) { File.Delete(FilePath); return; }
        var bytes = ProtectedData.Protect(Encoding.UTF8.GetBytes(token), Entropy, DataProtectionScope.CurrentUser);
        var temp = FilePath + ".tmp";
        File.WriteAllBytes(temp, bytes);
        File.Move(temp, FilePath, true);
    }
}
