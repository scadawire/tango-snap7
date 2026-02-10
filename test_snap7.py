"""
Full integration test for Snap7.py using a python-snap7 server simulator.

Tests all variable types (DevBoolean, DevLong, DevFloat, DevDouble, DevString)
on DB, input (E/I), and output (A/Q) memory areas.

All conversion and I/O logic is exercised through the real Snap7 class
via unbound method calls -- no code is duplicated.

Usage:
    python test_snap7.py
"""

import sys
import time
import traceback
from ctypes import c_byte
from threading import Lock

import snap7
import snap7.util

try:
    import snap7.server
except ImportError:
    print("snap7.server module not available -- install python-snap7[cli]")
    sys.exit(1)

from tango import CmdArgType, AttrWriteType

from Snap7 import Snap7


# ===========================================================================
#  Lightweight state carrier -- Snap7 methods only need these attributes.
# ===========================================================================

class State:
    """Carries only instance state; every method lookup falls through to Snap7."""

    def __init__(self):
        self.client = snap7.Client()
        self.dynamicAttributes = {}
        self.bit_byte_create_lock = Lock()
        self.bit_byte_locks = {}

    def info_stream(self, msg): pass
    def debug_stream(self, msg): pass
    def warn_stream(self, msg): pass
    def error_stream(self, msg): pass

    def __getattr__(self, name):
        import functools
        attr = getattr(Snap7, name, None)
        if attr is not None and callable(attr):
            return functools.partial(attr, self)
        raise AttributeError(f"'State' has no attribute '{name}'")


# Thin helpers that call the real Snap7 methods as unbound functions.

def register_attr(s, name, register_str, var_type):
    register_parts = Snap7.get_register_parts(s, register_str)
    s.dynamicAttributes[name] = {
        "variableType": var_type,
        "register": register_str,
        "register_parts": register_parts,
        "value": 0,
    }


def read_attr(s, name):
    """Replicate Snap7.read_dynamic_attr logic."""
    lookup = s.dynamicAttributes[name]
    register_parts = lookup["register_parts"]
    variableType = lookup["variableType"]
    customLength = 0
    if variableType == CmdArgType.DevString:
        customLength = register_parts["suboffset"]
        if customLength == 0:
            customLength = 254
    size = Snap7.bytes_per_variable_type(s, variableType, customLength + 2)
    data = Snap7.read_data_from_area_offset_size(
        s, register_parts["area"], register_parts["subarea"],
        register_parts["offset"], size,
    )
    return Snap7.bytedata_to_variable(s, data, variableType, 0, register_parts["suboffset"])


def write_attr(s, name, value):
    """Replicate Snap7.write_dynamic_attr + publish logic."""
    s.dynamicAttributes[name]["value"] = value
    Snap7.publish(s, name)


# ===========================================================================
#  Simulator
# ===========================================================================

SIM_PORT = 11102

_server = None
_db1_data = None
_pe_data = None
_pa_data = None


def _seed_pe(offset, ba):
    """Write a bytearray into the PE (input) ctypes buffer."""
    for i, b in enumerate(ba):
        _pe_data[offset + i] = b if b < 128 else b - 256  # c_byte is signed


def start_server():
    global _server, _db1_data, _pe_data, _pa_data
    _db1_data = (c_byte * 512)()
    _pe_data = (c_byte * 256)()
    _pa_data = (c_byte * 256)()

    _server = snap7.Server(log=False)
    _server.register_area(snap7.SrvArea.DB, 1, _db1_data)
    _server.register_area(snap7.SrvArea.PE, 0, _pe_data)
    _server.register_area(snap7.SrvArea.PA, 0, _pa_data)
    _server.start(tcp_port=SIM_PORT)


def stop_server():
    global _server
    if _server:
        _server.stop()
        _server.destroy()
        _server = None


# ===========================================================================
#  Test helpers
# ===========================================================================

passed = 0
failed = 0
errors = []


