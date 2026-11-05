using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;

namespace LibrePanel.Sensors;

/// <summary>A message that is not one of this protocol; the text says what is wrong with it.</summary>
internal sealed class ProtocolError(string message) : Exception(message);

/// <summary>
/// What the sensor service and Libre Panel say to each other over the pipe, as
/// sensorservice/protocol.py says it: one JSON object of at most a set number of bytes in
/// UTF-8, after its length as four bytes (little endian). The checks of a request, their
/// answers and the limits are that module's, word for word.
/// </summary>
internal static class Protocol
{
    public const int Version = 1;  // PROTOCOL
    public const int HeaderSize = 4;
    public const int MaxRequest = 4096;  // bytes: a request names at most MaxHardware pieces
    public const int MaxAnswer = 4 * 1024 * 1024;  // bytes: some thousand sensors fit many times
    public const int MaxHardware = 64;  // pieces of hardware one request may name
    public const int MaxSensors = 10_000;  // sensors one answer may carry
    public const int MaxText = 300;  // characters of a name, a label, a version or an error
    public const int MaxTimed = 256;  // parts one round's times may name

    /// <summary>The length that four bytes announce; ProtocolError when it is none or above
    /// limit (then the connection cannot go on: nobody knows where the next message starts).</summary>
    public static int BodyLength(byte[] header, int limit)
    {
        if (header.Length != HeaderSize)
            throw new ProtocolError("the length is cut short");
        uint length = header[0] | (uint)header[1] << 8 | (uint)header[2] << 16 | (uint)header[3] << 24;
        if (length == 0 || length > limit)
            throw new ProtocolError($"a message of {length} bytes (at most {limit})");
        return (int)length;
    }

    /// <summary>The answer with its length in front, in output; an error answer instead when it
    /// is longer than limit bytes.</summary>
    public static void Encode(IAnswer answer, Bytes output, int limit)
    {
        Frame(answer, output);
        int length = output.Length - HeaderSize;
        if (length > limit)
            Frame(new ErrorAnswer($"the answer is too large: {length} bytes, more than {limit}"), output);
    }

    static void Frame(IAnswer answer, Bytes output)
    {
        output.Clear();
        output.Reserve(HeaderSize);
        answer.Write(new JsonWriter(output));
        uint length = (uint)(output.Length - HeaderSize);
        output.Buffer[0] = (byte)length;
        output.Buffer[1] = (byte)(length >> 8);
        output.Buffer[2] = (byte)(length >> 16);
        output.Buffer[3] = (byte)(length >> 24);
    }

    /// <summary>What a client asks, checked; ProtocolError for anything this protocol does not have.</summary>
    public static Request ParseRequest(Dictionary<string, object> message)
    {
        message.TryGetValue("v", out object? version);
        // (as Python compares: 1, 1.0 and true are the protocol 1)
        bool protocol = version switch
        {
            bool yes => yes,
            Json.Number number => number.Value == Version,
            _ => false,
        };
        if (!protocol)
            throw new ProtocolError($"protocol {Json.Repr(version)} is not {Version}");
        message.TryGetValue("op", out object? op);
        if (op is "hello")
            return new Request("hello", [], false);
        if (op is not "read")
            throw new ProtocolError("a request is hello or read");
        object? hardware = message.TryGetValue("hardware", out object? named) ? named : new List<object>();
        if (hardware is not List<object> list || list.Count > MaxHardware)
            throw new ProtocolError($"hardware is a list of at most {MaxHardware} ids");
        foreach (object item in list)
        {
            if (item is not string id || !IsHardwareId(id))
                throw new ProtocolError("a hardware id is a path such as /amdcpu/0");
        }
        object? everything = message.TryGetValue("all", out object? said) ? said : false;
        if (everything is not bool all)
            throw new ProtocolError("all is true or false");
        var seen = new HashSet<string>(StringComparer.Ordinal);
        var ids = new List<string>();
        foreach (object item in list)
        {
            if (seen.Add((string)item))
                ids.Add((string)item);
        }
        return new Request("read", ids.ToArray(), all);
    }

