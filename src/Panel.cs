// ClaudeUsagePanel.exe - Claude kullanim limitlerini gosteren, hafif, her zaman ustte duran panel.
// Ag erisimi YOK. Sadece yerel dosyalari okur:
//   1) %LOCALAPPDATA%\ClaudeUsagePanel\limits.txt   (usage-feed.exe yazar, Claude Code statusLine'dan)
//   2) Claude Desktop'un plan-usage-history.json gunlugu (yedek kaynak, sifirlanma saati icermez)
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

namespace ClaudeUsagePanel
{
    static class Native
    {
        [DllImport("user32.dll")] public static extern bool ReleaseCapture();
        [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, int msg, IntPtr w, IntPtr l);
        [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, int msg, IntPtr w, IntPtr l);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int RegisterWindowMessage(string s);
        [DllImport("user32.dll")] public static extern bool DestroyIcon(IntPtr h);
        [DllImport("kernel32.dll")] public static extern bool SetProcessWorkingSetSize(IntPtr p, IntPtr min, IntPtr max);
        [DllImport("kernel32.dll")] public static extern IntPtr GetCurrentProcess();
        [DllImport("dwmapi.dll")] public static extern int DwmSetWindowAttribute(IntPtr h, int attr, ref int val, int size);
        [DllImport("dwmapi.dll")] public static extern int DwmExtendFrameIntoClientArea(IntPtr h, ref MARGINS m);
        [StructLayout(LayoutKind.Sequential)] public struct MARGINS { public int L, R, T, B; }
        public static readonly IntPtr HWND_BROADCAST = new IntPtr(0xffff);
    }

    class Cfg
    {
        public int X = int.MinValue, Y = int.MinValue;
        public bool Top = true, Compact = false, Visible = true;
        public int Opacity = 100;
        static string IniPath { get { return System.IO.Path.Combine(Store.Dir, "settings.ini"); } }

        public static Cfg Load()
        {
            var c = new Cfg();
            try
            {
                if (!File.Exists(IniPath)) return c;
                foreach (string line in File.ReadAllLines(IniPath))
                {
                    int i = line.IndexOf('=');
                    if (i <= 0) continue;
                    string k = line.Substring(0, i), v = line.Substring(i + 1).Trim();
                    int n;
                    switch (k)
                    {
                        case "x": if (int.TryParse(v, out n)) c.X = n; break;
                        case "y": if (int.TryParse(v, out n)) c.Y = n; break;
                        case "top": c.Top = v == "1"; break;
                        case "compact": c.Compact = v == "1"; break;
                        case "visible": c.Visible = v != "0"; break;
                        case "opacity": if (int.TryParse(v, out n) && n >= 30 && n <= 100) c.Opacity = n; break;
                    }
                }
            }
            catch (IOException) { }
            return c;
        }

