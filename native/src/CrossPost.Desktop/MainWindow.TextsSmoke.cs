using CrossPost.Core;
namespace CrossPost.Desktop;

public partial class MainWindow
{
    private Draft? _textSmokeOriginal;
    private HashSet<string> _textSmokeAllowed = [];
    private async Task RunTextSmokeAsync()
    {
        _textSmokeOriginal = CurrentDraft; _textSmokeAllowed = new(_customTextPlatforms);
        MainLanguageBoth.IsChecked = true;
        if (!EnglishEditor.IsExpanded || !CountLabel.Text.Contains("английский")) throw new InvalidOperationException("Missing English is not visible before publication");
        EnglishTitleInput.Text = "Native English title"; EnglishBodyInput.Text = "Native English body";
        PlatformPicker.SelectedIndex = 0; PlatformLanguage.SelectedIndex = 1;
        if (PreviewText.Text != _textSmokeOriginal.PlainText) throw new InvalidOperationException("Telegram language override did not choose Russian");
        PlatformPicker.SelectedIndex = 1; PlatformLanguage.SelectedIndex = 2;
        if (!PreviewText.Text.Contains(_textSmokeOriginal.Title) || !PreviewText.Text.Contains("Native English title")) throw new InvalidOperationException("Discord bilingual preview is incomplete");
        PlatformPicker.SelectedIndex = 2; PlatformLanguage.SelectedIndex = 3;
        CustomBluesky.IsChecked = true; await SaveCustomTextSettingsAsync(CancellationToken.None);
        CustomTextToggle.IsChecked = true;
        CustomEnglishTitleInput.Text = "Short Bluesky"; CustomEnglishBodyInput.Text = "#art https://example.com";
        CustomTextToggle.IsChecked = false; CustomTextToggle.IsChecked = true;
        if (CustomEnglishTitleInput.Text != "Short Bluesky" || PreviewText.Text.Contains(_textSmokeOriginal.Title) || !PreviewText.Text.Contains("Short Bluesky"))
            throw new InvalidOperationException("Custom English was lost while toggling, or preview contains Russian");
        CustomTitleInput.Clear(); CustomBodyInput.Clear(); CustomEnglishTitleInput.Clear(); CustomEnglishBodyInput.Clear();
        CustomTextToggle.IsChecked = false; CustomTextToggle.IsChecked = true;
        if (CustomEnglishTitleInput.Text.Length > 0 || CustomTitleInput.Text.Length > 0 || PreviewText.Text != "Без текста")
            throw new InvalidOperationException("Explicit image-only custom text was replaced by the common text");
        CustomEnglishTitleInput.Text = "Short Bluesky"; CustomEnglishBodyInput.Text = "#art https://example.com";
        var draft = CurrentDraft;
        await SaveDraftAsync(); var restored = await _store.ReadDocumentAsync<Draft>("draft");
        if (restored is null || !restored.SameContent(draft)) throw new InvalidOperationException("Bilingual/custom draft did not survive serialization");
        await InitializeTextsAsync(restored);
        if (PreviewText.Text.Contains(_textSmokeOriginal.Title) || !CurrentDraft.SameContent(draft) || CustomTextToggle.IsChecked != true)
            throw new InvalidOperationException("Restoring text controls changed the draft");
        await _store.SaveDocumentAsync("batch-worker-smoke-texts", new[] { "telegram","discord","bluesky" }.ToDictionary(platform => platform, platform => draft.ForPlatform(platform).PlainText));
    }
    private async Task RestoreTextSmokeAsync()
    {
        var original = _textSmokeOriginal ?? throw new InvalidOperationException("No original text smoke draft");
        _settingTexts = true;
        try
        {
            EnglishTitleInput.Text = original.EnglishTitle; EnglishBodyInput.Text = original.EnglishText;
            MainLanguageRu.IsChecked = original.Language == "ru"; MainLanguageBoth.IsChecked = original.Language == "both"; MainLanguageEnglish.IsChecked = original.Language == "en";
            _platformTexts.Clear(); foreach (var pair in original.PlatformTexts ?? []) _platformTexts[pair.Key] = pair.Value;
            _customTextPlatforms = new(_textSmokeAllowed);
            CustomTelegram.IsChecked = _customTextPlatforms.Contains("telegram"); CustomDiscord.IsChecked = _customTextPlatforms.Contains("discord"); CustomBluesky.IsChecked = _customTextPlatforms.Contains("bluesky");
            EnglishEditor.IsExpanded = false;
        }
        finally { _settingTexts = false; }
        await _store.SaveDocumentAsync("custom-text-platforms", _customTextPlatforms.ToArray());
        LoadPlatformTextEditor(); PlatformPicker.SelectedIndex = 0; UpdatePreview(); await SaveDraftAsync();
    }
}
