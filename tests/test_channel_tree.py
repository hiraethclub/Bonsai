import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6.QtCore import QCoreApplication, QModelIndex, Qt  # noqa: E402

from bonsai.models import Category, Channel  # noqa: E402
from bonsai.ui.channel_tree import COL_TICKED, ChannelTreeModel  # noqa: E402

Checked, Unchecked, Partial = (
    Qt.CheckState.Checked,
    Qt.CheckState.Unchecked,
    Qt.CheckState.PartiallyChecked,
)


@pytest.fixture(scope="module", autouse=True)
def app():
    return QCoreApplication.instance() or QCoreApplication([])


@pytest.fixture
def model():
    m = ChannelTreeModel()
    m.set_groups(
        [
            (Category("1", "UK General"), [Channel(1, "BBC One", "1"), Channel(2, "ITV", "1")]),
            (Category("2", "Sports"), [Channel(3, "Sky Sports", "2"), Channel(4, "BBC Sport", "2")]),
            (Category("3", "Empty"), []),
        ]
    )
    return m


def state(model, index):
    return model.data(index, Qt.ItemDataRole.CheckStateRole)


def tick(model, index, on=True):
    return model.setData(index, Checked if on else Unchecked, Qt.ItemDataRole.CheckStateRole)


def test_structure_hides_empty_categories(model):
    assert model.rowCount() == 2
    cat = model.index(0, 0)
    assert model.data(cat) == "UK General"
    assert model.rowCount(cat) == 2
    child = model.index(1, 0, cat)
    assert model.data(child) == "ITV"
    assert model.parent(child) == cat
    assert not model.parent(cat).isValid()
    assert model.rowCount(model.index(0, 1)) == 0


def test_tick_channel_makes_category_partial(model):
    cat = model.index(0, 0)
    tick(model, model.index(0, 0, cat))
    assert state(model, cat) == Partial
    assert model.data(model.index(0, COL_TICKED)) == "1 / 2"
    tick(model, model.index(1, 0, cat))
    assert state(model, cat) == Checked
    assert model.ticked_summary() == (2, 1)


def test_tick_category_ticks_children(model):
    cat = model.index(1, 0)
    tick(model, cat)
    assert model.ticked_ids() == {3, 4}
    assert all(state(model, model.index(r, 0, cat)) == Checked for r in range(2))
    tick(model, cat, False)
    assert model.ticked_ids() == set()


def test_filter_matches_channel_names_and_tick_only_visible(model):
    model.set_filter("bbc")
    assert model.rowCount() == 2
    sports = model.index(1, 0)
    assert model.rowCount(sports) == 1
    assert model.data(model.index(0, 0, sports)) == "BBC Sport"
    tick(model, sports)
    assert model.ticked_ids() == {4}
    model.set_filter("")
    assert state(model, model.index(1, 0)) == Partial


def test_filter_on_category_name_shows_all_its_channels(model):
    model.set_filter("SPORTS")
    assert model.rowCount() == 1
    assert model.rowCount(model.index(0, 0)) == 2


def test_ticks_survive_reload_when_ids_still_exist(model):
    tick(model, model.index(0, 0))
    model.set_groups([(Category("1", "UK General"), [Channel(2, "ITV", "1"), Channel(9, "New", "1")])])
    assert model.ticked_ids() == {2}


def test_ticked_signal(model):
    hits = []
    model.tickedChanged.connect(lambda: hits.append(1))
    tick(model, model.index(0, 0))
    assert hits
    model.clear_ticked()
    assert model.ticked_ids() == set()


def test_index_out_of_range(model):
    assert not model.index(5, 0).isValid()
    assert not model.index(5, 0, model.index(0, 0)).isValid()
    assert not model.parent(QModelIndex()).isValid()


@pytest.fixture
def tiered():
    m = ChannelTreeModel()
    m.set_hidden_tiers(set())
    m.set_groups(
        [
            (Category("1", "UK"), [Channel(1, "BBC One FHD", "1"), Channel(2, "BBC One HD", "1"),
                                    Channel(3, "BBC One", "1")]),
            (Category("2", "4K"), [Channel(4, "Demo UHD", "2")]),
        ]
    )
    return m


def test_tier_column(tiered):
    from bonsai.ui.channel_tree import COL_TIER

    cat = tiered.index(0, 0)
    assert [tiered.data(tiered.index(r, COL_TIER, cat)) for r in range(3)] == ["FHD", "HD", ""]


def test_hidden_tiers_leave_view_and_export(tiered):
    from bonsai.quality import Tier

    tick(tiered, tiered.index(0, 0))  # whole UK category, all three tiers shown
    assert tiered.ticked_ids() == {1, 2, 3}

    tiered.set_hidden_tiers({Tier.FHD, Tier.UHD})
    assert tiered.rowCount() == 1  # 4K category has nothing left
    assert tiered.rowCount(tiered.index(0, 0)) == 2
    assert tiered.ticked_ids() == {2, 3}
    assert tiered.ticked_summary() == (2, 1)
    assert tiered.hidden_ticked_count() == 1
    assert state(tiered, tiered.index(0, 0)) == Checked

    # Ticking a category while FHD is hidden does not touch the hidden channel
    tick(tiered, tiered.index(0, 0), False)
    assert tiered.ticked_ids() == set()
    tiered.set_hidden_tiers(set())
    assert tiered.ticked_ids() == {1}  # remembered from before


def test_tier_counts(tiered):
    from bonsai.quality import Tier

    counts = tiered.tier_counts()
    assert counts[Tier.FHD] == counts[Tier.HD] == counts[Tier.UHD] == counts[Tier.UNTAGGED] == 1


