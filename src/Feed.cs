// limitlamp-feed.exe: Claude Code'un "statusLine" komutu olarak calisir.
// Claude Code, stdin'den oturum JSON'unu verir; icindeki rate_limits (5 saatlik + haftalik
// yuzde ve sifirlanma zamani) alinip yerel dosyaya yazilir. Ag yok, token yok, baska hicbir alan saklanmaz.
// stdout'a kisa bir ozet basar (Claude Code terminalinde durum cubugunda gorunur).
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;

namespace LimitLamp
{
    static class Feed
    {
        static bool Win(Dictionary<string, object> rl, string key, out double pct, out long reset)
        {
            pct = 0; reset = 0;
            object o;
            if (!rl.TryGetValue(key, out o)) return false;
            var d = o as Dictionary<string, object>;
            if (d == null) return false;
            object p, r;
            if (!d.TryGetValue("used_percentage", out p) || p == null) return false;
            pct = Convert.ToDouble(p);
            if (d.TryGetValue("resets_at", out r) && r != null) reset = Convert.ToInt64(Convert.ToDouble(r));
            return true;
        }

        static int Main()
        {
            try
            {
                string json;
                using (var stdin = Console.OpenStandardInput())
                using (var rd = new StreamReader(stdin, new UTF8Encoding(false)))
                    json = rd.ReadToEnd();
                if (json.Length == 0) return 0;

                var ser = new JavaScriptSerializer();
                ser.MaxJsonLength = int.MaxValue;
                var root = ser.DeserializeObject(json) as Dictionary<string, object>;
                if (root == null) return 0;

                object rlo;
                if (!root.TryGetValue("rate_limits", out rlo)) return 0;   // abone degil / henuz yanit yok
                var rl = rlo as Dictionary<string, object>;
                if (rl == null) return 0;

                var s = new Snapshot();
                s.Updated = Store.Now();
                s.HasFive = Win(rl, "five_hour", out s.FivePct, out s.FiveReset);
                s.HasSeven = Win(rl, "seven_day", out s.SevenPct, out s.SevenReset);

                object mo;
                if (root.TryGetValue("model", out mo))
                {
                    var md = mo as Dictionary<string, object>;
                    object dn;
                    if (md != null && md.TryGetValue("display_name", out dn) && dn != null) s.Model = dn.ToString();
                }

                Snapshot old = Store.ReadLimits();
                bool changed = old == null
                    || old.HasFive != s.HasFive || old.HasSeven != s.HasSeven
                    || Math.Abs(old.FivePct - s.FivePct) > 0.001 || Math.Abs(old.SevenPct - s.SevenPct) > 0.001
                    || old.FiveReset != s.FiveReset || old.SevenReset != s.SevenReset;

                // Degisiklik yoksa ve veri taze ise diske hic dokunma (statusline sik tetiklenir).
                if (changed || s.Updated - old.Updated >= 60)
                {
                    Store.WriteLimits(s);
                    if (changed || s.Updated - old.Updated >= 600)
                    {
                        Store.AppendHistory(s);
                        Store.TrimHistoryIfBig();
                    }
                }

                var sb = new StringBuilder();
                if (s.HasFive) sb.Append("5h ").Append(Math.Round(s.FivePct)).Append('%');
                if (s.HasSeven) { if (sb.Length > 0) sb.Append(" | "); sb.Append("7d ").Append(Math.Round(s.SevenPct)).Append('%'); }
                Console.Out.Write(sb.ToString());
            }
            catch (Exception ex)
            {
                // Besleyici Claude Code'u asla bozmamali: sessizce cik (hata ayiklama icin env ile goster).
                if (Environment.GetEnvironmentVariable("LIMITLAMP_DEBUG") == "1") Console.Error.WriteLine(ex.ToString());
            }
            return 0;
        }
    }
}
