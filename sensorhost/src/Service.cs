using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;

namespace LibrePanel.Sensors;

/// <summary>The service: read, answer on the pipe, until told to stop (winservice.run).</summary>
internal static class Service
{
    public const string Name = "LibrePanelSensors";
    public const string PipeName = @"\\.\pipe\LibrePanelSensors";
    public const double RetrySeconds = 5.0;  // the pipe's name is taken: try again after this long

    public static readonly string Version =
        typeof(Service).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion ?? "0";

    /// <summary>The reader of the LibreHardwareMonitor that came with this program.</summary>
    public static Reader MakeReader()
    {
        if (Libraries.Unavailable() is string why)
            return new Reader(() => throw new LhmUnavailable(why), ThisPc.PawnioVersion, trim: null);
        return new Reader(OpenComputer.Open, ThisPc.PawnioVersion, trim: Memory.GiveBack);
    }

    /// <summary>onRunning is called once the reader runs and the pipe was tried (a name that is
    /// taken is tried again every few seconds; Windows need not wait for that).</summary>
    public static void Run(WaitHandle stop, Action? onRunning)
    {
        Reader reader = MakeReader();
        reader.Start();
        var server = new PipeServer(new Handler(reader, Version, Memory.Own));
        bool running = false;
        try
        {
            while (!stop.WaitOne(0))
            {
                try
                {
                    server.Start();
                    running = true;
                    break;
                }
                catch (Win32Exception e)
                {
                    Log.Warning("service", $"the pipe cannot be made ({e.Message}): another program holds its name; trying again in {RetrySeconds:0} s");
                    onRunning?.Invoke();
                    onRunning = null;
                    stop.WaitOne(TimeSpan.FromSeconds(RetrySeconds));
                }
            }
            onRunning?.Invoke();
            stop.WaitOne();
        }
        finally
        {
            if (running)
                server.Stop();
            reader.Close();
        }
    }
}

/// <summary>ServiceMain and the control handler, for StartServiceCtrlDispatcher (winservice._Host).</summary>
internal sealed class ServiceHost
{
    readonly ManualResetEvent stop = new(false);
    IntPtr status;
    uint checkpoint;
    // kept here: Windows calls them for as long as the service runs
    readonly Native.ServiceMainFunction main;
    readonly Native.HandlerFunctionEx handler;

    public ServiceHost()
    {
        main = ServiceMain;
        handler = Control;
    }

    void Report(uint state, uint waitHint = 0, bool failed = false)
    {
        bool pending = state == Native.SERVICE_START_PENDING || state == Native.SERVICE_STOP_PENDING;
        checkpoint = pending ? checkpoint + 1 : 0;
        var said = new Native.SERVICE_STATUS
        {
            dwServiceType = Native.SERVICE_WIN32_OWN_PROCESS,
            dwCurrentState = state,
            dwControlsAccepted = state == Native.SERVICE_RUNNING ? Native.SERVICE_ACCEPT_STOP | Native.SERVICE_ACCEPT_SHUTDOWN : 0,
            // ERROR_SERVICE_SPECIFIC_ERROR: Windows' recovery starts it again
            dwWin32ExitCode = failed ? Native.ERROR_SERVICE_SPECIFIC_ERROR : 0,
            dwServiceSpecificExitCode = failed ? 1u : 0,
            dwCheckPoint = checkpoint,
            dwWaitHint = waitHint,
        };
        if (!Native.SetServiceStatus(status, ref said))
            Log.Warning("service", $"SetServiceStatus failed: error {Marshal.GetLastWin32Error()}");
    }

    uint Control(uint code, uint eventType, IntPtr eventData, IntPtr context)
    {
        try
        {
            if (code == Native.SERVICE_CONTROL_STOP || code == Native.SERVICE_CONTROL_SHUTDOWN)
            {
                Report(Native.SERVICE_STOP_PENDING, waitHint: 15_000);
                stop.Set();
                return Native.NO_ERROR;
            }
            if (code == Native.SERVICE_CONTROL_INTERROGATE)
                return Native.NO_ERROR;
            return Native.ERROR_CALL_NOT_IMPLEMENTED;
        }
        catch (Exception e)  // (nothing may go back into Windows' call)
        {
            Log.Error("service", "a control of the service failed", e);
            return Native.NO_ERROR;
        }
    }

