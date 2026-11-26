using System;
using System.Collections.Generic;
using System.Management;
using System.Runtime.InteropServices;

namespace LibrePanel.Sensors;

/// <summary>
/// Dell's fans and temperatures as Dell Command | Monitor reports them (its WMI namespace
/// root\dcim\sysman), asked for a client. On many Dell PCs it answers administrators only, and
/// Libre Panel runs without those rights; the service is the system (rust/sensors dell.rs asks
/// it itself where it may, and here where it is turned away). One query of its numeric sensors
/// of the properties dell.rs reads, at most once a second whoever asks (a younger answer is
/// given again, as a read is answered from what the service read last): nothing of the
/// client's goes into it, and it only reads.
/// </summary>
internal static class Dell
{
    public const string Namespace = @"root\dcim\sysman";
    public const string Query = "SELECT ElementName, DeviceID, SensorType, BaseUnits, UnitModifier, CurrentReading FROM CIM_NumericSensor";
    public static readonly string[] Fields = ["ElementName", "DeviceID", "SensorType", "BaseUnits", "UnitModifier", "CurrentReading"];
    public const int MaxRows = 256;  // sensors one answer carries
    public const double FreshSeconds = 1.0;  // an answer this young is given again
    static readonly TimeSpan Wait = TimeSpan.FromSeconds(5);  // for the next sensor
    static readonly object Gate = new();
    static double askedAt = double.NaN;
    static IAnswer? last;

    /// <summary>Its sensors, each a row of <see cref="Fields"/>' values; why there are none
    /// (Dell Command | Monitor is not there, or did not answer). Clients that ask meanwhile
    /// wait for the one query and get its answer.</summary>
    public static IAnswer Answer()
    {
        lock (Gate)
        {
            double age = Clock.Now() - askedAt;
            if (last is not null && age >= 0 && age < FreshSeconds)
                return last;
            last = Ask();
            askedAt = Clock.Now();
            return last;
        }
    }

    static IAnswer Ask()
    {
        var rows = new List<object?[]>();
        try
        {
            var options = new EnumerationOptions { Timeout = Wait, ReturnImmediately = true, Rewindable = false };
            using var searcher = new ManagementObjectSearcher(new ManagementScope(Namespace), new ObjectQuery(Query), options);
            using ManagementObjectCollection found = searcher.Get();
            foreach (ManagementBaseObject sensor in found)
            {
                using (sensor)
                {
                    if (rows.Count == MaxRows)
                        break;
                    rows.Add(Row(sensor));
                }
            }
        }
        catch (Exception e) when (e is ManagementException || e is COMException || e is UnauthorizedAccessException || e is TimeoutException)
        {
            return new ErrorAnswer($"Dell Command | Monitor: {e.Message.Trim()}");
        }
        return new DellAnswer(rows);
    }

    /// <summary>A sensor's values: a text, a whole number, a float, or null where it has none.</summary>
    static object?[] Row(ManagementBaseObject sensor)
    {
        var row = new object?[Fields.Length];
        for (int i = 0; i < Fields.Length; i++)
        {
            object? value;
            try
            {
                value = sensor[Fields[i]];
            }
            catch (ManagementException)  // (a property it does not have)
            {
                value = null;
            }
            row[i] = value switch
            {
                string text => Text.Cut(text, Protocol.MaxText),
                sbyte n => (long)n,
                byte n => (long)n,
                short n => (long)n,
                ushort n => (long)n,
                int n => (long)n,
                uint n => (long)n,
                long n => n,
                ulong n when n <= long.MaxValue => (long)n,
                float f when !float.IsNaN(f) && !float.IsInfinity(f) => (double)f,
                double d when !double.IsNaN(d) && !double.IsInfinity(d) => d,
                _ => null,
            };
        }
        return row;
    }
}

/// <summary>Dell Command | Monitor's sensors: {"v": 1, "ok": true, "rows": [[ElementName,
/// DeviceID, SensorType, BaseUnits, UnitModifier, CurrentReading], ...]}.</summary>
internal sealed class DellAnswer(List<object?[]> rows) : IAnswer
{
    public void Write(JsonWriter json)
    {
        json.Begin('{');
        json.Name("v").Number(Protocol.Version);
        json.Name("ok").Bool(true);
        json.Name("rows").Begin('[');
        foreach (object?[] row in rows)
        {
            json.Begin('[');
            foreach (object? value in row)
            {
                switch (value)
                {
                    case string text:
                        json.String(text);
                        break;
                    case long n:
                        json.Number(n);
                        break;
                    case double d:
                        json.Float(d);
                        break;
                    default:
                        json.Null();
                        break;
                }
            }
            json.End(']');
        }
        json.End(']');
        json.End('}');
    }
}
