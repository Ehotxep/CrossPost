using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace CrossPost.Core;

public sealed class DiscordEndpoint
{
    public string Id { get; }
    internal Uri Uri { get; }
    private DiscordEndpoint(string id, string token) { Id = id; Uri = new Uri($"https://discord.com/api/v10/webhooks/{id}/{token}"); }
    public static DiscordEndpoint Parse(string value)
    {
        if (!System.Uri.TryCreate(value.Trim(), UriKind.Absolute, out var uri) || uri.Scheme != "https" || uri.Port != 443
            || uri.UserInfo.Length > 0 || uri.Query.Length > 0 || uri.Fragment.Length > 0
            || (uri.Host != "discord.com" && uri.Host != "discordapp.com"))
            throw new InvalidOperationException("Укажите стандартную HTTPS-ссылку webhook Discord без дополнительных параметров.");
        var match = Regex.Match(uri.AbsolutePath, @"^/api(?:/v10)?/webhooks/(\d{1,32})/([A-Za-z0-9_-]{20,200})/?$");
        if (!match.Success) throw new InvalidOperationException("Не распознана ссылка webhook Discord.");
        return new(match.Groups[1].Value, match.Groups[2].Value);
    }
    public override string ToString() => "Discord webhook " + Id; // Never include the credential in diagnostics.
}
public record DiscordWebhookInfo(string Id, string ChannelId, string GuildId, string Name);