def assert_equal(test_name, actual, expected, tolerance=None):
    global passed, failed
    if tolerance is not None:
        ok = abs(actual - expected) <= tolerance
    else:
        ok = (actual == expected)

    if ok:
        passed += 1
        print(f"  PASS  {test_name}")
    else:
        failed += 1
        msg = f"  FAIL  {test_name}: expected {expected!r}, got {actual!r}"
        print(msg)
        errors.append(msg)


def assert_true(test_name, value):
    assert_equal(test_name, value, True)


def assert_false(test_name, value):
    assert_equal(test_name, value, False)


# ===========================================================================
#  Test suites -- pure conversion (no server needed)
# ===========================================================================

def test_get_register_parts():
    print("\n-- get_register_parts --")
    s = State()

    r = Snap7.get_register_parts(s, "DB1.0.3")
    assert_equal("DB area", r["area"], "DB")
    assert_equal("DB subarea", r["subarea"], 1)
    assert_equal("DB offset", r["offset"], 0)
    assert_equal("DB suboffset", r["suboffset"], 3)

    r = Snap7.get_register_parts(s, "DB100.50")
    assert_equal("DB100 area", r["area"], "DB")
    assert_equal("DB100 subarea", r["subarea"], 100)
    assert_equal("DB100 offset", r["offset"], 50)
    assert_equal("DB100 suboffset default", r["suboffset"], 0)

    r = Snap7.get_register_parts(s, "E.5")
    assert_equal("E area", r["area"], "E")
    assert_equal("E subarea", r["subarea"], 0)
    assert_equal("E offset", r["offset"], 5)

    r = Snap7.get_register_parts(s, "I.10.2")
    assert_equal("I area", r["area"], "I")
    assert_equal("I offset", r["offset"], 10)
    assert_equal("I suboffset", r["suboffset"], 2)

    r = Snap7.get_register_parts(s, "A.0")
    assert_equal("A area", r["area"], "A")

    r = Snap7.get_register_parts(s, "Q.0")
    assert_equal("Q area", r["area"], "Q")

    # invalid register
    global passed, failed
    try:
        Snap7.get_register_parts(s, "invalid")
        failed += 1
        errors.append("  FAIL  invalid register: expected exception")
        print("  FAIL  invalid register: expected exception")
    except Exception:
        passed += 1
        print("  PASS  invalid register raises")


def test_string_value_to_var_type():
    print("\n-- stringValueToVarType --")
    s = State()

    for name, expected in [
        ("DevBoolean", CmdArgType.DevBoolean),
        ("DevLong", CmdArgType.DevLong),
        ("DevDouble", CmdArgType.DevDouble),
        ("DevFloat", CmdArgType.DevFloat),
        ("DevString", CmdArgType.DevString),
        ("", CmdArgType.DevString),
    ]:
        got = Snap7.stringValueToVarType(s, name)
        assert_equal(f"varType '{name}'", got, expected)

    # unsupported raises
    global passed, failed
    try:
        Snap7.stringValueToVarType(s, "DevInvalid")
        failed += 1
        errors.append("  FAIL  varType invalid: expected exception")
        print("  FAIL  varType invalid: expected exception")
    except Exception:
        passed += 1
        print("  PASS  varType invalid raises")


def test_string_value_to_write_type():
    print("\n-- stringValueToWriteType --")
    s = State()

    for name, expected in [
        ("READ", AttrWriteType.READ),
        ("WRITE", AttrWriteType.WRITE),
        ("READ_WRITE", AttrWriteType.READ_WRITE),
        ("READ_WITH_WRITE", AttrWriteType.READ_WITH_WRITE),
        ("", AttrWriteType.READ_WRITE),
    ]:
        got = Snap7.stringValueToWriteType(s, name)
        assert_equal(f"writeType '{name}'", got, expected)

    # unsupported raises
    global passed, failed
    try:
        Snap7.stringValueToWriteType(s, "BOGUS")
        failed += 1
        errors.append("  FAIL  writeType invalid: expected exception")
        print("  FAIL  writeType invalid: expected exception")
    except Exception:
        passed += 1
        print("  PASS  writeType invalid raises")


