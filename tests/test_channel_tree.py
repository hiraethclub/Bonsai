import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6.QtCore import QCoreApplication, QModelIndex, Qt  # noqa: E402

from bonsai.models import Category, Channel  # noqa: E402
from bonsai.ui.channel_tree import ChannelTreeModel  # noqa: E402

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
    assert model.data(model.index(0, 1)) == "1 / 2"
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
