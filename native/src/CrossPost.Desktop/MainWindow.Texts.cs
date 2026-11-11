using System.Windows;
using System.Windows.Controls;
using CrossPost.Core;

namespace CrossPost.Desktop;
public partial class MainWindow
{
    private readonly Dictionary<string, PlatformText> _platformTexts = new();
    private HashSet<string> _customTextPlatforms = [];
    private bool _settingTexts;
    private string _variantPlatform = "telegram";
    private string SelectedTextLanguage => MainLanguageBoth?.IsChecked == true ? "both" : MainLanguageEnglish?.IsChecked == true ? "en" : "ru";
    private async Task InitializeTextsAsync(Draft draft)
    {
        _settingTexts = true;
        try
        {
            EnglishTitleInput.Text = draft.EnglishTitle; EnglishBodyInput.Text = draft.EnglishText;
            MainLanguageRu.IsChecked = draft.Language == "ru"; MainLanguageBoth.IsChecked = draft.Language == "both"; MainLanguageEnglish.IsChecked = draft.Language == "en";
            EnglishEditor.IsExpanded = draft.Language != "ru" || draft.EnglishTitle.Length > 0 || draft.EnglishText.Length > 0;
            _platformTexts.Clear(); foreach (var pair in draft.PlatformTexts ?? []) _platformTexts[pair.Key] = pair.Value;
            // Restore availability from the draft too, so saved custom text is never silently discarded.
            _customTextPlatforms = (await _store.ReadDocumentAsync<string[]>("custom-text-platforms") ?? []).ToHashSet();
            foreach (var pair in _platformTexts.Where(pair => pair.Value.Enabled)) _customTextPlatforms.Add(pair.Key);
            CustomTelegram.IsChecked = _customTextPlatforms.Contains("telegram"); CustomDiscord.IsChecked = _customTextPlatforms.Contains("discord"); CustomBluesky.IsChecked = _customTextPlatforms.Contains("bluesky");
        }
        finally { _settingTexts = false; }
        LoadPlatformTextEditor();
    }
    private void LoadPlatformTextEditor()
    {
        if (PlatformLanguage is null) return;
        _settingTexts = true;
        try
        {
            _variantPlatform = CurrentOptions.Platform;
            var rule = _platformTexts.GetValueOrDefault(_variantPlatform) ?? new();
            PlatformTextHeading.Text = "Текст для " + CurrentPlatformLabel;
            PlatformLanguage.SelectedIndex = rule.Language switch { "ru" => 1, "both" => 2, "en" => 3, _ => 0 };
            CustomTextToggle.Visibility = _customTextPlatforms.Contains(_variantPlatform) ? Visibility.Visible : Visibility.Collapsed;
            CustomTextToggle.IsChecked = rule.Enabled;
            CustomTitleInput.Text = rule.Title; CustomBodyInput.Text = rule.Text;
            CustomEnglishTitleInput.Text = rule.EnglishTitle; CustomEnglishBodyInput.Text = rule.EnglishText;
            CustomTextFields.Visibility = rule.Enabled ? Visibility.Visible : Visibility.Collapsed;
            PlatformTextEditor.Visibility = rule.Enabled ? Visibility.Visible : Visibility.Collapsed;
        }
        finally { _settingTexts = false; }
    }
    private void TextLanguageChanged(object sender, RoutedEventArgs e)
    {
        if (!_loaded || _settingTexts) return;
        if (SelectedTextLanguage != "ru") EnglishEditor.IsExpanded = true;
        TextsChanged();
    }
    private void PlatformLanguageChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!_loaded || _settingTexts) return;
        var rule = _platformTexts.GetValueOrDefault(_variantPlatform) ?? new();
        var language = PlatformLanguage.SelectedIndex switch { 1 => "ru", 2 => "both", 3 => "en", _ => "inherit" };
        _platformTexts[_variantPlatform] = rule with { Language = language }; TextsChanged();
    }
    private void CustomTextToggled(object sender, RoutedEventArgs e)
    {
        if (!_loaded || _settingTexts) return;
        var enabled = CustomTextToggle.IsChecked == true;
        var existing = _platformTexts.GetValueOrDefault(_variantPlatform);
        var rule = existing ?? new();
        if (enabled && !rule.Initialized)
            rule = rule with { Title = TitleInput.Text, Text = BodyInput.Text, EnglishTitle = EnglishTitleInput.Text, EnglishText = EnglishBodyInput.Text, Initialized = true };
        _platformTexts[_variantPlatform] = rule with { Enabled = enabled };
        LoadPlatformTextEditor(); PlatformTextEditor.IsExpanded = enabled; TextsChanged();
    }
    private void CustomTextChanged(object sender, TextChangedEventArgs e)
    {
        if (!_loaded || _settingTexts) return;
        var rule = _platformTexts.GetValueOrDefault(_variantPlatform) ?? new();
        _platformTexts[_variantPlatform] = rule with { Title = CustomTitleInput.Text, Text = CustomBodyInput.Text,
            EnglishTitle = CustomEnglishTitleInput.Text, EnglishText = CustomEnglishBodyInput.Text, Initialized = true };
        TextsChanged();
    }
    private void TextsChanged()
    { UpdatePreview(); RenderTargetSummary(); _draftTimer.Stop(); _draftTimer.Start(); }
    private async void SaveCustomTextSettings(object sender, RoutedEventArgs e) => await RunOperation(SaveCustomTextSettingsAsync);
    private async Task SaveCustomTextSettingsAsync(CancellationToken _)
    {
        _customTextPlatforms = new[] { (CustomTelegram.IsChecked == true,"telegram"), (CustomDiscord.IsChecked == true,"discord"), (CustomBluesky.IsChecked == true,"bluesky") }
            .Where(pair => pair.Item1).Select(pair => pair.Item2).ToHashSet();
        foreach (var platform in _platformTexts.Keys.ToArray())
            if (!_customTextPlatforms.Contains(platform)) _platformTexts[platform] = _platformTexts[platform] with { Enabled = false };
        await _store.SaveDocumentAsync("custom-text-platforms", _customTextPlatforms.ToArray());
        LoadPlatformTextEditor(); await SaveDraftAsync(); UpdatePreview(); RenderTargetSummary();
        StatusLabel.Text = "Отдельные тексты настроены. Уже созданные задания очереди не меняются.";
    }
    private void ClearDraftTexts()
    {
        _settingTexts = true;
        try { EnglishTitleInput.Clear(); EnglishBodyInput.Clear(); MainLanguageRu.IsChecked = true; _platformTexts.Clear(); EnglishEditor.IsExpanded = false; }
        finally { _settingTexts = false; }
        LoadPlatformTextEditor();
    }
}