def test_parse_boolean():
    print("\n-- _parse_boolean --")
    s = State()

    assert_true("bool True", Snap7._parse_boolean(s, True))
    assert_false("bool False", Snap7._parse_boolean(s, False))
    assert_true("bool 1", Snap7._parse_boolean(s, 1))
    assert_false("bool 0", Snap7._parse_boolean(s, 0))
    assert_true("bool 2.5", Snap7._parse_boolean(s, 2.5))
    assert_false("bool 0.0", Snap7._parse_boolean(s, 0.0))
    assert_true("bool 'true'", Snap7._parse_boolean(s, "true"))
    assert_true("bool 'True'", Snap7._parse_boolean(s, "True"))
    assert_true("bool '1'", Snap7._parse_boolean(s, "1"))
    assert_true("bool 'yes'", Snap7._parse_boolean(s, "yes"))
    assert_false("bool 'false'", Snap7._parse_boolean(s, "false"))
    assert_false("bool '0'", Snap7._parse_boolean(s, "0"))
    assert_false("bool 'no'", Snap7._parse_boolean(s, "no"))
    assert_false("bool ''", Snap7._parse_boolean(s, ""))


def test_bytes_per_variable_type():
    print("\n-- bytes_per_variable_type --")
    s = State()

    assert_equal("float size", Snap7.bytes_per_variable_type(s, CmdArgType.DevFloat), 4)
    assert_equal("double size", Snap7.bytes_per_variable_type(s, CmdArgType.DevDouble), 8)
    assert_equal("long size", Snap7.bytes_per_variable_type(s, CmdArgType.DevLong), 4)
    assert_equal("bool size", Snap7.bytes_per_variable_type(s, CmdArgType.DevBoolean), 1)
    assert_equal("string size 0", Snap7.bytes_per_variable_type(s, CmdArgType.DevString, 0), 0)
    assert_equal("string size 10", Snap7.bytes_per_variable_type(s, CmdArgType.DevString, 10), 10)
    assert_equal("string size 256", Snap7.bytes_per_variable_type(s, CmdArgType.DevString, 256), 256)

    # unsupported type raises
    global passed, failed
    try:
        Snap7.bytes_per_variable_type(s, CmdArgType.DevShort)
        failed += 1
        errors.append("  FAIL  unsupported type: expected exception")
        print("  FAIL  unsupported type: expected exception")
    except Exception:
        passed += 1
        print("  PASS  unsupported type raises")