        public void Save()
        {
            try
            {
                Directory.CreateDirectory(Store.Dir);
                File.WriteAllText(IniPath,
                    "x=" + X + "\ny=" + Y + "\ntop=" + (Top ? 1 : 0) + "\ncompact=" + (Compact ? 1 : 0) +
                    "\nvisible=" + (Visible ? 1 : 0) + "\nopacity=" + Opacity + "\n");
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
    }

    class Row
    {
        public string Title = "";
        public bool Has;            // veri var mi
        public double Pct;
        public long Reset;
        public string ResetText = "";
        public string PaceText = "";
        public Color PaceColor;
        public string Forecast = "";
        public double Ideal = -1;   // beklenen yuzde (cubuk uzerinde isaret)
        public List<PointF> Spark = new List<PointF>();
        public string SparkLabel = "";
        public string CompactTime = "";
    }

    class DarkColors : ProfessionalColorTable
    {
        static readonly Color Bg = Color.FromArgb(0x1f, 0x23, 0x2b), Sel = Color.FromArgb(0x2c, 0x32, 0x3e), Bd = Color.FromArgb(0x3a, 0x40, 0x4c);
        public override Color ToolStripDropDownBackground { get { return Bg; } }
        public override Color ImageMarginGradientBegin { get { return Bg; } }
        public override Color ImageMarginGradientMiddle { get { return Bg; } }
        public override Color ImageMarginGradientEnd { get { return Bg; } }
        public override Color MenuItemSelected { get { return Sel; } }
        public override Color MenuItemSelectedGradientBegin { get { return Sel; } }
        public override Color MenuItemSelectedGradientEnd { get { return Sel; } }
        public override Color MenuItemBorder { get { return Sel; } }
        public override Color MenuBorder { get { return Bd; } }
        public override Color SeparatorDark { get { return Bd; } }
        public override Color SeparatorLight { get { return Bd; } }
        public override Color CheckBackground { get { return Sel; } }
        public override Color CheckSelectedBackground { get { return Sel; } }
        public override Color CheckPressedBackground { get { return Sel; } }
    }

    class PanelForm : Form
    {
        // ---- boyutlar (mantiksal piksel, DPI ile olceklenir) ----
        const int W = 360, HFull = 338, HCompact = 128;

        // ---- renkler ----
        static readonly Color CBg = Color.FromArgb(0x14, 0x16, 0x1b);
        static readonly Color CCard = Color.FromArgb(0x1d, 0x20, 0x27);
        static readonly Color CText = Color.FromArgb(0xec, 0xee, 0xf3);
        static readonly Color CMuted = Color.FromArgb(0x8d, 0x95, 0xa3);
        static readonly Color CTrack = Color.FromArgb(0x2b, 0x30, 0x3a);
        static readonly Color CGreen = Color.FromArgb(0x3e, 0xcf, 0x8e);
        static readonly Color CAmber = Color.FromArgb(0xf5, 0xb9, 0x42);
        static readonly Color CRed = Color.FromArgb(0xef, 0x5b, 0x5b);
        static readonly Color CAccent = Color.FromArgb(0xd9, 0x77, 0x57);

        static readonly CultureInfo Tr = new CultureInfo("tr-TR");

        readonly Cfg cfg;
        readonly bool demo;
        readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
        readonly NotifyIcon tray = new NotifyIcon();
        readonly ContextMenuStrip menu = new ContextMenuStrip();
        ToolStripMenuItem miTop, miCompact, miAuto;
        readonly ToolStripMenuItem[] miOpac = new ToolStripMenuItem[4];
        static readonly int[] OpacVals = { 100, 90, 80, 60 };

        Snapshot cur;
        List<HistPoint> hist = new List<HistPoint>();
        readonly Row[] rows = new Row[2] { new Row(), new Row() };
        string footer = "";
        Color footerColor = CMuted;

        DateTime feedStamp = DateTime.MinValue, histStamp = DateTime.MinValue, logStamp = DateTime.MinValue;
        string logPath; DateTime logSearched = DateTime.MinValue;
        long lastMinute = -1;
        readonly HashSet<string> notified = new HashSet<string>();
        string trayKey = "";
        Icon trayIcon;
        readonly int wmShow;

        Font fBig, fTitle, fBody, fSmall, fSemi;
        readonly StringFormat sfL, sfR, sfC;

        float Sc { get { return DeviceDpi / 96f; } }

        public PanelForm(Cfg c, bool demoMode)
        {
            cfg = c; demo = demoMode;
            wmShow = Native.RegisterWindowMessage("ClaudeUsagePanel.Show.v1");

            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint |
                     ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            FormBorderStyle = FormBorderStyle.None;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.Manual;
            AutoScaleMode = AutoScaleMode.None;
            BackColor = CBg;
            Text = "Claude Kullanım";
            TopMost = cfg.Top;
            Opacity = cfg.Opacity / 100.0;

            fBig = Px(30, "Segoe UI Semibold");
            fTitle = Px(13, "Segoe UI Semibold");
            fSemi = Px(12, "Segoe UI Semibold");
            fBody = Px(12, "Segoe UI");
            fSmall = Px(11, "Segoe UI");

            sfL = MakeFmt(StringAlignment.Near);
            sfR = MakeFmt(StringAlignment.Far);
            sfC = MakeFmt(StringAlignment.Center);
            sfC.LineAlignment = StringAlignment.Center;

            BuildMenu();
            tray.ContextMenuStrip = menu;
            tray.Text = "Claude Kullanım";
            tray.MouseClick += delegate(object s, MouseEventArgs e) { if (e.Button == MouseButtons.Left) ToggleVisible(); };
            tray.Visible = !demo;

            ApplySize();
            Reload(true);

            timer.Interval = 10000;
            timer.Tick += delegate { Tick(); };
            if (!demo) timer.Start();

            SystemEvents.PowerModeChanged += delegate(object s, PowerModeChangedEventArgs e)
            {
                if (e.Mode == PowerModes.Resume) { try { BeginInvoke(new Action(delegate { Reload(true); })); } catch (InvalidOperationException) { } }
            };
        }

        static Font Px(float px, string family)
        {
            try { return new Font(family, px, FontStyle.Regular, GraphicsUnit.Pixel); }
            catch (ArgumentException) { return new Font("Segoe UI", px, FontStyle.Regular, GraphicsUnit.Pixel); }
        }

        static StringFormat MakeFmt(StringAlignment a)
        {
            var f = new StringFormat(StringFormat.GenericDefault);
            f.FormatFlags = StringFormatFlags.NoWrap;
            f.Trimming = StringTrimming.EllipsisCharacter;
            f.Alignment = a;
            return f;
        }

        protected override bool ShowWithoutActivation { get { return true; } }

        protected override CreateParams CreateParams
        {
            get
            {
                var cp = base.CreateParams;
                cp.ExStyle |= 0x80;   // WS_EX_TOOLWINDOW: Alt+Tab'ta gorunmez
                return cp;
            }
        }

        void ApplySize()
        {
            float s = Sc;
            ClientSize = new Size((int)Math.Round(W * s), (int)Math.Round((cfg.Compact ? HCompact : HFull) * s));
        }

        protected override void OnDpiChanged(DpiChangedEventArgs e) { base.OnDpiChanged(e); ApplySize(); Invalidate(); }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            try
            {
                int round = 2; Native.DwmSetWindowAttribute(Handle, 33, ref round, 4);          // yuvarlak kose (Win11)
                int border = 0x4C4030; Native.DwmSetWindowAttribute(Handle, 34, ref border, 4);  // ince kenar rengi
                var m = new Native.MARGINS(); m.L = m.R = m.T = m.B = 1;
                Native.DwmExtendFrameIntoClientArea(Handle, ref m);                              // golge
            }
            catch (Exception) { }
        }

        protected override void OnLoad(EventArgs e)
        {
            base.OnLoad(e);
            PlaceWindow();
        }

        void PlaceWindow()
        {
            Rectangle want = new Rectangle(cfg.X, cfg.Y, Width, Height);
            bool ok = false;
            if (cfg.X != int.MinValue)
                foreach (Screen sc in Screen.AllScreens)
                    if (sc.WorkingArea.IntersectsWith(want)) ok = true;
            if (ok) Location = new Point(cfg.X, cfg.Y);
            else
            {
                Rectangle wa = Screen.PrimaryScreen.WorkingArea;
                Location = new Point(wa.Right - Width - 16, wa.Bottom - Height - 16);
            }
        }

        public void ShowPanel() { if (!Visible) Show(); cfg.Visible = true; TopMost = cfg.Top; Invalidate(); }
        void ToggleVisible() { if (Visible) { Hide(); cfg.Visible = false; } else ShowPanel(); cfg.Save(); }

        protected override void WndProc(ref Message m)
        {
            if (m.Msg == wmShow && wmShow != 0) { ShowPanel(); return; }
            base.WndProc(ref m);
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            // Kapat (X) tepsiye gizler; gercek cikis menuden.
            if (e.CloseReason == CloseReason.UserClosing && !quitting) { e.Cancel = true; Hide(); cfg.Visible = false; cfg.Save(); return; }
            base.OnFormClosing(e);
        }
        bool quitting;
        void Quit()
        {
            quitting = true; cfg.Save(); tray.Visible = false; tray.Dispose();
            if (trayIcon != null) trayIcon.Dispose();
            Application.Exit();
        }

        // =============================================================== veri
        void Tick()
        {
            Reload(false);
            long minute = Store.Now() / 60;
            if (minute != lastMinute) { lastMinute = minute; Recompute(); if (Visible) Invalidate(); }
        }

        static DateTime Stamp(string path)
        {
            try { var fi = new FileInfo(path); return fi.Exists ? fi.LastWriteTimeUtc : DateTime.MinValue; }
            catch (IOException) { return DateTime.MinValue; }
        }

        string FindDesktopLog()
        {
            if (logPath != null && File.Exists(logPath)) return logPath;
            if ((DateTime.UtcNow - logSearched).TotalMinutes < 5 && logPath == null) return null;
            logSearched = DateTime.UtcNow; logPath = null;
            try
            {
                string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string cd = System.IO.Path.Combine(local, "Claude-Data");
                if (Directory.Exists(cd))
                    foreach (string d in Directory.GetDirectories(cd))
                    {
                        string p = System.IO.Path.Combine(d, "plan-usage-history.json");
                        if (File.Exists(p)) { logPath = p; return p; }
                    }
                string roam = System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Claude", "plan-usage-history.json");
                if (File.Exists(roam)) logPath = roam;
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            return logPath;
        }

        static readonly Regex RxSample = new Regex("\"t\":(\\d+),\"org\":\"[^\"]*\",\"u\":\\{([^}]*)\\}", RegexOptions.Compiled);
        static readonly Regex RxFh = new Regex("\"fh\":(\\d+(?:\\.\\d+)?)", RegexOptions.Compiled);
        static readonly Regex RxSd = new Regex("\"sd\":(\\d+(?:\\.\\d+)?)", RegexOptions.Compiled);

        static Snapshot ReadDesktopLog(string path)
        {
            try
            {
                string txt;
                using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var r = new StreamReader(fs)) txt = r.ReadToEnd();
                Snapshot best = null;
                foreach (Match m in RxSample.Matches(txt))
                {
                    string u = m.Groups[2].Value;
                    Match f = RxFh.Match(u), d = RxSd.Match(u);
                    if (!f.Success && !d.Success) continue;
                    long t = long.Parse(m.Groups[1].Value) / 1000;
                    if (best != null && t < best.Updated) continue;
                    var s = new Snapshot(); s.Updated = t; s.Source = "Claude Desktop günlüğü";
                    if (f.Success) { s.HasFive = true; s.FivePct = double.Parse(f.Groups[1].Value, CultureInfo.InvariantCulture); }
                    if (d.Success) { s.HasSeven = true; s.SevenPct = double.Parse(d.Groups[1].Value, CultureInfo.InvariantCulture); }
                    best = s;
                }
                return best;
            }
            catch (IOException) { return null; }
            catch (UnauthorizedAccessException) { return null; }
        }

        static Snapshot Merge(Snapshot feed, Snapshot log)
        {
            if (feed == null) return log;
            if (log == null || feed.Updated >= log.Updated) return feed;
            long now = Store.Now();
            var m = new Snapshot();
            m.Updated = log.Updated; m.Source = log.Source;
            m.HasFive = log.HasFive; m.FivePct = log.FivePct; m.HasSeven = log.HasSeven; m.SevenPct = log.SevenPct;
            m.FiveReset = feed.FiveReset > now ? feed.FiveReset : 0;      // pencere ayni ise sifirlanma saati hala gecerli
            m.SevenReset = feed.SevenReset > now ? feed.SevenReset : 0;
            return m;
        }

        void Reload(bool force)
        {
            if (demo) { if (cur == null) LoadDemo(); return; }
            DateTime f = Stamp(Store.LimitsPath), h = Stamp(Store.HistoryPath);
            string lp = FindDesktopLog();
            DateTime l = lp != null ? Stamp(lp) : DateTime.MinValue;
            if (!force && f == feedStamp && h == histStamp && l == logStamp) return;
            bool histChanged = force || h != histStamp;
            feedStamp = f; histStamp = h; logStamp = l;

            Snapshot feed = Store.ReadLimits();
            Snapshot log = lp != null ? ReadDesktopLog(lp) : null;
            cur = Merge(feed, log);
            if (histChanged) hist = Store.ReadHistory(Store.Now() - 7L * 86400);
            Recompute();
            CheckNotify();
            if (Visible) Invalidate();
            TrimMemory();
        }

        void LoadDemo()
        {
            long now = Store.Now();
            cur = new Snapshot();
            cur.HasFive = true; cur.FivePct = 6; cur.FiveReset = now + 4 * 3600 + 38 * 60;
            cur.HasSeven = true; cur.SevenPct = 73; cur.SevenReset = now + 16 * 3600 + 5 * 60;
            cur.Updated = now - 120; cur.Source = "Claude Code";
            hist = new List<HistPoint>();
            double sv = 0;
            for (long t = now - 6 * 86400; t <= now; t += 1800)
            {
                double day = (now - t) / 86400.0;
                sv += (day > 5.5 ? 0.4 : (((t / 1800) % 9) < 3 ? 1.1 : 0.15)) * 1.25;
                if (sv > 73) sv = 73;
                var p = new HistPoint(); p.T = t; p.HasSeven = true; p.Seven = sv;
                p.HasFive = true; p.Five = ((now - t) < 5 * 3600) ? Math.Max(0, 6 - (now - t) / 3600.0 * 1.2) : (((t / 1800) % 10) * 7 % 60);
                hist.Add(p);
            }
            Recompute();
        }

        // =============================================================== hesaplama
        static string Dur(long sec)
        {
            if (sec < 0) sec = 0;
            long d = sec / 86400, h = (sec % 86400) / 3600, m = (sec % 3600) / 60;
            if (d > 0) return d + "g " + h + "sa";
            if (h > 0) return h + "sa " + m.ToString("00") + "dk";
            return Math.Max(m, 1) + "dk";
        }

        static string Ago(long sec)
        {
            if (sec < 90) return "az önce";
            if (sec < 3600) return (sec / 60) + " dk önce";
            if (sec < 86400) return (sec / 3600) + " sa önce";
            return (sec / 86400) + " gün önce";
        }

        static string AbsText(long epoch)
        {
            DateTime t = Store.FromEpoch(epoch);
            int dd = (int)(t.Date - DateTime.Now.Date).TotalDays;
            string hm = t.ToString("HH:mm", Tr);
            if (dd == 0) return "bugün " + hm;
            if (dd == 1) return "yarın " + hm;
            return t.ToString("ddd", Tr) + " " + hm;
        }

        static Color Sev(double p) { return p >= 90 ? CRed : (p >= 70 ? CAmber : CGreen); }

        Row Build(string title, bool haveData, bool hasWin, double pct, long reset, int windowSec, bool five, long now)
        {
            var r = new Row();
            r.Title = title; r.PaceColor = CMuted;
            r.SparkLabel = five ? "son 24 sa" : "son 7 gün";
            if (!haveData) { r.Has = false; r.ResetText = "Henüz veri yok"; return r; }
            r.Has = true;

            bool expired = reset > 0 && reset <= now;
            if (expired) { r.Pct = 0; r.ResetText = "Pencere yenilendi · yeni kullanımda başlar"; r.CompactTime = "yenilendi"; }
            else if (!hasWin) { r.Pct = 0; r.ResetText = "Aktif pencere yok · kullanınca başlar"; r.CompactTime = "pencere yok"; }
            else
            {
                r.Pct = pct; r.Reset = reset;
                if (reset > 0)
                {
                    r.ResetText = "Sıfırlanma " + AbsText(reset) + " · " + Dur(reset - now) + " kaldı";
                    r.CompactTime = Dur(reset - now);
                    double remain = reset - now, total = windowSec, elapsed = total - remain;
                    if (elapsed >= total * 0.03 && remain > 0)
                    {
                        double ideal = elapsed / total * 100.0;
                        r.Ideal = Math.Min(100, ideal);
                        double delta = pct - ideal;
                        if (delta >= 5) { r.PaceText = "Beklenenden +" + Math.Round(delta) + " puan hızlı"; r.PaceColor = delta >= 20 ? CRed : CAmber; }
                        else if (delta <= -5) { r.PaceText = "Beklenenden " + Math.Round(-delta) + " puan yavaş"; r.PaceColor = CGreen; }
                        else { r.PaceText = "Tempo dengeli"; r.PaceColor = CMuted; }

                        if (pct > 0 && elapsed >= total * 0.10)
                        {
                            double rate = pct / elapsed;                  // % / sn
                            double secToFull = (100.0 - pct) / rate;
                            if (pct >= 100) r.Forecast = "Limit doldu";
                            else if (secToFull < remain) r.Forecast = "Bu hızla " + AbsText(now + (long)secToFull) + " dolar";
                            else r.Forecast = "Bu hızla sonda ≈%" + Math.Round(Math.Min(99, pct / elapsed * total));
                        }
                    }
                }
                else { r.ResetText = "Sıfırlanma saati bilinmiyor"; r.CompactTime = "?"; }
            }

            // mini grafik
            long span = five ? 24 * 3600 : 7L * 86400;
            long from = now - span;
            foreach (HistPoint p in hist)
            {
                if (p.T < from || p.T > now) continue;
                bool ok = five ? p.HasFive : p.HasSeven;
                double v = five ? p.Five : p.Seven;
                if (!ok) v = 0;
                r.Spark.Add(new PointF((float)((p.T - from) / (double)span), (float)Math.Max(0, Math.Min(1, v / 100.0))));
            }
            if (r.Spark.Count > 0) r.Spark.Add(new PointF(1f, (float)Math.Max(0, Math.Min(1, r.Pct / 100.0))));
            return r;
        }

        void Recompute()
        {
            long now = Store.Now();
            bool have = cur != null;
            rows[0] = Build("5 saatlik oturum", have, have && cur.HasFive, have ? cur.FivePct : 0, have ? cur.FiveReset : 0, 5 * 3600, true, now);
            rows[1] = Build("Haftalık limit", have, have && cur.HasSeven, have ? cur.SevenPct : 0, have ? cur.SevenReset : 0, 7 * 86400, false, now);

            if (!have) { footer = "Veri yok · sağ tık ▸ Bağlantı durumu"; footerColor = CRed; }
            else
            {
                long age = now - cur.Updated;
                if (age < 15 * 60) { footer = "Canlı · " + cur.Source + " · " + Ago(age); footerColor = CGreen; }
                else { footer = "Son veri " + Ago(age) + " · " + cur.Source + " ile güncellenir"; footerColor = age < 6 * 3600 ? CAmber : CRed; }
            }
            UpdateTray();
        }

        void CheckNotify()
        {
            if (demo || cur == null) return;
            Check("5 saatlik oturum", rows[0], cur.FiveReset);
            Check("Haftalık limit", rows[1], cur.SevenReset);
        }

        void Check(string name, Row r, long reset)
        {
            if (!r.Has || r.Pct < 90) return;
            string key = name + reset;
            if (notified.Contains(key)) return;
            notified.Add(key);
            tray.ShowBalloonTip(6000, "Claude limiti: " + name, "%" + Math.Round(r.Pct) + " kullanıldı. " + r.ResetText + ".", ToolTipIcon.Warning);
        }

        // =============================================================== tepsi
        void UpdateTray()
        {
            if (demo) return;
            string txt = rows[0].Has ? Math.Round(rows[0].Pct).ToString(CultureInfo.InvariantCulture) : "–";
            double worst = Math.Max(rows[0].Has ? rows[0].Pct : 0, rows[1].Has ? rows[1].Pct : 0);
            Color c = !rows[0].Has ? Color.FromArgb(0x55, 0x5b, 0x68) : Sev(worst);
            string key = txt + "|" + c.ToArgb();
            string tip = rows[0].Has
                ? "5 saat %" + Math.Round(rows[0].Pct) + " · Hafta %" + Math.Round(rows[1].Pct)
                : "Claude Kullanım: veri yok";
            tray.Text = tip.Length > 120 ? tip.Substring(0, 120) : tip;
            if (key == trayKey) return;
            trayKey = key;
            Icon ni = MakeIcon(txt, c);
            tray.Icon = ni;
            if (trayIcon != null) trayIcon.Dispose();
            trayIcon = ni;
        }

        static Icon MakeIcon(string text, Color bg)
        {
            using (var bmp = new Bitmap(32, 32))
            using (var g = Graphics.FromImage(bmp))
            {
                g.SmoothingMode = SmoothingMode.AntiAlias;
                g.TextRenderingHint = TextRenderingHint.AntiAliasGridFit;
                using (var br = new SolidBrush(bg))
                using (var path = RoundRect(new RectangleF(1, 1, 30, 30), 8))
                    g.FillPath(br, path);
                using (var f = new Font("Segoe UI", text.Length > 2 ? 14f : 19f, FontStyle.Bold, GraphicsUnit.Pixel))
                using (var sf = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center })
                    g.DrawString(text, f, Brushes.White, new RectangleF(0, 1, 32, 32), sf);
                IntPtr h = bmp.GetHicon();
                try { using (Icon tmp = Icon.FromHandle(h)) return (Icon)tmp.Clone(); }
                finally { Native.DestroyIcon(h); }
            }
        }

