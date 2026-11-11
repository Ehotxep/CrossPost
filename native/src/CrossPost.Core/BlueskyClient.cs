using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace CrossPost.Core;

public record BlueskyAccount(string Did, string Handle, string Pds);
public sealed class BlueskyClient(HttpClient http, string identifier, string appPassword, string expectedDid, string contentLabel = "sfw") : IPublisher
{
    private const string Gateway = "https://bsky.social";
    private sealed class Session(string did, string handle, string pds, string jwt)
    { public string Did { get; } = did; public string Handle { get; } = handle; public string Pds { get; } = pds; public string Jwt { get; } = jwt; }
    private sealed class ApiFailure(string message, bool unknown) : Exception(message) { public bool Unknown { get; } = unknown; }
    public static string NormalizeHandle(string value)
    {
        value = value.Trim().TrimStart('@');
        if (!value.Contains('.') && !value.StartsWith("did:", StringComparison.Ordinal)) value += ".bsky.social";
        if (!IsDid(value) && !Regex.IsMatch(value, @"^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}$"))
            throw new InvalidOperationException("Укажите handle Bluesky, например name.bsky.social.");
        return value;
    }
    public static bool IsDid(string value) => Regex.IsMatch(value, @"^did:(plc:[a-z2-7]{24}|web:[A-Za-z0-9._:%-]+)$");
    private static string Home(JsonElement root)
    {
        if (root.TryGetProperty("didDoc", out var doc) && doc.ValueKind == JsonValueKind.Object && doc.TryGetProperty("service", out var services))
            foreach (var service in services.EnumerateArray())
            {
                if (!service.TryGetProperty("id", out var id) || !id.ToString().EndsWith("#atproto_pds", StringComparison.Ordinal)) continue;
                var value = service.GetProperty("serviceEndpoint").GetString();
                if (!Uri.TryCreate(value, UriKind.Absolute, out var uri) || uri.Scheme != "https" || uri.Port != 443 || uri.UserInfo.Length > 0
                    || uri.Query.Length > 0 || uri.Fragment.Length > 0 || uri.AbsolutePath != "/"
                    || (uri.Host != "bsky.social" && !uri.Host.EndsWith(".host.bsky.network", StringComparison.Ordinal)))
                    throw new InvalidOperationException("Этот PDS пока не поддерживается. В прототипе доступны серверы Bluesky bsky.social / host.bsky.network.");
                return uri.GetLeftPart(UriPartial.Authority);
            }
        return Gateway;
    }
    private static StringContent Json(object value) => new(JsonSerializer.Serialize(value), Encoding.UTF8, "application/json");
    private async Task<JsonElement> CallAsync(string pds, string method, HttpContent body, string? jwt, bool postMutation, CancellationToken ct)
    {
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Post, pds + "/xrpc/" + method) { Content = body };
            if (jwt is not null) request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", jwt);
            using var response = await http.SendAsync(request, ct);
            if (!response.IsSuccessStatusCode)
            {
                var knownRefusal = response.StatusCode is HttpStatusCode.BadRequest or HttpStatusCode.Unauthorized or HttpStatusCode.Forbidden
                    or HttpStatusCode.NotFound or HttpStatusCode.RequestEntityTooLarge or HttpStatusCode.UnsupportedMediaType
                    or HttpStatusCode.UnprocessableEntity or HttpStatusCode.TooManyRequests;
                var transient = false;
                try
                {
                    using var failure = JsonDocument.Parse(await response.Content.ReadAsStringAsync(ct));
                    transient = failure.RootElement.TryGetProperty("error", out var code) && code.ToString() is "UpstreamFailure" or "UpstreamTimeout" or "InternalServerError" or "NotEnoughResources";
                }
                catch (Exception parseError) when (parseError is JsonException or InvalidOperationException) { }
                throw new ApiFailure($"Bluesky: HTTP {(int)response.StatusCode}. Проверьте вход, ограничения и доступность сервера. Автоматического повтора нет.", postMutation && (!knownRefusal || transient));
            }
            using var document = JsonDocument.Parse(await response.Content.ReadAsStringAsync(ct)); return document.RootElement.Clone();
        }
        catch (ApiFailure) { throw; }
        catch (Exception error) when (error is HttpRequestException or IOException or OperationCanceledException or JsonException)
        { throw new ApiFailure(postMutation ? "Bluesky не подтвердил создание поста. Проверьте ссылку перед повтором." : "Подготовка Bluesky не завершена; запись поста не отправлялась.", postMutation); }
    }
    private async Task<Session> LoginAsync(CancellationToken ct)
    {
        var handle = NormalizeHandle(identifier);
        if (string.IsNullOrWhiteSpace(appPassword)) throw new InvalidOperationException("Укажите пароль приложения Bluesky.");
        using var body = Json(new { identifier = handle, password = appPassword });
        try
        {
            var root = await CallAsync(Gateway, "com.atproto.server.createSession", body, null, false, ct);
            var did = root.GetProperty("did").GetString() ?? ""; var jwt = root.GetProperty("accessJwt").GetString() ?? "";
            if (!IsDid(did) || jwt.Length == 0 || jwt.Any(char.IsWhiteSpace)) throw new JsonException();
            return new(did, root.TryGetProperty("handle", out var name) ? name.GetString() ?? handle : handle, Home(root), jwt);
        }
        catch (ApiFailure error) { throw new InvalidOperationException(error.Message); }
        catch (Exception error) when (error is JsonException or KeyNotFoundException or InvalidOperationException or FormatException)
        { throw new InvalidOperationException(error is InvalidOperationException && error.Message.Contains("PDS") ? error.Message : "Bluesky вернул неполные сведения о входе."); }
    }
    public async Task<BlueskyAccount> InspectAsync(CancellationToken ct)
    { var session = await LoginAsync(ct); return new(session.Did, session.Handle, session.Pds); }
    public async Task<string> CheckAsync(CancellationToken ct)
    { var info = await InspectAsync(ct); return $"{info.Handle} • {info.Did}"; }
    public async Task<Delivery> SendAsync(string chat, Draft draft, CancellationToken ct, Func<Delivery, Task>? checkpoint = null)
    {
        draft.Validate("bluesky");
        if (chat != expectedDid || !IsDid(expectedDid)) throw new InvalidOperationException("Не сохранён подтверждённый DID назначения Bluesky.");
        if (contentLabel is not ("sfw" or "erotic" or "explicit")) throw new InvalidOperationException("Выберите рейтинг Bluesky.");
        Session session;
        try { session = await LoginAsync(ct); }
        catch (InvalidOperationException error) { return new(DeliveryState.Refused, chat, Kind: "bluesky", Error: error.Message); }
        if (session.Did != expectedDid) return new(DeliveryState.Refused, chat, Kind: "bluesky", Error: "Выполнен вход в другой аккаунт Bluesky. Пост не отправлялся.");
        var rich = BlueskyText.Build(draft.PlainText); var blobs = new List<object>(); var blobIds = new List<string>();
        try
        {
            for (var index = 0; index < draft.Images.Length; index++)
            {
                ct.ThrowIfCancellationRequested();
                using var file = new StreamContent(File.OpenRead(draft.Images[index]));
                var mime = Path.GetExtension(draft.Images[index]).Equals(".png", StringComparison.OrdinalIgnoreCase) ? "image/png" : "image/jpeg";
                file.Headers.ContentType = new MediaTypeHeaderValue(mime);
                var uploaded = await CallAsync(session.Pds, "com.atproto.repo.uploadBlob", file, session.Jwt, false, ct);
                var blob = uploaded.GetProperty("blob");
                var cid = blob.GetProperty("ref").GetProperty("$link").GetString() ?? "";
                if (!Regex.IsMatch(cid, @"^[A-Za-z0-9]+$") || blob.GetProperty("mimeType").GetString() != mime
                    || blob.GetProperty("size").GetInt64() <= 0 || blob.GetProperty("size").GetInt64() > PublishLimits.BlueskyImage) throw new JsonException();
                blobs.Add(new { alt = draft.AltFor(index), image = blob.Clone() }); blobIds.Add(cid);
            }
        }
        catch (Exception error) when (error is ApiFailure or IOException or UnauthorizedAccessException or OperationCanceledException or JsonException or KeyNotFoundException or InvalidOperationException or FormatException)
        { return new(DeliveryState.Refused, chat, Kind: "bluesky", Error: "Подготовка изображений Bluesky не завершена; пост не создавался. Автоматического повтора нет."); }
        var rkey = Guid.NewGuid().ToString("N"); var uri = $"at://{session.Did}/app.bsky.feed.post/{rkey}";
        var link = $"https://bsky.app/profile/{session.Did}/post/{rkey}";
        var pending = new Delivery(DeliveryState.Sending, session.Did, Kind: "bluesky", Link: link, RemoteMessageId: uri, SentText: rich.Text);
        if (ct.IsCancellationRequested) return pending with { State = DeliveryState.Refused, Error = "Отменено до создания поста Bluesky." };
        if (checkpoint is not null) await checkpoint(pending); // Persist the chosen record URI before the only createRecord call.
        var record = new Dictionary<string, object> { ["$type"] = "app.bsky.feed.post", ["text"] = rich.Text,
            ["createdAt"] = DateTimeOffset.UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'", System.Globalization.CultureInfo.InvariantCulture), ["facets"] = rich.Facets };
        if (blobs.Count > 0) record["embed"] = new Dictionary<string, object> { ["$type"] = "app.bsky.embed.images", ["images"] = blobs.ToArray() };
        if (contentLabel != "sfw") record["labels"] = new Dictionary<string, object> { ["$type"] = "com.atproto.label.defs#selfLabels",
            ["values"] = new[] { new { val = contentLabel == "erotic" ? "nudity" : "porn" } } };
        using var payload = Json(new { repo = session.Did, collection = "app.bsky.feed.post", rkey, record });
        Delivery delivered;
        try
        {
            var result = await CallAsync(session.Pds, "com.atproto.repo.createRecord", payload, session.Jwt, true, ct);
            var cid = result.GetProperty("cid").GetString() ?? "";
            if (result.GetProperty("uri").GetString() != uri || !Regex.IsMatch(cid, @"^[A-Za-z0-9]+$")) throw new JsonException();
            delivered = pending with { State = DeliveryState.Sent, ContentId = cid, AttachmentIds = blobIds.ToArray() };
        }
        catch (ApiFailure error) { return pending with { State = error.Unknown ? DeliveryState.Unknown : DeliveryState.Refused, Error = error.Message }; }
        catch (Exception error) when (error is JsonException or KeyNotFoundException or InvalidOperationException or FormatException)
        { return pending with { State = DeliveryState.Unknown, Error = "Bluesky вернул неполное подтверждение. Проверьте сохранённую ссылку перед повтором." }; }
        if (checkpoint is not null) await checkpoint(delivered); return delivered;
    }
    public Task<EditResult> EditAsync(Publication publication, string text, CancellationToken ct) =>
        Task.FromResult(new EditResult(false, Error: "Bluesky не поддерживает редактирование опубликованных постов. Автоматическая замена с удалением не выполняется."));
}
public sealed class DemoBluesky : IPublisher
{
    public const string Did = "did:plc:aaaaaaaaaaaaaaaaaaaaaaaa";
    public Task<string> CheckAsync(CancellationToken ct) => Task.FromResult("ДЕМО Bluesky");
    public async Task<Delivery> SendAsync(string chat, Draft draft, CancellationToken ct, Func<Delivery, Task>? checkpoint = null)
    {
        draft.Validate("bluesky"); await Task.Delay(300, ct);
        var key = Guid.NewGuid().ToString("N"); var rich = BlueskyText.Build(draft.PlainText);
        var sent = new Delivery(DeliveryState.Sent, Did, Kind: "bluesky", RemoteMessageId: $"at://{Did}/app.bsky.feed.post/{key}",
            SentText: rich.Text, ContentId: "demo", AttachmentIds: draft.Images.Select((_, index) => "demoBlob" + index).ToArray());
        if (checkpoint is not null) await checkpoint(sent); return sent;
    }
    public Task<EditResult> EditAsync(Publication publication, string text, CancellationToken ct) => Task.FromResult(new EditResult(false, Error: "Bluesky не поддерживает редактирование постов."));
}
