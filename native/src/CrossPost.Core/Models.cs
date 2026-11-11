using System.Net;
using System.Text.Json.Serialization;

namespace CrossPost.Core;

public record Draft(string Title = "", string Text = "", string? ImagePath = null, string[]? AdditionalImagePaths = null, string[]? AltTexts = null, string ContentLabel = "sfw")
{
    // Keep ImagePath for compatibility with the first prototype's saved documents.
    [JsonIgnore]
    public string[] Images => (ImagePath is null ? Array.Empty<string>() : new[] { ImagePath })
        .Concat(AdditionalImagePaths ?? []).ToArray();
    public bool SameContent(Draft other) => Title == other.Title && Text == other.Text
        && Images.SequenceEqual(other.Images, StringComparer.OrdinalIgnoreCase) && Enumerable.Range(0, Images.Length).All(index => AltFor(index) == other.AltFor(index)) && ContentLabel == other.ContentLabel;
    public string AltFor(int index) => AltTexts is { } descriptions && index >= 0 && index < descriptions.Length ? descriptions[index] ?? "" : "";
    public string PlainText => string.Join("\n\n", new[] { Title.Trim(), Text.Trim() }.Where(x => x.Length > 0));
    public string Html => WebUtility.HtmlEncode(PlainText);
    public void Validate(string platform = "telegram", bool allowImagePreparation = false)
    {
        if (platform is not ("telegram" or "discord" or "bluesky")) throw new InvalidOperationException("Эта площадка ещё не поддерживается.");
        var textLimit = PublishLimits.Text(platform);
        var content = platform == "bluesky" ? BlueskyText.Build(PlainText).Text : PlainText;
        var imageLimit = platform == "bluesky" ? (allowImagePreparation ? PublishLimits.DiscordImage : PublishLimits.BlueskyImage)
            : platform == "discord" ? PublishLimits.DiscordImage : PublishLimits.TelegramImage;
        if (string.IsNullOrWhiteSpace(PlainText) && Images.Length == 0)
            throw new InvalidOperationException("Добавьте текст или изображение.");
        if (PublishLimits.Count(content, platform) > textLimit)
            throw new InvalidOperationException($"{PublishLimits.Name(platform)}: текст должен содержать не более {textLimit} символов. Сократите текст перед отправкой.");
        if (platform == "bluesky" && System.Text.Encoding.UTF8.GetByteCount(content) > 3000)
            throw new InvalidOperationException("Bluesky: текст превышает лимит 3000 байт UTF-8, даже если графем не больше 300.");
        var maxImages = platform == "bluesky" ? 4 : 10;
        if (Images.Length > maxImages) throw new InvalidOperationException($"{PublishLimits.Name(platform)}: до {maxImages} изображений в одном посте.");
        if (platform == "bluesky" && (AltTexts ?? []).Any(alt => PublishLimits.Count(alt ?? "", "bluesky") > 2000))
            throw new InvalidOperationException("Bluesky: описание изображения должно быть не длиннее 2000 графем.");
        if (Images.Distinct(StringComparer.OrdinalIgnoreCase).Count() != Images.Length)
            throw new InvalidOperationException("Одно изображение добавлено несколько раз.");
        long totalBytes = 0;
        foreach (var path in Images)
        {
            if (!File.Exists(path)) throw new InvalidOperationException("Выбранное изображение не найдено.");
            if (!new[] { ".jpg", ".jpeg", ".png" }.Contains(Path.GetExtension(path).ToLowerInvariant()))
                throw new InvalidOperationException("Поддерживаются изображения JPEG и PNG.");
            var size = new FileInfo(path).Length;
            if (size > imageLimit)
                throw new InvalidOperationException(platform == "bluesky" ? "Bluesky: подготовьте копии изображений до 2 000 000 байт кнопкой в редакторе." : $"Изображение должно быть не больше {imageLimit / 1024 / 1024} MiB для выбранной площадки.");
            totalBytes += size;
        }
        if (platform == "discord" && totalBytes > PublishLimits.DiscordFiles)
            throw new InvalidOperationException("Discord: суммарный размер файлов должен быть не больше 24 MiB. Уменьшите изображения или число вложений.");
    }
}
public enum DeliveryState { Sending, Sent, Refused, Partial, Unknown }
public record Delivery(DeliveryState State, string ChatId, long? EditMessageId = null,
    long? MediaMessageId = null, string Kind = "text", string Error = "", string? Link = null,
    long[]? MediaMessageIds = null, string? RemoteMessageId = null, string[]? AttachmentIds = null, string? SentText = null, string? ContentId = null)
{
    [JsonIgnore]
    public long[] AllMediaMessageIds => MediaMessageIds ?? (MediaMessageId is { } id ? [id] : []);
}
public record Publication(Guid Id, DateTimeOffset Created, Draft Draft, bool Demo,
    string BotId, Delivery Delivery, string EditText, int Revision = 1, string EditState = "", Guid? QueueId = null, string Platform = "telegram", AppOptions? Options = null);
public record AppOptions(bool Demo = true, string Chat = "", string Platform = "telegram", bool Spoiler = false, bool SuppressEmbeds = true, string ContentLabel = "sfw");
public record BlueskyOptions(bool Demo = true, string Handle = "", string Did = "", string Pds = "", string ContentLabel = "sfw")
{
    public AppOptions PublishOptions => new(Demo, Did, "bluesky", ContentLabel: ContentLabel);
}
public record DiscordOptions(bool Demo = true, string WebhookId = "", string ChannelId = "", string GuildId = "", string Name = "Discord",
    bool Spoiler = false, bool SuppressEmbeds = true)
{
    public AppOptions PublishOptions => new(Demo, ChannelId, "discord", Spoiler, SuppressEmbeds);
}
public record EditResult(bool Confirmed, bool Unknown = false, string Error = "");

public interface IPublisher
{
    Task<string> CheckAsync(CancellationToken cancellationToken);
    Task<Delivery> SendAsync(string chat, Draft draft, CancellationToken cancellationToken,
        Func<Delivery, Task>? checkpoint = null);
    Task<EditResult> EditAsync(Publication publication, string text, CancellationToken cancellationToken);
}