    /// <summary>A hardware id as LibreHardwareMonitor makes them ("/amdcpu/0", "/nic/%7B…%7D"):
    /// printable ASCII without spaces, so nothing else gets through to the service's wishes.</summary>
    static bool IsHardwareId(string id)
    {
        if (id.Length < 2 || id.Length > 200 || id[0] != '/')
            return false;
        foreach (char c in id)
        {
            if (c < '!' || c > '~')
                return false;
        }
        return true;
    }
}

internal sealed class Request(string op, string[] hardware, bool everything)
{
    public string Op { get; } = op;  // "hello" or "read"
    public string[] Hardware { get; } = hardware;  // read: the hardware to keep up to date
    public bool Everything { get; } = everything;  // read: update every piece once
}

internal interface IAnswer
{
    void Write(JsonWriter json);
}

internal sealed class ErrorAnswer(string why) : IAnswer
{
    readonly string text = Text.Cut(why, Protocol.MaxText);

    public void Write(JsonWriter json)
    {
        json.Begin('{');
        json.Name("v").Number(Protocol.Version);
        json.Name("ok").Bool(false);
        json.Name("error").String(text);
        json.End('}');
    }
}

internal sealed class HelloAnswer(string version) : IAnswer
{
    readonly string service = Text.Cut(version, Protocol.MaxText);

    public void Write(JsonWriter json)
    {
        json.Begin('{');
        json.Name("v").Number(Protocol.Version);
        json.Name("ok").Bool(true);
        json.Name("service").String(service);
        json.Name("protocol").Number(Protocol.Version);
        json.End('}');
    }
}

/// <summary>The readings, with what the rounds cost and what the service holds (protocol.read_answer).</summary>
internal sealed class ReadAnswer(Snapshot snapshot, double? age, Timing timing, ServiceMemory? memory) : IAnswer
{
    public void Write(JsonWriter json)
    {
        json.Begin('{');
        json.Name("v").Number(Protocol.Version);
        json.Name("ok").Bool(true);
        json.Name("state").String(snapshot.State);
        json.Name("error").String(Text.Cut(snapshot.Error, Protocol.MaxText));
        if (age is double seconds)
            json.Name("age").Rounded(Math.Max(0.0, seconds), 3);
        else
            json.Name("age").Null();
        json.Name("ring0").Bool(snapshot.Ring0);
        json.Name("pawnio").String(snapshot.Pawnio);
        json.Name("sensors").Begin('[');
        int rows = 0;
        foreach (Sensor sensor in snapshot.Sensors)
        {
            if (double.IsNaN(sensor.Value) || double.IsInfinity(sensor.Value))
                continue;
            if (rows == Protocol.MaxSensors)
                break;
            rows++;
            json.Begin('[');
            json.String(sensor.SensorId).String(sensor.HardwareId).String(sensor.HardwareName)
                .String(sensor.SensorType).String(sensor.Label).Float(sensor.Value).String(sensor.Unit);
            json.End(']');
        }
        json.End(']');
        json.Name("timing").Begin('{');
        Round(json.Name("last"), timing.Last);
        Round(json.Name("full"), timing.Full);
        json.End('}');
        if (memory is not null)
        {
            json.Name("memory").Begin('{');
            json.Name("private").Number(memory.Private);
            json.Name("working_set").Number(memory.WorkingSet);
            json.Name("managed").Number(memory.Managed);
            json.End('}');
        }
        json.End('}');
    }

    static void Round(JsonWriter json, RoundTime? done)
    {
        if (done is null)
        {
            json.Null();
            return;
        }
        json.Begin('{');
        json.Name("full").Bool(done.Full);
        json.Name("parts").Begin('[');
        int count = 0;
        foreach (PartTime part in done.Parts)
        {
            if (double.IsNaN(part.Seconds) || double.IsInfinity(part.Seconds) || part.HardwareId.Length == 0 || part.Kind.Length == 0)
                continue;
            if (count == Protocol.MaxTimed)
                break;
            count++;
            json.Begin('[');
            json.String(Text.Cut(part.HardwareId, Protocol.MaxText)).String(Text.Cut(part.Name, Protocol.MaxText))
                .String(Text.Cut(part.Kind, Protocol.MaxText)).Rounded(part.Seconds, 6);
            json.End(']');
        }
        json.End(']');
        json.End('}');
    }
}