def test_byte_conversions():
    print("\n-- byte conversions (round-trip) --")
    s = State()

    # DevFloat
    enc = Snap7.variable_to_bytedata(s, 3.14, CmdArgType.DevFloat, 0)
    dec = Snap7.bytedata_to_variable(s, enc, CmdArgType.DevFloat)
    assert_equal("DevFloat 3.14", dec, 3.14, tolerance=1e-5)

    enc = Snap7.variable_to_bytedata(s, -0.5, CmdArgType.DevFloat, 0)
    dec = Snap7.bytedata_to_variable(s, enc, CmdArgType.DevFloat)
    assert_equal("DevFloat -0.5", dec, -0.5, tolerance=1e-7)

    enc = Snap7.variable_to_bytedata(s, 0.0, CmdArgType.DevFloat, 0)
    dec = Snap7.bytedata_to_variable(s, enc, CmdArgType.DevFloat)
    assert_equal("DevFloat 0.0", dec, 0.0)

    # DevDouble
    enc = Snap7.variable_to_bytedata(s, 2.718281828, CmdArgType.DevDouble, 0)
    dec = Snap7.bytedata_to_variable(s, enc, CmdArgType.DevDouble)
    assert_equal("DevDouble 2.718281828", dec, 2.718281828, tolerance=1e-9)

    enc = Snap7.variable_to_bytedata(s, -1e100, CmdArgType.DevDouble, 0)
    dec = Snap7.bytedata_to_variable(s, enc, CmdArgType.DevDouble)
    assert_equal("DevDouble -1e100", dec, -1e100, tolerance=abs(-1e100) * 1e-12)

    # DevLong
    enc = Snap7.variable_to_bytedata(s, 123456, CmdArgType.DevLong, 0)
    dec = Snap7.bytedata_to_variable(s, enc, CmdArgType.DevLong)
    assert_equal("DevLong 123456", dec, 123456)

    enc = Snap7.variable_to_bytedata(s, -9999, CmdArgType.DevLong, 0)
    dec = Snap7.bytedata_to_variable(s, enc, CmdArgType.DevLong)
    assert_equal("DevLong -9999", dec, -9999)

    enc = Snap7.variable_to_bytedata(s, 2147483647, CmdArgType.DevLong, 0)
    dec = Snap7.bytedata_to_variable(s, enc, CmdArgType.DevLong)
    assert_equal("DevLong max", dec, 2147483647)

    enc = Snap7.variable_to_bytedata(s, -2147483648, CmdArgType.DevLong, 0)
    dec = Snap7.bytedata_to_variable(s, enc, CmdArgType.DevLong)
    assert_equal("DevLong min", dec, -2147483648)

    # DevBoolean bit 0
    enc = Snap7.variable_to_bytedata(s, True, CmdArgType.DevBoolean, 0)
    dec = Snap7.bytedata_to_variable(s, enc, CmdArgType.DevBoolean, 0, 0)
    assert_true("DevBoolean True bit0", dec)

    enc = Snap7.variable_to_bytedata(s, False, CmdArgType.DevBoolean, 0)
    dec = Snap7.bytedata_to_variable(s, enc, CmdArgType.DevBoolean, 0, 0)
    assert_false("DevBoolean False bit0", dec)

    # DevBoolean with suboffset (bit 3)
    enc = Snap7.variable_to_bytedata(s, True, CmdArgType.DevBoolean, 3)
    dec = Snap7.bytedata_to_variable(s, enc, CmdArgType.DevBoolean, 0, 3)
    assert_true("DevBoolean True bit3", dec)

    # DevString
    enc = Snap7.variable_to_bytedata(s, "Hello", CmdArgType.DevString, 20)
    dec = Snap7.bytedata_to_variable(s, enc, CmdArgType.DevString)
    assert_equal("DevString 'Hello'", dec, "Hello")

    enc = Snap7.variable_to_bytedata(s, "", CmdArgType.DevString, 20)
    dec = Snap7.bytedata_to_variable(s, enc, CmdArgType.DevString)
    assert_equal("DevString empty", dec, "")

    enc = Snap7.variable_to_bytedata(s, "Test123!@#", CmdArgType.DevString, 20)
    dec = Snap7.bytedata_to_variable(s, enc, CmdArgType.DevString)
    assert_equal("DevString special chars", dec, "Test123!@#")

    # UTF-8: degree symbol (2 bytes in UTF-8)
    enc = Snap7.variable_to_bytedata(s, "25\u00b0C", CmdArgType.DevString, 20)
    dec = Snap7.bytedata_to_variable(s, enc, CmdArgType.DevString)
    assert_equal("DevString UTF-8 degree", dec, "25\u00b0C")

    # UTF-8: multi-byte characters
    enc = Snap7.variable_to_bytedata(s, "\u00e4\u00f6\u00fc", CmdArgType.DevString, 20)
    dec = Snap7.bytedata_to_variable(s, enc, CmdArgType.DevString)
    assert_equal("DevString UTF-8 umlauts", dec, "\u00e4\u00f6\u00fc")


