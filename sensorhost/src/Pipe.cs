using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Threading;

namespace LibrePanel.Sensors;

/// <summary>
/// The named pipe between the sensor service and Libre Panel, made as sensorservice/winpipe.py
/// makes it: Instances instances of \\.\pipe\LibrePanelSensors, each served by a thread of its
/// own and used again for the next client. The first is made with FILE_FLAG_FIRST_PIPE_INSTANCE:
/// if another program holds the name already, the service does not join it but tries again
/// later. Who may open it is fixed then, by <see cref="Sddl"/>: the system and administrators
/// may do anything with it; people signed in at this PC may read and write its data and read
/// its attributes, nothing more; network logons may do nothing, and Windows turns away clients
/// on other computers. Every wait has an end, and stopping the service ends all of them at once.
/// </summary>
internal sealed class PipeServer
{
    public const int Instances = 4;  // clients served at once (each signed-in person's Libre Panel is one)
    public const int BufferSize = 64 * 1024;  // bytes the pipe holds each way
    // read and write its data, read its attributes and its security descriptor, wait on it
    // (FILE_READ_DATA | FILE_WRITE_DATA | FILE_READ_ATTRIBUTES | READ_CONTROL | SYNCHRONIZE);
    // not FILE_CREATE_PIPE_INSTANCE, WRITE_DAC or WRITE_OWNER
    public const uint ClientRights = 0x120083;

    /// <summary>The security descriptor of the pipe in SDDL: its DACL protected (nothing is
    /// inherited), network logons denied first, whatever else they are.</summary>
    public static string Sddl(params string[] readers)
    {
        string aces = "(D;;GA;;;NU)(A;;GA;;;SY)(A;;GA;;;BA)";
        foreach (string reader in readers.Length > 0 ? readers : new[] { "IU" })
            aces += $"(A;;0x{ClientRights:x};;;{reader})";
        return "D:P" + aces;
    }

    readonly Handler handler;
    readonly string name;
    readonly string sddl;
    readonly IntPtr stop = Events.New();
    readonly List<Thread> threads = [];

    public PipeServer(Handler handler, string name = Service.PipeName)
    {
        this.handler = handler;
        this.name = name;
        sddl = Sddl();
    }

    IntPtr Instance(bool first)
    {
        uint mode = Native.PIPE_ACCESS_DUPLEX | Native.FILE_FLAG_OVERLAPPED | (first ? Native.FILE_FLAG_FIRST_PIPE_INSTANCE : 0);
        uint kind = Native.PIPE_TYPE_BYTE | Native.PIPE_READMODE_BYTE | Native.PIPE_WAIT | Native.PIPE_REJECT_REMOTE_CLIENTS;
        if (!Native.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, out IntPtr descriptor, IntPtr.Zero))
            throw new Win32Exception(Marshal.GetLastWin32Error(), "ConvertStringSecurityDescriptorToSecurityDescriptor");
        try
        {
            var attributes = new Native.SECURITY_ATTRIBUTES
            {
                nLength = Marshal.SizeOf(typeof(Native.SECURITY_ATTRIBUTES)),
                lpSecurityDescriptor = descriptor,
                bInheritHandle = 0,
            };
            IntPtr handle = Native.CreateNamedPipeW(name, mode, kind, Instances, BufferSize, BufferSize, 0, ref attributes);
            int code = Marshal.GetLastWin32Error();  // before anything else is called
            if (handle == IntPtr.Zero || handle == Native.INVALID_HANDLE_VALUE)
                throw new Win32Exception(code, $"CreateNamedPipe {name}: error {code}");
            return handle;
        }
        finally
        {
            Native.LocalFree(descriptor);
        }
    }

    /// <summary>Make the instances and serve them. Win32Exception when the name is taken (by
    /// another program, or a service that runs already).</summary>
    public void Start()
    {
        var handles = new List<IntPtr> { Instance(first: true) };
        try
        {
            for (int number = 1; number < Instances; number++)
                handles.Add(Instance(first: false));
        }
        catch (Win32Exception)
        {
            foreach (IntPtr handle in handles)
                Native.CloseHandle(handle);
            throw;
        }
        for (int number = 0; number < handles.Count; number++)
        {
            IntPtr handle = handles[number];
            var thread = new Thread(() => Serve(handle), 256 * 1024) { Name = $"pipe-{number}", IsBackground = true };
            threads.Add(thread);
            thread.Start();
        }
        Log.Info("pipe", $"answering on {name}");
    }

    /// <summary>Wait for a client: true when one came, null when it went before it was served
    /// (try again), false when the service stops.</summary>
    bool? Connect(IntPtr handle)
    {
        IntPtr done = Events.New();
        IntPtr overlapped = OverlappedBlock.New(done);
        try
        {
            if (Native.ConnectNamedPipe(handle, overlapped))
                return true;
            int code = Marshal.GetLastWin32Error();
            if (code == Native.ERROR_PIPE_CONNECTED)
                return true;
            if (code == Native.ERROR_NO_DATA)  // it came and went already
                return null;
            if (code != Native.ERROR_IO_PENDING)
                throw new Win32Exception(code, $"ConnectNamedPipe: error {code}");
            if (Native.WaitForMultipleObjects(2, [done, stop], false, Native.INFINITE) != Native.WAIT_OBJECT_0)
            {
                Native.CancelIoEx(handle, overlapped);
                Native.GetOverlappedResult(handle, overlapped, out _, true);
                return false;
            }
            return Native.GetOverlappedResult(handle, overlapped, out _, false) ? true : null;
        }
        finally
        {
            OverlappedBlock.Free(overlapped);
            Native.CloseHandle(done);
        }
    }

    void Serve(IntPtr handle)
    {
        try
        {
            while (true)
            {
                bool? connected = Connect(handle);
                if (connected == false)
                    return;
                if (connected == true)
                {
                    using var stream = new PipeStream(handle, stop);
                    try
                    {
                        Serving.Serve(stream, handler);
                    }
                    catch (Exception e)  // one client must not end the instance
                    {
                        Log.Error("pipe", "a client could not be served", e);
                    }
                }
                Native.DisconnectNamedPipe(handle);
            }
        }
        catch (Exception e)  // (nothing may leave a thread of the service: it would end it)
        {
            Log.Error("pipe", "an instance of the pipe failed", e);
        }
        finally
        {
            Native.CloseHandle(handle);
        }
    }

    public void Stop()
    {
        Native.SetEvent(stop);
        foreach (Thread thread in threads)
            thread.Join(TimeSpan.FromSeconds(10));
        Native.CloseHandle(stop);
    }
}

