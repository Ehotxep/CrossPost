namespace CrossPost.Core;

public record PlatformText(bool Enabled = false, string Title = "", string Text = "", string EnglishTitle = "", string EnglishText = "", string Language = "inherit", bool Initialized = false);
public sealed partial record Draft
{
    public string LanguageFor(string platform)
    {
        var language = PlatformTexts?.GetValueOrDefault(platform)?.Language;
        language = language is null or "inherit" ? Language : language;
        if (language is not ("ru" or "both" or "en")) throw new InvalidOperationException("Выберите язык публикации: русский, оба или English.");
        return language;
    }
    public static string LanguageName(string language) => language switch { "both" => "Русский + English", "en" => "English", _ => "Русский" };
    private static string Join(params string[] parts) => string.Join("\n\n", parts.Select(part => part.Trim()).Where(part => part.Length > 0));
    public Draft ForPlatform(string platform)
    {
        var language = LanguageFor(platform); var custom = PlatformTexts?.GetValueOrDefault(platform);
        var source = custom?.Enabled == true ? custom : new PlatformText(Title: Title, Text: Text, EnglishTitle: EnglishTitle, EnglishText: EnglishText);
        var ru = Join(source.Title, source.Text); var en = Join(source.EnglishTitle, source.EnglishText);
        if (language is "en" or "both" && en.Length == 0 && ru.Length > 0)
            throw new InvalidOperationException($"{PublishLimits.Name(platform)}: заполните английский текст или выберите русский язык.");
        if (language == "both" && ru.Length == 0 && en.Length > 0)
            throw new InvalidOperationException($"{PublishLimits.Name(platform)}: заполните русский текст или выберите English.");
        var title = language == "en" ? source.EnglishTitle : source.Title;
        var body = language switch { "en" => source.EnglishText, "both" => Join(source.Text, source.EnglishTitle, source.EnglishText), _ => source.Text };
        return this with { Title = title.Trim(), Text = body.Trim(), EnglishTitle = "", EnglishText = "", PlatformTexts = null, Language = "ru" };
    }
    private bool SameTextVariants(Draft other) => EnglishTitle == other.EnglishTitle && EnglishText == other.EnglishText && Language == other.Language
        && (PlatformTexts ?? []).Count == (other.PlatformTexts ?? []).Count
        && (PlatformTexts ?? []).All(pair => other.PlatformTexts is { } rules && rules.TryGetValue(pair.Key, out var value) && pair.Value == value);
}
