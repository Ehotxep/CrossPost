using System.Net;
using System.Text.Json.Serialization;

namespace CrossPost.Core;

public record Draft(string Title = "", string Text = "", string? ImagePath = null, string[]? AdditionalImagePaths = null)
{
    // Keep ImagePath for compatibility with the first prototype's saved documents.
    [JsonIgnore]
    public string[] Images => (ImagePath is null ? Array.Empty<string>() : new[] { ImagePath })
        .Concat(AdditionalImagePaths ?? []).ToArray();
    public bool SameContent(Draft other) => Title == other.Title && Text == other.Text
        && Images.SequenceEqual(other.Images, StringComparer.OrdinalIgnoreCase);
    public string PlainText => string.Join("\n\n", new[] { Title.Trim(), Text.Trim() }.Where(x => x.Length > 0));
    public string Html => WebUtility.HtmlEncode(PlainText);
    public void Validate()
    {
        if (string.IsNullOrWhiteSpace(PlainText) && Images.Length == 0)
            throw new InvalidOperationException("Добавьте текст или изображение.");
        if (PlainText.Length > 4096)
            throw new InvalidOperationException("Telegram: текст должен содержать не более 4096 символов.");
        if (Images.Length > 10)
            throw new InvalidOperationException("В одном посте поддерживается до 10 изображений.");
        if (Images.Distinct(StringComparer.OrdinalIgnoreCase).Count() != Images.Length)
            throw new InvalidOperationException("Одно изображение добавлено несколько раз.");
        foreach (var path in Images)
        {
            if (!File.Exists(path)) throw new InvalidOperationException("Выбранное изображение не найдено.");
            if (!new[] { ".jpg", ".jpeg", ".png" }.Contains(Path.GetExtension(path).ToLowerInvariant()))
                throw new InvalidOperationException("Поддерживаются изображения JPEG и PNG.");
            if (new FileInfo(path).Length > 10 * 1024 * 1024)
                throw new InvalidOperationException("Изображение должно быть не больше 10 МБ.");
        }
    }
}
public enum DeliveryState { Sending, Sent, Refused, Partial, Unknown }
public record Delivery(DeliveryState State, string ChatId, long? EditMessageId = null,
    long? MediaMessageId = null, string Kind = "text", string Error = "", string? Link = null,
    long[]? MediaMessageIds = null)
{
    [JsonIgnore]
    public long[] AllMediaMessageIds => MediaMessageIds ?? (MediaMessageId is { } id ? [id] : []);
}
public record Publication(Guid Id, DateTimeOffset Created, Draft Draft, bool Demo,
    string BotId, Delivery Delivery, string EditText, int Revision = 1, string EditState = "");
public record AppOptions(bool Demo = true, string Chat = "");
public record EditResult(bool Confirmed, bool Unknown = false, string Error = "");

public interface ITelegram
{
    Task<string> CheckAsync(CancellationToken cancellationToken);
    Task<Delivery> SendAsync(string chat, Draft draft, CancellationToken cancellationToken,
        Func<Delivery, Task>? checkpoint = null);
    Task<EditResult> EditAsync(Publication publication, string text, CancellationToken cancellationToken);
}
