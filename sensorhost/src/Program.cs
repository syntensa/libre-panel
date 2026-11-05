using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading;
using Microsoft.Win32;

namespace LibrePanel.Sensors;

/// <summary>
/// libre-panel-sensors.exe, Libre Panel's sensor service (Windows), as the Python one
/// (sensorservice/winservice.py) is started: by the Service Control Manager, or by hand, as
/// administrator, with --check (open LibreHardwareMonitor once, say what it finds, end: the
/// build's smoke test) or --console (run the service in the terminal until Ctrl+C). It takes no
/// other arguments from anybody: what it reads, where its libraries are and where it writes its
/// log follow from where it lies, a folder only administrators may change.
/// </summary>
internal static class Program
{
    const string Usage = "usage: libre-panel-sensors [-h] [--check] [--console] [-v]";

    static int Main(string[] args)
    {
        bool check = false, console = false, verbose = false;
        foreach (string arg in args)
        {
            switch (arg)
            {
                case "--check": check = true; break;
                case "--console": console = true; break;
                case "-v": case "--verbose": verbose = true; break;
                case "-h": case "--help":
                    Console.WriteLine(Usage);
                    Console.WriteLine();
                    Console.WriteLine("Libre Panel's sensor service (Windows).");
                    Console.WriteLine();
                    Console.WriteLine("  --check    open the sensors once, then end");
                    Console.WriteLine("  --console  run in this terminal");
                    Console.WriteLine("  -v         say more in the log");
                    return 0;
                default:
                    Console.Error.WriteLine(Usage);
                    Console.Error.WriteLine($"libre-panel-sensors: error: unrecognized arguments: {arg}");
                    return 2;
            }
        }
        Log.Verbose = verbose;
        AppDomain.CurrentDomain.UnhandledException += (_, e) =>
            Log.Error("service", "the sensor service ends: an error that nothing caught", e.ExceptionObject as Exception);
        Libraries.Resolve();
        Unblock.Program();  // (before LibreHardwareMonitor is loaded)
        if (check)
            return Check.Run();
        Log.ToFile();
        if (console)
        {
            var stop = new ManualResetEvent(false);
            Console.CancelKeyPress += (_, e) =>
            {
                e.Cancel = true;
                stop.Set();
            };
            Service.Run(stop, () => Console.WriteLine($"answering; Ctrl+C ends ({Service.Version})"));
            return 0;
        }
        return new ServiceHost().Dispatch();
    }
}

/// <summary>Seconds on a clock that only goes forward (Python's time.monotonic).</summary>
internal static class Clock
{
    static readonly double Tick = 1.0 / Stopwatch.Frequency;

    public static double Now() => Stopwatch.GetTimestamp() * Tick;
}

/// <summary>Texts as the protocol cuts them: by characters (code points), as Python counts.</summary>
internal static class Text
{
    public static string Cut(string text, int most)
    {
        int count = 0;
        for (int i = 0; i < text.Length; i++)
        {
            if (count == most)
                return text.Substring(0, i);
            if (char.IsHighSurrogate(text[i]) && i + 1 < text.Length && char.IsLowSurrogate(text[i + 1]))
                i++;
            count++;
        }
        return text;
    }
}

/// <summary>What the service says on the way: to the terminal (--check, --console) and, once
/// <see cref="ToFile"/> was called, to logs\service.log next to the program (a folder only
/// administrators may change; never one another user could have made first), a megabyte and
/// one older one, as the Python service's log.</summary>
internal static class Log
{
    const long LogBytes = 1_000_000;
    static readonly object Gate = new();
    static string? file;

    public static bool Verbose;

    public static void Debug(string name, string message, Exception? error = null)
    {
        if (Verbose)
            Write("DEBUG", name, message, error);
    }

    public static void Info(string name, string message) => Write("INFO", name, message, null);

    public static void Warning(string name, string message, Exception? error = null) => Write("WARNING", name, message, error);

    public static void Error(string name, string message, Exception? error = null) => Write("ERROR", name, message, error);

    public static void ToFile()
    {
        string folder = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "logs");
        try
        {
            Directory.CreateDirectory(folder);
        }
        catch (Exception e) when (e is IOException || e is UnauthorizedAccessException)
        {
            return;
        }
        lock (Gate)
            file = Path.Combine(folder, "service.log");
    }

    static void Write(string level, string name, string message, Exception? error)
    {
        string text = error is null ? message : message + Environment.NewLine + error;
        lock (Gate)
        {
            try
            {
                Console.Error.WriteLine($"{level} {name}: {text}");
            }
            catch (IOException)
            {
            }
            if (file is null)
                return;
            try
            {
                string stamp = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss,fff", CultureInfo.InvariantCulture);
                byte[] line = Encoding.UTF8.GetBytes($"{stamp} {level} {name}: {text}{Environment.NewLine}");
                var info = new FileInfo(file);
                if (info.Exists && info.Length + line.Length >= LogBytes)
                {
                    File.Delete(file + ".1");
                    File.Move(file, file + ".1");
                }
                using var stream = new FileStream(file, FileMode.Append, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete);
                stream.Write(line, 0, line.Length);
            }
            catch (Exception e) when (e is IOException || e is UnauthorizedAccessException)
            {
            }
        }
    }
}

