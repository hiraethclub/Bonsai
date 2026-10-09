import json

import pytest

from bonsai import profiles
from bonsai.filters import FilterState, Flag
from bonsai.models import Category, Channel
from bonsai.profiles import NEW_HIGHLIGHT, NEW_INCLUDE, Profile, ProfileError, apply, snapshot
from bonsai.quality import Tier


@pytest.fixture(autouse=True)
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setenv("BONSAI_CONFIG_DIR", str(tmp_path / "cfg"))


UK = Category("1", "UK: GENERAL")
SPORT = Category("2", "UK: SPORT")


def groups_v1():
    return [
        (UK, [Channel(1, "BBC One", "1"), Channel(2, "ITV", "1"), Channel(3, "Channel 4", "1")]),
        (SPORT, [Channel(10, "Sky Sports 1", "2"), Channel(11, "Sky Sports 2", "2")]),
    ]


def test_snapshot_records_ticks_categories_and_known_ids():
    p = snapshot("Vita", groups_v1(), {1, 2, 10, 11}, {"2"}, new_channels=NEW_INCLUDE)
    assert [(c.stream_id, c.name, c.category) for c in p.channels] == [
        (1, "BBC One", "UK: GENERAL"),
        (2, "ITV", "UK: GENERAL"),
        (10, "Sky Sports 1", "UK: SPORT"),
        (11, "Sky Sports 2", "UK: SPORT"),
    ]
    assert [(c.id, c.full, c.known) for c in p.categories] == [("1", False, [1, 2, 3]), ("2", True, [10, 11])]
    assert p.saved_at


def test_apply_same_list_round_trips():
    p = snapshot("Vita", groups_v1(), {1, 3, 10}, set())
    r = apply(p, groups_v1())
    assert r.ticked == {1, 3, 10}
    assert not r.unmatched and not r.new and r.rematched == 0


def test_renumbered_streams_found_by_category_and_name():
    p = snapshot("Vita", groups_v1(), {1, 2}, set())
    renumbered = [(UK, [Channel(101, "BBC One", "1"), Channel(102, "ITV", "1"), Channel(103, "Channel 4", "1")])]
    r = apply(p, renumbered)
    assert r.ticked == {101, 102}
    assert r.rematched == 2


def test_fallback_to_unique_name_in_another_category():
    p = snapshot("Vita", groups_v1(), {2}, set())
    moved = [(Category("9", "UK: ENTERTAINMENT"), [Channel(202, "ITV", "9")])]
    assert apply(p, moved).ticked == {202}


def test_name_matching_ignores_case_spacing_and_superscripts():
    p = snapshot("Vita", [(Category("1", "US| NEWS ᴴᴰ"), [Channel(1, "CNN ᴴᴰ", "1")])], {1}, set())
    r = apply(p, [(Category("7", "us| news hd"), [Channel(70, "cnn  HD", "7")])])
    assert r.ticked == {70}


def test_ambiguous_or_missing_names_are_reported():
    p = snapshot("Vita", groups_v1(), {2, 3}, set())
    later = [
        (Category("5", "A"), [Channel(50, "ITV", "5")]),
        (Category("6", "B"), [Channel(60, "ITV", "6")]),
    ]
    r = apply(p, later)
    assert r.ticked == set()
    assert sorted(c.name for c in r.unmatched) == ["Channel 4", "ITV"]


def test_new_channels_highlighted_in_partly_ticked_category():
    p = snapshot("Vita", groups_v1(), {1}, set(), new_channels=NEW_INCLUDE)
    later = groups_v1()
    later[0][1].append(Channel(4, "ITV2", "1"))
    later[1][1].append(Channel(12, "Sky Sports 3", "2"))  # nothing ticked here: not watched
    r = apply(p, later)
    assert r.new == {4}
    assert r.auto_ticked == set()  # category was only partly ticked
    assert r.ticked == {1}


def test_new_channels_auto_ticked_only_in_full_categories_with_include():
    later = groups_v1()
    later[1][1].append(Channel(12, "Sky Sports 3", "2"))

    include = snapshot("Vita", groups_v1(), {10, 11}, {"2"}, new_channels=NEW_INCLUDE)
    r = apply(include, later)
    assert r.new == {12} and r.auto_ticked == {12}
    assert r.ticked == {10, 11, 12}

    highlight = snapshot("Vita", groups_v1(), {10, 11}, {"2"}, new_channels=NEW_HIGHLIGHT)
    r = apply(highlight, later)
    assert r.new == {12} and r.auto_ticked == set()
    assert r.ticked == {10, 11}


def test_fully_renumbered_category_does_not_flag_everything_as_new():
    p = snapshot("Vita", groups_v1(), {10, 11}, {"2"}, new_channels=NEW_INCLUDE)
    renumbered = [(SPORT, [Channel(110, "Sky Sports 1", "2"), Channel(111, "Sky Sports 2", "2")])]
    r = apply(p, renumbered)
    assert r.new == set()
    assert r.ticked == {110, 111}


def test_save_load_round_trip_and_listing():
    filters = FilterState(frozenset({Tier.UHD}), frozenset({Flag.PPV, Flag.RAW}), frozenset({"US"}))
    p = snapshot(
        "Vita / PS",
        groups_v1(),
        {1, 10},
        {"2"},
        new_channels=NEW_INCLUDE,
        export_path="/tmp/vita.m3u",
        stream_format="m3u8",
        include_epg=True,
        filters=filters,
    )
    path = profiles.save(p)
    assert path.name == "Vita_PS.json"
    loaded = profiles.load("Vita / PS")
    assert loaded == p
    assert profiles.list_names() == ["Vita / PS"]
    profiles.delete("Vita / PS")
    assert profiles.list_names() == []


def test_missing_and_damaged_profiles():
    with pytest.raises(ProfileError):
        profiles.load("nope")
    profiles.profiles_dir().mkdir(parents=True)
    (profiles.profiles_dir() / "bad.json").write_text("{not json")
    with pytest.raises(ProfileError):
        profiles.load("bad")
    (profiles.profiles_dir() / "bad.json").write_text(json.dumps({"name": "bad", "channels": [["x"]]}))
    with pytest.raises(ProfileError):
        profiles.load("bad")
    assert profiles.list_names() == ["bad"]


def test_validate_name():
    with pytest.raises(ProfileError):
        profiles.validate_name("   ")
    profiles.save(Profile(name="Vita!"))
    with pytest.raises(ProfileError):
        profiles.validate_name("Vita?")  # same file name, different profile
    assert profiles.validate_name(" Vita! ") == "Vita!"


def test_old_or_partial_json_gets_defaults():
    p = Profile.from_json({"name": "x"})
    assert p.new_channels == NEW_HIGHLIGHT
    assert p.filters == FilterState()
    assert p.stream_format == "ts"


def test_filter_state_json():
    s = FilterState(frozenset({Tier.FHD}), frozenset({Flag.HEVC}), frozenset({"UK", "Other"}))
    assert FilterState.from_json(s.to_json()) == s
    assert FilterState.from_json({"hidden_tiers": []}).hidden_tiers == frozenset()
    assert FilterState.from_json(None) == FilterState()


def test_renumbered_channel_is_not_also_new():
    p = snapshot("Vita", groups_v1(), {1}, set())
    later = [(UK, [Channel(101, "BBC One", "1"), Channel(2, "ITV", "1"), Channel(3, "Channel 4", "1"),
                   Channel(4, "ITV2", "1")])]
    r = apply(p, later)
    assert r.ticked == {101}
    assert r.new == {4}
