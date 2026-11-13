using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;

namespace CrossPost.Desktop;
internal static class NativeWindowTheme
{
    [DllImport("dwmapi.dll", ExactSpelling=true)]
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    private static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int size);
    public static void Apply(Window window)
    {
        // Keep the standard caption, resizing and system menu; opt into its dark Windows palette.
        var enabled=1;var handle=new WindowInteropHelper(window).Handle;
        if(DwmSetWindowAttribute(handle,20,ref enabled,sizeof(int))!=0)
            _=DwmSetWindowAttribute(handle,19,ref enabled,sizeof(int));
    }
}
