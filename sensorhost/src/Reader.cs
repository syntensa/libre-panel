using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;

namespace LibrePanel.Sensors;

/// <summary>What the reader read last, as the answers give it.</summary>
internal sealed class Snapshot(string state, string error, Sensor[] sensors, bool? ring0, string? pawnio, double? stamp)
{
    public static readonly Snapshot Starting = new("starting", "", [], null, null, null);

    public string State { get; } = state;  // "starting", "running" or "failed"
    public string Error { get; } = error;
    public Sensor[] Sensors { get; } = sensors;
    public bool? Ring0 { get; } = ring0;
    public string? Pawnio { get; } = pawnio;
    public double? Stamp { get; } = stamp;  // when, on the reader's clock; null before the first time

    public Snapshot At(double when) => new(State, Error, Sensors, Ring0, Pawnio, when);
}

/// <summary>How long one piece of hardware took in a round: its Update() and the reading of its
/// sensors after it.</summary>
internal sealed class PartTime(string hardwareId, string name, string kind, double seconds)
{
    public string HardwareId { get; } = hardwareId;
    public string Name { get; } = name;
    public string Kind { get; } = kind;  // LibreHardwareMonitor's HardwareType: Cpu, GpuNvidia, ...
    public double Seconds { get; } = seconds;
}

/// <summary>One round of updates; full: it updated all hardware, not only what was asked for.</summary>
internal sealed class RoundTime(PartTime[] parts, bool full)
{
    public static readonly RoundTime Nothing = new([], false);

    public PartTime[] Parts { get; } = parts;
    public bool Full { get; } = full;
}

/// <summary>What the rounds cost: the last one, and the last that updated all hardware.</summary>
internal sealed class Timing(RoundTime? last, RoundTime? full)
{
    public static readonly Timing None = new(null, null);

    public RoundTime? Last { get; } = last;
    public RoundTime? Full { get; } = full;

    public Timing After(RoundTime done) => new(done, done.Full ? done : Full);
}

internal sealed class LhmUnavailable(string why) : Exception(why);

/// <summary>
/// LibreHardwareMonitor's Computer, opened and read by a thread of the service, as the Python
/// service's reader (sensorservice/core.py: Reader) does: only the hardware a client names is
/// read, once a second, for ten seconds after it was named, and every piece once a minute while
/// anyone asks; the first request after a quiet time reads everything at once.
/// </summary>
internal sealed class Reader
{
    public const double PollSeconds = 1.0;  // how often the hardware that is asked for is read
    public const double RequestSeconds = 10.0;  // a piece named in a request is read for this long
    public const double RefreshSeconds = 60.0;  // while anyone asks, every piece is read this often
    public const double PauseSeconds = 0.002;  // between two pieces of a round
    public const int MaxAsked = 512;  // pieces of hardware kept in mind at once
    public const double TrimSeconds = 600.0;  // the memory not used since goes back this often
    const string Name = "reader";

    readonly Func<OpenComputer> opener;
    readonly Func<string?> pawnio;
    // what gives the memory not used since back to the system: once everything was read the
    // first time (LibreHardwareMonitor's look at all the hardware), then every TrimSeconds
    readonly Action? trim;
    readonly double poll;
    double? trimmed;
    Timing timing = Timing.None;
    List<Node> nodes = [];  // read by the thread only
    Dictionary<string, double> asked = new(StringComparer.Ordinal);  // hardware id -> when a client last named it
    double? lastRequest;  // when a client last asked at all
    bool everything;  // every piece in the next round
    double? full;  // when every piece was last read
    readonly HashSet<string> failed = new(StringComparer.Ordinal);  // hardware whose update raised (said once)
    Snapshot snapshot = Snapshot.Starting;
    readonly object gate = new();
    readonly ManualResetEvent stop = new(false);
    readonly AutoResetEvent wake = new(false);  // a request after a quiet time: read at once
    readonly Thread thread;

    public Reader(Func<OpenComputer> opener, Func<string?> pawnio, Action? trim, double pollSeconds = PollSeconds)
    {
        this.opener = opener;
        this.pawnio = pawnio;
        this.trim = trim;
        poll = Math.Max(0.05, pollSeconds);
        thread = new Thread(Run) { Name = "lhm-service", IsBackground = true };
    }

    public void Start() => thread.Start();

    // -- the thread -------------------------------------------------------------------------------

    void Run()
    {
        OpenComputer? computer = null;
        try
        {
            try
            {
                computer = opener();
                Begin(computer);
            }
            catch (Exception e)
            {
                Log.Warning(Name, $"LibreHardwareMonitor did not start: {e.Message}", e);
                lock (gate)
                    snapshot = new Snapshot("failed", $"{e.GetType().Name}: {e.Message}", [], null, null, null);
                return;
            }
            TrimNow();
            double delay = poll;
            while (!stop.WaitOne(0))
            {
                wake.WaitOne(TimeSpan.FromSeconds(delay));
                if (stop.WaitOne(0))
                    break;
                double started = Clock.Now();
                try
                {
                    Refresh();
                }
                catch (Exception e)  // one bad round must not end the service
                {
                    Log.Warning(Name, "LibreHardwareMonitor could not be read", e);
                }
                // what a round made is garbage at once: collected now, the memory the service
                // touches stays what a round makes; left to itself, .NET lets it pile up to a
                // budget the processor's cache sets (some 30 MB on a PC with a large cache)
                GC.Collect(0, GCCollectionMode.Forced, blocking: true);
                if (trimmed is double at && started - at >= TrimSeconds)
                    TrimNow();
                delay = Math.Max(0.05, poll - (Clock.Now() - started));
            }
        }
        catch (Exception e)  // (nothing may leave a thread of the service: it would end it)
        {
            Log.Error(Name, "the reader stops", e);
        }
        finally
        {
            if (computer is not null)
            {
                try
                {
                    computer.Close();
                }
                catch (Exception e)  // closing at the end changes nothing now
                {
                    Log.Debug(Name, "LibreHardwareMonitor did not close cleanly", e);
                }
            }
        }
    }