/// <summary>An open pipe handle, read and written with overlapped I/O: each operation waits for
/// its end, its time or the service's stop (a Win32 event), whichever comes first, and one that
/// is cut off is cancelled, and waited for, before its buffer is let go.</summary>
internal sealed class PipeStream : IStream, IDisposable
{
    readonly IntPtr handle;
    readonly IntPtr stop;
    readonly IntPtr done = Events.New();
    readonly IntPtr overlapped;  // one operation's at a time

    public PipeStream(IntPtr handle, IntPtr stop)
    {
        this.handle = handle;
        this.stop = stop;
        overlapped = OverlappedBlock.New(done);
    }

    public byte[] Read(int size, double timeout)
    {
        double deadline = Clock.Now() + timeout;
        var data = new byte[size];
        int got = 0;
        while (got < size)
            got += Io(false, data, got, size - got, deadline);
        return data;
    }

    public void Write(byte[] data, int count, double timeout)
    {
        double deadline = Clock.Now() + timeout;
        int sent = 0;
        while (sent < count)
            sent += Io(true, data, sent, count - sent, deadline);
    }

    public bool Wait(double seconds) => Native.WaitForSingleObject(stop, Milliseconds(seconds)) == Native.WAIT_OBJECT_0;

    int Io(bool write, byte[] data, int offset, int count, double deadline)
    {
        GCHandle pinned = GCHandle.Alloc(data, GCHandleType.Pinned);
        try
        {
            OverlappedBlock.Reset(overlapped, done);
            IntPtr at = pinned.AddrOfPinnedObject() + offset;
            bool finished = write
                ? Native.WriteFile(handle, at, (uint)count, IntPtr.Zero, overlapped)
                : Native.ReadFile(handle, at, (uint)count, IntPtr.Zero, overlapped);
            if (!finished)
            {
                int code = Marshal.GetLastWin32Error();
                if (code != Native.ERROR_IO_PENDING)
                    throw new ConnectionEnded($"pipe error {code}");
                Finish(deadline);
            }
            if (!Native.GetOverlappedResult(handle, overlapped, out uint moved, true))
                throw new ConnectionEnded($"pipe error {Marshal.GetLastWin32Error()}");
            if (moved == 0)
                throw new ConnectionEnded("the other side closed the pipe");
            return (int)moved;
        }
        finally
        {
            pinned.Free();
        }
    }

    /// <summary>Wait for the operation; cancel it (and wait for that) when its time is up or
    /// the service stops.</summary>
    void Finish(double deadline)
    {
        if (Native.WaitForMultipleObjects(2, [done, stop], false, Milliseconds(deadline - Clock.Now())) == Native.WAIT_OBJECT_0)
            return;
        Native.CancelIoEx(handle, overlapped);
        Native.GetOverlappedResult(handle, overlapped, out _, true);
        throw new ConnectionEnded(Native.WaitForSingleObject(stop, 0) == Native.WAIT_OBJECT_0 ? "stop" : "timed out");
    }

    static uint Milliseconds(double seconds) => (uint)Math.Max(0.0, Math.Min(seconds * 1000.0, 3_600_000.0));

    public void Dispose()
    {
        OverlappedBlock.Free(overlapped);
        Native.CloseHandle(done);
    }
}

/// <summary>Win32 events, reset by hand.</summary>
internal static class Events
{
    public static IntPtr New()
    {
        IntPtr made = Native.CreateEventW(IntPtr.Zero, true, false, null);
        if (made == IntPtr.Zero)
            throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateEvent");
        return made;
    }
}

/// <summary>An OVERLAPPED in memory of its own, where the operation it is given to may write
/// until it is over.</summary>
internal static class OverlappedBlock
{
    static readonly int Size = Marshal.SizeOf(typeof(NativeOverlapped));

    public static IntPtr New(IntPtr done)
    {
        IntPtr made = Marshal.AllocHGlobal(Size);
        Reset(made, done);
        return made;
    }

    public static void Reset(IntPtr overlapped, IntPtr done)
    {
        Native.ResetEvent(done);
        Marshal.StructureToPtr(new NativeOverlapped { EventHandle = done }, overlapped, false);
    }

    public static void Free(IntPtr overlapped) => Marshal.FreeHGlobal(overlapped);
}