    void ServiceMain(uint argc, IntPtr argv)
    {
        try
        {
            status = Native.RegisterServiceCtrlHandlerExW(Service.Name, handler, IntPtr.Zero);
            if (status == IntPtr.Zero)
            {
                Log.Error("service", $"RegisterServiceCtrlHandlerEx failed: error {Marshal.GetLastWin32Error()}");
                return;
            }
            Report(Native.SERVICE_START_PENDING, waitHint: 30_000);
            try
            {
                Service.Run(stop, () => Report(Native.SERVICE_RUNNING));
            }
            catch (Exception e)  // said in the log; Windows' recovery starts it again
            {
                Log.Error("service", "the sensor service failed", e);
                Report(Native.SERVICE_STOPPED, failed: true);
                return;
            }
            Log.Info("service", "the sensor service stops");
            Report(Native.SERVICE_STOPPED);
        }
        catch (Exception e)  // (nothing may go back into Windows' call)
        {
            Log.Error("service", "the sensor service failed", e);
        }
    }

    public int Dispatch()
    {
        // SERVICE_TABLE_ENTRYW[2]: the service's name and its ServiceMain, then the end
        int entry = 2 * IntPtr.Size;
        IntPtr name = Marshal.StringToHGlobalUni(Service.Name);
        IntPtr table = Marshal.AllocHGlobal(2 * entry);
        try
        {
            Marshal.WriteIntPtr(table, 0, name);
            Marshal.WriteIntPtr(table, IntPtr.Size, Marshal.GetFunctionPointerForDelegate(main));
            Marshal.WriteIntPtr(table, entry, IntPtr.Zero);
            Marshal.WriteIntPtr(table, entry + IntPtr.Size, IntPtr.Zero);
            if (Native.StartServiceCtrlDispatcherW(table))
                return 0;
            int code = Marshal.GetLastWin32Error();
            if (code == Native.ERROR_FAILED_SERVICE_CONTROLLER_CONNECT)
            {
                Console.Error.WriteLine("This is Libre Panel's sensor service: Windows starts it. To try it by hand, as administrator: --check, or --console.");
                return 2;
            }
            Log.Error("service", $"StartServiceCtrlDispatcher failed: error {code}");
            return 1;
        }
        finally
        {
            Marshal.FreeHGlobal(table);
            Marshal.FreeHGlobal(name);
            GC.KeepAlive(main);
            GC.KeepAlive(handler);
        }
    }
}

/// <summary>--check: LibreHardwareMonitor opened on everything once, and what it finds
/// (lhm_inprocess.check with the service's opener).</summary>
internal static class Check
{
    public static int Run()
    {
        Console.WriteLine($"Libre Panel sensor service {Service.Version}");
        bool worked = Look(out List<string> lines);
        foreach (string line in lines)
            Console.WriteLine(line);
        return worked ? 0 : 1;
    }

    static bool Look(out List<string> lines)
    {
        string? why = Libraries.Unavailable();
        if (why is not null)
        {
            lines = [$"LibreHardwareMonitor cannot run in the sensor service: {why}"];
            return false;
        }
        lines = [$"LibreHardwareMonitor libraries: {Libraries.Folder}"];
        int have = Libraries.All.Count(name => File.Exists(Libraries.PathOf(name)));
        lines.Add($"libraries found: {have} of {Libraries.All.Length}");
        OpenComputer? computer = null;
        List<Node> nodes;
        bool? state;
        try
        {
            computer = OpenComputer.Open();
            nodes = computer.Nodes();
            foreach (Node node in nodes)
            {
                try
                {
                    node.Update();
                }
                catch (Exception e)  // one chip failing is not the question
                {
                    Log.Debug("check", $"could not update a part ({node.HardwareType})", e);
                }
            }
            state = Readings.Ring0State(nodes);
        }
        catch (Exception e)
        {
            lines.Add($"could not be opened: {e.GetType().Name}: {e.Message}");
            return false;
        }
        finally
        {
            if (computer is not null)
            {
                try
                {
                    computer.Close();
                }
                catch (Exception e)
                {
                    Log.Debug("check", "LibreHardwareMonitor did not close cleanly", e);
                }
            }
        }
        string? pawnio = ThisPc.PawnioVersion();
        lines.Add($"opened: {nodes.Count} devices with {nodes.Sum(node => node.Entries.Count)} sensors");
        lines.Add($"administrator rights: {(ThisPc.IsAdmin() ? "yes" : "no")}");
        lines.Add($"PawnIO driver: {(pawnio is null ? "not installed" : pawnio.Length > 0 ? pawnio : "installed")}");
        lines.Add("CPU temperature, power and clock: " + state switch
        {
            true => "available",
            false => "not available",
            null => "no such sensors",
        });
        return true;
    }
}
