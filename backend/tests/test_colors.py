import pytest

from stylist.tagging.colors import COLOR_WORDS, REFERENCE_SHADES, color_from_text, main_color, nearest_color


@pytest.mark.parametrize(
    ("hex_color", "expected"),
    [
        ("#000000", "black"),
        ("#2e384d", "navy"),    # the Lottie Sweater's dominant color
        ("#f3ead8", "cream"),
        ("#9caf88", "green"),   # sage
        ("#c8a2c8", "purple"),  # lilac
        ("#cc032c", "red"),
    ],
)
def test_nearest_color(hex_color, expected):
    assert nearest_color(hex_color) == expected


def test_nearest_color_without_a_color():
    assert nearest_color(None) is None
    assert nearest_color("") is None


def test_every_reference_shade_maps_to_its_own_tag():
    for tag, shades in REFERENCE_SHADES.items():
        for shade in shades:
            assert nearest_color(shade) == tag, shade


def test_only_taxonomy_tags_are_used():
    taxonomy = {"black", "white", "cream", "beige", "camel", "brown", "grey", "navy", "denim", "olive",
                "red", "burgundy", "pink", "orange", "yellow", "green", "teal", "blue", "purple",
                "metallic", "multi"}
    assert set(REFERENCE_SHADES) <= taxonomy
    assert set(COLOR_WORDS) == taxonomy


@pytest.mark.parametrize(
    ("alt_text", "slot", "expected"),
    [
        ("Flat lay of the navy blue cable knit Lottie sweater.", "top", "navy"),
        # On-model shot of the pants: the top is named first.
        ("Woman wearing black tank top and beige wide-leg chino pants.", "bottom", "beige"),
        ("Woman wearing black tank top and beige wide-leg chino pants.", "top", "black"),
        # Another garment after "with" is ignored.
        ("Model wearing a beige wool coat with black trousers.", "outerwear", "beige"),
        # Pattern words win within the product's own phrase.
        ("Flat lay of blue and white striped linen pants.", "bottom", "multi"),
        # Synonyms map to the taxonomy.
        ("Flat lay of a heather grey hoodie.", "top", "grey"),
        ("Woman wearing tan wide-leg pants.", "bottom", "camel"),
        # The slot noun isn't in the text: first color word before "with".
        ("Close-up of a burgundy knit with ribbed trim.", "top", "burgundy"),
        ("Linen pants laid flat on a surface.", "bottom", None),
    ],
)
def test_color_from_text(alt_text, slot, expected):
    assert color_from_text(alt_text, slot) == expected


def test_main_color_prefers_alt_text_then_hex():
    assert main_color("Flat lay of red sweater.", "#000000", "top") == ("red", "alt_text")
    assert main_color("Sweater laid flat.", "#000000", "top") == ("black", "hex")
    assert main_color("Sweater laid flat.", None, "top") == (None, "none")