        static void TrimMemory()
        {
            GC.Collect(); GC.WaitForPendingFinalizers();
            try { Native.SetProcessWorkingSetSize(Native.GetCurrentProcess(), (IntPtr)(-1), (IntPtr)(-1)); } catch (Exception) { }
        }

        // =============================================================== menu
        ToolStripMenuItem Item(string text, EventHandler h)
        {
            var it = new ToolStripMenuItem(text);
            it.ForeColor = CText;
            if (h != null) it.Click += h;
            return it;
        }

        void BuildMenu()
        {
            menu.Renderer = new ToolStripProfessionalRenderer(new DarkColors()) { RoundedEdges = false };
            menu.BackColor = Color.FromArgb(0x1f, 0x23, 0x2b);
            menu.ShowImageMargin = false;
            menu.Font = new Font("Segoe UI", 9.5f);

            menu.Items.Add(Item("Yenile", delegate { Reload(true); }));
            menu.Items.Add(new ToolStripSeparator());
            miTop = Item("Her zaman üstte", delegate { cfg.Top = !cfg.Top; TopMost = cfg.Top; cfg.Save(); });
            miCompact = Item("Kompakt görünüm", delegate { SetCompact(!cfg.Compact); });
            menu.Items.Add(miTop);
            menu.Items.Add(miCompact);
            var op = Item("Saydamlık", null);
            for (int i = 0; i < OpacVals.Length; i++)
            {
                int v = OpacVals[i];
                miOpac[i] = Item("%" + v, delegate { cfg.Opacity = v; Opacity = v / 100.0; cfg.Save(); });
                op.DropDownItems.Add(miOpac[i]);
            }
            op.DropDown.BackColor = Color.FromArgb(0x1f, 0x23, 0x2b);
            ((ToolStripDropDownMenu)op.DropDown).ShowImageMargin = false;
            menu.Items.Add(op);
            miAuto = Item("Windows ile başlat", delegate { SetAutostart(!AutostartOn()); });
            menu.Items.Add(miAuto);
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(Item("Bağlantı durumu…", delegate { ShowStatus(); }));
            menu.Items.Add(Item("Veri klasörünü aç", delegate { try { Directory.CreateDirectory(Store.Dir); System.Diagnostics.Process.Start(Store.Dir); } catch (Exception) { } }));
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(Item("Gizle", delegate { Hide(); cfg.Visible = false; cfg.Save(); }));
            menu.Items.Add(Item("Çıkış", delegate { Quit(); }));

            menu.Opening += delegate
            {
                miTop.Checked = cfg.Top; miCompact.Checked = cfg.Compact; miAuto.Checked = AutostartOn();
                for (int i = 0; i < OpacVals.Length; i++) miOpac[i].Checked = cfg.Opacity == OpacVals[i];
            };
        }

