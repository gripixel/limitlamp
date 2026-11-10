// Ortak veri katmani: besleyici (usage-feed.exe) yazar, panel (ClaudeUsagePanel.exe) okur.
// Hicbir ag erisimi yok. Sadece %LOCALAPPDATA%\ClaudeUsagePanel altindaki iki kucuk dosya.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;

namespace ClaudeUsagePanel
{
    public class Snapshot
    {
        public bool HasFive, HasSeven;
        public double FivePct, SevenPct;
        public long FiveReset, SevenReset;   // epoch saniye, 0 = bilinmiyor
        public long Updated;                 // epoch saniye
        public string Source = "";
        public string Model = "";
    }

    public struct HistPoint
    {
        public long T;
        public bool HasFive, HasSeven;
        public double Five, Seven;
    }

    public static class Store
    {
        static readonly DateTime Epoch = new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc);

        public static readonly string Dir = Environment.GetEnvironmentVariable("CLAUDE_USAGE_PANEL_DIR")
            ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ClaudeUsagePanel");
        public static string LimitsPath { get { return Path.Combine(Dir, "limits.txt"); } }
        public static string HistoryPath { get { return Path.Combine(Dir, "history.csv"); } }

        public static long Now() { return (long)(DateTime.UtcNow - Epoch).TotalSeconds; }
        public static DateTime FromEpoch(long s) { return Epoch.AddSeconds(s).ToLocalTime(); }

        static string N(double v) { return v.ToString("0.##", CultureInfo.InvariantCulture); }

        // ---- limits.txt: anahtar=deger satirlari, atomik yazim ----
        public static void WriteLimits(Snapshot s)
        {
            Directory.CreateDirectory(Dir);
            var sb = new StringBuilder();
            sb.Append("updated=").Append(s.Updated).Append('\n');
            if (s.HasFive)
            {
                sb.Append("five_pct=").Append(N(s.FivePct)).Append('\n');
                sb.Append("five_reset=").Append(s.FiveReset).Append('\n');
            }
            if (s.HasSeven)
            {
                sb.Append("seven_pct=").Append(N(s.SevenPct)).Append('\n');
                sb.Append("seven_reset=").Append(s.SevenReset).Append('\n');
            }
            string model = (s.Model ?? "").Replace("\r", " ").Replace("\n", " ");
            sb.Append("model=").Append(model).Append('\n');

            string tmp = LimitsPath + "." + Guid.NewGuid().ToString("N") + ".tmp";
            File.WriteAllText(tmp, sb.ToString(), new UTF8Encoding(false));
            try
            {
                if (File.Exists(LimitsPath)) File.Replace(tmp, LimitsPath, null);
                else File.Move(tmp, LimitsPath);
            }
            catch (IOException)
            {
                try { File.Copy(tmp, LimitsPath, true); } catch (IOException) { }
                try { File.Delete(tmp); } catch (IOException) { }
            }
        }

        public static Snapshot ReadLimits()
        {
            try
            {
                if (!File.Exists(LimitsPath)) return null;
                string txt;
                using (var fs = new FileStream(LimitsPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var r = new StreamReader(fs, Encoding.UTF8))
                    txt = r.ReadToEnd();
                var s = new Snapshot();
                s.Source = "Claude Code";
                foreach (string line in txt.Split('\n'))
                {
                    int i = line.IndexOf('=');
                    if (i <= 0) continue;
                    string k = line.Substring(0, i).Trim();
                    string v = line.Substring(i + 1).Trim();
                    double d; long l;
                    switch (k)
                    {
                        case "updated": if (long.TryParse(v, out l)) s.Updated = l; break;
                        case "five_pct": if (double.TryParse(v, NumberStyles.Float, CultureInfo.InvariantCulture, out d)) { s.FivePct = d; s.HasFive = true; } break;
                        case "five_reset": if (long.TryParse(v, out l)) s.FiveReset = l; break;
                        case "seven_pct": if (double.TryParse(v, NumberStyles.Float, CultureInfo.InvariantCulture, out d)) { s.SevenPct = d; s.HasSeven = true; } break;
                        case "seven_reset": if (long.TryParse(v, out l)) s.SevenReset = l; break;
                        case "model": s.Model = v; break;
                    }
                }
                return s.Updated > 0 ? s : null;
            }
            catch (IOException) { return null; }
            catch (UnauthorizedAccessException) { return null; }
        }

        // ---- history.csv: t,five,seven (bos = pencere yok) ----
        public static void AppendHistory(Snapshot s)
        {
            Directory.CreateDirectory(Dir);
            string line = s.Updated + "," + (s.HasFive ? N(s.FivePct) : "") + "," + (s.HasSeven ? N(s.SevenPct) : "") + "\n";
            using (var fs = new FileStream(HistoryPath, FileMode.Append, FileAccess.Write, FileShare.ReadWrite))
            {
                byte[] b = Encoding.ASCII.GetBytes(line);
                fs.Write(b, 0, b.Length);
            }
        }

        public static List<HistPoint> ReadHistory(long since)
        {
            var list = new List<HistPoint>();
            try
            {
                if (!File.Exists(HistoryPath)) return list;
                using (var fs = new FileStream(HistoryPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var r = new StreamReader(fs, Encoding.ASCII))
                {
                    string line;
                    while ((line = r.ReadLine()) != null)
                    {
                        string[] p = line.Split(',');
                        if (p.Length < 3) continue;
                        long t; double d;
                        if (!long.TryParse(p[0], out t) || t < since) continue;
                        var h = new HistPoint();
                        h.T = t;
                        if (p[1].Length > 0 && double.TryParse(p[1], NumberStyles.Float, CultureInfo.InvariantCulture, out d)) { h.Five = d; h.HasFive = true; }
                        if (p[2].Length > 0 && double.TryParse(p[2], NumberStyles.Float, CultureInfo.InvariantCulture, out d)) { h.Seven = d; h.HasSeven = true; }
                        list.Add(h);
                    }
                }
            }
            catch (IOException) { }
            return list;
        }

        // Dosya buyurse son 8 gunu tut.
        public static void TrimHistoryIfBig()
        {
            try
            {
                var fi = new FileInfo(HistoryPath);
                if (!fi.Exists || fi.Length < 300 * 1024) return;
                long cutoff = Now() - 8L * 86400;
                var keep = new StringBuilder();
                foreach (string line in File.ReadAllLines(HistoryPath))
                {
                    int c = line.IndexOf(',');
                    long t;
                    if (c > 0 && long.TryParse(line.Substring(0, c), out t) && t >= cutoff) keep.Append(line).Append('\n');
                }
                string tmp = HistoryPath + "." + Guid.NewGuid().ToString("N") + ".tmp";
                File.WriteAllText(tmp, keep.ToString(), Encoding.ASCII);
                File.Replace(tmp, HistoryPath, null);
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
    }
}
