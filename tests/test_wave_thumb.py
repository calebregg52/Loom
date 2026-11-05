from loom.gui import wave_thumb as wt


def test_shapes_are_remembered_and_journeys_recognised(tmp_path):
    path = tmp_path / "journey_shapes.json"
    assert wt.load_shapes(path) == {}
    wt.remember_shape(path, "p1", {"points": [(0, 0.2), (1, 0.9)], "shape_label": "Build up"})
    shapes = wt.load_shapes(path)
    assert shapes["p1"]["shape_label"] == "Build up"
    assert wt.is_journey({"id": "p1", "name": "Loom · Anything"}, shapes)
    assert wt.is_journey({"id": "p2", "name": "Loom · Journey · Wind down"}, shapes)
    assert not wt.is_journey({"id": "p3", "name": "Loom · Country"}, shapes)


def test_playlist_bpms_skip_songs_without_tempo():
    lib = {"tracks": {"a": {"features": {"tempo": 120}}, "b": {"features": None}, "c": {"features": {"tempo": 90}}}}
    assert wt.playlist_bpms(lib, {"tracks": [{"id": "a"}, {"id": "b"}, {"id": "x"}, {"id": "c"}]}) == [120, 90]