        void SetCompact(bool on)
        {
            Point bottomRight = new Point(Right, Bottom);
            cfg.Compact = on; ApplySize();
            Location = new Point(bottomRight.X - Width, bottomRight.Y - Height);   // alt kose sabit kalsin
            cfg.X = Location.X; cfg.Y = Location.Y; cfg.Save(); Invalidate();
        }

        const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
        static bool AutostartOn()
        {
            try { using (var k = Registry.CurrentUser.OpenSubKey(RunKey)) return k != null && k.GetValue("ClaudeUsagePanel") != null; }
            catch (Exception) { return false; }
        }
        static void SetAutostart(bool on)
        {
            try
            {
                using (var k = Registry.CurrentUser.OpenSubKey(RunKey, true))
                {
                    if (k == null) return;
                    if (on) k.SetValue("ClaudeUsagePanel", "\"" + Application.ExecutablePath + "\"");
                    else k.DeleteValue("ClaudeUsagePanel", false);
                }
            }
            catch (Exception) { }
        }

        void ShowStatus()
        {
            string settings = System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".claude", "settings.json");
            bool wired = false;
            try { wired = File.Exists(settings) && File.ReadAllText(settings).Contains("usage-feed"); } catch (IOException) { }
            string msg =
                "Claude Code bağlantısı (statusLine): " + (wired ? "kurulu" : "KURULU DEĞİL — connect-claude.ps1 çalıştırılmalı") + "\n" +
                "Son besleme: " + (cur != null && cur.Source == "Claude Code" ? Ago(Store.Now() - cur.Updated) : (File.Exists(Store.LimitsPath) ? Ago(Store.Now() - Store.ReadLimits().Updated) : "hiç gelmedi")) + "\n" +
                "Geçerli kaynak: " + (cur != null ? cur.Source : "yok") + "\n" +
                "Claude Desktop günlüğü: " + (logPath ?? "bulunamadı") + "\n" +
                "Veri klasörü: " + Store.Dir + "\n\n" +
                "Bu program ağa hiç bağlanmaz; yalnızca yerel dosyaları okur.\n" +
                "Not: Claude Code limitleri her yanıtta günceller; Claude Code kullanılmıyorsa değer eski kalabilir.";
            MessageBox.Show(msg, "Claude Kullanım · Bağlantı durumu", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }

