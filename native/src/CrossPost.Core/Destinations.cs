namespace CrossPost.Core;

public record DestinationProfile(string Id, string Name, AppOptions Options, string CredentialId, bool Enabled = true)
{
    public override string ToString() => $"{PublishLimits.Name(Options.Platform)} · {Name} · {DestinationRouting.RatingName(Options.RatingRule)} · {(Options.Demo ? "ДЕМО" : Options.Chat)}";
}
public static class DestinationRouting
{
    public static bool Accepts(string rule, string label)
    {
        if (label is not ("sfw" or "erotic" or "explicit")) throw new InvalidOperationException("Выберите рейтинг публикации.");
        return rule switch { "all" => true, "sfw" => label == "sfw", "nsfw" => label != "sfw", _ => throw new InvalidOperationException("Неизвестное правило рейтинга назначения.") };
    }
    public static string RatingName(string rule) => rule switch { "sfw" => "Только SFW", "nsfw" => "Только 18+", _ => "Любой рейтинг" };
    public static PublishTarget[] Select(IEnumerable<DestinationProfile> profiles, IEnumerable<string> selected, string label)
    {
        var ids = selected.ToHashSet(StringComparer.Ordinal);
        return profiles.Where(p => ids.Contains(p.Id) && p.Enabled && Accepts(p.Options.RatingRule, label))
            .Select(p => new PublishTarget(p.Options with { DestinationId = p.Id, DestinationName = p.Name }, p.Options.Demo ? "demo" : p.CredentialId)).ToArray();
    }
    public static DestinationProfile RequireForJob(ScheduledPost job, IEnumerable<DestinationProfile> profiles)
    {
        var profile = profiles.SingleOrDefault(p => p.Id == job.Options.DestinationId);
        if (profile is null || !profile.Enabled) throw new QueuePausedException("Назначение удалено или отключено. Проверьте задание.");
        if (profile.Options.Platform != job.Options.Platform || profile.Options.Chat != job.Options.Chat
            || profile.CredentialId != job.BotId && !job.Options.Demo)
            throw new QueuePausedException("Настройки назначения изменились. Сохранённое задание не перенаправляется автоматически.");
        if (!Accepts(profile.Options.RatingRule, job.Draft.ContentLabel)) throw new QueuePausedException("Рейтинг поста больше не подходит назначению.");
        if (!job.Options.Demo && profile.Options.Demo) throw new QueuePausedException("Для назначения включён деморежим.");
        return profile;
    }
}