    void TrimNow()
    {
        if (trim is null)
            return;
        trimmed = Clock.Now();
        try
        {
            trim();
        }
        catch (Exception e)  // the memory stays: the readings go on
        {
            Log.Debug(Name, "the memory stays with the service", e);
        }
    }

    /// <summary>Everything the computer has, read once in full (so that every sensor is known).</summary>
    void Begin(OpenComputer computer)
    {
        nodes = computer.Nodes();
        var entries = new List<Entry>();
        foreach (Node node in nodes)
            entries.AddRange(node.Entries);
        Lhm.Forget(entries);
        foreach (Node node in nodes)
            Update(node, fresh: false);  // Nodes() has just updated it
        full = Clock.Now();
        Log.Info(Name, $"LibreHardwareMonitor is open: {nodes.Count} parts, {entries.Count} sensors");
        Publish();
    }

    void Update(Node node, bool fresh = true)
    {
        try
        {
            if (fresh)
                node.Update();
            else
                node.Read();
        }
        catch (Exception e)  // LibreHardwareMonitor's exceptions come as all kinds
        {
            if (failed.Add(node.HardwareId))
                Log.Warning(Name, $"could not update a part ({node.HardwareType}): {e.Message}");
            return;
        }
        failed.Remove(node.HardwareId);
    }

    /// <summary>One round: read what was asked for (everything, now and then). False when
    /// nobody asked, and nothing was read.</summary>
    public bool Refresh()
    {
        double now = Clock.Now();
        HashSet<string> recent;
        bool all;
        lock (gate)
        {
            recent = new HashSet<string>(asked.Where(item => now - item.Value <= RequestSeconds).Select(item => item.Key), StringComparer.Ordinal);
            asked = asked.Where(item => recent.Contains(item.Key)).ToDictionary(item => item.Key, item => item.Value, StringComparer.Ordinal);
            all = everything;
            everything = false;
            bool asking = lastRequest is double last && now - last <= RequestSeconds;
            if (recent.Count == 0 && !all)
            {
                // clients ask, for none of the hardware: what was read stays good, only nobody
                // reads anything now
                if (asking && snapshot.State == "running")
                    snapshot = snapshot.At(now);
                timing = timing.After(RoundTime.Nothing);  // a round that reads nothing
                return false;
            }
        }
        bool whole = all || full is null || now - full.Value >= RefreshSeconds;
        // a full round reads all hardware, also a part without sensors yet: LibreHardwareMonitor
        // lists some only once they have a value (a fan that starts), and the update finds them
        var round = nodes.Where(node => whole || (node.Entries.Count > 0 && recent.Contains(node.HardwareId))).ToList();
        var parts = new PartTime[round.Count];
        for (int k = 0; k < round.Count; k++)
        {
            if (k > 0)
                stop.WaitOne(TimeSpan.FromSeconds(PauseSeconds));  // between two pieces of a round
            double started = Clock.Now();
            Update(round[k]);
            double took = Math.Max(0.0, Clock.Now() - started);
            parts[k] = new PartTime(round[k].HardwareId, round[k].Name, round[k].HardwareType, took);
        }
        if (whole)
            full = now;
        lock (gate)
            timing = timing.After(new RoundTime(parts, whole));
        Publish();
        return true;
    }

    void Publish()
    {
        bool? ring0 = Readings.Ring0State(nodes);
        Sensor[] sensors = Readings.Live(nodes, ring0).ToArray();
        string? version = pawnio();
        lock (gate)
            snapshot = new Snapshot("running", "", sensors, ring0, version, Clock.Now());
    }

    // -- for the answers --------------------------------------------------------------------------

    /// <summary>A client wants this hardware read (everything: all of it, once). The first
    /// request after a quiet time reads everything at once: what was read last is old.</summary>
    public void Ask(IEnumerable<string> hardware, bool all = false)
    {
        double now = Clock.Now();
        bool quiet;
        lock (gate)
        {
            double? last = lastRequest;
            lastRequest = now;
            quiet = last is null || now - last.Value > RequestSeconds;
            foreach (string id in hardware)
                asked[id] = now;
            if (asked.Count > MaxAsked)  // a client that names ever new ids: the oldest go
                asked = asked.OrderBy(item => item.Value).Skip(asked.Count - MaxAsked).ToDictionary(item => item.Key, item => item.Value, StringComparer.Ordinal);
            everything = everything || all || quiet;
        }
        if (all || quiet)
            wake.Set();
    }

    public Snapshot Latest()
    {
        lock (gate)
            return snapshot;
    }

    public Timing Rounds()
    {
        lock (gate)
            return timing;
    }

    /// <summary>Stop; the thread closes the computer, so nothing is closed while it reads.</summary>
    public void Close()
    {
        stop.Set();
        wake.Set();
        if (thread.IsAlive)
            thread.Join(TimeSpan.FromSeconds(10));
    }
}
