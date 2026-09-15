# Photograph the tracker's own window and nothing else.
#
# Two versions of this have now photographed something else. The first
# matched any window whose title contained "poker" and caught a browser tab
# and a chat client. The second matched strictly -- right process, exact
# title -- and still caught a browser, because matching the window was only
# half of it: it asked Windows to bring that window forward and then
# photographed the SCREEN at the window's coordinates. Windows refuses to
# raise a window on request from a background process, so the tracker stayed
# where it was and the rectangle filled with whatever happened to be on top.
#
# So the picture no longer comes from the screen. `PrintWindow` asks the
# window to draw ITSELF into a bitmap, which works whether it is in front,
# behind, or covered entirely. Nothing else can be captured, because nothing
# else is ever asked to draw.
#
# The screen is used only if PrintWindow fails outright, and then only after
# checking that the target really is the foreground window. If it is not,
# nothing is written at all: photographing the wrong window is worse than
# photographing none, and that has now been demonstrated twice.
param([string]$Out = "window.png", [string]$Title = "TraceEV")

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class Shot {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out R r);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
  public struct R { public int L, T, Rr, B; }
}
"@

$p = Get-Process pythonw, python, TraceEV -ErrorAction SilentlyContinue |
     Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -eq $Title } |
     Select-Object -First 1
if (-not $p) {
  "no window titled '$Title' belonging to this program is open"
  exit 1
}
$hwnd = $p.MainWindowHandle
[void][Shot]::ShowWindow($hwnd, 9)          # restore it if it is minimised
[void][Shot]::SetForegroundWindow($hwnd)    # nice to have; not relied on
Start-Sleep -Milliseconds 700

$r = New-Object Shot+R
[void][Shot]::GetWindowRect($hwnd, [ref]$r)
$w = $r.Rr - $r.L; $h = $r.B - $r.T
if ($w -le 0 -or $h -le 0) { "window has no size"; exit 1 }

$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
# 2 is PW_RENDERFULLCONTENT, which is what makes this work for windows that
# draw through the compositor rather than the old GDI path.
$drew = [Shot]::PrintWindow($hwnd, $hdc, 2)
$g.ReleaseHdc($hdc)

if (-not $drew) {
  if ([Shot]::GetForegroundWindow() -ne $hwnd) {
    $g.Dispose(); $bmp.Dispose()
    "'$Title' would not draw itself and is not in front -- refusing to photograph whatever is on top of it"
    exit 1
  }
  $g.CopyFromScreen($r.L, $r.T, 0, 0, $bmp.Size)
}

$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
if ($drew) { $how = "drew itself" } else { $how = "from the screen, in front" }
"captured '$($p.MainWindowTitle)' from $($p.ProcessName)  ${w}x${h} ($how) -> $Out"