public sealed class DiscordClient(HttpClient http, DiscordEndpoint endpoint, string expectedChannel, bool spoiler = false, bool suppressEmbeds = true) : IPublisher
{
    private sealed class DiscordFailure(string message, bool unknown) : Exception(message) { public bool Unknown { get; } = unknown; }
    private static string Id(JsonElement root, string name)
    {
        var value = root.GetProperty(name).ToString();
        if (!Regex.IsMatch(value, @"^[1-9]\d{0,31}$")) throw new JsonException("Invalid Discord identity");
        return value;
    }
    private static bool Refused(HttpStatusCode status) => status is HttpStatusCode.BadRequest or HttpStatusCode.Unauthorized
        or HttpStatusCode.Forbidden or HttpStatusCode.NotFound or HttpStatusCode.RequestEntityTooLarge
        or HttpStatusCode.UnsupportedMediaType or HttpStatusCode.UnprocessableEntity or HttpStatusCode.TooManyRequests;
    private async Task<JsonElement> CallAsync(HttpMethod method, Uri uri, HttpContent? content, bool mutation, CancellationToken ct)
    {
        try
        {
            using var request = new HttpRequestMessage(method, uri) { Content = content };
            using var response = await http.SendAsync(request, ct);
            if (!response.IsSuccessStatusCode)
                throw new DiscordFailure($"Discord: HTTP {(int)response.StatusCode}. " +
                    (response.StatusCode == HttpStatusCode.TooManyRequests ? "Лимит запросов; автоматического повтора нет." : "Проверьте доступ webhook и ограничения канала."),
                    mutation && !Refused(response.StatusCode));
            using var document = JsonDocument.Parse(await response.Content.ReadAsStringAsync(ct));
            return document.RootElement.Clone();
        }
        catch (DiscordFailure) { throw; }
        catch (Exception error) when (error is HttpRequestException or OperationCanceledException or JsonException or IOException)
        { throw new DiscordFailure(mutation ? "Discord не подтвердил результат. Проверьте канал перед повтором." : "Не удалось проверить webhook Discord; сообщение не отправлялось.", mutation); }
    }
    public async Task<DiscordWebhookInfo> InspectAsync(CancellationToken ct)
    {
        try
        {
            var root = await CallAsync(HttpMethod.Get, endpoint.Uri, null, false, ct);
            if (Id(root, "id") != endpoint.Id || (root.TryGetProperty("type", out var type) && type.GetInt32() != 1))
                throw new JsonException();
            var guild = root.TryGetProperty("guild_id", out _) ? Id(root, "guild_id") : "";
            return new(endpoint.Id, Id(root, "channel_id"), guild, root.TryGetProperty("name", out var name) ? name.GetString() ?? "Discord" : "Discord");
        }
        catch (Exception error) when (error is DiscordFailure or JsonException or KeyNotFoundException or InvalidOperationException or FormatException)
        { throw new InvalidOperationException(error is DiscordFailure ? error.Message : "Discord вернул неполные данные webhook."); }
    }
    public async Task<string> CheckAsync(CancellationToken ct)
    { var info = await InspectAsync(ct); return $"{info.Name} • канал {info.ChannelId}"; }
    private async Task<DiscordWebhookInfo> CheckTargetAsync(CancellationToken ct)
    {
        var info = await InspectAsync(ct);
        if (string.IsNullOrWhiteSpace(expectedChannel) || info.ChannelId != expectedChannel)
            throw new InvalidOperationException("Webhook Discord теперь указывает на другой канал. Проверьте настройки; сообщение не отправлялось.");
        return info;
    }
    private static object Mentions => new { parse = Array.Empty<string>(), users = Array.Empty<string>(), roles = Array.Empty<string>(), replied_user = false };
    public async Task<Delivery> SendAsync(string chat, Draft draft, CancellationToken ct, Func<Delivery, Task>? checkpoint = null)
    {
        draft = draft.ForPlatform("discord"); draft.Validate("discord");
        if (chat != expectedChannel) throw new InvalidOperationException("Назначение Discord не совпадает с сохранённым каналом.");
        DiscordWebhookInfo info;
        try { info = await CheckTargetAsync(ct); }
        catch (InvalidOperationException error) { return new(DeliveryState.Refused, chat, Error: error.Message, Kind: "discord"); }
        if (ct.IsCancellationRequested) return new(DeliveryState.Refused, chat, Error: "Отменено до отправки.", Kind: "discord");
        Delivery delivered;
        try
        {
            var names = draft.Images.Select(path => (spoiler ? "SPOILER_" : "") + Path.GetFileName(path)).ToArray();
            using var form = new MultipartFormDataContent();
            var payload = new { content = draft.PlainText, allowed_mentions = Mentions, flags = suppressEmbeds ? 4 : 0,
                attachments = names.Select((name, index) => new { id = index, filename = name }).ToArray() };
            form.Add(new StringContent(JsonSerializer.Serialize(payload), Encoding.UTF8, "application/json"), "payload_json");
            for (var index = 0; index < draft.Images.Length; index++)
            {
                var file = new StreamContent(File.OpenRead(draft.Images[index]));
                file.Headers.ContentType = new MediaTypeHeaderValue(Path.GetExtension(draft.Images[index]).Equals(".png", StringComparison.OrdinalIgnoreCase) ? "image/png" : "image/jpeg");
                form.Add(file, $"files[{index}]", names[index]);
            }
            if (form.Headers.ContentLength is not { } length || length > PublishLimits.DiscordRequest)
                return new(DeliveryState.Refused, chat, Kind: "discord", Error: "Discord: весь запрос должен быть не больше 25 MiB; сообщение не отправлялось.");
            var root = await CallAsync(HttpMethod.Post, new Uri(endpoint.Uri + "?wait=true"), form, true, ct);
            var message = Id(root, "id"); var channel = Id(root, "channel_id");
            if (channel != expectedChannel || (root.TryGetProperty("webhook_id", out _) && Id(root, "webhook_id") != endpoint.Id)) throw new JsonException();
            var attachments = root.TryGetProperty("attachments", out var files) ? files.EnumerateArray().Select(value => Id(value, "id")).ToArray() : [];
            delivered = new(attachments.Length == names.Length ? DeliveryState.Sent : DeliveryState.Partial, channel,
                Kind: "discord", Error: attachments.Length == names.Length ? "" : "Сообщение создано, но не все вложения подтверждены. Проверьте канал.",
                Link: info.GuildId.Length == 0 ? null : $"https://discord.com/channels/{info.GuildId}/{channel}/{message}", RemoteMessageId: message, AttachmentIds: attachments);
        }
        catch (DiscordFailure error) { return new(error.Unknown ? DeliveryState.Unknown : DeliveryState.Refused, chat, Kind: "discord", Error: error.Message); }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        { return new(DeliveryState.Refused, chat, Kind: "discord", Error: "Не удалось прочитать вложения; сообщение не отправлялось."); }
        catch (Exception error) when (error is JsonException or KeyNotFoundException or InvalidOperationException or FormatException)
        { return new(DeliveryState.Unknown, chat, Kind: "discord", Error: "Discord вернул неполное подтверждение. Проверьте канал перед повтором."); }
        if (checkpoint is not null) await checkpoint(delivered);
        return delivered;
    }
    public async Task<EditResult> EditAsync(Publication publication, string text, CancellationToken ct)
    {
        if (publication.Platform != "discord" || publication.BotId != endpoint.Id || publication.Delivery.RemoteMessageId is not { } message
            || !Regex.IsMatch(message, @"^[1-9]\d{0,31}$")) return new(false, Error: "Это сообщение отправлено другим webhook или не имеет подтверждённого ID.");
        if (text.Length > PublishLimits.DiscordText || (string.IsNullOrWhiteSpace(text) && (publication.Delivery.AttachmentIds?.Length ?? 0) == 0))
            return new(false, Error: "Discord: до 2000 символов; пустой текст допустим только с вложениями.");
        if (publication.Delivery.ChatId != expectedChannel) return new(false, Error: "Канал сообщения не совпадает с настройками.");
        try { await CheckTargetAsync(ct); }
        catch (InvalidOperationException error) { return new(false, Error: error.Message); }
        using var body = new StringContent(JsonSerializer.Serialize(new { content = text, allowed_mentions = Mentions,
            flags = (publication.Options?.SuppressEmbeds ?? true) ? 4 : 0 }), Encoding.UTF8, "application/json");
        try
        {
            var root = await CallAsync(HttpMethod.Patch, new Uri(endpoint.Uri + "/messages/" + message), body, true, ct);
            if (Id(root, "id") != message || Id(root, "channel_id") != expectedChannel) throw new JsonException();
            return new(true);
        }
        catch (DiscordFailure error) { return new(false, error.Unknown, error.Message); }
        catch (Exception error) when (error is JsonException or KeyNotFoundException or InvalidOperationException or FormatException)
        { return new(false, true, "Изменение Discord не подтверждено. Проверьте сообщение."); }
    }
}

public sealed class DemoDiscord : IPublisher
{
    private static long _next = 9007199254740993;
    public Task<string> CheckAsync(CancellationToken ct) => Task.FromResult("ДЕМО Discord");
    public async Task<Delivery> SendAsync(string chat, Draft draft, CancellationToken ct, Func<Delivery, Task>? checkpoint = null)
    {
        draft = draft.ForPlatform("discord"); draft.Validate("discord"); await Task.Delay(600, ct);
        var delivered = new Delivery(DeliveryState.Sent, "demo-discord-channel", Kind: "discord", RemoteMessageId: Interlocked.Increment(ref _next).ToString(),
            AttachmentIds: draft.Images.Select(_ => Interlocked.Increment(ref _next).ToString()).ToArray());
        if (checkpoint is not null) await checkpoint(delivered);
        return delivered;
    }
    public async Task<EditResult> EditAsync(Publication publication, string text, CancellationToken ct)
    {
        if (text.Length > PublishLimits.DiscordText || (string.IsNullOrWhiteSpace(text) && (publication.Delivery.AttachmentIds?.Length ?? 0) == 0)) return new(false, Error: "Discord: до 2000 символов.");
        await Task.Delay(200, ct); return new(true);
    }
}
