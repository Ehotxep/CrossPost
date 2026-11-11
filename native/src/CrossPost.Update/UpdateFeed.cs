using System.Net;
using System.Text.Json;

namespace CrossPost.Update;
public record NativeRelease(string Version, string Tag, string Notes, Uri Archive, Uri Checksum, long Size);
public sealed class UpdateFeed(HttpClient http)
{
    public const string Repository = "Ehotxep/CrossPost";
    public const string ReleasesUrl = "https://api.github.com/repos/" + Repository + "/releases?per_page=100";
    public static Uri Asset(string url, string tag, string name)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) || uri.Scheme != "https" || uri.Host != "github.com"
            || uri.Port != 443 || uri.UserInfo.Length > 0 || uri.Query.Length > 0 || uri.Fragment.Length > 0
            || uri.AbsolutePath != $"/{Repository}/releases/download/{Uri.EscapeDataString(tag)}/{Uri.EscapeDataString(name)}")
            throw new InvalidOperationException("Ссылка обновления не принадлежит нативным выпускам CrossPost.");
        return uri;
    }
    public static NativeRelease? Select(string json, string current, bool preview)
    {
        var installed = NativeVersion.Parse(current); using var document = JsonDocument.Parse(json);
        var choices = new List<NativeRelease>();
        foreach (var release in document.RootElement.EnumerateArray())
        {
            if (release.GetProperty("draft").GetBoolean() || (!preview && release.GetProperty("prerelease").GetBoolean())) continue;
            var tag = release.GetProperty("tag_name").GetString() ?? "";
            if (!tag.StartsWith("native-v", StringComparison.Ordinal)) continue;
            var version = tag[8..]; NativeVersion parsed;
            try { parsed = NativeVersion.Parse(version); } catch (Exception e) when (e is FormatException or OverflowException) { continue; }
            if ((!preview && parsed.Pre.Length > 0) || parsed.CompareTo(installed) <= 0) continue;
            var name = $"CrossPost-Native-Prototype-{version}-win-x64.zip";
            var assets = release.GetProperty("assets").EnumerateArray().ToArray();
            var archive = assets.FirstOrDefault(asset => asset.GetProperty("name").GetString() == name);
            var checksum = assets.FirstOrDefault(asset => asset.GetProperty("name").GetString() == name + ".sha256");
            if (archive.ValueKind == JsonValueKind.Undefined || checksum.ValueKind == JsonValueKind.Undefined) continue;
            var size = archive.GetProperty("size").GetInt64(); if (size is <= 0 or > UpdatePackage.MaxArchive) continue;
            choices.Add(new(version, tag, release.TryGetProperty("body", out var notes) ? notes.GetString() ?? "" : "",
                Asset(archive.GetProperty("browser_download_url").GetString()!, tag, name),
                Asset(checksum.GetProperty("browser_download_url").GetString()!, tag, name + ".sha256"), size));
        }
        return choices.OrderByDescending(release => NativeVersion.Parse(release.Version)).FirstOrDefault();
    }
    public async Task<NativeRelease?> CheckAsync(string current, bool preview, CancellationToken ct)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, ReleasesUrl);
        request.Headers.UserAgent.ParseAdd("CrossPost-Native/" + current); request.Headers.Accept.ParseAdd("application/vnd.github+json");
        using var response = await http.SendAsync(request, ct); response.EnsureSuccessStatusCode();
        var text = await response.Content.ReadAsStringAsync(ct); if (text.Length > 8 * 1024 * 1024) throw new InvalidOperationException("Слишком большой ответ обновлений.");
        return Select(text, current, preview);
    }
    private async Task<HttpResponseMessage> DownloadResponseAsync(Uri uri, CancellationToken ct)
    {
        for (var count = 0; count < 6; count++)
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, uri); request.Headers.UserAgent.ParseAdd("CrossPost-Native-Updater");
            var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, ct);
            if (response.StatusCode is HttpStatusCode.MovedPermanently or HttpStatusCode.Redirect or HttpStatusCode.TemporaryRedirect or HttpStatusCode.PermanentRedirect or HttpStatusCode.SeeOther)
            {
                var next = response.Headers.Location; response.Dispose();
                if (next is null) throw new InvalidOperationException("Нет адреса скачивания.");
                uri = new Uri(uri, next);
                if (uri.Scheme != "https" || uri.Port != 443 || uri.UserInfo.Length > 0
                    || uri.Host is not ("github.com" or "release-assets.githubusercontent.com" or "objects.githubusercontent.com"))
                    throw new InvalidOperationException("Недопустимое перенаправление обновления.");
                continue;
            }
            try { response.EnsureSuccessStatusCode(); return response; } catch { response.Dispose(); throw; }
        }
        throw new InvalidOperationException("Слишком много перенаправлений скачивания.");
    }
    public async Task<(string Path, string Sha256)> DownloadAsync(NativeRelease release, string folder, IProgress<double>? progress, CancellationToken ct)
    {
        Directory.CreateDirectory(folder); var path = Path.Combine(folder, "package.zip");
        try
        {
            string sum;
            using (var checksum = await DownloadResponseAsync(release.Checksum, ct))
            {
                using var input = await checksum.Content.ReadAsStreamAsync(ct); var bytes = new byte[1025]; var count = 0; int read;
                while (count < bytes.Length && (read = await input.ReadAsync(bytes.AsMemory(count), ct)) > 0) count += read;
                if (count > 1024) throw new InvalidOperationException("Неверная контрольная сумма.");
                sum = System.Text.Encoding.UTF8.GetString(bytes, 0, count).Trim().TrimStart('\uFEFF');
            }
            using var response = await DownloadResponseAsync(release.Archive, ct);
            await using (var input = await response.Content.ReadAsStreamAsync(ct))
            await using (var output = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None, 81920, true))
            {
                var buffer = new byte[81920]; long count = 0; int read;
                while ((read = await input.ReadAsync(buffer, ct)) > 0)
                {
                    count += read; if (count > release.Size || count > UpdatePackage.MaxArchive) throw new InvalidOperationException("Размер скачивания превышен.");
                    await output.WriteAsync(buffer.AsMemory(0, read), ct); progress?.Report(100d * count / release.Size);
                }
                if (count != release.Size) throw new InvalidOperationException("Пакет скачан не полностью.");
            }
            await Task.Run(() => UpdatePackage.VerifyAsync(path, sum, release.Version, ct: ct), ct); return (path, sum);
        }
        catch { if (File.Exists(path)) File.Delete(path); throw; }
    }
}
