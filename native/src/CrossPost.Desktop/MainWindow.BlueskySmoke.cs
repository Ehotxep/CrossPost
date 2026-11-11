using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using CrossPost.Core;

namespace CrossPost.Desktop;

public partial class MainWindow
{
    private async Task RunBlueskySmokeAsync()
    {
        var original = CurrentDraft;
        var telegram = SecretVault.Load(); var discord = SecretVault.Load("discord");
        const string password = "isolated-bluesky-app-password";
        SecretVault.Save(password, "bluesky");
        if (SecretVault.Load("bluesky") != password || SecretVault.Load() != telegram || SecretVault.Load("discord") != discord
            || Encoding.UTF8.GetString(await File.ReadAllBytesAsync(Path.Combine(App.DataFolder, "bluesky-password.dpapi"))).Contains(password))
            throw new InvalidOperationException("Bluesky credential protection/isolation failed");
        PlatformPicker.SelectedIndex = 2; UpdateLayout();
        TitleInput.Text = "Тест Bluesky"; BodyInput.Text = "[ссылка](https://example.com) #арт";
        if (!ModeLabel.Text.Contains("Bluesky") || !CountLabel.Text.Contains("300") || PreviewText.Text.Contains("](https"))
            throw new InvalidOperationException("Bluesky WPF selection or rich text preview failed");
        var noisy = Path.Combine(App.DataFolder, "large-alpha.png");
        var rotated = Path.Combine(App.DataFolder, "orientation.jpg");
        var pixels = new byte[1024 * 1024 * 4]; new Random(123).NextBytes(pixels);
        for (var y = 0; y < 128; y++) for (var x = 0; x < 128; x++) pixels[(y * 1024 + x) * 4 + 3] = 0;
        var bitmap = BitmapSource.Create(1024, 1024, 96, 96, PixelFormats.Bgra32, null, pixels, 1024 * 4);
        var png = new PngBitmapEncoder(); png.Frames.Add(BitmapFrame.Create(bitmap));
        using (var stream = File.Create(noisy)) png.Save(stream);
        if (new FileInfo(noisy).Length <= PublishLimits.BlueskyImage) throw new InvalidOperationException("Oversize smoke fixture is too small");
        var small = BitmapSource.Create(16, 8, 96, 96, PixelFormats.Bgra32, null, new byte[16 * 8 * 4], 16 * 4);
        var metadata = new BitmapMetadata("jpg"); metadata.SetQuery("/app1/ifd/{ushort=274}", (ushort)6);
        var jpeg = new JpegBitmapEncoder(); jpeg.Frames.Add(BitmapFrame.Create(small, null, metadata, null));
        using (var stream = File.Create(rotated)) jpeg.Save(stream);
        var hash = SHA256.HashData(await File.ReadAllBytesAsync(noisy));
        _images.Clear(); _images.AddRange([noisy, rotated]); _alts.Clear(); RefreshImages();
        ImageAltInput.Text = "Прозрачность"; ImageList.SelectedIndex = 1; ImageAltInput.Text = "Поворот";
        await MoveImage(-1); BlueskyRating.SelectedIndex = 2;
        await PrepareBlueskyDraftAsync(CancellationToken.None);
        var prepared = CurrentDraft;
        prepared.Validate("bluesky");
        var originalBytes = await File.ReadAllBytesAsync(noisy);
        if (prepared.AltFor(0) != "Поворот" || prepared.AltFor(1) != "Прозрачность" || prepared.ContentLabel != "explicit"
            || PreviewImage.Source is null || !hash.SequenceEqual(SHA256.HashData(originalBytes)))
            throw new InvalidOperationException("Prepared media lost ALT/order/label, changed original or failed preview");
        BitmapFrame Read(string path)
        {
            using var stream = File.OpenRead(path);
            return BitmapDecoder.Create(stream, BitmapCreateOptions.None, BitmapCacheOption.OnLoad).Frames[0];
        }
        var transparentFiles = await BlueskyMedia.PrepareAsync([original.Images[0]], CancellationToken.None);
        var transparent = new FormatConvertedBitmap(Read(transparentFiles[0]), PixelFormats.Bgra32, null, 0);
        var alpha = new byte[4]; transparent.CopyPixels(new System.Windows.Int32Rect(0, 0, 1, 1), alpha, 4, 0);
        if (transparent.PixelWidth != 2 || transparent.PixelHeight != 2 || alpha[3] != 0)
            throw new InvalidOperationException("Small PNG transparency or no-upscale preservation failed");
        var oriented = Read(prepared.Images[0]); var opaque = Read(prepared.Images[1]);
        if (oriented.PixelWidth != 8 || oriented.PixelHeight != 16 || opaque.PixelWidth != 1024 || opaque.PixelHeight != 1024)
            throw new InvalidOperationException("EXIF orientation or non-upscaling image preparation failed");
        var rgb = new FormatConvertedBitmap(opaque, PixelFormats.Bgra32, null, 0);
        var corner = new byte[4]; rgb.CopyPixels(new System.Windows.Int32Rect(10, 10, 1, 1), corner, 4, 0);
        if (Path.GetExtension(prepared.Images[1]) != ".jpg" || corner.Take(3).Any(value => value < 245))
            throw new InvalidOperationException("JPEG compression failed to put transparency on white");
        var saved = await _store.ReadDocumentAsync<Draft>("draft");
        if (saved is null || !saved.SameContent(prepared)) throw new InvalidOperationException("Bluesky draft persistence failed");
        var sent = await new Workflow(_store).SendAsync(new DemoBluesky(), DemoBluesky.Did,
            CurrentOptions with { Chat = DemoBluesky.Did }, prepared, CancellationToken.None);
        await LoadHistoryAsync(); HistoryList.SelectedItem = History.Single(row => row.Record.Id == sent.Id);
        if (EditButton.IsEnabled || !EditInput.IsReadOnly || _selected?.Platform != "bluesky"
            || (await _store.LibraryAsync()).Posts.Single(post => post.NativeId == sent.Id).Platform != "demo.bluesky")
            throw new InvalidOperationException("Bluesky history/edit guard or library projection failed");
        await WorkerProcess.EnsureStartedAsync();
        var queued = await new QueuePlanner(_store).AddAsync(prepared,
            new(Demo: true, Chat: DemoBluesky.Did, Platform: "bluesky", ContentLabel: "explicit"), DemoBluesky.Did, DateTimeOffset.UtcNow.AddSeconds(20));
        await _store.SaveDocumentAsync("bluesky-worker-smoke-id", queued.Id);
        TitleInput.Text = original.Title; BodyInput.Text = original.Text;
        _images.Clear(); _images.AddRange(original.Images); _alts.Clear(); _alts.AddRange(original.AltTexts ?? []);
        BlueskyRating.SelectedIndex = 0; RefreshImages(); PlatformPicker.SelectedIndex = 0; ApplyMode(); UpdatePreview(); await SaveDraftAsync();
    }
}