/// <summary>LibreHardwareMonitor's libraries, in the folder "lhm" next to the program, loaded by
/// their name whichever version a library asks for (as pythonnet loads them for the Python
/// service, and as packaging/windows/fetch_lhm.py pins them, the versions they ask for).</summary>
internal static class Libraries
{
    public const string Library = "LibreHardwareMonitorLib";

    // in the order the Python service loads them (lhm_inprocess.LIBRARIES)
    public static readonly string[] All =
    [
        "System.Memory", "System.Buffers", "System.Numerics.Vectors",
        "System.Runtime.CompilerServices.Unsafe", "BlackSharp.Core", "RAMSPDToolkit-NDD",
        "DiskInfoToolkit", "HidSharp", Library,
    ];

    public static readonly HashSet<string> Optional = new(StringComparer.Ordinal) { "DiskInfoToolkit", "HidSharp" };

    public static readonly string Folder = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "lhm");

    public static string PathOf(string name) => Path.Combine(Folder, name + ".dll");

    public static void Resolve() => AppDomain.CurrentDomain.AssemblyResolve += Find;

    static Assembly? Find(object? sender, ResolveEventArgs args)
    {
        string? name = new AssemblyName(args.Name).Name;
        if (string.IsNullOrEmpty(name))
            return null;
        // (the very file once loaded is the same assembly again: LoadFrom knows its path)
        string path = PathOf(name!);
        return File.Exists(path) ? Assembly.LoadFrom(path) : null;
    }

    /// <summary>Why LibreHardwareMonitor cannot be opened here; null when it can (its library is
    /// there; one that is missing of those it needs costs some sensors, and the log says so).</summary>
    public static string? Unavailable()
    {
        if (!File.Exists(PathOf(Library)))
            return $"{Library}.dll was not found in {Folder}";
        var missing = new List<string>();
        foreach (string name in All)
        {
            if (!Optional.Contains(name) && !File.Exists(PathOf(name)))
                missing.Add(name);
        }
        if (missing.Count > 0)
            Log.Warning("service", $"{Folder} lacks {string.Join(", ", missing)}: some sensors may be missing");
        return null;
    }
}

/// <summary>The mark Windows puts on what comes from the internet, off the program's own files
/// (libre_panel/unblock.py): one look at the library .NET loads first, all files where it has
/// the mark (a zip unpacked by Explorer passes it on, and copies keep it).</summary>
internal static class Unblock
{
    const string Stream = ":Zone.Identifier";

    public static void Program()
    {
        if (Native.GetFileAttributesW(Libraries.PathOf(Libraries.Library) + Stream) == Native.INVALID_FILE_ATTRIBUTES)
            return;
        string folder = AppDomain.CurrentDomain.BaseDirectory;
        int taken = 0;
        try
        {
            foreach (string path in Directory.EnumerateFiles(folder, "*", SearchOption.AllDirectories))
            {
                if (Native.DeleteFileW(path + Stream))
                    taken++;
            }
        }
        catch (Exception e) when (e is IOException || e is UnauthorizedAccessException)
        {
            Log.Debug("service", "the download mark stays on some files", e);
        }
        Log.Info("service", $"took the download mark off {taken} files in {folder} (else .NET would not load them)");
    }
}

/// <summary>What Windows says of this PC that LibreHardwareMonitor does not.</summary>
internal static class ThisPc
{
    const string PawnioKey = @"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO";

    /// <summary>The version of the installed PawnIO driver ("" if it does not say), null where
    /// it is not installed. LibreHardwareMonitor looks in the same place.</summary>
    public static string? PawnioVersion()
    {
        foreach (RegistryView view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
        {
            try
            {
                using RegistryKey root = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, view);
                using RegistryKey? key = root.OpenSubKey(PawnioKey);
                if (key is null)
                    continue;
                return key.GetValue("DisplayVersion")?.ToString() ?? "";
            }
            catch (Exception e) when (e is IOException || e is UnauthorizedAccessException || e is System.Security.SecurityException)
            {
            }
        }
        return null;
    }

    public static bool IsAdmin()
    {
        try
        {
            return Native.IsUserAnAdmin();
        }
        catch (Exception e) when (e is EntryPointNotFoundException || e is DllNotFoundException)
        {
            return false;
        }
    }
}