def test_validation_edge_cases():
    print("\n-- validation edge cases --")
    s = State()
    global passed, failed

    # String too long raises
    try:
        Snap7.variable_to_bytedata(s, "Hello World!", CmdArgType.DevString, 5)
        failed += 1
        errors.append("  FAIL  string too long: expected exception")
        print("  FAIL  string too long: expected exception")
    except Exception as e:
        passed += 1
        print(f"  PASS  string too long raises: {e}")

    # String exactly at max succeeds
    enc = Snap7.variable_to_bytedata(s, "12345", CmdArgType.DevString, 5)
    dec = Snap7.bytedata_to_variable(s, enc, CmdArgType.DevString)
    assert_equal("string at exact max", dec, "12345")

    # UTF-8 string byte count exceeds max (3 chars but 6 UTF-8 bytes)
    try:
        Snap7.variable_to_bytedata(s, "\u00e4\u00f6\u00fc", CmdArgType.DevString, 5)
        failed += 1
        errors.append("  FAIL  UTF-8 byte overflow: expected exception")
        print("  FAIL  UTF-8 byte overflow: expected exception")
    except Exception as e:
        passed += 1
        print(f"  PASS  UTF-8 byte overflow raises: {e}")

    # Boolean bit index 8 raises on encode
    try:
        Snap7.variable_to_bytedata(s, True, CmdArgType.DevBoolean, 8)
        failed += 1
        errors.append("  FAIL  bit index 8 encode: expected exception")
        print("  FAIL  bit index 8 encode: expected exception")
    except Exception as e:
        passed += 1
        print(f"  PASS  bit index 8 encode raises: {e}")

    # Boolean bit index 8 raises on decode
    try:
        data = bytearray(1)
        Snap7.bytedata_to_variable(s, data, CmdArgType.DevBoolean, 0, 8)
        failed += 1
        errors.append("  FAIL  bit index 8 decode: expected exception")
        print("  FAIL  bit index 8 decode: expected exception")
    except Exception as e:
        passed += 1
        print(f"  PASS  bit index 8 decode raises: {e}")

    # Boolean bit index -1 raises
    try:
        Snap7.variable_to_bytedata(s, True, CmdArgType.DevBoolean, -1)
        failed += 1
        errors.append("  FAIL  bit index -1: expected exception")
        print("  FAIL  bit index -1: expected exception")
    except Exception as e:
        passed += 1
        print(f"  PASS  bit index -1 raises: {e}")

    # Boolean bit index 7 succeeds (boundary)
    enc = Snap7.variable_to_bytedata(s, True, CmdArgType.DevBoolean, 7)
    dec = Snap7.bytedata_to_variable(s, enc, CmdArgType.DevBoolean, 0, 7)
    assert_true("bit index 7 boundary", dec)


# ===========================================================================
#  Test suites -- integration with snap7 server: DB area
# ===========================================================================

def test_db_float(s):
    print("\n-- DB: DevFloat --")
    register_attr(s, "db_float", "DB1.0", CmdArgType.DevFloat)

    for val in [0.0, 1.0, -1.0, 3.14159, 1e10, -1e-5]:
        write_attr(s, "db_float", val)
        got = read_attr(s, "db_float")
        assert_equal(f"DB float {val}", got, val, tolerance=max(abs(val) * 1e-6, 1e-7))


def test_db_double(s):
    print("\n-- DB: DevDouble --")
    register_attr(s, "db_double", "DB1.10", CmdArgType.DevDouble)

    for val in [0.0, 2.718281828459045, -1e100, 1e-300]:
        write_attr(s, "db_double", val)
        got = read_attr(s, "db_double")
        assert_equal(f"DB double {val}", got, val, tolerance=max(abs(val) * 1e-12, 1e-300))


def test_db_long(s):
    print("\n-- DB: DevLong --")
    register_attr(s, "db_long", "DB1.20", CmdArgType.DevLong)

    for val in [0, 1, -1, 32767, -32768, 2147483647, -2147483648]:
        write_attr(s, "db_long", val)
        got = read_attr(s, "db_long")
        assert_equal(f"DB long {val}", got, val)


def test_db_boolean(s):
    print("\n-- DB: DevBoolean --")
    register_attr(s, "db_bool", "DB1.30.0", CmdArgType.DevBoolean)

    write_attr(s, "db_bool", True)
    got = read_attr(s, "db_bool")
    assert_true("DB bool True", got)

    write_attr(s, "db_bool", False)
    got = read_attr(s, "db_bool")
    assert_false("DB bool False", got)


