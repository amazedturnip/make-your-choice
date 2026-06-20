using System;
using System.Collections.Generic;
using System.Drawing;
using System.Linq;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace MakeYourChoice
{
    public partial class Form1
    {
        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool DestroyIcon(IntPtr handle);

        // Two tray indicators (sourced from Dead by Queue):
        //   _trayServer = is your preferred server online?  (green dot = online, red = offline)
        //   _trayQueue  = current killer queue time          (minutes drawn as the icon)
        private NotifyIcon _trayServer;
        private NotifyIcon _trayQueue;
        private IntPtr _trayServerHandle = IntPtr.Zero;
        private IntPtr _trayQueueHandle = IntPtr.Zero;
        private ContextMenuStrip _trayMenu;
        private Timer _dbqTimer;
        private bool _minimizeBalloonShown;
        private bool _exiting;

        // AWS region code -> online(true)/offline(false), from Dead by Queue /regions.
        // Read by the latency list to show a ✓ / ⚠ next to unstable servers.
        private readonly Dictionary<string, bool> _dbqOnline = new();

        private void SetupTray()
        {
            if (_trayServer != null) return; // already set up

            _trayMenu = new ContextMenuStrip();
            _trayMenu.Items.Add("Show Make Your Choice", null, (_, __) => RestoreFromTray());
            _trayMenu.Items.Add(new ToolStripSeparator());
            _trayMenu.Items.Add("Exit", null, (_, __) => ExitFromTray());

            _trayServer = new NotifyIcon
            {
                Text = "Preferred server: waiting…",
                Visible = true,
                ContextMenuStrip = _trayMenu,
            };
            _trayServer.DoubleClick += (_, __) => RestoreFromTray();

            _trayQueue = new NotifyIcon
            {
                Text = "Killer queue: waiting…",
                Visible = true,
                ContextMenuStrip = _trayMenu,
            };
            _trayQueue.DoubleClick += (_, __) => RestoreFromTray();

            SetTrayIcon(_trayServer, ref _trayServerHandle, MakeDotIcon(Color.Gray));
            SetTrayIcon(_trayQueue, ref _trayQueueHandle, MakeNumberIcon("…"));
        }

        private void StartDbqTimer()
        {
            _dbqTimer = new Timer { Interval = 30_000 };
            _dbqTimer.Tick += async (_, __) => await RefreshDbqAsync();
            _dbqTimer.Start();
            _ = RefreshDbqAsync(); // immediate first fetch
        }

        private async System.Threading.Tasks.Task RefreshDbqAsync()
        {
            if (_exiting || IsDisposed) return;

            // 1) Region online/offline map (also drives the latency list ✓/⚠).
            var status = await DbqClient.GetRegionStatusAsync();
            if (status.Count > 0)
            {
                _dbqOnline.Clear();
                foreach (var kv in status) _dbqOnline[kv.Key] = kv.Value;
            }

            // 2) Preferred server status + its queue time.
            var preferred = GetPreferredRegionKey();
            if (preferred == null)
            {
                if (_trayServer != null) _trayServer.Text = "Preferred server: select a region";
                SetTrayIcon(_trayServer, ref _trayServerHandle, MakeDotIcon(Color.Gray));
                if (_trayQueue != null) _trayQueue.Text = "Killer queue: select a region";
                SetTrayIcon(_trayQueue, ref _trayQueueHandle, MakeNumberIcon("–"));
                return;
            }

            var code = AwsCodeForRegion(preferred);
            bool? online = (code != null && _dbqOnline.TryGetValue(code, out var on)) ? on : (bool?)null;

            var shortName = preferred.Contains("(")
                ? preferred.Substring(preferred.IndexOf('(') + 1).TrimEnd(')')
                : preferred;

            Color dot = online == true ? Color.LimeGreen : online == false ? Color.Red : Color.Gray;
            string state = online == true ? "ONLINE" : online == false ? "OFFLINE" : "unknown";
            SetTrayIcon(_trayServer, ref _trayServerHandle, MakeDotIcon(dot));
            if (_trayServer != null) _trayServer.Text = Trunc($"{shortName}: {state}");

            var (queueText, minutes) = await DbqClient.GetQueueAsync(code ?? "");
            SetTrayIcon(_trayQueue, ref _trayQueueHandle,
                MakeNumberIcon(minutes >= 0 ? minutes.ToString() : "?"));
            if (_trayQueue != null) _trayQueue.Text = Trunc($"{shortName} queue — {queueText}");
        }

        // The region the tray reports on: first checked unstable region, else first checked region.
        private string GetPreferredRegionKey()
        {
            if (_lv == null || _lv.IsDisposed) return null;
            var checkedKeys = _lv.CheckedItems.Cast<ListViewItem>()
                .Select(i => i.Tag as string)
                .Where(s => s != null && _regions.ContainsKey(s))
                .ToList();
            if (checkedKeys.Count == 0) return null;
            var unstable = checkedKeys.FirstOrDefault(k => !_regions[k].Stable);
            return unstable ?? checkedKeys[0];
        }

        private static string Trunc(string s) => s.Length > 120 ? s.Substring(0, 119) + "…" : s;

        private void SetTrayIcon(NotifyIcon ni, ref IntPtr handleStore, Bitmap bmp)
        {
            if (ni == null) { bmp.Dispose(); return; }
            IntPtr h = bmp.GetHicon();
            try
            {
                ni.Icon = Icon.FromHandle(h);
                if (handleStore != IntPtr.Zero) DestroyIcon(handleStore);
                handleStore = h;
            }
            finally
            {
                bmp.Dispose();
            }
        }

        private static Bitmap MakeDotIcon(Color color)
        {
            var bmp = new Bitmap(32, 32);
            using var g = Graphics.FromImage(bmp);
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            g.Clear(Color.Transparent);
            using var brush = new SolidBrush(color);
            g.FillEllipse(brush, 4, 4, 24, 24);
            using var pen = new Pen(Color.FromArgb(120, 0, 0, 0), 2);
            g.DrawEllipse(pen, 4, 4, 24, 24);
            return bmp;
        }

        private static Bitmap MakeNumberIcon(string text)
        {
            var bmp = new Bitmap(32, 32);
            using var g = Graphics.FromImage(bmp);
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAlias;
            g.Clear(Color.Transparent);
            float size = text.Length >= 3 ? 16f : 22f;
            using var font = new Font("Segoe UI", size, FontStyle.Bold, GraphicsUnit.Pixel);
            using var fmt = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center };
            var rect = new RectangleF(0, 0, 32, 32);
            using var shadow = new SolidBrush(Color.FromArgb(180, 0, 0, 0));
            g.DrawString(text, font, shadow, new RectangleF(1, 1, 32, 32), fmt);
            using var brush = new SolidBrush(Color.Aqua);
            g.DrawString(text, font, brush, rect, fmt);
            return bmp;
        }

        // Minimize-to-tray: minimizing hides the window (and its taskbar button); the tray icons remain.
        protected override void OnResize(EventArgs e)
        {
            base.OnResize(e);
            if (WindowState == FormWindowState.Minimized)
            {
                Hide();
                if (!_minimizeBalloonShown && _trayServer != null)
                {
                    _minimizeBalloonShown = true;
                    _trayServer.BalloonTipTitle = "Make Your Choice";
                    _trayServer.BalloonTipText = "Still running in the system tray. Double-click to restore.";
                    try { _trayServer.ShowBalloonTip(2000); } catch { }
                }
            }
        }

        private void RestoreFromTray()
        {
            if (IsDisposed) return;
            Show();
            WindowState = FormWindowState.Normal;
            ShowInTaskbar = true;
            Activate();
            BringToFront();
        }

        private void ExitFromTray()
        {
            _exiting = true;
            Close();
        }

        private void DisposeTray()
        {
            _exiting = true;
            try { _dbqTimer?.Stop(); _dbqTimer?.Dispose(); } catch { }
            if (_trayServer != null) { _trayServer.Visible = false; _trayServer.Dispose(); }
            if (_trayQueue != null) { _trayQueue.Visible = false; _trayQueue.Dispose(); }
            if (_trayServerHandle != IntPtr.Zero) { DestroyIcon(_trayServerHandle); _trayServerHandle = IntPtr.Zero; }
            if (_trayQueueHandle != IntPtr.Zero) { DestroyIcon(_trayQueueHandle); _trayQueueHandle = IntPtr.Zero; }
            _trayMenu?.Dispose();
        }
    }
}