/// <summary>Bytes that grow as they are written, kept for the next message of a connection.</summary>
internal sealed class Bytes
{
    public byte[] Buffer = new byte[4096];
    public int Length;

    public void Clear() => Length = 0;

    public void Reserve(int count)
    {
        Room(count);
        Length += count;
    }

    public void Room(int more)
    {
        if (Length + more > Buffer.Length)
            Array.Resize(ref Buffer, Math.Max(Buffer.Length * 2, Length + more));
    }

    public void Add(byte value)
    {
        Room(1);
        Buffer[Length++] = value;
    }

    public void Ascii(string text)
    {
        Room(text.Length);
        foreach (char c in text)
            Buffer[Length++] = (byte)c;
    }
}

/// <summary>JSON as Python's json.dumps writes it for the protocol (separators "," and ":",
/// ensure_ascii off, no NaN): in UTF-8, straight into the bytes of the message.</summary>
internal sealed class JsonWriter(Bytes output)
{
    bool comma;  // a value was written at this level: the next one needs a comma first

    void Separate()
    {
        if (comma)
            output.Add((byte)',');
        comma = true;
    }

    public JsonWriter Begin(char bracket)
    {
        Separate();
        output.Add((byte)bracket);
        comma = false;
        return this;
    }

    public JsonWriter End(char bracket)
    {
        output.Add((byte)bracket);
        comma = true;
        return this;
    }

    public JsonWriter Name(string name)
    {
        String(name);
        output.Add((byte)':');
        comma = false;
        return this;
    }

    public JsonWriter Null()
    {
        Separate();
        output.Ascii("null");
        return this;
    }

    public JsonWriter Bool(bool? value)
    {
        Separate();
        output.Ascii(value is null ? "null" : value.Value ? "true" : "false");
        return this;
    }

    public JsonWriter Number(long? value)
    {
        Separate();
        output.Ascii(value is null ? "null" : value.Value.ToString(CultureInfo.InvariantCulture));
        return this;
    }

    /// <summary>A float, as a number that reads back as the very same one (as repr writes it).</summary>
    public JsonWriter Float(double value)
    {
        Separate();
        output.Ascii(Numbers.Exact(value));
        return this;
    }

    /// <summary>round(value, digits), as Python rounds: half to even, of the value's own digits.</summary>
    public JsonWriter Rounded(double value, int digits)
    {
        Separate();
        output.Ascii(Numbers.Rounded(value, digits));
        return this;
    }

    public JsonWriter String(string? text)
    {
        Separate();
        if (text is null)
        {
            output.Ascii("null");
            return this;
        }
        output.Add((byte)'"');
        for (int i = 0; i < text.Length; i++)
        {
            char c = text[i];
            switch (c)
            {
                case '"': output.Ascii("\\\""); continue;
                case '\\': output.Ascii("\\\\"); continue;
                case '\n': output.Ascii("\\n"); continue;
                case '\r': output.Ascii("\\r"); continue;
                case '\t': output.Ascii("\\t"); continue;
                case '\b': output.Ascii("\\b"); continue;
                case '\f': output.Ascii("\\f"); continue;
            }
            if (c < 0x20)
            {
                output.Ascii("\\u" + ((int)c).ToString("x4", CultureInfo.InvariantCulture));
                continue;
            }
            if (c < 0x80)
            {
                output.Add((byte)c);
                continue;
            }
            int end = i + 1;  // the run of characters beyond ASCII, written at once
            while (end < text.Length && text[end] >= 0x80)
                end++;
            output.Room(3 * (end - i));
            output.Length += Encoding.UTF8.GetBytes(text, i, end - i, output.Buffer, output.Length);
            i = end - 1;
        }
        output.Add((byte)'"');
        return this;
    }
}