        // =============================================================== fare
        RectangleF BtnMenu { get { return new RectangleF(W - 62, 6, 26, 26); } }
        RectangleF BtnClose { get { return new RectangleF(W - 34, 6, 26, 26); } }

        protected override void OnMouseDown(MouseEventArgs e)
        {
            base.OnMouseDown(e);
            float s = Sc; var p = new PointF(e.X / s, e.Y / s);
            if (e.Button == MouseButtons.Right) { menu.Show(this, e.Location); return; }
            if (e.Button != MouseButtons.Left) return;
            if (BtnMenu.Contains(p)) { menu.Show(this, new Point((int)(BtnMenu.Left * s), (int)(BtnMenu.Bottom * s))); return; }
            if (BtnClose.Contains(p)) { Hide(); cfg.Visible = false; cfg.Save(); return; }
            Native.ReleaseCapture();
            Native.SendMessage(Handle, 0xA1, (IntPtr)2, IntPtr.Zero);   // basligi tutup surukle
        }

        protected override void OnMouseDoubleClick(MouseEventArgs e)
        {
            base.OnMouseDoubleClick(e);
            if (e.Button == MouseButtons.Left && e.Y / Sc < 40) SetCompact(!cfg.Compact);
        }

        protected override void OnResizeEnd(EventArgs e)
        {
            base.OnResizeEnd(e);
            cfg.X = Left; cfg.Y = Top; cfg.Save();
        }

