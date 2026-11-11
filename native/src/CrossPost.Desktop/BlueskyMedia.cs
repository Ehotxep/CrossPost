using System.IO;
using System.Threading;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using CrossPost.Core;

namespace CrossPost.Desktop;

internal static class BlueskyMedia
{
    public static Task<string[]> PrepareAsync(string[] paths, CancellationToken ct)
    {
        var result = new TaskCompletionSource<string[]>(TaskCreationOptions.RunContinuationsAsynchronously);
        var thread = new Thread(() =>
        {
            var folder = Path.Combine(App.DataFolder, "prepared-media", Guid.NewGuid().ToString("N"));
            try
            {
                Directory.CreateDirectory(folder); var files = new List<string>();
                for (var index = 0; index < paths.Length; index++)
                {
                    ct.ThrowIfCancellationRequested();
                    BitmapSource source;
                    ushort orientation = 1; var decodeWidth = 2048;
                    using (var stream = File.OpenRead(paths[index]))
                    {
                        var decoder = BitmapDecoder.Create(stream, BitmapCreateOptions.DelayCreation, BitmapCacheOption.None);
                        var frame = decoder.Frames[0];
                        decodeWidth = Math.Max(1, (int)(frame.PixelWidth * Math.Min(1d, 2048d / Math.Max(frame.PixelWidth, frame.PixelHeight))));
                        if (frame.Metadata is BitmapMetadata metadata)
                        {
                            try { if (metadata.GetQuery("/app1/ifd/{ushort=274}") is ushort value) orientation = value; }
                            catch (Exception error) when (error is NotSupportedException or ArgumentException or InvalidOperationException) { }
                        }
                    }
                    var image = new BitmapImage(); image.BeginInit(); image.CacheOption = BitmapCacheOption.OnLoad;
                    image.DecodePixelWidth = decodeWidth; image.UriSource = new Uri(Path.GetFullPath(paths[index])); image.EndInit(); image.Freeze(); source = image;
                    var matrix = orientation switch
                    {
                        2 => new Matrix(-1, 0, 0, 1, 0, 0), 3 => new Matrix(-1, 0, 0, -1, 0, 0), 4 => new Matrix(1, 0, 0, -1, 0, 0),
                        5 => new Matrix(0, 1, 1, 0, 0, 0), 6 => new Matrix(0, 1, -1, 0, 0, 0), 7 => new Matrix(0, -1, -1, 0, 0, 0),
                        8 => new Matrix(0, -1, 1, 0, 0, 0), _ => Matrix.Identity
                    };
                    if (!matrix.IsIdentity) { var oriented = new TransformedBitmap(source, new MatrixTransform(matrix)); oriented.Freeze(); source = oriented; }
                    var maximum = 2048;
                    byte[]? bytes = null; var extension = ".png";
                    for (var attempt = 0; attempt < 8 && bytes is null; attempt++)
                    {
                        ct.ThrowIfCancellationRequested();
                        var scale = Math.Min(1d, maximum / (double)Math.Max(source.PixelWidth, source.PixelHeight));
                        var width = Math.Max(1, (int)Math.Round(source.PixelWidth * scale)); var height = Math.Max(1, (int)Math.Round(source.PixelHeight * scale));
                        BitmapSource Resize(bool white)
                        {
                            var visual = new DrawingVisual(); using (var drawing = visual.RenderOpen())
                            {
                                if (white) drawing.DrawRectangle(Brushes.White, null, new Rect(0, 0, width, height));
                                drawing.DrawImage(source, new Rect(0, 0, width, height));
                            }
                            var rendered = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32); rendered.Render(visual); rendered.Freeze(); return rendered;
                        }
                        byte[] Encode(BitmapSource bitmap, bool png, int quality = 85)
                        {
                            BitmapEncoder encoder = png ? new PngBitmapEncoder() : new JpegBitmapEncoder { QualityLevel = quality };
                            encoder.Frames.Add(BitmapFrame.Create(bitmap)); using var output = new MemoryStream(); encoder.Save(output); return output.ToArray();
                        }
                        var png = Encode(Resize(false), true);
                        if (png.Length <= PublishLimits.BlueskyImage) { bytes = png; extension = ".png"; break; }
                        var opaque = Resize(true);
                        foreach (var quality in new[] { 90, 80, 65, 50 })
                        {
                            ct.ThrowIfCancellationRequested(); var jpeg = Encode(opaque, false, quality);
                            if (jpeg.Length <= PublishLimits.BlueskyImage) { bytes = jpeg; extension = ".jpg"; break; }
                        }
                        maximum = (int)(maximum * 0.75);
                    }
                    if (bytes is null) throw new InvalidOperationException("Не удалось подготовить изображение под лимит Bluesky.");
                    var file = Path.Combine(folder, index + extension); File.WriteAllBytes(file, bytes); files.Add(file);
                }
                ct.ThrowIfCancellationRequested(); result.SetResult(files.ToArray());
            }
            catch (Exception error)
            {
                try { if (Directory.Exists(folder)) Directory.Delete(folder, true); } catch (Exception cleanup) when (cleanup is IOException or UnauthorizedAccessException) { }
                if (error is OperationCanceledException) result.SetCanceled(ct); else result.SetException(error);
            }
        }) { IsBackground = true, Name = "Bluesky media preparation" };
        thread.SetApartmentState(ApartmentState.STA); thread.Start(); return result.Task;
    }
}
