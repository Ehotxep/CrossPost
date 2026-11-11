using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace CrossPost.Desktop;
public partial class MainWindow
{
    private static Color OpaqueColor(Brush? brush) => brush is SolidColorBrush { Color.A: 255 } solid ? solid.Color : throw new InvalidOperationException("Expected an opaque UI color");
    private static double Luminance(Color color)
    {
        static double Linear(byte value) { var v = value / 255d; return v <= .04045 ? v / 12.92 : Math.Pow((v + .055) / 1.055, 2.4); }
        return .2126 * Linear(color.R) + .7152 * Linear(color.G) + .0722 * Linear(color.B);
    }
    private static void Readable(Brush? foreground, Brush? background, string element)
    {
        var fg = Luminance(OpaqueColor(foreground)); var bg = Luminance(OpaqueColor(background));
        if ((Math.Max(fg,bg)+.05)/(Math.Min(fg,bg)+.05) < 4.5) throw new InvalidOperationException("Insufficient text contrast: " + element);
    }
    private async Task CaptureWindowAsync(string name)
    {
        await Dispatcher.InvokeAsync(UpdateLayout, DispatcherPriority.Render);
        var bitmap = new RenderTargetBitmap((int)Math.Ceiling(ActualWidth), (int)Math.Ceiling(ActualHeight), 96, 96, PixelFormats.Pbgra32);
        bitmap.Render(this);
        var folder = Path.Combine(App.DataFolder,"screenshots"); Directory.CreateDirectory(folder);
        var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap));
        using var output = File.Create(Path.Combine(folder,name+".png")); encoder.Save(output);
    }
    private async Task RunAppearanceSmokeAsync()
    {
        Width=1100; Height=800; SwitchView(ComposeView);
        await CaptureWindowAsync("01-compose-empty-1100x800");
        if (Style != FindResource("AppWindowStyle") || OpaqueColor(Background) != OpaqueColor((Brush)FindResource("CanvasBrush")) || FontSize < 14)
            throw new InvalidOperationException("MainWindow did not receive its explicit dark window style");
        Readable(Foreground,Background,"window"); Readable(TitleInput.Foreground,TitleInput.Background,"title input");
        Readable(BodyInput.Foreground,BodyInput.Background,"body input");
        Readable(StatusLabel.Foreground,(Brush)FindResource("Panel"),"status footer");
        Readable(PreviewText.Foreground,(Brush)FindResource("Panel"),"preview");
        Readable(TargetTelegram.Foreground,(Brush)FindResource("Panel"),"target checkbox");
        Readable(MainLanguageRu.Foreground,Background,"language radio button");
        Readable(SendButton.Foreground,SendButton.Background,"primary publish action");
        Readable(PlatformPicker.Foreground,PlatformPicker.Background,"platform combo");
        if (PreviewImage.Visibility != Visibility.Collapsed || ImageList.Visibility != Visibility.Collapsed || RemoveImageButton.IsEnabled)
            throw new InvalidOperationException("Empty media still reserves space or exposes active image actions");
        PlatformPicker.IsDropDownOpen=true; UpdateLayout();
        if (PlatformPicker.Template.FindName("PART_Popup",PlatformPicker) is not Popup { Child: Border popup })
            throw new InvalidOperationException("Platform selector popup template is missing");
        Readable(PlatformPicker.Foreground,popup.Background,"platform dropdown");
        PlatformPicker.SelectedIndex=1; PlatformPicker.IsDropDownOpen=false;
        if (CurrentOptions.Platform!="discord") throw new InvalidOperationException("Themed combo stopped changing platform");
        PlatformPicker.SelectedIndex=0;
        foreach (var (view,name) in new[] {((UIElement)QueueView,"02-queue-empty"), (HistoryView,"03-history-empty"), (SettingsView,"04-settings")})
        { SwitchView(view); await CaptureWindowAsync(name); if (ComposeActions.Visibility != Visibility.Collapsed) throw new InvalidOperationException("Publish footer leaked into another screen"); }
        Width=850; Height=650; SwitchView(ComposeView); await CaptureWindowAsync("05-compose-minimum-850x650");
        var before=SendButton.TranslatePoint(new Point(),this);
        ComposeView.ScrollToEnd(); UpdateLayout(); var after=SendButton.TranslatePoint(new Point(),this);
        if (Math.Abs(before.Y-after.Y)>.1 || before.Y<0 || after.Y+SendButton.ActualHeight>ActualHeight || !SendButton.IsVisible)
            throw new InvalidOperationException("Publish action is clipped or scrolls away at minimum window size");
        ComposeView.ScrollToTop(); Width=1400; Height=900; await CaptureWindowAsync("06-compose-wide-1400x900");
        Width=1100;Height=800;SwitchView(ComposeView);
    }
    private async Task CapturePopulatedViewsAsync()
    {
        SwitchView(LibraryView); await CaptureWindowAsync("07-publications-with-data");
        SwitchView(LibraryStatsView); await CaptureWindowAsync("08-statistics-with-data");
        SwitchView(HistoryView); await CaptureWindowAsync("09-history-with-data");
        SwitchView(ComposeView);
    }
}