        // =============================================================== cizim
        static GraphicsPath RoundRect(RectangleF r, float rad)
        {
            var p = new GraphicsPath();
            float d = Math.Min(rad * 2, Math.Min(r.Width, r.Height));
            if (d <= 0) { p.AddRectangle(r); return p; }
            p.AddArc(r.Left, r.Top, d, d, 180, 90);
            p.AddArc(r.Right - d, r.Top, d, d, 270, 90);
            p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            p.AddArc(r.Left, r.Bottom - d, d, d, 90, 90);
            p.CloseFigure();
            return p;
        }

        static void FillRound(Graphics g, Brush b, RectangleF r, float rad)
        {
            using (var p = RoundRect(r, rad)) g.FillPath(b, p);
        }

        static Color Lighten(Color c, int a) { return Color.FromArgb(Math.Min(255, c.R + a), Math.Min(255, c.G + a), Math.Min(255, c.B + a)); }

        void Str(Graphics g, string t, Font f, Color c, RectangleF r, StringFormat sf)
        {
            using (var b = new SolidBrush(c)) g.DrawString(t, f, b, r, sf);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TextRenderingHint = TextRenderingHint.ClearTypeGridFit;
            g.Clear(CBg);
            g.ScaleTransform(Sc, Sc);

            DrawHeader(g);
            if (cfg.Compact)
            {
                DrawCompactRow(g, rows[0], 40);
                DrawCompactRow(g, rows[1], 82);
            }
            else
            {
                DrawRow(g, rows[0], 44);
                DrawRow(g, rows[1], 178);
                using (var b = new SolidBrush(footerColor)) g.FillEllipse(b, 18, HFull - 21, 7, 7);
                Str(g, footer, fSmall, CMuted, new RectangleF(32, HFull - 26, W - 44, 16), sfL);
            }
        }

