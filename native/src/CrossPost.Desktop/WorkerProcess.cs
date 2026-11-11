using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using CrossPost.Core;

namespace CrossPost.Desktop;

public record WorkerInfo(int ProcessId, DateTimeOffset Started, int ProtocolVersion = 1);
internal static class WorkerProcess
{
    private static string LockPath => Path.Combine(App.DataFolder, "worker.lock");
    private static string PipeName => "CrossPost.Native.v1." + Convert.ToHexString(SHA256.HashData(
        Encoding.UTF8.GetBytes(App.DataFolder.ToUpperInvariant())))[..24];
    private static FileStream Acquire()
    {
        Directory.CreateDirectory(App.DataFolder);
        return new FileStream(LockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
    }
    public static bool IsRunning
    {
        get
        {
            try { using var probe = Acquire(); return false; }
            catch (IOException) { return true; }
        }
    }
    public static void Start()
    {
        if (IsRunning) return;
        var path = Environment.ProcessPath ?? throw new InvalidOperationException("Не найден исполняемый файл приложения.");
        var start = new ProcessStartInfo(path) { UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = AppContext.BaseDirectory };
        if (Path.GetFileNameWithoutExtension(path).Equals("dotnet", StringComparison.OrdinalIgnoreCase))
            start.ArgumentList.Add(typeof(App).Assembly.Location);
        start.ArgumentList.Add("--worker");
        using var process = Process.Start(start) ?? throw new InvalidOperationException("Не удалось запустить фоновую очередь.");
    }
    public static async Task EnsureStartedAsync()
    {
        var nextLaunch = DateTimeOffset.MinValue;
        for (var attempt = 0; attempt < 30; attempt++)
        {
            // A previous process may finish stopping while we are waiting for readiness.
            if (!IsRunning && DateTimeOffset.UtcNow >= nextLaunch)
            {
                Start(); nextLaunch = DateTimeOffset.UtcNow.AddSeconds(2);
            }
            var reply = await RequestAsync("protocol|2", 1000);
            if (reply == "ok") return;
            if (reply == "error")
                throw new InvalidOperationException("Работает Worker предыдущей версии. Остановите очередь на вкладке «Очередь», затем нажмите «Запустить очередь». Новое задание ещё не добавлено.");
            await Task.Delay(100);
        }
        throw new InvalidOperationException("Фоновый процесс не подтвердил запуск. Проверьте worker-error.log в папке данных.");
    }
    public static async Task<bool> CommandAsync(string command, int timeoutMilliseconds = 2000) =>
        await RequestAsync(command, timeoutMilliseconds) == "ok";
    private static async Task<string?> RequestAsync(string command, int timeoutMilliseconds)
    {
        try
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromMilliseconds(timeoutMilliseconds));
            using var pipe = new NamedPipeClientStream(".", PipeName, PipeDirection.InOut, PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
            await pipe.ConnectAsync(timeout.Token);
            using var writer = new StreamWriter(pipe, Encoding.UTF8, leaveOpen: true) { AutoFlush = true };
            using var reader = new StreamReader(pipe, Encoding.UTF8, leaveOpen: true);
            await writer.WriteLineAsync(("v1|" + command).AsMemory(), timeout.Token);
            return await reader.ReadLineAsync(timeout.Token);
        }
        catch (Exception error) when (error is IOException or OperationCanceledException or UnauthorizedAccessException)
        { return null; }
    }
    public static async Task VerifyLegacyRefusalAsync()
    {
        using var owner = Acquire(); using var stopping = new CancellationTokenSource();
        var server = ServeAsync(stopping.Token, command => command == "ping");
        var refused = false;
        try
        {
            try { await EnsureStartedAsync(); }
            catch (InvalidOperationException error) when (error.Message.Contains("предыдущей версии")) { refused = true; }
            if (!refused) throw new InvalidOperationException("Legacy Worker compatibility guard did not reject an explicit unsupported protocol");
        }
        finally { stopping.Cancel(); await server; }
    }
    public static async Task<int> RunAsync()
    {
        FileStream? owner = null;
        // Status probes briefly open the same file. Retry acquisition rather than
        // mistaking a short-lived probe for a persistent second worker.
        for (var attempt = 0; attempt < 20 && owner is null; attempt++)
        {
            try { owner = Acquire(); }
            catch (IOException) { await Task.Delay(50); }
        }
        if (owner is null) return 0;
        using (owner)
        using (var stopping = new CancellationTokenSource())
        using (var http = new HttpClient(new HttpClientHandler { AllowAutoRedirect = false }) { Timeout = TimeSpan.FromSeconds(90) })
        {
            var gate = new object();
            Guid? activeId = null; CancellationTokenSource? active = null;
            var server = ServeAsync(stopping.Token, command =>
            {
                if (command == "stop") { stopping.Cancel(); return true; }
                lock (gate)
                {
                    if (command.StartsWith("cancel|", StringComparison.Ordinal) && Guid.TryParse(command[7..], out var id) && id == activeId)
                    { active?.Cancel(); return true; }
                }
                return command is "ping" or "protocol|2";
            });
            try
            {
                var store = new Store(App.DataFolder); await store.InitializeAsync(recoverUnfinished: false);
                await store.RecoverQueueAsync();
                await store.SaveDocumentAsync("worker-info", new WorkerInfo(Environment.ProcessId, DateTimeOffset.UtcNow, 2));
                var runner = new QueueRunner(store);
                while (!stopping.IsCancellationRequested)
                {
                    using var operation = CancellationTokenSource.CreateLinkedTokenSource(stopping.Token);
                    lock (gate) { active = operation; }
                    try
                    {
                        var ran = await runner.RunNextAsync(DateTimeOffset.UtcNow, async job =>
                        {
                            if (job.Options.Platform == "discord")
                            {
                                if (job.Options.Demo) return (IPublisher)new DemoDiscord();
                                var discord = await store.ReadDocumentAsync<DiscordOptions>("discord-options") ?? new();
                                if (discord.Demo) throw new QueuePausedException("Включён деморежим Discord. Задание приостановлено.");
                                var endpoint = DiscordEndpoint.Parse(SecretVault.Load("discord"));
                                if (endpoint.Id != job.BotId) throw new QueuePausedException("Выбран другой webhook Discord. Проверьте задание вручную.");
                                return new DiscordClient(http, endpoint, job.Options.Chat, job.Options.Spoiler, job.Options.SuppressEmbeds);
                            }
                            if (job.Options.Platform != "telegram") throw new QueuePausedException("Площадка задания не поддерживается этой версией.");
                            if (job.Options.Demo) return new DemoTelegram();
                            var options = await store.ReadDocumentAsync<AppOptions>("options") ?? new();
                            if (options.Demo) throw new QueuePausedException("Включён деморежим. Реальное задание приостановлено.");
                            var token = SecretVault.Load();
                            if (TelegramClient.BotId(token) != job.BotId)
                                throw new QueuePausedException("Выбран другой бот. Верните нужного бота и включите задание вручную.");
                            return (IPublisher)new TelegramClient(http, token);
                        }, operation.Token, id => { lock (gate) { activeId = id; } });
                        if (!ran)
                        {
                            await Task.Delay(1000, stopping.Token);
                        }
                    }
                    finally { lock (gate) { active = null; activeId = null; } }
                }
                return 0;
            }
            catch (OperationCanceledException) when (stopping.IsCancellationRequested) { return 0; }
            catch (Exception error)
            {
                await File.AppendAllTextAsync(Path.Combine(App.DataFolder, "worker-error.log"),
                    $"{DateTimeOffset.UtcNow:O} {error.GetType().Name}\n");
                return 1;
            }
            finally
            {
                stopping.Cancel();
                await server;
            }
        }
    }
    public static async Task<int> VerifySmokeAsync()
    {
        try
        {
            var store = new Store(App.DataFolder);
            var jobId = await store.ReadDocumentAsync<Guid?>("worker-smoke-id") ?? throw new InvalidOperationException("No isolated smoke job");
            var job = (await store.QueueAsync()).Single(value => value.Id == jobId);
            if (!job.Options.Demo) throw new InvalidOperationException("Worker smoke job must be demo");
            async Task<Publication> WaitFor(Guid id)
            {
                for (var attempt = 0; attempt < 500; attempt++)
                {
                    var publication = await store.PublicationAsync(id);
                    if (publication?.Delivery.State == DeliveryState.Sent) return publication;
                    await Task.Delay(100);
                }
                throw new InvalidOperationException("Scheduled demo job did not finish");
            }
            var sent = await WaitFor(jobId);
            var closed = File.GetLastWriteTimeUtc(Path.Combine(App.DataFolder, "window-closed.txt"));
            if (sent.Created.UtcDateTime < closed || sent.Delivery.AllMediaMessageIds.Length != 2)
                throw new InvalidOperationException("Job did not execute after the window closed");
            var discordId = await store.ReadDocumentAsync<Guid?>("discord-worker-smoke-id") ?? throw new InvalidOperationException("Discord smoke queue job missing");
            var discord = await WaitFor(discordId);
            if (discord.Platform != "discord" || discord.Delivery.Kind != "discord" || discord.Created.UtcDateTime < closed || discord.Delivery.AttachmentIds?.Length != 2)
                throw new InvalidOperationException("Discord queue was routed incorrectly or did not run after window exit");
            var info = await store.ReadDocumentAsync<WorkerInfo>("worker-info");
            if (info?.ProcessId == Environment.ProcessId || !await CommandAsync("ping"))
                throw new InvalidOperationException("Independent worker IPC failed");
            var pending = await new QueuePlanner(store).AddAsync(new("Restart demo"), new(), "demo", DateTimeOffset.UtcNow.AddHours(1));
            async Task Stop()
            {
                if (!await CommandAsync("stop")) throw new InvalidOperationException("Worker did not acknowledge stop");
                for (var attempt = 0; attempt < 100 && IsRunning; attempt++) await Task.Delay(100);
                if (IsRunning) throw new InvalidOperationException("Worker did not stop");
            }
            await Stop();
            if ((await store.QueueAsync()).Single(value => value.Id == pending.Id).State != QueueState.Pending)
                throw new InvalidOperationException("Stopping worker lost a pending job");
            await store.ChangeQueueAsync(pending.Id, QueueState.Pending, DateTimeOffset.UtcNow);
            Start(); await WaitFor(pending.Id); await Stop();
            await File.WriteAllTextAsync(Path.Combine(App.DataFolder, "worker-smoke-ready.txt"),
                "Scheduled album sent after window exit; user-only IPC; stop/restart preserved and completed pending job");
            return 0;
        }
        catch (Exception error)
        {
            await File.AppendAllTextAsync(Path.Combine(App.DataFolder, "worker-smoke-error.log"), error + Environment.NewLine);
            return 1;
        }
        finally { if (IsRunning) await CommandAsync("stop"); }
    }
    private static async Task ServeAsync(CancellationToken ct, Func<string, bool> handle)
    {
        while (!ct.IsCancellationRequested)
        {
            try
            {
                using var pipe = new NamedPipeServerStream(PipeName, PipeDirection.InOut, 1,
                    PipeTransmissionMode.Byte, PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
                await pipe.WaitForConnectionAsync(ct).ConfigureAwait(false);
                using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
                timeout.CancelAfter(TimeSpan.FromSeconds(2));
                // Fixed-size byte reads bound control messages before allocating strings.
                var bytes = new byte[256]; var count = 0;
                while (count < bytes.Length)
                {
                    var read = await pipe.ReadAsync(bytes.AsMemory(count, 1), timeout.Token).ConfigureAwait(false);
                    if (read == 0 || bytes[count++] == '\n') break;
                }
                var request = Encoding.UTF8.GetString(bytes, 0, count).Trim('\uFEFF', '\r', '\n');
                var ok = count < bytes.Length && request.StartsWith("v1|", StringComparison.Ordinal) && handle(request[3..]);
                // A stop command cancels the listener, but its acknowledgement must still reach the caller.
                await pipe.WriteAsync(Encoding.UTF8.GetBytes(ok ? "ok\n" : "error\n")).ConfigureAwait(false);
                await pipe.FlushAsync().ConfigureAwait(false);
            }
            catch (Exception error) when (error is IOException or OperationCanceledException)
            {
                if (ct.IsCancellationRequested) break;
                // A client may still hold the previous pipe instance. Yield so its
                // continuation can close it; a tight retry loop can starve WPF.
                await Task.Delay(25).ConfigureAwait(false);
            }
        }
    }
}