/// <summary>Numbers as Python writes them.</summary>
internal static class Numbers
{
    static readonly CultureInfo Invariant = CultureInfo.InvariantCulture;

    /// <summary>The shortest digits that read back as this very value ("R", or the 17 digits
    /// where the .NET Framework's "R" would not), with ".0" on a whole one, as repr.</summary>
    public static string Exact(double value)
    {
        string text = Digits(value);
        return text.IndexOf('E') < 0 && text.IndexOf('.') < 0 ? text + ".0" : text;
    }

    /// <summary>"R", or "G17" where the .NET Framework's "R" does not read back; with the sign
    /// of a negative zero, which the .NET Framework leaves out.</summary>
    static string Digits(double value)
    {
        string text = value.ToString("R", Invariant);
        if (double.Parse(text, NumberStyles.Float, Invariant) != value)
            text = value.ToString("G17", Invariant);
        return value == 0 && BitConverter.DoubleToInt64Bits(value) < 0 ? "-0" : text;
    }

    /// <summary>round(value, digits): to the nearest, half to even, of the value's own decimal
    /// digits, as Python rounds; written as those digits.</summary>
    public static string Rounded(double value, int digits)
    {
        try
        {
            decimal exact = decimal.Parse(value.ToString("G17", Invariant), NumberStyles.Float, Invariant);
            string text = Math.Round(exact, digits, MidpointRounding.ToEven).ToString("G29", Invariant);
            return text.IndexOf('E') < 0 && text.IndexOf('.') < 0 ? text + ".0" : text;
        }
        catch (OverflowException)
        {
            return Exact(Math.Round(value, digits, MidpointRounding.ToEven));
        }
    }

    /// <summary>repr(value) of a float, for the texts of an error.</summary>
    public static string Repr(double value)
    {
        if (double.IsNaN(value))
            return "nan";
        if (double.IsInfinity(value))
            return value > 0 ? "inf" : "-inf";
        string text = Digits(value);
        bool negative = text.StartsWith("-", StringComparison.Ordinal);
        if (negative)
            text = text.Substring(1);
        int exponent = 0;
        int e = text.IndexOf('E');
        if (e >= 0)
        {
            exponent = int.Parse(text.Substring(e + 1), NumberStyles.AllowLeadingSign, Invariant);
            text = text.Substring(0, e);
        }
        int dot = text.IndexOf('.');
        string digits = dot < 0 ? text : text.Remove(dot, 1);
        int point = (dot < 0 ? text.Length : dot) + exponent;  // where the point goes in digits
        int lead = 0;
        while (lead < digits.Length - 1 && digits[lead] == '0')
            lead++;
        digits = digits.Substring(lead).TrimEnd('0');
        point -= lead;
        if (digits.Length == 0)
        {
            digits = "0";
            point = 1;
        }
        int power = point - 1;
        string body;
        if (power < -4 || power >= 16)
        {
            string mantissa = digits.Length == 1 ? digits : digits.Substring(0, 1) + "." + digits.Substring(1);
            body = mantissa + "e" + (power < 0 ? "-" : "+") + Math.Abs(power).ToString("00", Invariant);
        }
        else if (point <= 0)
            body = "0." + new string('0', -point) + digits;
        else if (point >= digits.Length)
            body = digits + new string('0', point - digits.Length) + ".0";
        else
            body = digits.Substring(0, point) + "." + digits.Substring(point);
        return (negative ? "-" : "") + body;
    }
}

/// <summary>JSON as Python's json.loads reads a request: a strict reader of RFC 8259 (no NaN,
/// no control characters in strings, nothing after the value), and Python's repr of what it read,
/// for the texts of an error.</summary>
internal static class Json
{
    public sealed class NullValue
    {
        public static readonly NullValue Instance = new();
    }

    public sealed class Number(string text, double value, bool whole)
    {
        public string Text { get; } = text;
        public double Value { get; } = value;  // NaN where it does not fit a double
        public bool Whole { get; } = whole;  // no fraction, no exponent: an int to Python
    }

