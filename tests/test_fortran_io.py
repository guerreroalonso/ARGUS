import pytest

from argus.core.fortran_io import A, E, FortranReadError, I, X, format_e22_15, parse_int, parse_real, read_record
from argus.pengeom.writer import format_value


def test_parse_real_basic():
    assert parse_real("+2.500000000000000E+02", 15) == 250.0
    assert parse_real(" -3.000000000000000E+00", 15) == -3.0
    assert parse_real("1.5D+02", 15) == 150.0
    assert parse_real("                      ", 15) == 0.0


def test_parse_real_is_correctly_rounded():
    # Multiplicar la mantisa por 10**e no redondea bien; strtod sí.
    for txt in ("3.070000076293945E+01", "2.163374355000000E+01", "4.326748711000000E+01",
                "1.000000100000000E+06", "7.673360840683074E-02"):
        assert parse_real(txt, 15) == float(txt)


def test_parse_real_implied_decimals():
    # Sin punto decimal, E22.15 aplica 15 decimales implícitos (trampa habitual).
    assert parse_real("250", 15) == 2.5e-13
    assert parse_real("250E+02", 15) == 2.5e-11
    assert parse_real("12.5", 15) == 12.5


def test_parse_real_errors():
    with pytest.raises(FortranReadError):
        parse_real("abc", 15)


def test_parse_int():
    assert parse_int("   4") == 4
    assert parse_int("    ") == 0
    assert parse_int("-1") == -1


def test_read_record_param_line():
    rec = "X-SCALE=(+5.000000000000000E+02,   3)".ljust(72)
    kw, value, ipar, unit = read_record(rec, [A(8), X(1), E(22, 15), X(1), I(4), A(8)])
    assert kw == "X-SCALE=" and value == 500.0 and ipar == 3 and unit.startswith(")")


def test_format_e22_15():
    assert format_e22_15(250.0) == " 2.500000000000000E+02"
    assert format_e22_15(-3.0) == "-3.000000000000000E+00"
    assert format_e22_15(0.0) == " 0.000000000000000E+00"
    assert len(format_e22_15(1.234e-5)) == 22


def test_format_value_sign():
    assert format_value(250.0) == "+2.500000000000000E+02"
    assert format_value(-0.5) == "-5.000000000000000E-01"
    assert float(format_value(0.1)) == 0.1
