"""Named-item resolution: typos, missing words, casing, no match, and near-ties (FR-NAME)."""

import pytest

from factories import make_item
from stylist.core.catalog import Catalog
from stylist.core.names import resolve

NAMES = [
    ("pant-1", "bottom", "The Effortless Pant"),
    ("pant-2", "bottom", "The Effortless Pant™ Lo-Rise – Twill"),
    ("pant-1b", "bottom", "The Effortless Pant™"),  # another colorway of pant-1
    ("coat-1", "outerwear", "The Only Coat"),
    ("coat-2", "outerwear", "The Elowen Coat"),
    ("tote-1", "accessory", "THE SUPER PUFF™ Tote Bag"),
    ("tote-2", "accessory", "The Super Puff™ Mini Tote Bag"),
    ("shoe-1", "shoes", "Women's G.H.BASS x Aritzia Milton Coin Penny Loafer"),
    ("hoodie-1", "top", "Cozy Sweatfleece Hoodie"),
    ("hoodie-2", "top", "Cozy Sweatfleece Zip Hoodie"),
]


@pytest.fixture(scope="module")
def catalog():
    return Catalog([make_item(item_id, slot, name=name) for item_id, slot, name in NAMES])


@pytest.mark.parametrize("query,expected", [
    ("The Effortless Pant", "pant-1"),       # exact
    ("effortless pant", "pant-1"),           # casing, missing "the"
    ("efortless pnt", "pant-1"),             # typos
    ("effortless pant lo rise", "pant-2"),   # the more specific variant
    ("only coat", "coat-1"),
    ("milton lofer", "shoe-1"),              # brand prefix and "coin penny" left out
])
def test_typed_names_resolve_to_the_right_product(catalog, query, expected):  # FR-NAME-1
    found = resolve(catalog, query)
    assert found.item is not None and found.item.product_key == catalog.get(expected).product_key


def test_unknown_name_says_so_and_suggests_up_to_three(catalog):  # FR-NAME-2
    found = resolve(catalog, "purple unicorn onesie")

    assert found.item is None
    assert 1 <= len(found.suggestions) <= 3


def test_near_tie_returns_the_choices(catalog):  # FR-NAME-3
    found = resolve(catalog, "super puff tote")

    assert found.ambiguous
    assert {i.item_id for i in found.choices} == {"tote-1", "tote-2"}


def test_exact_name_is_never_ambiguous(catalog):
    found = resolve(catalog, "Cozy Sweatfleece Hoodie")
    assert found.item.item_id == "hoodie-1" and not found.ambiguous


def test_colorways_of_one_product_are_not_a_tie(catalog):
    found = resolve(catalog, "effortless pant")
    assert not found.ambiguous


def test_slot_limits_the_search(catalog):
    assert resolve(catalog, "effortless pant", slot="outerwear").item is None or \
        resolve(catalog, "effortless pant", slot="outerwear").item.slot == "outerwear"


def test_blank_query_matches_nothing(catalog):
    assert resolve(catalog, "   ").item is None
