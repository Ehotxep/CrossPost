using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using CrossPost.Core;
using Microsoft.Data.Sqlite;

namespace CrossPost.Desktop;
public partial class MainWindow
{
    private static IEnumerable<T> DesignChildren<T>(DependencyObject parent) where T:DependencyObject
    {
        for(var i=0;i<VisualTreeHelper.GetChildrenCount(parent);i++)
        {
            var child=VisualTreeHelper.GetChild(parent,i);if(child is T match) yield return match;
            foreach(var descendant in DesignChildren<T>(child)) yield return descendant;
        }
    }
    private async Task RunPythonDesignSmokeAsync()
    {
        var original=CurrentDraft;var selectedPlatforms=SelectedPlatformNames();var originalExtras=_extraSelected.ToArray();
        _draftTimer.Stop();
        var paths=new string[2];
        // Generated local fixtures: no user artwork or credentials in published screenshots.
        for(var i=0;i<paths.Length;i++)
        {
            paths[i]=Path.Combine(App.DataFolder,"design-landscape-"+i+".png");
            var visual=new DrawingVisual();
            using(var drawing=visual.RenderOpen())
            {
                drawing.DrawRectangle(new LinearGradientBrush(i==0?Color.FromRgb(244,158,131):Color.FromRgb(77,142,174),Color.FromRgb(55,63,108),90),null,new Rect(0,0,320,240));
                drawing.DrawEllipse(new SolidColorBrush(Color.FromRgb(255,217,148)),null,new Point(235,70),27,27);
                var mountains=Geometry.Parse("M0,240 L0,183 L72,91 L143,177 L219,112 L320,210 L320,240 Z");
                drawing.DrawGeometry(new SolidColorBrush(i==0?Color.FromRgb(76,59,93):Color.FromRgb(39,79,101)),null,mountains);
                drawing.DrawGeometry(new SolidColorBrush(Color.FromRgb(35,46,67)),null,Geometry.Parse("M0,240 L0,213 L110,164 L180,214 L265,176 L320,208 L320,240 Z"));
            }
            var image=new RenderTargetBitmap(320,240,96,96,PixelFormats.Pbgra32);image.Render(visual);
            var encoder=new PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(image));using var file=File.Create(paths[i]);encoder.Save(file);
        }
        var source=Path.Combine(App.DataFolder,"design-measurements.db");
        using(var database=new SqliteConnection(new SqliteConnectionStringBuilder { DataSource=source,Pooling=false }.ToString()))
        {
            await database.OpenAsync();using var command=database.CreateCommand();
            command.CommandText="CREATE TABLE posts(id INTEGER PRIMARY KEY,platform TEXT,chat_id TEXT,msg_id INTEGER,date_utc TEXT,text TEXT,views INTEGER,reactions INTEGER,updated_at TEXT);";await command.ExecuteNonQueryAsync();
            for(var i=0;i<2;i++)
            {
                var date=new DateTimeOffset(new DateTime(DateTime.Now.Year,DateTime.Now.Month,5+i*7,15,20,0,DateTimeKind.Local));
                var chat="-100980"+i;var title=i==0?"Демо · Закат над горами":"Демо · Тихий вечер";
                var draft=new Draft(title,"Проверка оформления",paths[i]);
                var id=Guid.NewGuid();await _store.SavePublicationAsync(new(id,date,draft,false,"ui-fixture",new(DeliveryState.Sent,chat,900+i,900+i,"caption"),draft.PlainText,Options:new(false,chat),WorkId:id));
                command.CommandText="INSERT INTO posts VALUES($id,'telegram',$chat,$message,$at,$text,$views,$reactions,$at)";
                command.Parameters.Clear();command.Parameters.AddWithValue("$id",i+1);command.Parameters.AddWithValue("$chat",chat);command.Parameters.AddWithValue("$message",900+i);command.Parameters.AddWithValue("$at",date.ToUniversalTime().ToString("O"));
                command.Parameters.AddWithValue("$text",draft.PlainText);command.Parameters.AddWithValue("$views",60+i*180);command.Parameters.AddWithValue("$reactions",4+i*8);await command.ExecuteNonQueryAsync();
            }
        }
        await new LegacyImporter(_store).ImportAsync(source);
        try
        {
            _loaded=false;
            TitleInput.Text="Закат над горами";BodyInput.Text="Последние лучи солнца и тихий вечер.";
            _images.Clear();_images.AddRange(paths);_alts.Clear();_alts.AddRange(["Горы на закате","Вечерний пейзаж"]);RefreshImages();
            var design=CurrentDraft with { EnglishTitle="Sunset in the mountains",EnglishText="The last rays of sunlight and a quiet evening.",Language="both",PlatformTexts=null };
            await InitializeTextsAsync(design);BlueskyRating.SelectedIndex=0;_loaded=true;PlatformPicker.SelectedIndex=0;UpdatePreview();ApplyMode();
            Width=1400;Height=1000;SwitchView(ComposeView);ComposeView.ScrollToTop();await CaptureWindowAsync("11-compose-python-design");
            var actionBottom=ComposeActions.TranslatePoint(new Point(),PublicationSidebar).Y+ComposeActions.ActualHeight;
            if(actionBottom>PublicationSidebar.ActualHeight-12)throw new InvalidOperationException("Destination list pushed publication actions outside the sidebar");
            var host=TitleInput.Template.FindName("PART_ContentHost",TitleInput) as ScrollViewer ?? throw new InvalidOperationException("Title editor content host missing");
            var glyph=TitleInput.GetRectFromCharacterIndex(0);var hostTop=host.TranslatePoint(new Point(),TitleInput).Y;
            if(TitleInput.Text.Length==0 || glyph.IsEmpty || glyph.Height<TitleInput.FontSize*.7 || glyph.Y<hostTop-.5 || glyph.Bottom>hostTop+host.ActualHeight+.5)
                throw new InvalidOperationException($"Title glyph is clipped: glyph={glyph}, host top={hostTop}, height={host.ActualHeight}");
            var ru=TitleInput.TranslatePoint(new Point(),TextCard).Y;var en=EnglishTitleInput.TranslatePoint(new Point(),TextCard).Y;
            if(Grid.GetRow(EnglishEditor)==0)
            { if(Math.Abs(ru-en)>1.5) throw new InvalidOperationException($"Bilingual columns not aligned: RU={ru}, EN={en}, card width={TextCard.ActualWidth}"); }
            else if(TextCard.ActualWidth>=660 || en<=BodyInput.TranslatePoint(new Point(),TextCard).Y+BodyInput.ActualHeight)
                throw new InvalidOperationException("Narrow bilingual editor overlaps or remains stacked at a wide width");
            RatingExplicit.IsChecked=true;
            if(BlueskyRating.SelectedIndex!=2 || CurrentDraft.ContentLabel!="explicit") throw new InvalidOperationException("Segmented content rating did not update routing");
            RatingSafe.IsChecked=true;
            _libraryMonth=new(DateTime.Now.Year,DateTime.Now.Month,1);_libraryDay=null;await LoadLibraryAsync();SwitchView(LibraryView);LibraryScroll.ScrollToTop();UpdateLayout();
            var fixture=WorkList.Items.Cast<WorkRow>().Single(r=>r.Title=="Демо · Закат над горами");
            if(fixture.Thumbnail is null || fixture.ViewsLabel!="Просмотры: 60" || !CalendarDays.Items.Cast<CalendarCell>().Any(c=>c.Works.Any(w=>w.Id==fixture.Id))) throw new InvalidOperationException("Calendar preview or measured publication metrics missing");
            await CaptureWindowAsync("12-publications-python-design");
            var tile=DesignChildren<Button>(CalendarDays).FirstOrDefault(b=>b.Tag is Guid id && id==fixture.Id) ?? throw new InvalidOperationException("Calendar publication tile is not interactive");
            tile.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            if(WorkList.SelectedItem is not WorkRow selected || selected.Id!=fixture.Id || WorkInspector.Visibility!=Visibility.Visible || !WorkDetails.Text.Contains("60")) throw new InvalidOperationException("Calendar tile did not open its publication details");
            await CaptureWindowAsync("13-publication-details");
            CloseWorkInspector(this,new RoutedEventArgs());ClearLibraryDay(this,new RoutedEventArgs());
            var rows=WorkList.Items.Cast<WorkRow>().Take(2).ToArray();foreach(var row in rows)WorkList.SelectedItems.Add(row);
            if(!JoinWorksButton.IsEnabled)throw new InvalidOperationException("Card list lost multi-selection merge action");
            WorkList.SelectedItems.Clear();Width=850;Height=650;UpdateLayout();LibraryScroll.ScrollToTop();
            if(CalendarDays.ActualWidth>LibraryColumns.ActualWidth || CalendarDays.ActualWidth<500)throw new InvalidOperationException("Calendar is clipped at the minimum window size");
            await CaptureWindowAsync("14-publications-minimum");
            File.WriteAllText(Path.Combine(App.DataFolder,"python-design-smoke-ready.txt"),"Aligned bilingual editor, rating segments, full calendar, lazy thumbnails, metric cards and selection checked");
        }
        finally
        {
            _loaded=false;TitleInput.Text=original.Title;BodyInput.Text=original.Text;_images.Clear();_images.AddRange(original.Images);_alts.Clear();_alts.AddRange(Enumerable.Range(0,original.Images.Length).Select(original.AltFor));RefreshImages();
            await InitializeTextsAsync(original);BlueskyRating.SelectedIndex=original.ContentLabel switch { "explicit"=>2,"erotic"=>1,_=>0 };_loaded=true;
            TargetTelegram.IsChecked=selectedPlatforms.Contains("telegram");TargetDiscord.IsChecked=selectedPlatforms.Contains("discord");TargetBluesky.IsChecked=selectedPlatforms.Contains("bluesky");_extraSelected=originalExtras.ToHashSet();RefreshDestinations();
            Width=1100;Height=800;SwitchView(ComposeView);ComposeView.ScrollToTop();UpdatePreview();ApplyMode();await SaveDraftAsync();
        }
    }
}
