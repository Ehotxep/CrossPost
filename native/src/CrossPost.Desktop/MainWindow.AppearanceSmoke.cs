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
        var root = (FrameworkElement)Content;
        var width = (int)Math.Ceiling(root.ActualWidth+root.Margin.Left+root.Margin.Right);
        var height = (int)Math.Ceiling(root.ActualHeight+root.Margin.Top+root.Margin.Bottom);
        var visual = new DrawingVisual();
        using (var drawing = visual.RenderOpen())
        {
            // HWND backgrounds are not guaranteed to participate in RenderTargetBitmap.
            drawing.DrawRectangle(Background,null,new Rect(0,0,width,height));
            drawing.DrawRectangle(new VisualBrush(root) { ViewboxUnits=BrushMappingMode.Absolute, Viewbox=new Rect(0,0,root.ActualWidth,root.ActualHeight) },null,new Rect(root.Margin.Left,root.Margin.Top,root.ActualWidth,root.ActualHeight));
        }
        var bitmap = new RenderTargetBitmap(width,height,96,96,PixelFormats.Pbgra32); bitmap.Render(visual);
        var corner = new byte[4]; bitmap.CopyPixels(new Int32Rect(2,2,1,1),corner,4,0);
        var expected = OpaqueColor(Background);
        if (corner[3]!=255 || corner[0]!=expected.B || corner[1]!=expected.G || corner[2]!=expected.R)
            throw new InvalidOperationException("UI screenshot did not preserve the actual opaque window background");
        if (name=="01-compose-empty-1100x800")
        {
            var sample=PublicationSidebar.TranslatePoint(new Point(-8,40),root);
            bitmap.CopyPixels(new Int32Rect((int)(sample.X+root.Margin.Left),(int)(sample.Y+root.Margin.Top),1,1),corner,4,0);
            if (corner[0]!=expected.B || corner[1]!=expected.G || corner[2]!=expected.R)
                throw new InvalidOperationException("The gap between the editor and publication sidebar has the wrong background");
        }
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
        var typeface=new Typeface(FontFamily,FontStyles.Normal,FontWeights.Normal,FontStretches.Normal);
        if (!typeface.TryGetGlyphTypeface(out var glyph) || !glyph.FamilyNames.Values.Any(name=>name.Contains("Inter",StringComparison.OrdinalIgnoreCase)))
            throw new InvalidOperationException("Embedded Inter font failed to load");
        if (MediaCard.TranslatePoint(new Point(),this).Y>=TextCard.TranslatePoint(new Point(),this).Y
            || PublicationSidebar.TranslatePoint(new Point(),this).X<=ComposeView.TranslatePoint(new Point(),this).X)
            throw new InvalidOperationException("Files-first editor and adjacent publication panel layout changed");
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
        SwitchView(SettingsView);
        foreach (var (category,panel) in new[] { ("telegram",SettingsTelegramPanel), ("discord",SettingsDiscordPanel), ("bluesky",SettingsBlueskyPanel), ("texts",SettingsTextsPanel) })
        {
            SelectSettingsCategory(category); UpdateLayout();
            if (!panel.IsVisible || SettingsGeneralPanel.IsVisible) throw new InvalidOperationException("Settings category navigation failed");
            await CaptureWindowAsync("settings-"+category);
        }
        SelectSettingsCategory("general");
        Width=850; Height=650; SwitchView(ComposeView); await CaptureWindowAsync("05-compose-minimum-850x650");
        var before=SendButton.TranslatePoint(new Point(),this);
        ComposeView.ScrollToEnd(); UpdateLayout(); var after=SendButton.TranslatePoint(new Point(),this);
        if (Math.Abs(before.Y-after.Y)>.1 || before.Y<0 || after.Y+SendButton.ActualHeight>ActualHeight || !SendButton.IsVisible)
            throw new InvalidOperationException("Publish action is clipped or scrolls away at minimum window size");
        if (Grid.GetRow(EnglishEditor)!=1) throw new InvalidOperationException("Bilingual editor did not stack in a narrow window");
        ComposeView.ScrollToTop(); Width=1400; Height=900; await CaptureWindowAsync("06-compose-wide-1400x900");
        Width=1100;Height=800;SwitchView(ComposeView);
    }
    private async Task CapturePopulatedViewsAsync()
    {
        await CaptureWindowAsync("10-compose-with-files");
        SwitchView(LibraryView); await CaptureWindowAsync("07-publications-with-data");
        SwitchView(LibraryStatsView); await CaptureWindowAsync("08-statistics-with-data");
        SwitchView(HistoryView); await CaptureWindowAsync("09-history-with-data");
        SwitchView(ComposeView);
    }
}
