using System.Text.Json;
using Microsoft.Data.Sqlite;

namespace CrossPost.Core;

public record MigrationDestination(string Id, string Name) { public override string ToString() => Name; }
public record MigrationItem(string Key, string Kind, string Name, Draft Draft, string[] Warnings)
{
    public override string ToString() => $"{Kind}: {Name}";
}
public record MigrationRecord(string Archive, string Source, MigrationItem[] Items, string[] Notes);
public record MigrationSettings(AppOptions Telegram, string TelegramToken, MigrationDestination[] Channels,
    DiscordOptions Discord, string[] Webhooks, MigrationDestination[] DiscordDestinations, BlueskyOptions Bluesky);

public static class PythonProfile
{
    private static JsonElement Property(JsonElement value, string key) => value.ValueKind == JsonValueKind.Object && value.TryGetProperty(key, out var item) ? item : default;
    private static string Text(JsonElement value, string key) => Property(value, key) is var item && item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : item.ValueKind == JsonValueKind.Number ? item.GetRawText() : "";
    private static bool Flag(JsonElement value, string key, bool fallback = false) => Property(value, key) is var item && item.ValueKind is JsonValueKind.True or JsonValueKind.False ? item.GetBoolean() : fallback;
    private static JsonElement[] Array(JsonElement value) => value.ValueKind == JsonValueKind.Array ? value.EnumerateArray().ToArray() : [];
    private static async Task<JsonElement> JsonAsync(EncryptedProfileArchive archive, string path, CancellationToken ct)
    {
        if (!archive.Files.Any(f => f.Path == path)) return default;
        using var document = JsonDocument.Parse(await archive.ReadAsync(path, ct: ct));
        return document.RootElement.Clone();
    }
    public static async Task<MigrationSettings> SettingsAsync(EncryptedProfileArchive archive, CancellationToken ct = default)
    {
        var settings = await JsonAsync(archive, "publish.json", ct);
        var telegram = Property(settings, "telegram"); var discord = Property(settings, "discord"); var bluesky = Property(settings, "bluesky");
        var channels = Array(Property(telegram, "channels")).Where(c => Text(c, "id").Length > 0)
            .Select(c => new MigrationDestination(Text(c, "id"), Text(c, "title") + " · " + Text(c, "id"))).ToList();
        var group = Property(telegram, "group");
        if (Text(group, "id").Length > 0) channels.Add(new(Text(group, "id"), Text(group, "title") + " · " + Text(group, "id") + " (темы требуют настройки)"));
        var hooks = Array(Property(discord, "webhooks"));
        var urls = hooks.Select(h => Text(h, "url")).ToArray();
        var destinations = hooks.Select((h, i) => new MigrationDestination(i.ToString(), Text(h, "name") is { Length: > 0 } name ? name : $"Webhook {i + 1}")).ToArray();
        // Python OAuth/JWT sessions are preserved in the archive, never treated as app passwords.
        return new(new(Demo: true, Spoiler: Flag(telegram, "spoiler_nsfw", true)), Text(telegram, "bot_token"), channels.ToArray(),
            new(Demo: true, Spoiler: Flag(discord, "blur_nsfw", true), SuppressEmbeds: Flag(discord, "no_previews", true)),
            urls, destinations, new(Demo: true, Handle: Text(bluesky, "handle")));
    }
    public static async Task<MigrationRecord> PrepareAsync(EncryptedProfileArchive archive, string source, string mediaFolder, CancellationToken ct = default)
    {
        var items = new List<MigrationItem>(); var notes = new List<string>();
        var publish = await JsonAsync(archive, "publish.json", ct);
        var ui = await JsonAsync(archive, "ui.json", ct);
        var media = new Dictionary<string, string>(StringComparer.Ordinal);
        async Task Add(string key, string kind, string name, JsonElement raw)
        {
            if (raw.ValueKind != JsonValueKind.Object) { notes.Add("Не удалось разобрать один из черновиков; исходник сохранён в защищённой копии."); return; }
            var warnings = new List<string>(); var images = new List<string>(); var alts = new List<string>();
            foreach (var file in Array(Property(raw, "files")))
            {
                var uid = file.ValueKind == JsonValueKind.String ? file.GetString() ?? "" : Text(file, "id");
                if (uid.Length != 16 || !uid.All(char.IsAsciiHexDigit)) { warnings.Add("Не распознан идентификатор вложения."); continue; }
                var candidates = archive.Files.Where(f => f.Path.StartsWith("uploads/" + uid + "/", StringComparison.Ordinal)
                    && !f.Path[("uploads/" + uid + "/").Length..].Contains('/')).ToArray();
                if (candidates.Length != 1) { warnings.Add("Исходное вложение отсутствует или неоднозначно: " + uid); continue; }
                var relative = candidates[0].Path; var extension = Path.GetExtension(relative).ToLowerInvariant();
                if (extension is not (".png" or ".jpg" or ".jpeg")) { warnings.Add("GIF, видео или другой формат сохранён в копии; редактор пока принимает JPEG/PNG."); continue; }
                if (!media.TryGetValue(uid, out var target))
                {
                    target = Path.Combine(mediaFolder, uid + extension);
                    await archive.CopyAsync(relative, target, ct); media[uid] = target;
                }
                if (!images.Contains(target)) { images.Add(target); alts.Add(Text(Property(raw, "alts"), uid)); }
            }
            var english = Property(Property(raw, "variants"), "en");
            var platformTexts = new Dictionary<string, PlatformText>();
            foreach (var platform in new[] { "telegram", "discord", "bluesky" })
            {
                var custom = Property(Property(raw, "platform_texts"), platform);
                var language = Flag(Property(publish, "modules"), "translation") ? Text(Property(publish, "text_lang"), platform) switch { "en" => "en", "both" => "both", _ => "ru" } : "ru";
                var enabled = Flag(custom, "enabled") && Flag(Property(publish, "platform_texts_enabled"), platform);
                var fallback = language == "en" ? english : raw;
                var customTitle = Property(custom, "title").ValueKind == JsonValueKind.String ? Text(custom, "title") : Text(fallback, "title");
                var text = Property(custom, "text").ValueKind == JsonValueKind.String ? Text(custom, "text") : Text(fallback, "text");
                platformTexts[platform] = language == "en"
                    ? new(enabled, EnglishTitle: customTitle, EnglishText: text, Language: language, Initialized: custom.ValueKind == JsonValueKind.Object)
                    : new(enabled, customTitle, text, Language: language, Initialized: custom.ValueKind == JsonValueKind.Object);
            }
            var label = Text(raw, "rating") switch { "erotic" or "q" or "questionable" => "erotic", "explicit" or "e" or "nsfw" => "explicit", _ => "sfw" };
            warnings.Add("Перед отправкой проверьте подпись, ссылки, хештеги, язык и площадки: профили, подписи и правила Python пока автоматически не применяются.");
            if (kind == "Расписание" || kind == "Архив расписания") warnings.Add(kind == "Архив расписания" ? "Это архивное задание: оно уже могло быть отправлено. Проверьте историю и площадку перед повтором; в рабочую очередь оно не добавлено." : "Задание на паузе для проверки. В рабочую очередь не добавлено; назначьте время заново после проверки.");
            var draft = new Draft(Text(raw, "title"), Text(raw, "text"), images.FirstOrDefault(), images.Skip(1).ToArray(), alts.ToArray(), label,
                Text(english, "title"), Text(english, "text"), PlatformTexts: platformTexts);
            items.Add(new(key, kind, string.IsNullOrWhiteSpace(name) ? draft.Title is { Length: > 0 } title ? title : "Без названия" : name, draft, warnings.Distinct().ToArray()));
        }
        var current = Property(ui, "draft");
        if (current.ValueKind == JsonValueKind.Object) await Add("current", "Текущий черновик", "", current);
        var templates = await JsonAsync(archive, "templates.json", ct);
        foreach (var template in Array(Property(templates, "items")))
            await Add("template:" + items.Count, "Шаблон", Text(template, "name"), Property(template, "draft").ValueKind == JsonValueKind.Object ? Property(template, "draft") : template);
        if (archive.Files.Any(f => f.Path == "posts.db"))
        {
            var temporary = Path.Combine(archive.Folder, "reading-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(temporary);
            try
            {
                var database = Path.Combine(temporary, "posts.db");
                foreach (var name in new[] { "posts.db", "posts.db-wal" })
                    if (archive.Files.Any(f => f.Path == name)) await archive.CopyAsync(name, Path.Combine(temporary, name), ct);
                using var connection = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = database, Mode = SqliteOpenMode.ReadOnly, Pooling = false }.ToString());
                await connection.OpenAsync(ct);
                foreach (var (table, kind) in new[] { ("saved_drafts", "Черновик"), ("scheduled", "Расписание") })
                {
                    using var schema = connection.CreateCommand(); schema.CommandText = "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=$name"; schema.Parameters.AddWithValue("$name", table);
                    if (Convert.ToInt32(await schema.ExecuteScalarAsync(ct)) == 0) continue;
                    using var command = connection.CreateCommand(); command.CommandText = "SELECT * FROM " + table;
                    using var reader = await command.ExecuteReaderAsync(ct);
                    var columns = Enumerable.Range(0, reader.FieldCount).Select(reader.GetName).ToHashSet();
                    if (!columns.Contains("draft") || !columns.Contains("id")) { notes.Add("Неизвестная структура " + kind + "; данные доступны в защищённой копии."); continue; }
                    while (await reader.ReadAsync(ct))
                    {
                        try
                        {
                            using var json = JsonDocument.Parse(reader["draft"].ToString() ?? "{}");
                            var name = columns.Contains("name") ? reader["name"].ToString() ?? "" : Text(json.RootElement, "title");
                            var itemKind = kind;
                            if (table == "scheduled")
                            {
                                var state = columns.Contains("state") ? reader["state"].ToString() ?? "" : "неизвестно";
                                var due = columns.Contains("run_at") ? reader["run_at"].ToString() ?? "" : "";
                                name += " · " + due + " · " + state;
                                if (state is "done" or "completed" or "cancelled" or "failed" or "partial" or "unknown") itemKind = "Архив расписания";
                            }
                            await Add(table + ":" + reader["id"], itemKind, name, json.RootElement);
                        }
                        catch (JsonException) { notes.Add("Повреждённый черновик сохранён только в защищённой копии."); }
                    }
                }
            }
            finally { Directory.Delete(temporary, true); }
        }
        notes.Add("Исходная папка целиком сохранена в зашифрованной копии. Ключ доступен только текущему пользователю Windows на этом ПК.");
        notes.Add("API-ключи остальных площадок, сессии, правила теггера и неизвестные настройки сохранены в копии. Они ещё не активируются в C#; браузерные сессии могут потребовать повторного входа.");
        notes.Add("Внешние папки моделей и экспортов за пределами выбранного профиля не копируются. Настройки старой папки приложения при необходимости перенесите отдельно.");
        return new(archive.Folder, source, items.ToArray(), notes.ToArray());
    }
}