    static readonly UTF8Encoding Strict = new(false, true);
    const int Deepest = 64;

    /// <summary>The JSON object in body; ProtocolError for anything else.</summary>
    public static Dictionary<string, object> Decode(byte[] body)
    {
        string text;
        try
        {
            text = Strict.GetString(body);
        }
        catch (DecoderFallbackException e)
        {
            throw new ProtocolError($"not JSON: {e.Message}");
        }
        object value;
        try
        {
            value = new Parser(text).Document();
        }
        catch (FormatException e)
        {
            throw new ProtocolError($"not JSON: {e.Message}");
        }
        return value as Dictionary<string, object> ?? throw new ProtocolError("not a JSON object");
    }

    public static string Repr(object? value) => value switch
    {
        null or NullValue => "None",
        bool yes => yes ? "True" : "False",
        Number number => number.Whole ? WholeRepr(number) : Numbers.Repr(number.Value),
        string text => StringRepr(text),
        List<object> list => "[" + string.Join(", ", list.Select(Repr)) + "]",
        Dictionary<string, object> map => "{" + string.Join(", ", map.Select(item => StringRepr(item.Key) + ": " + Repr(item.Value))) + "}",
        _ => Convert.ToString(value, CultureInfo.InvariantCulture) ?? "",
    };

    static string WholeRepr(Number number) => number.Text == "-0" ? "0" : number.Text;

    static string StringRepr(string text)
    {
        char quote = text.IndexOf('\'') >= 0 && text.IndexOf('"') < 0 ? '"' : '\'';
        var said = new StringBuilder().Append(quote);
        for (int i = 0; i < text.Length; i++)
        {
            char c = text[i];
            if (c == quote || c == '\\')
                said.Append('\\').Append(c);
            else if (c == '\n')
                said.Append("\\n");
            else if (c == '\r')
                said.Append("\\r");
            else if (c == '\t')
                said.Append("\\t");
            else if (char.IsHighSurrogate(c) && i + 1 < text.Length && char.IsLowSurrogate(text[i + 1]))
                said.Append(c).Append(text[++i]);
            else if (c < 0x20 || c == 0x7f || (c >= 0x80 && c < 0xa0))
                said.Append("\\x").Append(((int)c).ToString("x2", CultureInfo.InvariantCulture));
            else if (!Printable(c))
                said.Append(c < 0x100 ? "\\x" + ((int)c).ToString("x2", CultureInfo.InvariantCulture) : "\\u" + ((int)c).ToString("x4", CultureInfo.InvariantCulture));
            else
                said.Append(c);
        }
        return said.Append(quote).ToString();
    }

    static bool Printable(char c)
    {
        UnicodeCategory kind = char.GetUnicodeCategory(c);
        return kind switch
        {
            UnicodeCategory.Control or UnicodeCategory.Format or UnicodeCategory.Surrogate
                or UnicodeCategory.PrivateUse or UnicodeCategory.OtherNotAssigned
                or UnicodeCategory.LineSeparator or UnicodeCategory.ParagraphSeparator => false,
            UnicodeCategory.SpaceSeparator => c == ' ',
            _ => true,
        };
    }

    sealed class Parser(string text)
    {
        readonly string s = text;
        int i;
        int depth;

        FormatException Fail(string what) => new($"{what}: char {i}");

        bool At(char c) => i < s.Length && s[i] == c;

        bool Digit() => i < s.Length && s[i] >= '0' && s[i] <= '9';