def test_superscript_category_tag_inherited_and_searchable():
    from bonsai.quality import Tier
    from bonsai.ui.channel_tree import COL_TIER

    m = ChannelTreeModel()
    m.set_hidden_tiers({Tier.UHD})
    m.set_groups(
        [
            (Category("1", "4K| RELAX ᵁᴴᴰ ³⁸⁴⁰ᴾ"), [Channel(1, "Fireplace", "1")]),
            (Category("2", "US| NEWS ᴴᴰ/ᴿᴬᵂ"), [Channel(2, "CNN", "2"), Channel(3, "Fox ˢᴰ", "2")]),
        ]
    )
    assert m.rowCount() == 1  # the UHD category's untagged channel inherited UHD and is hidden
    news = m.index(0, 0)
    assert m.data(m.index(0, COL_TIER)) == "HD"
    assert [m.data(m.index(r, COL_TIER, news)) for r in range(2)] == ["HD", "SD"]
    m.set_filter("raw")  # matches the superscript ᴿᴬᵂ in the category name
    assert m.rowCount() == 1
    m.set_filter("fox sd")
    assert m.visible_channel_count() == 1


def test_label_and_region_filters():
    from bonsai.filters import Flag
    from bonsai.ui.channel_tree import COL_LABELS

    m = ChannelTreeModel()
    m.set_hidden_tiers(set())
    m.set_hidden_flags(set())
    m.set_groups(
        [
            (Category("1", "US| NEWS ᴿᴬᵂ"), [Channel(1, "CNN", "1", epg_channel_id="cnn.us"),
                                           Channel(2, "Fox", "1")]),
            (Category("2", "US| NBA PPV"), [Channel(3, "Lakers vs Celtics", "2")]),
            (Category("3", "UK: GENERAL"), [Channel(4, "BBC One", "3", epg_channel_id="bbc1")]),
        ]
    )
    assert m.data(m.index(0, COL_LABELS)) == "RAW"
    assert m.data(m.index(0, COL_LABELS, m.index(0, 0))) == "RAW"
    assert m.flag_counts()[Flag.RAW] == 2
    assert m.flag_counts()[Flag.NO_EPG] == 2
    assert dict(m.region_counts()) == {"US": 3, "UK": 1}

    tick(m, m.index(0, 0))
    tick(m, m.index(1, 0))
    tick(m, m.index(2, 0))
    assert m.ticked_ids() == {1, 2, 3, 4}

    m.set_hidden_flags({Flag.PPV})
    assert m.rowCount() == 2
    assert m.ticked_ids() == {1, 2, 4}

    m.set_hidden_flags({Flag.PPV, Flag.NO_EPG})
    assert m.ticked_ids() == {1, 4}

    m.set_hidden_regions({"UK"})
    assert m.ticked_ids() == {1}
    assert m.rowCount() == 1
    assert m.hidden_ticked_count() == 3

    m.set_hidden_flags(set())
    m.set_hidden_regions(set())
    assert m.ticked_ids() == {1, 2, 3, 4}


def test_adult_flag_from_api_field():
    from bonsai.filters import Flag

    m = ChannelTreeModel()
    m.set_groups([(Category("9", "Movies"), [Channel(1, "Late Film", "9", is_adult=True),
                                             Channel(2, "Day Film", "9")])])
    assert m.flags_of(1) >= {Flag.ADULT}
    assert m.visible_channel_count() == 1  # adult hidden by default


def test_playlist_mode_shows_server():
    from bonsai.ui.channel_tree import COL_ID

    m = ChannelTreeModel()
    m.set_hidden_tiers(set())
    m.set_groups([(Category("News", "News"), [Channel(123456789, "X", "News", url="https://cdn.example.com/x.m3u8")])])
    assert m.playlist_mode
    assert m.headerData(COL_ID, Qt.Orientation.Horizontal) == "Server"
    assert m.data(m.index(0, COL_ID, m.index(0, 0))) == "cdn.example.com"


def test_test_results_filter_and_live_updates():
    from bonsai.streamtest import FAIL, OK, VITA_NO, TestResult
    from bonsai.ui.channel_tree import COL_STATUS

    m = ChannelTreeModel()
    m.set_hidden_tiers(set())
    m.set_groups([(Category("1", "News"), [Channel(1, "A", "1"), Channel(2, "B", "1"), Channel(3, "C", "1")])])
    m.set_ticked({1, 2, 3}, True)
    m.set_results({1: TestResult(OK, "MPEG-TS"), 2: TestResult(FAIL, "HTTP 404: gone")})
    assert m.ticked_ids() == {1, 3}  # "Not working" hidden by default; untested stays
    assert m.status_counts() == {"ok": 1, "fail": 1, "untested": 1}
    news = m.index(0, 0)
    assert m.data(m.index(0, COL_STATUS, news)) == "✓ MPEG-TS"
    assert m.data(m.index(0, COL_STATUS)) == "1 ✓  1 ✗"

    m.mark_testing({3})
    assert m.data(m.index(1, COL_STATUS, news)) == "testing…"
    m.update_result(3, TestResult(FAIL, "timed out"))
    assert m.rowCount(news) == 2  # not re-filtered mid-test
    m.end_testing()
    assert m.rowCount(news) == 1 and m.ticked_ids() == {1}

    m.update_result(1, TestResult(OK, "HLS", vita=VITA_NO, vita_reason="fMP4 segments"))
    m.refilter()
    assert m.status_of(1) == "novita"
    assert m.data(m.index(0, COL_STATUS, news)) == "✓ not on Vita: fMP4 segments"
