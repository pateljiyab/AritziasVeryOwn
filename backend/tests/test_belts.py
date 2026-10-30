"""A belt only goes with bottoms that take one: never a dress, a skirt, or athletic or lounge bottoms."""

import pytest

from factories import make_item
from stylist.core.catalog import Catalog
from stylist.core.generator import generate
from stylist.core.models import Constraints, ParsedRequest

POLISHED = ParsedRequest(vibes=["polished"], formality=3)


def belt_catalog(*bottoms) -> Catalog:
    """The belt is the best accessory for a polished request, so only the rule keeps it out."""
    return Catalog([
        make_item("top", "top", vibes=("polished",)),
        make_item("dress", "dress", name="Slip Dress", vibes=("polished",), category="Clothing > Dresses"),
        make_item("shoes", "shoes", vibes=("polished",)),
        make_item("belt", "accessory", name="Leather Belt", vibes=("polished",)),
        make_item("scarf", "accessory", name="Silk Scarf", vibes=("minimal",)),
        *bottoms,
    ])


TROUSER = make_item("trouser", "bottom", name="Agency Pant", vibes=("polished",), category="Clothing > Pants > Trousers")
BELTLESS = [
    make_item("skirt", "bottom", name="Clarissa Skirt", vibes=("polished",), category="Clothing > Skirts"),
    make_item("legging", "bottom", name="Butter Legging", vibes=("polished",),
              category="Clothing > Activewear > Activewear Pants > Leggings"),
    make_item("sweat", "bottom", name="Perfect Banded Sweatpant", vibes=("polished",),
              category="Clothing > Activewear > Activewear Pants > Sweatpants"),
    make_item("lounge", "bottom", name="Gilmore Pant", vibes=("polished",),
              category="Clothing > Sleepwear & Loungewear > Loungewear > Loungewear Bottoms > Lounge Pants"),
    make_item("jogger", "bottom", name="Mega Jogger", vibes=("polished",)),  # no category: the name says it
    make_item("mini", "bottom", name="Bumper Mini Skirt", vibes=("polished",)),
]


def test_a_belt_goes_with_trousers():
    catalog = belt_catalog(TROUSER)

    outfit = generate(catalog, POLISHED, constraints=Constraints(locked={"bottom": "trouser"}, required={"accessory"}))

    assert outfit.pieces["accessory"].item_id == "belt"


def test_an_accessory_for_a_dress_is_never_a_belt():
    catalog = belt_catalog(TROUSER)

    outfit = generate(catalog, POLISHED, constraints=Constraints(locked={"dress": "dress"}, required={"accessory"}))

    assert outfit.pieces["accessory"].item_id == "scarf"


def test_no_belt_with_a_dress():
    catalog = belt_catalog(TROUSER)

    outfit = generate(catalog, POLISHED, constraints=Constraints(locked={"dress": "dress"}))

    assert outfit.pieces.get("accessory") is None or not outfit.pieces["accessory"].is_belt


@pytest.mark.parametrize("bottom", BELTLESS, ids=lambda item: item.item_id)
def test_no_belt_with_skirts_or_athletic_and_lounge_bottoms(bottom):
    catalog = belt_catalog(bottom)

    outfit = generate(catalog, POLISHED, constraints=Constraints(locked={"bottom": bottom.item_id}))

    assert outfit.pieces.get("accessory") is None or not outfit.pieces["accessory"].is_belt


def test_a_locked_belt_steers_to_bottoms_that_take_one():
    catalog = belt_catalog(TROUSER, *BELTLESS)

    outfit = generate(catalog, POLISHED, constraints=Constraints(locked={"accessory": "belt"}))

    assert "dress" not in outfit.pieces and outfit.pieces["bottom"].item_id == "trouser"


def test_both_locked_is_the_users_call():
    catalog = belt_catalog(*BELTLESS)

    outfit = generate(catalog, POLISHED, constraints=Constraints(locked={"accessory": "belt", "bottom": "skirt"}))

    assert outfit.pieces["accessory"].item_id == "belt" and outfit.pieces["bottom"].item_id == "skirt"


def test_no_outfit_pairs_a_belt_with_a_piece_that_doesnt_take_one():
    catalog = belt_catalog(TROUSER, *BELTLESS)
    for request in (POLISHED, ParsedRequest(vibes=["athleisure"]), ParsedRequest(vibes=["polished"], formality=4),
                    ParsedRequest(text="leather belt")):
        outfit = generate(catalog, request)
        belt = outfit.pieces.get("accessory")
        if belt is not None and belt.is_belt:
            assert all(piece.takes_a_belt for piece in outfit.pieces.values())
