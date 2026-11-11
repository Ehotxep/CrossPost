using System.Globalization;
using System.Net;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace CrossPost.Core;

public sealed class TelegramClient(HttpClient http, string token) : ITelegram
{
    private sealed class ApiFailure(string message, bool unknown) : Exception(message)
    { public bool Unknown { get; } = unknown; }
    public static string BotId(string value)
    {
        if (!Regex.IsMatch(value, @"^\d+:[A-Za-z0-9_-]{25,}$"))
            throw new InvalidOperationException("Проверьте токен бота из @BotFather.");
        return value.Split(':')[0];
    }
    private async Task<JsonElement> CallAsync(string method, HttpContent content, CancellationToken ct)
    {
        BotId(token);
        try
        {
            using var response = await http.PostAsync("https://api.telegram.org/bot" + token + "/" + method, content, ct);
            using var document = JsonDocument.Parse(await response.Content.ReadAsStringAsync(ct));
            var root = document.RootElement;
            if ((int)response.StatusCode >= 500)
                throw new ApiFailure("Telegram временно недоступен; результат запроса неизвестен.", true);
            if (!root.GetProperty("ok").GetBoolean())
            {
                var description = root.TryGetProperty("description", out var error) ? error.GetString() : "Telegram отклонил запрос.";
                throw new ApiFailure((description ?? "Ошибка Telegram").Replace(token, "***"), false);
            }
            return root.GetProperty("result").Clone();
        }
        catch (ApiFailure) { throw; }
        catch (Exception e) when (e is HttpRequestException or OperationCanceledException or JsonException or KeyNotFoundException or InvalidOperationException)
        { throw new ApiFailure("Ответ Telegram не подтверждён. Проверьте канал перед повтором.", true); }
    }
    private static FormUrlEncodedContent Form(params (string Key, string Value)[] values) =>
        new(values.Select(value => new KeyValuePair<string, string>(value.Key, value.Value)));
    public async Task<string> CheckAsync(CancellationToken ct)
    {
        try
        {
            using var content = Form();
            var me = await CallAsync("getMe", content, ct);
            return "@" + me.GetProperty("username").GetString();
        }
        catch (ApiFailure error) { throw new InvalidOperationException(error.Message); }
    }
    private static (string Chat, long Id, string? Link) Message(JsonElement message)
    {
        var id = message.GetProperty("message_id").GetInt64();
        var chat = message.GetProperty("chat");
        var chatId = chat.GetProperty("id").GetInt64().ToString(CultureInfo.InvariantCulture);
        var username = chat.TryGetProperty("username", out var name) ? name.GetString() : null;
        var link = username is { Length: > 0 } ? $"https://t.me/{username}/{id}"
            : chatId.StartsWith("-100", StringComparison.Ordinal) ? $"https://t.me/c/{chatId[4..]}/{id}" : null;
        return (chatId, id, link);
    }
    public async Task<Delivery> SendAsync(string chat, Draft draft, CancellationToken ct)
    {
        draft.Validate();
        if (string.IsNullOrWhiteSpace(chat)) throw new InvalidOperationException("Укажите канал Telegram.");
        BotId(token);
        ct.ThrowIfCancellationRequested();
        long? textId = null;
        string resolvedChat = chat;
        string? link = null;
        try
        {
            if (draft.ImagePath is null || draft.PlainText.Length > 1024)
            {
                using var body = Form(("chat_id", chat), ("text", draft.Html), ("parse_mode", "HTML"));
                var message = Message(await CallAsync("sendMessage", body, ct));
                textId = message.Id; resolvedChat = message.Chat; link = message.Link;
                if (draft.ImagePath is null) return new(DeliveryState.Sent, resolvedChat, textId, Kind: "text", Link: link);
            }
            // Once a separate text is confirmed, preserve it even if the photo fails.
            using var image = File.OpenRead(draft.ImagePath!);
            using var photo = new MultipartFormDataContent();
            photo.Add(new StringContent(resolvedChat), "chat_id");
            photo.Add(new StreamContent(image), "photo", Path.GetFileName(draft.ImagePath!));
            if (textId is null)
            {
                photo.Add(new StringContent(draft.Html), "caption");
                photo.Add(new StringContent("HTML"), "parse_mode");
            }
            var posted = Message(await CallAsync("sendPhoto", photo, ct));
            return new(DeliveryState.Sent, posted.Chat, textId ?? posted.Id, posted.Id,
                textId is null ? "caption" : "text", Link: posted.Link);
        }
        catch (ApiFailure error)
        {
            return new(error.Unknown ? DeliveryState.Unknown : textId.HasValue ? DeliveryState.Partial : DeliveryState.Refused,
                resolvedChat, textId, Kind: "text", Error: error.Message, Link: link);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            return new(textId.HasValue ? DeliveryState.Partial : DeliveryState.Refused, resolvedChat, textId,
                Kind: "text", Error: "Не удалось прочитать изображение.", Link: link);
        }
    }
    public async Task<EditResult> EditAsync(Publication publication, string text, CancellationToken ct)
    {
        if (publication.Delivery.EditMessageId is null) throw new InvalidOperationException("Нет подтверждённого сообщения для редактирования.");
        if (publication.BotId != BotId(token)) throw new InvalidOperationException("Пост отправлен другим ботом.");
        var isText = publication.Delivery.Kind == "text";
        if (text.Length > (isText ? 4096 : 1024) || (isText && string.IsNullOrWhiteSpace(text)))
            throw new InvalidOperationException(isText ? "Текст: от 1 до 4096 символов." : "Подпись: до 1024 символов.");
        using var body = Form(("chat_id", publication.Delivery.ChatId),
            ("message_id", publication.Delivery.EditMessageId.Value.ToString(CultureInfo.InvariantCulture)),
            (isText ? "text" : "caption", WebUtility.HtmlEncode(text)), ("parse_mode", "HTML"));
        try
        {
            await CallAsync(isText ? "editMessageText" : "editMessageCaption", body, ct);
            return new(true);
        }
        catch (ApiFailure error) when (!error.Unknown && error.Message.Contains("message is not modified", StringComparison.OrdinalIgnoreCase))
        { return new(true); }
        catch (ApiFailure error) { return new(false, error.Unknown, error.Message); }
    }
}

public sealed class DemoTelegram : ITelegram
{
    private static int _next = 100;
    public Task<string> CheckAsync(CancellationToken ct) => Task.FromResult("@CrossPostDemo");
    public async Task<Delivery> SendAsync(string chat, Draft draft, CancellationToken ct)
    {
        draft.Validate();
        await Task.Delay(600, ct);
        var id = Interlocked.Increment(ref _next);
        return new(DeliveryState.Sent, "demo-channel", id, draft.ImagePath is null ? null : id,
            draft.ImagePath is null || draft.PlainText.Length > 1024 ? "text" : "caption");
    }
    public async Task<EditResult> EditAsync(Publication publication, string text, CancellationToken ct)
    {
        var max = publication.Delivery.Kind == "text" ? 4096 : 1024;
        if (text.Length > max || (max == 4096 && string.IsNullOrWhiteSpace(text)))
            throw new InvalidOperationException($"Ограничение текста: {max} символов.");
        await Task.Delay(300, ct);
        return new(true);
    }
}
