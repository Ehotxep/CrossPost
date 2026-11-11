using System.Net;

namespace CrossPost.Core;

public record Draft(string Title = "", string Text = "", string? ImagePath = null)
{
    public string PlainText => string.Join("\n\n", new[] { Title.Trim(), Text.Trim() }.Where(x => x.Length > 0));
    public string Html => WebUtility.HtmlEncode(PlainText);
    public void Validate()
    {
        if (string.IsNullOrWhiteSpace(PlainText) && ImagePath is null)
            throw new InvalidOperationException("Добавьте текст или изображение.");
        if (PlainText.Length > 4096)
            throw new InvalidOperationException("Telegram: текст должен содержать не более 4096 символов.");
        if (ImagePath is { } path)
        {
            if (!File.Exists(path)) throw new InvalidOperationException("Выбранное изображение не найдено.");
            if (!new[] { ".jpg", ".jpeg", ".png" }.Contains(Path.GetExtension(path).ToLowerInvariant()))
                throw new InvalidOperationException("Прототип поддерживает одно изображение JPEG или PNG.");
            if (new FileInfo(path).Length > 10 * 1024 * 1024)
                throw new InvalidOperationException("Изображение должно быть не больше 10 МБ.");
        }
    }
}
public enum DeliveryState { Sending, Sent, Refused, Partial, Unknown }
public record Delivery(DeliveryState State, string ChatId, long? EditMessageId = null,
    long? MediaMessageId = null, string Kind = "text", string Error = "", string? Link = null);
public record Publication(Guid Id, DateTimeOffset Created, Draft Draft, bool Demo,
    string BotId, Delivery Delivery, string EditText, int Revision = 1, string EditState = "");
public record AppOptions(bool Demo = true, string Chat = "");
public record EditResult(bool Confirmed, bool Unknown = false, string Error = "");

public interface ITelegram
{
    Task<string> CheckAsync(CancellationToken cancellationToken);
    Task<Delivery> SendAsync(string chat, Draft draft, CancellationToken cancellationToken);
    Task<EditResult> EditAsync(Publication publication, string text, CancellationToken cancellationToken);
}
