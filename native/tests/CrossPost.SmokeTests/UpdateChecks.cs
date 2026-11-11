using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.Json;
using CrossPost.Update;
internal static class UpdateChecks
{
    private static void Check(bool value, string message) { if (!value) throw new Exception(message); }
    private static string Hash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes));
    private static async Task<(string Path, string Sum)> Package(string folder, string extra = "", bool broken = false)
    {
        var path = Path.Combine(folder, Guid.NewGuid() + ".zip");
        using (var zip = ZipFile.Open(path, ZipArchiveMode.Create))
        {
            var files = new Dictionary<string, byte[]> { ["CrossPost.Native.exe"] = [1,2,3], ["CrossPost.Updater.exe"] = [4,5] };
            if (extra.Length > 0) files[extra] = [9];
            foreach (var file in files) { using var output = zip.CreateEntry(file.Key).Open(); output.Write(file.Value); }
            var manifest = new PackageManifest("CrossPost.Native", "0.0.8-dev", "win-x64", files.Select(file => new PackageFile(file.Key, file.Value.Length, broken ? new string('0',64) : Hash(file.Value))).ToArray());
            using var entry = zip.CreateEntry(UpdatePackage.ManifestName).Open(); await JsonSerializer.SerializeAsync(entry, manifest);
        }
        return (path, await UpdatePackage.HashAsync(path));
    }
    public static async Task RunAsync(Func<string, Func<Task>, Task> run)
    {
        await run("native version ordering separates stable and prerelease channels", () =>
        {
            Check(NativeVersion.Parse("0.0.8").CompareTo(NativeVersion.Parse("0.0.8-dev")) > 0
                && NativeVersion.Parse("0.0.8-dev.10").CompareTo(NativeVersion.Parse("0.0.8-dev.2")) > 0, "Version ordering failed");
            foreach (var path in new[] { "../evil.exe", "C:/evil.exe", "a\\b", "CON.txt", "test./x", "/absolute", "file:stream" }) Check(!UpdatePackage.SafePath(path), "Unsafe Windows path accepted: " + path);
            return Task.CompletedTask;
        });
        await run("update package verifies SHA256, product, files and traversal before installation", async () =>
        {
            var folder = Path.Combine(Path.GetTempPath(), "crosspost-update-" + Guid.NewGuid()); Directory.CreateDirectory(folder);
            try
            {
                var good = await Package(folder); var destination = Path.Combine(folder, "stage");
                await UpdatePackage.VerifyAsync(good.Path, good.Sum, "0.0.8-dev", destination);
                Check(File.Exists(Path.Combine(destination, "CrossPost.Native.exe")), "Verified package was not extracted");
                var bad = await Package(folder, broken: true); var traversal = await Package(folder, "../outside.exe");
                foreach (var (path,sum,version) in new[] { (good.Path,new string('0',64),"0.0.8-dev"), (good.Path,good.Sum,"0.0.9"), (bad.Path,bad.Sum,"0.0.8-dev"), (traversal.Path,traversal.Sum,"0.0.8-dev") })
                { var rejected = false; try { await UpdatePackage.VerifyAsync(path, sum, version); } catch (InvalidOperationException) { rejected = true; } Check(rejected, "Damaged or wrong package accepted"); }
                Check(!File.Exists(Path.Combine(folder, "outside.exe")), "ZIP escaped extraction folder");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("update transaction rolls back both program and database if startup fails", async () =>
        {
            var folder = Path.Combine(Path.GetTempPath(), "crosspost-update-" + Guid.NewGuid()); Directory.CreateDirectory(folder);
            try
            {
                var install = Path.Combine(folder, "install"); var data = Path.Combine(folder, "data"); var transaction = Path.Combine(data, "transaction");
                Directory.CreateDirectory(install); Directory.CreateDirectory(transaction);
                await File.WriteAllTextAsync(Path.Combine(install, "CrossPost.Native.exe"), "OLD"); await File.WriteAllTextAsync(Path.Combine(data, "prototype.db"), "OLD DB");
                var package = await Package(folder); var request = new UpdateRequest(install, data, package.Path, package.Sum, "0.0.8-dev", 1, 0, false, Guid.NewGuid().ToString("N"));
                var failed = false;
                try { await UpdateTransaction.ApplyAsync(request, transaction, () => Task.CompletedTask, async exe => { await File.WriteAllTextAsync(Path.Combine(data, "prototype.db"), "NEW DB"); return false; }); }
                catch (InvalidOperationException) { failed = true; }
                Check(failed && await File.ReadAllTextAsync(Path.Combine(install, "CrossPost.Native.exe")) == "OLD" && await File.ReadAllTextAsync(Path.Combine(data, "prototype.db")) == "OLD DB", "Rollback lost old files or DB");
                Check(JsonSerializer.Deserialize<UpdateResult>(await File.ReadAllTextAsync(Path.Combine(transaction, "result.json")))?.State == "rolled-back", "Rollback outcome missing");
                var accepted = false;
                var nextTransaction = Path.Combine(data, "next-transaction"); Directory.CreateDirectory(nextTransaction);
                await UpdateTransaction.ApplyAsync(request with { Token = Guid.NewGuid().ToString("N") }, nextTransaction, () => { accepted = true; return Task.CompletedTask; }, exe => Task.FromResult(File.ReadAllBytes(exe).SequenceEqual(new byte[] {1,2,3})));
                Check(accepted && await File.ReadAllTextAsync(Path.Combine(data, "prototype.db")) == "OLD DB", "Successful upgrade changed user DB");
            }
            finally { Directory.Delete(folder, true); }
        });
        await run("updater stops the candidate before rolling back a failed final commit", async () =>
        {
            var folder = Path.Combine(Path.GetTempPath(), "crosspost-update-" + Guid.NewGuid()); Directory.CreateDirectory(folder);
            try
            {
                var install = Path.Combine(folder, "install"); var data = Path.Combine(folder, "data"); var transaction = Path.Combine(data,"transaction");
                Directory.CreateDirectory(install); Directory.CreateDirectory(Path.Combine(transaction,"commit"));
                await File.WriteAllTextAsync(Path.Combine(install,"CrossPost.Native.exe"), "OLD");
                var package = await Package(folder); var request = new UpdateRequest(install,data,package.Path,package.Sum,"0.0.8-dev",1,0,false,Guid.NewGuid().ToString("N"));
                FileStream? candidate = null; var stopped = false; var failed = false;
                try { await UpdateTransaction.ApplyAsync(request,transaction,() => Task.CompletedTask, exe => { candidate = new FileStream(exe,FileMode.Open,FileAccess.Read,FileShare.None); return Task.FromResult(true); },
                    () => { candidate?.Dispose(); stopped = true; return Task.CompletedTask; }); }
                catch (IOException) { failed = true; }
                Check(failed && stopped && await File.ReadAllTextAsync(Path.Combine(install,"CrossPost.Native.exe")) == "OLD", "Failed commit left candidate running or lost old program");
            }
            finally { Directory.Delete(folder,true); }
        });
        await run("update download validates checksum and refuses foreign redirects without forwarding", async () =>
        {
            var folder = Path.Combine(Path.GetTempPath(), "crosspost-update-" + Guid.NewGuid()); Directory.CreateDirectory(folder);
            try
            {
                var package = await Package(folder); var bytes = await File.ReadAllBytesAsync(package.Path);
                var release = new NativeRelease("0.0.8-dev","native-v0.0.8-dev","",new Uri("https://github.com/Ehotxep/CrossPost/releases/download/native-v0.0.8-dev/package.zip"),new Uri("https://github.com/Ehotxep/CrossPost/releases/download/native-v0.0.8-dev/package.zip.sha256"),bytes.Length);
                var handler = new ScriptedHandler((_,_) => Task.FromResult(new System.Net.Http.HttpResponseMessage(System.Net.HttpStatusCode.OK) { Content = new System.Net.Http.StringContent(package.Sum) }),
                    (_,_) => Task.FromResult(new System.Net.Http.HttpResponseMessage(System.Net.HttpStatusCode.OK) { Content = new System.Net.Http.ByteArrayContent(bytes) }));
                using (var http = new System.Net.Http.HttpClient(handler))
                {
                    var downloaded = await new UpdateFeed(http).DownloadAsync(release,Path.Combine(folder,"download"),null,CancellationToken.None);
                    Check(downloaded.Sha256 == package.Sum && handler.Calls == 2,"Verified download did not finish");
                }
                var redirect = new ScriptedHandler((_,_) => { var response = new System.Net.Http.HttpResponseMessage(System.Net.HttpStatusCode.Redirect); response.Headers.Location = new Uri("https://evil.test/payload"); return Task.FromResult(response); });
                using (var http = new System.Net.Http.HttpClient(redirect))
                {
                    var rejected = false; try { await new UpdateFeed(http).DownloadAsync(release,Path.Combine(folder,"bad"),null,CancellationToken.None); } catch (InvalidOperationException) { rejected = true; }
                    Check(rejected && redirect.Calls == 1 && !File.Exists(Path.Combine(folder,"bad","package.zip")),"Foreign update redirect was followed");
                }
            }
            finally { Directory.Delete(folder,true); }
        });
        await run("update gate prevents simultaneous installers and stale lock files do not block restarts", () =>
        {
            var folder = Path.Combine(Path.GetTempPath(), "crosspost-update-" + Guid.NewGuid());
            try { using (var gate = UpdateGate.Acquire(folder)) Check(UpdateGate.IsHeld(folder), "Concurrent update not blocked"); Check(!UpdateGate.IsHeld(folder), "Stale lock blocks startup"); }
            finally { Directory.Delete(folder, true); } return Task.CompletedTask;
        });
        await run("update feed ignores Python releases, incomplete releases and forbidden download hosts", () =>
        {
            object Release(string tag, bool pre = true, bool complete = true, string host = "https://github.com")
            {
                var version = tag.StartsWith("native-v") ? tag[8..] : tag; var name = $"CrossPost-Native-Prototype-{version}-win-x64.zip";
                return new { draft = false, prerelease = pre, tag_name = tag, body = "Notes", assets = (complete ? new[] {name,name+".sha256"} : new[] {name}).Select(value => new {name=value,size=100,browser_download_url=$"{host}/Ehotxep/CrossPost/releases/download/{tag}/{value}"}).ToArray() };
            }
            var json = JsonSerializer.Serialize(new[] { Release("v99.0.0", false), Release("native-v0.1.0", false), Release("native-v0.7.0-dev"), Release("native-v0.0.9-dev", complete:false), Release("native-v0.0.8-dev"), Release("native-v0.0.7", false) });
            Check(UpdateFeed.Select(json,"0.0.7-dev",true)?.Version == "0.0.8-dev" && UpdateFeed.Select(json,"0.0.7-dev",false)?.Version == "0.0.7", "Wrong release channel or product selected");
            var rejected = false; try { UpdateFeed.Select(JsonSerializer.Serialize(new[] {Release("native-v0.0.8",host:"https://evil.test")}),"0.0.7",true); } catch (InvalidOperationException) { rejected=true; }
            Check(rejected,"Foreign executable source accepted"); return Task.CompletedTask;
        });
    }
}