        void Space()
        {
            while (i < s.Length && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r'))
                i++;
        }

        public object Document()
        {
            Space();
            object value = Value();
            Space();
            if (i != s.Length)
                throw Fail("Extra data");
            return value;
        }

        object Value()
        {
            if (i >= s.Length)
                throw Fail("Expecting value");
            switch (s[i])
            {
                case '{': return Object();
                case '[': return Array();
                case '"': return String();
                case 't': return Word("true", true);
                case 'f': return Word("false", false);
                case 'n': return Word("null", NullValue.Instance);
                case '-':
                case >= '0' and <= '9':
                    return Number();
                default: throw Fail("Expecting value");
            }
        }

        object Word(string word, object value)
        {
            if (i + word.Length > s.Length || string.CompareOrdinal(s, i, word, 0, word.Length) != 0)
                throw Fail("Expecting value");
            i += word.Length;
            return value;
        }

        void Enter()
        {
            if (++depth > Deepest)
                throw Fail("Nested too deep");
            i++;
        }

        Dictionary<string, object> Object()
        {
            Enter();
            var found = new Dictionary<string, object>(StringComparer.Ordinal);
            Space();
            if (At('}'))
            {
                i++;
                depth--;
                return found;
            }
            while (true)
            {
                Space();
                if (!At('"'))
                    throw Fail("Expecting property name enclosed in double quotes");
                string name = String();
                Space();
                if (!At(':'))
                    throw Fail("Expecting ':' delimiter");
                i++;
                Space();
                found[name] = Value();  // (the last of a name counts, as in Python)
                Space();
                if (At(','))
                {
                    i++;
                    continue;
                }
                if (!At('}'))
                    throw Fail("Expecting ',' delimiter");
                i++;
                depth--;
                return found;
            }
        }

        List<object> Array()
        {
            Enter();
            var found = new List<object>();
            Space();
            if (At(']'))
            {
                i++;
                depth--;
                return found;
            }
            while (true)
            {
                Space();
                found.Add(Value());
                Space();
                if (At(','))
                {
                    i++;
                    continue;
                }
                if (!At(']'))
                    throw Fail("Expecting ',' delimiter");
                i++;
                depth--;
                return found;
            }
        }

        string String()
        {
            i++;  // the opening quote
            var found = new StringBuilder();
            while (true)
            {
                if (i >= s.Length)
                    throw Fail("Unterminated string");
                char c = s[i++];
                if (c == '"')
                    return found.ToString();
                if (c < 0x20)
                    throw Fail("Invalid control character");
                if (c != '\\')
                {
                    found.Append(c);
                    continue;
                }
                if (i >= s.Length)
                    throw Fail("Unterminated string");
                char escaped = s[i++];
                switch (escaped)
                {
                    case '"': found.Append('"'); break;
                    case '\\': found.Append('\\'); break;
                    case '/': found.Append('/'); break;
                    case 'b': found.Append('\b'); break;
                    case 'f': found.Append('\f'); break;
                    case 'n': found.Append('\n'); break;
                    case 'r': found.Append('\r'); break;
                    case 't': found.Append('\t'); break;
                    case 'u':
                        if (i + 4 > s.Length || !ushort.TryParse(s.Substring(i, 4), NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture, out ushort code))
                            throw Fail("Invalid \\uXXXX escape");
                        found.Append((char)code);
                        i += 4;
                        break;
                    default: throw Fail("Invalid \\escape");
                }
            }
        }

        Number Number()
        {
            int start = i;
            if (At('-'))
                i++;
            if (At('0'))
                i++;
            else if (i < s.Length && s[i] >= '1' && s[i] <= '9')
            {
                while (Digit())
                    i++;
            }
            else
                throw Fail("Expecting value");
            bool whole = true;
            if (At('.'))
            {
                i++;
                whole = false;
                if (!Digit())
                    throw Fail("Extra data");
                while (Digit())
                    i++;
            }
            if (At('e') || At('E'))
            {
                i++;
                whole = false;
                if (At('+') || At('-'))
                    i++;
                if (!Digit())
                    throw Fail("Extra data");
                while (Digit())
                    i++;
            }
            string literal = s.Substring(start, i - start);
            double value = double.TryParse(literal, NumberStyles.Float, CultureInfo.InvariantCulture, out double parsed) ? parsed : double.NaN;
            if (value == 0 && literal[0] == '-')
                value = -Math.Abs(value);  // (the .NET Framework reads "-0.0" as 0.0)
            return new Number(literal, value, whole);
        }
    }
}
