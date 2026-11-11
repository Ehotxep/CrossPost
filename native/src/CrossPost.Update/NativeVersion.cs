using System.Text.RegularExpressions;
namespace CrossPost.Update;

public sealed record NativeVersion(int Major, int Minor, int Patch, string Pre) : IComparable<NativeVersion>
{
    public static NativeVersion Parse(string value)
    {
        var match = Regex.Match(value, @"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$");
        if (!match.Success) throw new FormatException("Неверная версия нативной сборки.");
        return new(int.Parse(match.Groups[1].Value), int.Parse(match.Groups[2].Value), int.Parse(match.Groups[3].Value), match.Groups[4].Value);
    }
    public int CompareTo(NativeVersion? other)
    {
        if (other is null) return 1;
        foreach (var compare in new[] { Major.CompareTo(other.Major), Minor.CompareTo(other.Minor), Patch.CompareTo(other.Patch) }) if (compare != 0) return compare;
        if (Pre == other.Pre) return 0; if (Pre.Length == 0) return 1; if (other.Pre.Length == 0) return -1;
        var a = Pre.Split('.'); var b = other.Pre.Split('.');
        for (var index = 0; index < Math.Min(a.Length, b.Length); index++)
        {
            var an = int.TryParse(a[index], out var av); var bn = int.TryParse(b[index], out var bv);
            var c = an && bn ? av.CompareTo(bv) : an != bn ? an ? -1 : 1 : string.CompareOrdinal(a[index], b[index]);
            if (c != 0) return c;
        }
        return a.Length.CompareTo(b.Length);
    }
}