        void DrawHeader(Graphics g)
        {
            using (var b = new SolidBrush(CAccent)) g.FillEllipse(b, 16, 16, 9, 9);
            Str(g, "Claude Kullanım", fTitle, CText, new RectangleF(32, 11, 200, 20), sfL);

            RectangleF m = BtnMenu, x = BtnClose;
            using (var b = new SolidBrush(CMuted))
                for (int i = -1; i <= 1; i++) g.FillEllipse(b, m.Left + 13 - 1.6f + i * 5.5f, m.Top + 13 - 1.6f, 3.2f, 3.2f);
            using (var p = new Pen(CMuted, 1.6f) { StartCap = LineCap.Round, EndCap = LineCap.Round })
            {
                g.DrawLine(p, x.Left + 8, x.Top + 8, x.Right - 8, x.Bottom - 8);
                g.DrawLine(p, x.Right - 8, x.Top + 8, x.Left + 8, x.Bottom - 8);
            }
        }

        void DrawRow(Graphics g, Row r, float y)
        {
            using (var b = new SolidBrush(CCard)) FillRound(g, b, new RectangleF(12, y, 336, 126), 12);

            Str(g, r.Title, fBody, CMuted, new RectangleF(26, y + 10, 170, 16), sfL);
            Color sev = r.Has ? Sev(r.Pct) : CMuted;
            string big = r.Has ? "%" + Math.Round(r.Pct).ToString(CultureInfo.InvariantCulture) : "—";
            Str(g, big, fBig, r.Has ? CText : CMuted, new RectangleF(24, y + 26, 170, 40), sfL);

            // mini grafik
            RectangleF sp = new RectangleF(204, y + 16, 130, 38);
            DrawSpark(g, r.Spark, sp, sev);
            Str(g, r.SparkLabel, fSmall, Color.FromArgb(0x6a, 0x72, 0x80), new RectangleF(204, y + 54, 130, 14), sfR);

            // ilerleme cubugu
            RectangleF track = new RectangleF(26, y + 74, 308, 10);
            using (var b = new SolidBrush(CTrack)) FillRound(g, b, track, 5);
            if (r.Has && r.Pct > 0)
            {
                float w = (float)Math.Max(10, Math.Min(100, r.Pct) / 100.0 * track.Width);
                var fr = new RectangleF(track.Left, track.Top, w, track.Height);
                using (var br = new LinearGradientBrush(fr, Lighten(sev, -30), Lighten(sev, 25), 0f)) FillRound(g, br, fr, 5);
            }
            if (r.Ideal >= 0)
            {
                float ix = track.Left + (float)(r.Ideal / 100.0 * track.Width);
                using (var p = new Pen(Color.FromArgb(200, 255, 255, 255), 1.6f)) g.DrawLine(p, ix, track.Top - 3, ix, track.Bottom + 3);
            }

            Str(g, r.ResetText, fBody, CText, new RectangleF(26, y + 90, 310, 16), sfL);
            if (r.PaceText.Length > 0)
            {
                Str(g, r.PaceText, fSmall, r.PaceColor, new RectangleF(26, y + 106, 160, 15), sfL);
                if (r.Forecast.Length > 0) Str(g, r.Forecast, fSmall, CMuted, new RectangleF(170, y + 106, 164, 15), sfR);
            }
        }

