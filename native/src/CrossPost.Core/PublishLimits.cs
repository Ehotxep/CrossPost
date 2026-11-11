namespace CrossPost.Core;

public static class PublishLimits
{
    public const int TelegramText = 4096;
    public const int DiscordText = 2000;
    public const long TelegramImage = 10L * 1024 * 1024;
    public const long DiscordImage = 20L * 1024 * 1024;
    // Reserve room for JSON and multipart headers below Discord's 25 MiB request ceiling.
    public const long DiscordFiles = 24L * 1024 * 1024;
    public const long DiscordRequest = 25L * 1024 * 1024;
    public const long BlueskyImage = 2_000_000;
    public static int Text(string platform) => platform switch { "discord" => DiscordText, "bluesky" => 300, _ => TelegramText };
    public static int Count(string text, string platform) => platform == "bluesky" ? System.Globalization.StringInfo.ParseCombiningCharacters(text).Length : text.Length;
    public static string Name(string platform) => platform switch { "discord" => "Discord", "bluesky" => "Bluesky", _ => "Telegram" };
}