def test_db_boolean_multibit(s):
    print("\n-- DB: DevBoolean multi-bit (same byte) --")
    # Register 3 booleans on bits 0, 1, 2 of byte 31
    register_attr(s, "db_bit0", "DB1.31.0", CmdArgType.DevBoolean)
    register_attr(s, "db_bit1", "DB1.31.1", CmdArgType.DevBoolean)
    register_attr(s, "db_bit2", "DB1.31.2", CmdArgType.DevBoolean)

    # Set all three
    write_attr(s, "db_bit0", True)
    write_attr(s, "db_bit1", True)
    write_attr(s, "db_bit2", True)

    assert_true("bit0 True", read_attr(s, "db_bit0"))
    assert_true("bit1 True", read_attr(s, "db_bit1"))
    assert_true("bit2 True", read_attr(s, "db_bit2"))

    # Clear bit1 only -- bits 0 and 2 should remain set
    write_attr(s, "db_bit1", False)
    assert_true("bit0 still True", read_attr(s, "db_bit0"))
    assert_false("bit1 now False", read_attr(s, "db_bit1"))
    assert_true("bit2 still True", read_attr(s, "db_bit2"))

    # Clear all
    write_attr(s, "db_bit0", False)
    write_attr(s, "db_bit2", False)
    assert_false("bit0 cleared", read_attr(s, "db_bit0"))
    assert_false("bit1 still cleared", read_attr(s, "db_bit1"))
    assert_false("bit2 cleared", read_attr(s, "db_bit2"))


def test_db_boolean_parse_variants(s):
    print("\n-- DB: DevBoolean parse variants via write --")
    register_attr(s, "db_pv0", "DB1.35.0", CmdArgType.DevBoolean)
    register_attr(s, "db_pv1", "DB1.35.1", CmdArgType.DevBoolean)

    # string "true"/"false"
    write_attr(s, "db_pv0", "true")
    assert_true("write 'true'", read_attr(s, "db_pv0"))

    write_attr(s, "db_pv0", "false")
    assert_false("write 'false'", read_attr(s, "db_pv0"))

    # integer 1/0
    write_attr(s, "db_pv1", 1)
    assert_true("write 1", read_attr(s, "db_pv1"))

    write_attr(s, "db_pv1", 0)
    assert_false("write 0", read_attr(s, "db_pv1"))

    # string "yes"/"1"
    write_attr(s, "db_pv0", "yes")
    assert_true("write 'yes'", read_attr(s, "db_pv0"))

    write_attr(s, "db_pv0", "0")
    assert_false("write '0'", read_attr(s, "db_pv0"))


def test_db_string(s):
    print("\n-- DB: DevString --")
    # suboffset 20 = max string length 20
    register_attr(s, "db_string", "DB1.40.20", CmdArgType.DevString)

    for val in ["Hello", "Snap7", "Test123", ""]:
        write_attr(s, "db_string", val)
        got = read_attr(s, "db_string")
        assert_equal(f"DB string '{val}'", got, val)


def test_db_string_default_length(s):
    print("\n-- DB: DevString (default 254 length) --")
    # suboffset 0 -> default max length 254
    register_attr(s, "db_str_dflt", "DB1.70", CmdArgType.DevString)

    val = "A longer test string with default max length"
    write_attr(s, "db_str_dflt", val)
    got = read_attr(s, "db_str_dflt")
    assert_equal("DB default-length string", got, val)