        void DrawCompactRow(Graphics g, Row r, float y)
        {
            using (var b = new SolidBrush(CCard)) FillRound(g, b, new RectangleF(12, y, 336, 36), 10);
            Color sev = r.Has ? Sev(r.Pct) : CMuted;
            string name = r.Title.StartsWith("5") ? "5 saat" : "Hafta";
            Str(g, name, fSemi, CText, new RectangleF(24, y + 4, 60, 16), sfL);
            Str(g, r.CompactTime, fSmall, CMuted, new RectangleF(24, y + 19, 90, 14), sfL);

            RectangleF track = new RectangleF(100, y + 13, 190, 9);
            using (var b = new SolidBrush(CTrack)) FillRound(g, b, track, 4.5f);
            if (r.Has && r.Pct > 0)
            {
                float w = (float)Math.Max(9, Math.Min(100, r.Pct) / 100.0 * track.Width);
                var fr = new RectangleF(track.Left, track.Top, w, track.Height);
                using (var br = new LinearGradientBrush(fr, Lighten(sev, -30), Lighten(sev, 25), 0f)) FillRound(g, br, fr, 4.5f);
            }
            if (r.Ideal >= 0)
            {
                float ix = track.Left + (float)(r.Ideal / 100.0 * track.Width);
                using (var p = new Pen(Color.FromArgb(200, 255, 255, 255), 1.4f)) g.DrawLine(p, ix, track.Top - 2, ix, track.Bottom + 2);
            }
            string big = r.Has ? "%" + Math.Round(r.Pct).ToString(CultureInfo.InvariantCulture) : "—";
            Str(g, big, fTitle, r.Has ? CText : CMuted, new RectangleF(292, y + 8, 48, 20), sfR);
        }

        void DrawSpark(Graphics g, List<PointF> pts, RectangleF r, Color c)
        {
            using (var p = new Pen(Color.FromArgb(40, 255, 255, 255), 1f)) g.DrawLine(p, r.Left, r.Bottom, r.Right, r.Bottom);
            if (pts.Count < 2)
            {
                Str(g, "veri toplanıyor", fSmall, Color.FromArgb(0x6a, 0x72, 0x80), new RectangleF(r.Left, r.Top + 10, r.Width, 14), sfR);
                return;
            }
            var arr = new PointF[pts.Count];
            for (int i = 0; i < arr.Length; i++) arr[i] = new PointF(r.Left + pts[i].X * r.Width, r.Bottom - pts[i].Y * r.Height);

            var poly = new PointF[arr.Length + 2];
            Array.Copy(arr, poly, arr.Length);
            poly[arr.Length] = new PointF(arr[arr.Length - 1].X, r.Bottom);
            poly[arr.Length + 1] = new PointF(arr[0].X, r.Bottom);
            using (var b = new LinearGradientBrush(r, Color.FromArgb(90, c), Color.FromArgb(0, c), 90f)) g.FillPolygon(b, poly);
            using (var p = new Pen(c, 1.6f) { LineJoin = LineJoin.Round }) g.DrawLines(p, arr);
            using (var b = new SolidBrush(c)) g.FillEllipse(b, arr[arr.Length - 1].X - 2.5f, arr[arr.Length - 1].Y - 2.5f, 5, 5);
        }

        // =============================================================== giris noktasi
        public void RenderTo(string file)
        {
            var h = Handle;   // tutamac olustur
            using (var bmp = new Bitmap(Width, Height))
            {
                DrawToBitmap(bmp, new Rectangle(0, 0, Width, Height));
                bmp.Save(file, System.Drawing.Imaging.ImageFormat.Png);
            }
        }

        [STAThread]
        static int Main(string[] args)
        {
            bool demo = false, compact = false; string snap = null;
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--demo") demo = true;
                else if (args[i] == "--compact") compact = true;
                else if (args[i] == "--snapshot" && i + 1 < args.Length) { snap = args[++i]; }
            }

            Mutex mtx = null;
            if (!demo && snap == null)
            {
                bool created;
                mtx = new Mutex(true, "Local\\ClaudeUsagePanel.v1", out created);
                if (!created)
                {
                    // Zaten calisiyor: mevcut pencereyi one getir.
                    Native.PostMessage(Native.HWND_BROADCAST, Native.RegisterWindowMessage("ClaudeUsagePanel.Show.v1"), IntPtr.Zero, IntPtr.Zero);
                    return 0;
                }
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            Cfg cfg = demo ? new Cfg() : Cfg.Load();
            if (compact) cfg.Compact = true;
            var form = new PanelForm(cfg, demo);

            if (snap != null)
            {
                form.RenderTo(snap);
                return 0;
            }

            var hnd = form.Handle;                 // gizli baslasa bile tutamac olsun (ikinci calistirma pencereyi gosterebilsin)
            if (cfg.Visible || demo) form.Show();
            Application.Run(new ApplicationContext());
            if (mtx != null) mtx.ReleaseMutex();
            return 0;
        }
    }
}
