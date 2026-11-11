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
    public async Task<Delivery> SendAsync(string chat, Draft draft, CancellationToken ct,
        Func<Delivery, Task>? checkpoint = null)
    {
        draft.Validate();
        if (string.IsNullOrWhiteSpace(chat)) throw new InvalidOperationException("Укажите канал Telegram.");
        BotId(token);
        ct.ThrowIfCancellationRequested();
        var images = draft.Images;
        var confirmed = new Delivery(DeliveryState.Sending, chat);
        try
        {
            if (images.Length == 0 || draft.PlainText.Length > 1024)
            {
                using var body = Form(("chat_id", chat), ("text", draft.Html), ("parse_mode", "HTML"));
                var message = Message(await CallAsync("sendMessage", body, ct));
                confirmed = new(images.Length == 0 ? DeliveryState.Sent : DeliveryState.Sending,
                    message.Chat, message.Id, Kind: "text", Link: message.Link);
                if (checkpoint is not null) await checkpoint(confirmed);
                if (images.Length == 0) return confirmed;
            }
            // Every confirmed text is durably checkpointed before uploading any photos.
            ct.ThrowIfCancellationRequested();
            using var photo = new MultipartFormDataContent();
            photo.Add(new StringContent(confirmed.ChatId), "chat_id");
            var separateText = confirmed.EditMessageId.HasValue;
            for (var index = 0; index < images.Length; index++)
            {
                var stream = new StreamContent(File.OpenRead(images[index]));
                photo.Add(stream, images.Length == 1 ? "photo" : "photo" + index,
                    Path.GetFileName(images[index]));
            }
            if (images.Length == 1)
            {
                if (!separateText)
                {
                    photo.Add(new StringContent(draft.Html), "caption");
                    photo.Add(new StringContent("HTML"), "parse_mode");
                }
            }
            else
            {
                var media = images.Select((_, index) =>
                {
                    var item = new Dictionary<string, string>
                    { ["type"] = "photo", ["media"] = "attach://photo" + index };
                    if (index == 0 && !separateText)
                    { item["caption"] = draft.Html; item["parse_mode"] = "HTML"; }
                    return item;
                });
                photo.Add(new StringContent(JsonSerializer.Serialize(media)), "media");
            }
            var response = await CallAsync(images.Length == 1 ? "sendPhoto" : "sendMediaGroup", photo, ct);
            var posted = images.Length == 1 ? new[] { Message(response) }
                : response.EnumerateArray().Select(Message).ToArray();
            if (posted.Length > 0)
                confirmed = new(DeliveryState.Sending, posted[0].Chat, confirmed.EditMessageId ?? posted[0].Id,
                    posted[0].Id, separateText ? "text" : "caption", Link: posted[0].Link,
                    MediaMessageIds: posted.Select(p => p.Id).ToArray());
            if (posted.Length != images.Length || posted.Select(p => p.Id).Distinct().Count() != posted.Length
                || posted.Any(p => p.Chat != posted[0].Chat))
                throw new ApiFailure("Telegram вернул неполное подтверждение альбома. Проверьте канал перед повтором.", true);
            confirmed = confirmed with { State = DeliveryState.Sent };
            if (checkpoint is not null) await checkpoint(confirmed);
            return confirmed;
        }
        catch (ApiFailure error)
        {
            return confirmed with
            {
                State = error.Unknown ? DeliveryState.Unknown : confirmed.EditMessageId.HasValue
                    ? DeliveryState.Partial : DeliveryState.Refused,
                Error = error.Message
            };
        }
        catch (OperationCanceledException)
        {
            // Cancellation between requests cannot undo already confirmed text.
            return confirmed with { State = confirmed.EditMessageId.HasValue ? DeliveryState.Partial : DeliveryState.Refused,
                Error = "Отправка отменена до загрузки медиа." };
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            return confirmed with { State = confirmed.EditMessageId.HasValue ? DeliveryState.Partial : DeliveryState.Refused,
                Error = "Не удалось прочитать изображение." };
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
    public async Task<Delivery> SendAsync(string chat, Draft draft, CancellationToken ct,
        Func<Delivery, Task>? checkpoint = null)
    {
        draft.Validate();
        await Task.Delay(600, ct);
        var separateText = draft.Images.Length == 0 || draft.PlainText.Length > 1024;
        var textId = separateText ? (long?)Interlocked.Increment(ref _next) : null;
        var ids = draft.Images.Select(_ => (long)Interlocked.Increment(ref _next)).ToArray();
        var editId = textId ?? ids[0];
        var delivery = new Delivery(DeliveryState.Sent, "demo-channel", editId,
            ids.Length == 0 ? null : ids[0], separateText ? "text" : "caption", MediaMessageIds: ids);
        if (checkpoint is not null) await checkpoint(delivery);
        return delivery;
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