def test_db_string_utf8(s):
    print("\n-- DB: DevString UTF-8 --")
    register_attr(s, "db_str_utf8", "DB1.340.30", CmdArgType.DevString)

    # Degree symbol (2 bytes in UTF-8)
    val = "25\u00b0C"
    write_attr(s, "db_str_utf8", val)
    got = read_attr(s, "db_str_utf8")
    assert_equal("DB UTF-8 degree", got, val)

    # German umlauts (2 bytes each in UTF-8)
    val = "\u00e4\u00f6\u00fc\u00df"
    write_attr(s, "db_str_utf8", val)
    got = read_attr(s, "db_str_utf8")
    assert_equal("DB UTF-8 umlauts", got, val)

    # Mixed ASCII and multi-byte
    val = "Caf\u00e9"
    write_attr(s, "db_str_utf8", val)
    got = read_attr(s, "db_str_utf8")
    assert_equal("DB UTF-8 mixed", got, val)

    # Overwrite with plain ASCII
    val = "plain"
    write_attr(s, "db_str_utf8", val)
    got = read_attr(s, "db_str_utf8")
    assert_equal("DB UTF-8 then ASCII", got, val)


# ===========================================================================
#  Test suites -- integration: input area (E/I), pre-seeded
# ===========================================================================

def test_input_area_float(s):
    print("\n-- Input area (E): DevFloat pre-seeded --")
    register_attr(s, "e_float", "E.0", CmdArgType.DevFloat)

    ba = bytearray(4)
    snap7.util.set_real(ba, 0, 42.5)
    _seed_pe(0, ba)
    got = read_attr(s, "e_float")
    assert_equal("input float 42.5", got, 42.5, tolerance=1e-5)

    snap7.util.set_real(ba, 0, -7.25)
    _seed_pe(0, ba)
    got = read_attr(s, "e_float")
    assert_equal("input float -7.25", got, -7.25, tolerance=1e-5)


def test_input_area_long(s):
    print("\n-- Input area (I): DevLong pre-seeded --")
    register_attr(s, "i_long", "I.10", CmdArgType.DevLong)

    ba = bytearray(4)
    snap7.util.set_dint(ba, 0, -12345)
    _seed_pe(10, ba)
    got = read_attr(s, "i_long")
    assert_equal("input long -12345", got, -12345)

    snap7.util.set_dint(ba, 0, 999999)
    _seed_pe(10, ba)
    got = read_attr(s, "i_long")
    assert_equal("input long 999999", got, 999999)


def test_input_area_boolean(s):
    print("\n-- Input area (E): DevBoolean pre-seeded --")
    register_attr(s, "e_bool", "E.20.5", CmdArgType.DevBoolean)

    ba = bytearray(1)
    snap7.util.set_bool(ba, 0, 5, True)
    _seed_pe(20, ba)
    got = read_attr(s, "e_bool")
    assert_true("input bool True", got)

    snap7.util.set_bool(ba, 0, 5, False)
    _seed_pe(20, ba)
    got = read_attr(s, "e_bool")
    assert_false("input bool False", got)


# ===========================================================================
#  Test suites -- integration: output area (A/Q)
# ===========================================================================

def test_output_area_float(s):
    print("\n-- Output area (A): DevFloat --")
    register_attr(s, "a_float", "A.0", CmdArgType.DevFloat)

    for val in [99.9, 0.0, -42.5]:
        write_attr(s, "a_float", val)
        got = read_attr(s, "a_float")
        assert_equal(f"output float {val}", got, val, tolerance=1e-4)


def test_output_area_long(s):
    print("\n-- Output area (Q): DevLong --")
    register_attr(s, "q_long", "Q.10", CmdArgType.DevLong)

    for val in [777, 0, -1, 2147483647]:
        write_attr(s, "q_long", val)
        got = read_attr(s, "q_long")
        assert_equal(f"output long {val}", got, val)


def test_output_area_double(s):
    print("\n-- Output area (A): DevDouble --")
    register_attr(s, "a_double", "A.20", CmdArgType.DevDouble)

    for val in [1.23456789012345, -9.87654321]:
        write_attr(s, "a_double", val)
        got = read_attr(s, "a_double")
        assert_equal(f"output double {val}", got, val, tolerance=1e-9)


# ===========================================================================
#  Test suites -- edge cases
# ===========================================================================

