using System;
using System.Collections.Generic;
using System.Runtime;
using System.Runtime.InteropServices;

namespace LibrePanel.Sensors;

/// <summary>What the service holds, as it says itself (bytes; null where it does not know).</summary>
internal sealed class ServiceMemory(long? privateSet, long? workingSet, long? managed)
{
    public long? Private { get; } = privateSet;
    public long? WorkingSet { get; } = workingSet;
    public long? Managed { get; } = managed;
}

/// <summary>The answer to each request, from what the reader read last, with what the service
/// holds (asked anew after MemorySeconds), as sensorservice/core.py's Handler answers.</summary>
internal sealed class Handler(Reader reader, string version, Func<ServiceMemory?>? memory)
{
    public const double MemorySeconds = 5.0;
    readonly object heldGate = new();
    double heldAt = double.NaN;
    ServiceMemory? held;

    public ServiceMemory? Memory()
    {
        if (memory is null)
            return null;
        double now = Clock.Now();
        lock (heldGate)
        {
            if (now - heldAt >= 0 && now - heldAt < MemorySeconds)
                return held;
            try
            {
                held = memory();
            }
            catch (Exception e)  // the answer goes on without it
            {
                Log.Debug("pipe", "what the service holds could not be asked", e);
                held = null;
            }
            heldAt = now;
            return held;
        }
    }

    public IAnswer Answer(Dictionary<string, object> message)
    {
        Request request;
        try
        {
            request = Protocol.ParseRequest(message);
        }
        catch (ProtocolError e)
        {
            return new ErrorAnswer(e.Message);
        }
        if (request.Op == "hello")
            return new HelloAnswer(version);
        reader.Ask(request.Hardware, request.Everything);
        Snapshot snapshot = reader.Latest();
        double? age = snapshot.Stamp is double stamp ? Clock.Now() - stamp : null;
        return new ReadAnswer(snapshot, age, reader.Rounds(), Memory());
    }
}

/// <summary>At most rate requests a second, after a first burst (a token bucket).</summary>
internal sealed class Limiter(double rate = Serving.Rate, int burst = Serving.Burst)
{
    double tokens = -1.0;
    double? at;

    /// <summary>How long the request that comes now has to wait.</summary>
    public double Delay(double now)
    {
        tokens = at is double then ? Math.Min(burst, tokens + (now - then) * rate) : burst;
        at = now;
        tokens -= 1.0;
        return tokens >= 0 ? 0.0 : -tokens / rate;
    }
}

/// <summary>A connection's bytes: read and written whole, or ConnectionEnded.</summary>
internal interface IStream
{
    /// <summary>Exactly size bytes, or ConnectionEnded.</summary>
    byte[] Read(int size, double timeout);

    /// <summary>The first count bytes of data, or ConnectionEnded.</summary>
    void Write(byte[] data, int count, double timeout);

    /// <summary>Pause; true when the service stops meanwhile.</summary>
    bool Wait(double seconds);
}

/// <summary>The other side went, said nothing for too long, or the service stops.</summary>
internal sealed class ConnectionEnded(string why) : Exception(why);

internal static class Serving
{
    public const double Rate = 4.0;  // requests a second a connection may make, after
    public const int Burst = 8;  // this many at once
    public const double IdleSeconds = 30.0;  // a client that asks nothing for this long is let go
    public const double MessageSeconds = 5.0;  // a message that has begun must be through within this long

    /// <summary>Answer one client until it goes; how many requests it made. A request that is
    /// no JSON object, or not one of the protocol, gets an error answer and the connection goes
    /// on; a length out of bounds ends it (nobody knows where the next message would start).</summary>
    public static int Serve(IStream stream, Handler handler, Limiter? limiter = null, double idleSeconds = IdleSeconds)
    {
        limiter ??= new Limiter();
        var output = new Bytes();
        int count = 0;
        while (true)
        {
            byte[] body;
            try
            {
                int length = Protocol.BodyLength(stream.Read(Protocol.HeaderSize, idleSeconds), Protocol.MaxRequest);
                body = stream.Read(length, MessageSeconds);
            }
            catch (Exception e) when (e is ConnectionEnded || e is ProtocolError)
            {
                Log.Debug("pipe", $"a client is let go: {e.Message}");
                return count;
            }
            count++;
            double delay = limiter.Delay(Clock.Now());
            if (delay > 0 && stream.Wait(delay))
                return count;
            IAnswer answer;
            try
            {
                answer = handler.Answer(Json.Decode(body));
            }
            catch (ProtocolError e)
            {
                answer = new ErrorAnswer(e.Message);
            }
            Protocol.Encode(answer, output, Protocol.MaxAnswer);
            try
            {
                stream.Write(output.Buffer, output.Length, MessageSeconds);
            }
            catch (ConnectionEnded)
            {
                return count;
            }
        }
    }
}

/// <summary>What the service holds, and giving back what it does not use.</summary>
internal static class Memory
{
    /// <summary>Its private working set, as the Task Manager's Memory column shows it (Windows
    /// 10 1809 and later say it; null before), its working set and .NET's heap.</summary>
    public static ServiceMemory Own()
    {
        var counters = new Native.PROCESS_MEMORY_COUNTERS_EX2();
        uint size = (uint)Marshal.SizeOf(typeof(Native.PROCESS_MEMORY_COUNTERS_EX2));
        counters.cb = size;
        long? privateSet = null, workingSet = null;
        IntPtr me = Native.GetCurrentProcess();
        if (Native.K32GetProcessMemoryInfo(me, ref counters, size))
        {
            long found = (long)counters.PrivateWorkingSetSize.ToUInt64();
            privateSet = found == 0 ? null : found;  // (0: not filled in)
            workingSet = (long)counters.WorkingSetSize.ToUInt64();
        }
        else  // (an older Windows: the counters without the private working set)
        {
            uint older = (uint)Marshal.OffsetOf(typeof(Native.PROCESS_MEMORY_COUNTERS_EX2), "PrivateWorkingSetSize").ToInt32();
            if (Native.K32GetProcessMemoryInfo(me, ref counters, older))
                workingSet = (long)counters.WorkingSetSize.ToUInt64();
        }
        return new ServiceMemory(privateSet, workingSet, GC.GetTotalMemory(false));
    }

    /// <summary>The memory the service does not use back to the system: what .NET's collector
    /// finds unused (the large objects' heap compacted too), then the pages of the working set
    /// not touched since: LibreHardwareMonitor's look at all the hardware at the start, the
    /// libraries loaded once.</summary>
    public static void GiveBack()
    {
        GCSettings.LargeObjectHeapCompactionMode = GCLargeObjectHeapCompactionMode.CompactOnce;
        GC.Collect();
        GC.WaitForPendingFinalizers();
        GC.Collect();
        Native.SetProcessWorkingSetSize(Native.GetCurrentProcess(), new IntPtr(-1), new IntPtr(-1));
    }
}
