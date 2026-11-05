using System;
using System.Collections.Generic;
using System.Reflection;
using LibreHardwareMonitor.Hardware;

namespace LibrePanel.Sensors;

// LibreHardwareMonitor, read as the Python service reads it (sensors/lhm_inprocess.py): its
// pieces of hardware as nodes, their sensors as entries, every name, type and unit the same.
// Only this file names LibreHardwareMonitor's types, and only where it is open: a library that
// is missing fails the opening (the reader says so), not the service.

/// <summary>One sensor with a value, as the answers carry it (librehardwaremonitor.LhmSensor).</summary>
internal sealed class Sensor(string sensorId, string hardwareId, string hardwareName, string sensorType, string label, double value, string unit)
{
    public string SensorId { get; } = sensorId;
    public string HardwareId { get; } = hardwareId;
    public string HardwareName { get; } = hardwareName;
    public string SensorType { get; } = sensorType;
    public string Label { get; } = label;
    public double Value { get; } = value;
    public string Unit { get; } = unit;
}

/// <summary>One sensor: LibreHardwareMonitor's, and what it was last read as.</summary>
internal sealed class Entry(object handle, string sensorId, string label, string sensorType, string unit, bool ring0)
{
    public readonly object Handle = handle;  // an ISensor
    public readonly string SensorId = sensorId;
    public readonly string Label = label;
    public readonly string SensorType = sensorType;
    public readonly string Unit = unit;
    public readonly bool Ring0 = ring0;
    public double? Value;
}

/// <summary>One piece of hardware (a CPU, a graphics card, a Super I/O chip): what is updated.</summary>
internal sealed class Node(int index, object handle, string hardwareId, string name, string hardwareType)
{
    public readonly int Index = index;
    readonly object hardware = handle;  // an IHardware
    public readonly string HardwareId = hardwareId;
    public readonly string Name = name;
    public readonly string HardwareType = hardwareType;
    public readonly List<Entry> Entries = [];
    int seen = -1;  // how many sensors the hardware listed when they were last looked at

    /// <summary>Read the hardware; the sensors it lists now that it did not before come along
    /// and are returned (a Super I/O chip lists its fans after its first update, a fan only
    /// once it turns).</summary>
    public List<Entry> Update()
    {
        ((IHardware)hardware).Update();
        return Read();
    }

    /// <summary>The values as the last update left them, with the sensors listed since.</summary>
    public List<Entry> Read()
    {
        List<Entry> fresh = Grow();
        Lhm.Forget(fresh);
        foreach (Entry entry in Entries)
            entry.Value = Lhm.Number(((ISensor)entry.Handle).Value);
        return fresh;
    }

    public List<Entry> Grow()
    {
        ISensor[] sensors = ((IHardware)hardware).Sensors;
        if (sensors.Length == seen)
            return [];
        seen = sensors.Length;
        var known = new HashSet<string>(StringComparer.Ordinal);
        foreach (Entry entry in Entries)
            known.Add(entry.SensorId);
        var fresh = new List<Entry>();
        foreach (ISensor sensor in sensors)
        {
            Entry entry = Lhm.EntryOf(sensor, HardwareType);
            if (!known.Contains(entry.SensorId))
                fresh.Add(entry);
        }
        Entries.AddRange(fresh);
        return fresh;
    }
}

/// <summary>LibreHardwareMonitor's Computer, open on every kind of hardware it reads.</summary>
internal sealed class OpenComputer
{
    // Opened after the kinds the Python service opens first, one at a time, so that a kind
    // that cannot be read (a library missing) costs only itself (winservice.MORE_HARDWARE)
    static readonly string[] MoreHardware =
        ["IsStorageEnabled", "IsControllerEnabled", "IsPsuEnabled", "IsBatteryEnabled", "IsPowerMonitorEnabled"];

    readonly object computer;  // a Computer

    OpenComputer(object computer)
    {
        this.computer = computer;
    }

    /// <summary>CPU, GPU, memory, mainboard and network, then drives, controllers (fan
    /// controllers, pumps), power supplies, batteries and power monitors where there are any.</summary>
    public static OpenComputer Open()
    {
        Lhm.LearnUnits();
        var computer = new Computer
        {
            IsCpuEnabled = true,
            IsGpuEnabled = true,
            IsMemoryEnabled = true,
            IsMotherboardEnabled = true,
            IsNetworkEnabled = true,
        };
        computer.Open();
        foreach (string name in MoreHardware)
        {
            try
            {
                PropertyInfo property = typeof(Computer).GetProperty(name)
                    ?? throw new MissingMemberException(nameof(Computer), name);
                property.SetValue(computer, true);  // an open Computer adds the kind at once
            }
            catch (Exception e)
            {
                Exception cause = e is TargetInvocationException { InnerException: { } inner } ? inner : e;
                Log.Warning("reader", $"LibreHardwareMonitor cannot read {name.Substring(2, name.Length - 9)}: {cause.Message}");
            }
        }
        return new OpenComputer(computer);
    }

    /// <summary>Every piece of hardware, sub-hardware (the chips of a mainboard) included, each
    /// updated once first: LibreHardwareMonitor lists some sensors only after an update.</summary>
    public List<Node> Nodes()
    {
        var nodes = new List<Node>();
        foreach (IHardware hardware in Walk(((Computer)computer).Hardware))
        {
            string kind = hardware.HardwareType.ToString();
            try
            {
                hardware.Update();
            }
            catch (Exception e)
            {
                Log.Debug("reader", $"LibreHardwareMonitor: a first update failed ({kind}): {e.Message}");
            }
            var node = new Node(nodes.Count, hardware, hardware.Identifier.ToString(), hardware.Name ?? "", kind);
            node.Grow();
            nodes.Add(node);
        }
        return nodes;
    }

