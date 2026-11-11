using System.Text.Json;
using CrossPost.Core;
using Microsoft.Data.Sqlite;
internal static class TextChecks
{
    private static void Check(bool value, string message) { if (!value) throw new Exception(message); }
    private static Draft Source() => new("Русское название", "Русское описание", EnglishTitle: "English title", EnglishText: "English description");
    public static async Task RunAsync(Func<string, Func<Task>, Task> run)
    {
        await run("Russian, English and bilingual rendering preserve complete blocks and are idempotent", () =>
        {
            var source = Source();
            Check(source.ForPlatform("telegram").PlainText == "Русское название\n\nРусское описание", "Default Russian changed");
            Check((source with { Language = "en" }).ForPlatform("discord").PlainText == "English title\n\nEnglish description", "English contains Russian or lost text");
            var both = (source with { Language = "both" }).ForPlatform("telegram");
            Check(both.PlainText == "Русское название\n\nРусское описание\n\nEnglish title\n\nEnglish description", "Bilingual order changed");
            Check(both.ForPlatform("telegram").SameContent(both) && both.PlatformTexts is null, "Resolving twice duplicates text");
            return Task.CompletedTask;
        });
        await run("per-platform language and optional custom texts resolve independently without fallback", () =>
        {
            var source = Source() with { PlatformTexts = new() { ["telegram"] = new(Language:"ru"), ["discord"] = new(Language:"both"),
                ["bluesky"] = new(true,"Другой заголовок","Другой текст","Short title","Short body","en") } };
            Check(source.ForPlatform("telegram").PlainText == Source().PlainText && source.ForPlatform("discord").PlainText.Contains("English description")
                && source.ForPlatform("bluesky").PlainText == "Short title\n\nShort body", "Per-platform routing failed");
            source.PlatformTexts["bluesky"] = source.PlatformTexts["bluesky"] with { Enabled = false };
            Check(source.ForPlatform("bluesky").PlainText == "English title\n\nEnglish description", "Disabled custom text was sent");
            return Task.CompletedTask;
        });
        await run("missing requested translations and invalid language fail before queue persistence", async () =>
        {
            var folder = Path.Combine(Path.GetTempPath(),"crosspost-text-"+Guid.NewGuid());
            try
            {
                var store = new Store(folder); await store.InitializeAsync();
                foreach (var source in new[] { new Draft("RU",Language:"en"), new Draft("RU",Language:"both"), new Draft(EnglishText:"EN",Language:"both"), new Draft("RU",Language:"invalid"), Source() with { PlatformTexts = new() { ["discord"] = new(true,Title:"Отдельный русский",Language:"en") } } })
                {
                    var rejected = false;
                    try { await new QueuePlanner(store).AddBatchAsync(source,[new(new(),"demo"),new(new(Platform:"discord"),"demo")],DateTimeOffset.UtcNow); }
                    catch (InvalidOperationException) { rejected = true; }
                    Check(rejected && (await store.QueueAsync()).Count == 0,"Incomplete translation produced runnable jobs");
                }
            }
            finally { Directory.Delete(folder,true); }
        });
        await run("text limits apply to rendered destination text rather than unused source variants", () =>
        {
            var source = new Draft(Text:new string('r',4000),EnglishText:"Short",PlatformTexts:new() { ["bluesky"] = new(Language:"en") });
            source.Validate("telegram"); source.Validate("bluesky");
            var rejected = false; try { new Draft(Text:new string('r',4090),EnglishText:"English text",Language:"both").Validate("telegram"); } catch (InvalidOperationException) { rejected=true; }
            Check(rejected,"Combined text bypassed Telegram limit");
            var custom = source with { PlatformTexts = new() { ["bluesky"] = new(true,Text:"Small",Language:"ru") } };
            custom.Validate("bluesky");
            Check(custom.ForPlatform("bluesky").PlainText=="Small","Custom text appended to common text"); return Task.CompletedTask;
        });
        await run("queued text snapshots retain each language after editor variants are modified and restart", async () =>
        {
            var folder=Path.Combine(Path.GetTempPath(),"crosspost-text-"+Guid.NewGuid());
            try
            {
                var store=new Store(folder);await store.InitializeAsync();
                var source=Source() with { PlatformTexts=new() { ["telegram"]=new(Language:"ru"),["discord"]=new(Language:"both"),["bluesky"]=new(true,EnglishTitle:"Custom English",EnglishText:"Body",Language:"en") } };
                var targets=new[] { "telegram","discord","bluesky" }.Select(platform=>new PublishTarget(new(Platform:platform),"demo")).ToArray();
                var expected=targets.ToDictionary(target=>target.Options.Platform,target=>source.ForPlatform(target.Options.Platform).PlainText);
                await new QueuePlanner(store).AddBatchAsync(source,targets,DateTimeOffset.UtcNow);
                source.PlatformTexts.Clear();await store.SaveDocumentAsync("draft",new Draft("Completely changed"));
                var restarted=new Store(folder);await restarted.InitializeAsync();var runner=new QueueRunner(restarted);
                for(var i=0;i<3;i++) await runner.RunNextAsync(DateTimeOffset.UtcNow,job=>Task.FromResult<IPublisher>(job.Options.Platform switch {"discord"=>new DemoDiscord(),"bluesky"=>new DemoBluesky(),_=>new DemoTelegram()}),CancellationToken.None);
                foreach(var publication in await restarted.HistoryAsync())
                    Check(publication.EditText==expected[publication.Platform] && publication.Options?.TextLanguage==(publication.Platform=="telegram"?"ru":publication.Platform=="discord"?"both":"en"),"Worker changed frozen text or language");
                Check((await restarted.LibraryAsync()).Posts.Select(post=>post.WorkId).Distinct().Count()==1,"Text variants split one crosspost into different works");
            }
            finally { Directory.Delete(folder,true); }
        });
        await run("Telegram HTTP adapter sends the selected English text with HTML escaping", async () =>
        {
            var source=Source() with {EnglishTitle="English <title>",EnglishText="English & body",Language="en"};
            var handler=new ScriptedHandler(async(request,ct)=>
            {
                var form=(await request.Content!.ReadAsStringAsync(ct)).Split('&').Select(item=>item.Split('=',2)).ToDictionary(parts=>System.Net.WebUtility.UrlDecode(parts[0]),parts=>System.Net.WebUtility.UrlDecode(parts[1]));
                Check(form["text"]==System.Net.WebUtility.HtmlEncode(source.ForPlatform("telegram").PlainText),"Telegram sent source Russian or unescaped English");
                return new System.Net.Http.HttpResponseMessage(System.Net.HttpStatusCode.OK){Content=new System.Net.Http.StringContent("""{"ok":true,"result":{"message_id":10,"chat":{"id":-100777}}}""")};
            });
            using var http=new System.Net.Http.HttpClient(handler);
            var sent=await new TelegramClient(http,"123:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa").SendAsync("-100777",source,CancellationToken.None);
            Check(sent.State==DeliveryState.Sent&&handler.Calls==1,"English Telegram send failed");
        });
        await run("Discord HTTP adapter sends both language blocks exactly once", async () =>
        {
            var source=Source() with {Language="both"};
            var handler=new ScriptedHandler((_,_)=>Task.FromResult(new System.Net.Http.HttpResponseMessage(System.Net.HttpStatusCode.OK){Content=new System.Net.Http.StringContent("""{"id":"123","channel_id":"456","guild_id":"789","name":"Test"}""")}),
                async(request,ct)=>
                {
                    var part=((System.Net.Http.MultipartFormDataContent)request.Content!).Single(part=>part.Headers.ContentDisposition?.Name?.Trim('"')=="payload_json");
                    using var json=JsonDocument.Parse(await part.ReadAsStringAsync(ct));
                    Check(json.RootElement.GetProperty("content").GetString()==source.ForPlatform("discord").PlainText,"Discord bilingual content changed");
                    return new System.Net.Http.HttpResponseMessage(System.Net.HttpStatusCode.OK){Content=new System.Net.Http.StringContent("""{"id":"42","channel_id":"456","attachments":[]}""")};
                });
            using var http=new System.Net.Http.HttpClient(handler);
            var sent=await new DiscordClient(http,DiscordEndpoint.Parse("https://discord.com/api/webhooks/123/"+new string('a',40)),"456").SendAsync("456",source,CancellationToken.None);
            Check(sent.State==DeliveryState.Sent&&handler.Calls==2,"Bilingual Discord send failed");
        });
        await run("Bluesky HTTP adapter builds facets from its selected English variant", async () =>
        {
            const string did="did:plc:aaaaaaaaaaaaaaaaaaaaaaaa";
            var source=Source() with {EnglishText="[link](https://example.com) #art",Language="en"};
            var handler=new ScriptedHandler((_,_)=>Task.FromResult(new System.Net.Http.HttpResponseMessage(System.Net.HttpStatusCode.OK){Content=new System.Net.Http.StringContent(JsonSerializer.Serialize(new {did,handle="test.bsky.social",accessJwt="fakeJwt"}))}),
                async(request,ct)=>
                {
                    using var json=JsonDocument.Parse(await request.Content!.ReadAsStringAsync(ct));var record=json.RootElement.GetProperty("record");
                    Check(record.GetProperty("text").GetString()==BlueskyText.Build(source.ForPlatform("bluesky").PlainText).Text&&record.GetProperty("facets").GetArrayLength()==2,"Bluesky used wrong language or facet offsets");
                    return new System.Net.Http.HttpResponseMessage(System.Net.HttpStatusCode.OK){Content=new System.Net.Http.StringContent(JsonSerializer.Serialize(new {uri=$"at://{did}/app.bsky.feed.post/"+json.RootElement.GetProperty("rkey").GetString(),cid="bafyconfirmed"}))};
                });
            using var http=new System.Net.Http.HttpClient(handler);
            var sent=await new BlueskyClient(http,"test","fakePassword",did).SendAsync(did,source,CancellationToken.None);
            Check(sent.State==DeliveryState.Sent&&handler.Calls==2,"English Bluesky send failed");
        });
        await run("saved editor variants preserve disabled texts and dictionary order does not change content", () =>
        {
            var source=Source() with {Language="both",PlatformTexts=new(){["telegram"]=new(Language:"ru"),["bluesky"]=new(false,EnglishText:"Retained",Language:"en",Initialized:true)}};
            var restored=JsonSerializer.Deserialize<Draft>(JsonSerializer.Serialize(source))!;
            Check(restored.SameContent(source) && restored.PlatformTexts!["bluesky"].EnglishText=="Retained","Draft serialization lost disabled text");
            var reversed=source with { PlatformTexts=source.PlatformTexts!.Reverse().ToDictionary(pair=>pair.Key,pair=>pair.Value) };
            Check(source.SameContent(reversed),"Dictionary order changed draft equality");
            Check(!source.SameContent(source with {EnglishText="Changed"}),"English change ignored by autosave comparison");return Task.CompletedTask;
        });
        await run("schema 4 upgrade preserves existing drafts and queued text without assigning English", async () =>
        {
            var folder=Path.Combine(Path.GetTempPath(),"crosspost-text-"+Guid.NewGuid());
            try
            {
                var store=new Store(folder);await store.InitializeAsync();await store.SaveDocumentAsync("draft",new {Title="Old Russian",Text=""});
                var job=await new QueuePlanner(store).AddAsync(new("Existing queue"),new(),"demo",DateTimeOffset.UtcNow.AddHours(1));
                using(var database=new SqliteConnection(new SqliteConnectionStringBuilder{DataSource=Path.Combine(folder,"prototype.db"),Pooling=false}.ToString()))
                {await database.OpenAsync();using var command=database.CreateCommand();command.CommandText="PRAGMA user_version=4";await command.ExecuteNonQueryAsync();}
                await store.InitializeAsync();var draft=await store.ReadDocumentAsync<Draft>("draft");var queued=(await store.QueueAsync()).Single();
                Check(draft?.Language=="ru"&&draft.EnglishText==""&&queued.Id==job.Id&&queued.Draft.PlainText=="Existing queue","Schema upgrade changed Russian content or queue");
                Check(Directory.GetFiles(Path.Combine(folder,"backups"),"*.db").Length==1,"Schema migration did not back up database");
            }
            finally{Directory.Delete(folder,true);}
        });
    }
}