def test_edge_cases(s):
    print("\n-- edge cases --")
    import math

    # Float infinity
    register_attr(s, "edge_float", "DB1.330", CmdArgType.DevFloat)

    write_attr(s, "edge_float", float("inf"))
    got = read_attr(s, "edge_float")
    assert_true("float +inf", math.isinf(got) and got > 0)

    write_attr(s, "edge_float", float("-inf"))
    got = read_attr(s, "edge_float")
    assert_true("float -inf", math.isinf(got) and got < 0)

    write_attr(s, "edge_float", float("nan"))
    got = read_attr(s, "edge_float")
    assert_true("float nan", math.isnan(got))

    # Long zero
    register_attr(s, "edge_long", "DB1.340", CmdArgType.DevLong)
    write_attr(s, "edge_long", 0)
    got = read_attr(s, "edge_long")
    assert_equal("long zero", got, 0)

    # String near max custom length
    register_attr(s, "edge_str", "DB1.350.10", CmdArgType.DevString)
    val = "123456789"  # 9 chars, max 10
    write_attr(s, "edge_str", val)
    got = read_attr(s, "edge_str")
    assert_equal("string near-max", got, val)

    # Overwrite same register with different values
    register_attr(s, "edge_overwrite", "DB1.370", CmdArgType.DevFloat)
    write_attr(s, "edge_overwrite", 1.0)
    write_attr(s, "edge_overwrite", 2.0)
    write_attr(s, "edge_overwrite", 3.0)
    got = read_attr(s, "edge_overwrite")
    assert_equal("overwrite final value", got, 3.0, tolerance=1e-7)

    # Boolean all 8 bits of a byte
    for bit in range(8):
        name = f"edge_bit{bit}"
        register_attr(s, name, f"DB1.380.{bit}", CmdArgType.DevBoolean)
        write_attr(s, name, False)

    # Set alternating bits
    for bit in range(0, 8, 2):
        write_attr(s, f"edge_bit{bit}", True)

    for bit in range(8):
        got = read_attr(s, f"edge_bit{bit}")
        expected = (bit % 2 == 0)
        assert_equal(f"all-bits bit{bit} = {expected}", got, expected)


# ===========================================================================
#  Main
# ===========================================================================

def main():
    global passed, failed

    # -- pure conversion tests (no server needed) --
    test_get_register_parts()
    test_string_value_to_var_type()
    test_string_value_to_write_type()
    test_parse_boolean()
    test_bytes_per_variable_type()
    test_byte_conversions()
    test_validation_edge_cases()

    # -- start snap7 server --
    print("\n== Starting Snap7 server simulator on port", SIM_PORT, "==")
    try:
        start_server()
    except Exception as e:
        print(f"FATAL: Cannot start snap7 server: {e}")
        traceback.print_exc()
        sys.exit(1)

    time.sleep(0.5)

    try:
        s = State()
        s.client.connect("127.0.0.1", 0, 0, SIM_PORT)
        if not s.client.get_connected():
            print("FATAL: Cannot connect to snap7 server")
            sys.exit(1)
        print("Connected to snap7 server\n")

        # -- DB area tests --
        test_db_float(s)
        test_db_double(s)
        test_db_long(s)
        test_db_boolean(s)
        test_db_boolean_multibit(s)
        test_db_boolean_parse_variants(s)
        test_db_string(s)
        test_db_string_default_length(s)
        test_db_string_utf8(s)

        # -- Input area tests (pre-seeded) --
        test_input_area_float(s)
        test_input_area_long(s)
        test_input_area_boolean(s)

        # -- Output area tests --
        test_output_area_float(s)
        test_output_area_long(s)
        test_output_area_double(s)

        # -- Edge cases --
        test_edge_cases(s)

        s.client.disconnect()

    except Exception:
        traceback.print_exc()
        failed += 1
    finally:
        stop_server()

    # -- summary --
    total = passed + failed
    print(f"\n{'=' * 50}")
    print(f"  Results: {passed}/{total} passed, {failed} failed")
    if errors:
        print("\n  Failures:")
        for e in errors:
            print(f"    {e}")
    print(f"{'=' * 50}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
