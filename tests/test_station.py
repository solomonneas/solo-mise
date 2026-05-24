from pathlib import Path

from brigade.station import Station, DoctorContext


def test_station_holds_identity_and_doctor():
    called = {}

    def fake_doctor(ctx: DoctorContext):
        called["target"] = ctx.target
        return [("OK", "demo: check", "ok")]

    st = Station(name="memory", summary="memory station", aliases=("garde",), doctor=fake_doctor)
    assert st.name == "memory"
    assert "garde" in st.aliases
    assert st.kind == "builtin"
    ctx = DoctorContext(target=Path("/tmp/x"), selection=None, harnesses=[])
    assert st.doctor(ctx) == [("OK", "demo: check", "ok")]
    assert called["target"] == Path("/tmp/x")


def test_station_matches_name_and_aliases():
    st = Station(name="guard", summary="guard", aliases=("pass",), doctor=None)
    assert st.matches("guard")
    assert st.matches("pass")
    assert not st.matches("memory")