    public void Close() => ((Computer)computer).Close();

    static IEnumerable<IHardware> Walk(IEnumerable<IHardware> hardware)
    {
        foreach (IHardware item in hardware)
        {
            yield return item;
            foreach (IHardware sub in Walk(item.SubHardware))
                yield return sub;
        }
    }
}

internal static class Lhm
{
    // What LibreHardwareMonitor's web server writes after a value, by sensor type
    static readonly Dictionary<string, string> Units = new(StringComparer.Ordinal)
    {
        ["voltage"] = "V",
        ["current"] = "A",
        ["power"] = "W",
        ["clock"] = "MHz",
        ["temperature"] = "°C",
        ["load"] = "%",
        ["frequency"] = "Hz",
        ["fan"] = "RPM",
        ["flow"] = "L/h",
        ["control"] = "%",
        ["level"] = "%",
        ["factor"] = "",
        ["data"] = "GB",
        ["smalldata"] = "MB",
        ["throughput"] = "B/s",
        ["timespan"] = "s",
        ["timing"] = "ns",
        ["energy"] = "mWh",
        ["noise"] = "dBA",
        ["conductivity"] = "µS/cm",
        ["humidity"] = "%",
    };

    /// <summary>0.9.7 tells every amount of data in bytes; 0.9.6 told GB, and MB as SmallData.</summary>
    public static void LearnUnits()
    {
        lock (Units)
            Units["data"] = Enum.IsDefined(typeof(SensorType), "SmallData") ? "GB" : "B";
    }

    static string UnitOf(string sensorType)
    {
        lock (Units)
            return Units.TryGetValue(sensorType.ToLowerInvariant(), out string unit) ? unit : "";
    }

    /// <summary>Whether LibreHardwareMonitor reads this sensor through its ring0 driver only:
    /// everything of a CPU but its load, the Super I/O chips and embedded controllers of the
    /// mainboard, and the temperature of a memory module.</summary>
    public static bool NeedsRing0(string hardwareType, string sensorType)
    {
        hardwareType = hardwareType.ToLowerInvariant();
        sensorType = sensorType.ToLowerInvariant();
        if (hardwareType == "cpu")
            return sensorType != "load";
        if (hardwareType == "superio" || hardwareType == "embeddedcontroller")
            return true;
        return hardwareType == "memory" && sensorType == "temperature";
    }

    public static Entry EntryOf(object handle, string hardwareType)
    {
        var sensor = (ISensor)handle;
        string sensorType = sensor.SensorType.ToString();
        return new Entry(sensor, sensor.Identifier.ToString(), sensor.Name ?? "", sensorType, UnitOf(sensorType),
            NeedsRing0(hardwareType, sensorType));
    }

    /// <summary>A sensor's value as a number; null where it has none (or no finite one).</summary>
    public static double? Number(float? value)
    {
        if (value is not float found)
            return null;
        double number = found;
        return double.IsNaN(number) || double.IsInfinity(number) ? null : number;
    }

    /// <summary>LibreHardwareMonitor keeps a day of values for every sensor (tens of megabytes
    /// for a few hundred sensors read once a second); Libre Panel keeps its own history.</summary>
    public static void Forget(List<Entry> entries)
    {
        foreach (Entry entry in entries)
        {
            try
            {
                ((ISensor)entry.Handle).ValuesTimeWindow = TimeSpan.Zero;
            }
            catch (Exception e)
            {
                Log.Debug("reader", "could not switch a sensor's history off", e);
                return;
            }
        }
    }
}

/// <summary>What the nodes read, as the answers give it (lhm_inprocess's ring0_state and
/// live_sensors).</summary>
internal static class Readings
{
    // A working ring0 driver shows in one of these being above zero: a fan may stand still, a
    // core's power may be nothing, but a temperature, a clock or a voltage are not
    static readonly HashSet<string> Probes = new(StringComparer.Ordinal) { "temperature", "clock", "power", "voltage" };

    /// <summary>True if one of the ring0 sensors is above zero, false if they exist and all read
    /// zero (no administrator rights, no PawnIO), null if there are none or none was read.</summary>
    public static bool? Ring0State(List<Node> nodes)
    {
        bool any = false, read = false;
        foreach (Node node in nodes)
        {
            foreach (Entry entry in node.Entries)
            {
                if (!entry.Ring0 || !Probes.Contains(entry.SensorType.ToLowerInvariant()))
                    continue;
                if (entry.Value is double value)
                {
                    read = true;
                    if (value > 0)
                        any = true;
                }
            }
        }
        return read ? any : null;
    }

    /// <summary>The sensors that have a value now. Where the ring0 driver does not work, what it
    /// should have read is zero: those are left out ("--" on the panel). So is a chip
    /// temperature of zero or less where it works: that is not a measurement.</summary>
    public static List<Sensor> Live(List<Node> nodes, bool? ring0)
    {
        var live = new List<Sensor>();
        foreach (Node node in nodes)
        {
            foreach (Entry entry in node.Entries)
            {
                if (entry.Value is not double value)
                    continue;
                if (entry.Ring0 && value <= 0 && (ring0 == false || entry.SensorType.ToLowerInvariant() == "temperature"))
                    continue;
                live.Add(new Sensor(entry.SensorId, node.HardwareId, node.Name, entry.SensorType, entry.Label, value, entry.Unit));
            }
        }
        return live;
    }
}
