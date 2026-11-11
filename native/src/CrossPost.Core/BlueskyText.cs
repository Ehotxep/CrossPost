using System.Text;
using System.Text.RegularExpressions;

namespace CrossPost.Core;

public record BlueskyRichText(string Text, object[] Facets);
public static class BlueskyText
{
    public static BlueskyRichText Build(string input)
    {
        var output = new StringBuilder(); var links = new List<(int Start, int End, string Uri)>(); var cursor = 0;
        foreach (Match match in Regex.Matches(input, @"\[([^\]\r\n]+)\]\((https?://[^\s<>()]+)\)"))
        {
            output.Append(input[cursor..match.Index]); var start = output.Length; output.Append(match.Groups[1].Value);
            links.Add((start, output.Length, match.Groups[2].Value)); cursor = match.Index + match.Length;
        }
        output.Append(input[cursor..]); var text = output.ToString();
        foreach (Match match in Regex.Matches(text, @"https?://[^\s<>()\[\]]+"))
        {
            var value = match.Value.TrimEnd('.', ',', ';', ':', '!', '?', '»', '"', '\'');
            if (value.Length > 0 && !links.Any(link => match.Index < link.End && match.Index + value.Length > link.Start))
                links.Add((match.Index, match.Index + value.Length, value));
        }
        object Index(int start, int end) => new { byteStart = Encoding.UTF8.GetByteCount(text[..start]), byteEnd = Encoding.UTF8.GetByteCount(text[..end]) };
        var facets = links.Select(link => (object)new { index = Index(link.Start, link.End), features = new[] { new Dictionary<string, string>
            { ["$type"] = "app.bsky.richtext.facet#link", ["uri"] = link.Uri } } }).ToList();
        foreach (Match match in Regex.Matches(text, @"(?<![\w#])#([^\s#.,;:!?()\[\]«»""']+)"))
        {
            if (PublishLimits.Count(match.Groups[1].Value, "bluesky") > 64 || Encoding.UTF8.GetByteCount(match.Groups[1].Value) > 640) continue;
            if (links.Any(link => match.Index < link.End && match.Index + match.Length > link.Start)) continue;
            facets.Add(new { index = Index(match.Index, match.Index + match.Length), features = new[] { new Dictionary<string, string>
                { ["$type"] = "app.bsky.richtext.facet#tag", ["tag"] = match.Groups[1].Value } } });
        }
        return new(text, facets.ToArray());
    }
}
