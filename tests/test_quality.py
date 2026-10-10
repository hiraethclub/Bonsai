import pytest

from bonsai.quality import DEFAULT_HIDDEN_TIERS, Tier, parse_tiers, tier_from_name


@pytest.mark.parametrize(
    "name, tier",
    [
        # UHD tier
        ("Sky Sports Main Event 4K", Tier.UHD),
        ("UK: BT Sport Ultimate UHD", Tier.UHD),
        ("Nature 2160p", Tier.UHD),
        ("Channel [4K]", Tier.UHD),
        ("|UK| Movies 4k", Tier.UHD),
        ("4K UHD Demo", Tier.UHD),
        # FHD tier
        ("UK: Channel FHD", Tier.FHD),
        ("|UK| Channel FHD", Tier.FHD),
        ("Channel (FHD)", Tier.FHD),
        ("BBC One Full HD", Tier.FHD),
        ("BBC One FullHD", Tier.FHD),
        ("BBC One Full-HD", Tier.FHD),
        ("Eurosport 1080p", Tier.FHD),
        ("Eurosport 1080i", Tier.FHD),
        ("Eurosport 1080", Tier.FHD),
        ("BBC One HD 1080", Tier.FHD),  # most specific tag wins
        ("fhd sky news", Tier.FHD),
        # HD tier
        ("|UK| Channel HD", Tier.HD),
        ("UK: Channel HD", Tier.HD),
        ("Channel (HD)", Tier.HD),
        ("Channel [hd]", Tier.HD),
        ("ITV 720p", Tier.HD),
        ("ITV 720", Tier.HD),
        ("Das Erste HD+", Tier.HD),
        ("BBC_ONE_HD", Tier.HD),
        ("UK:HD:BBC", Tier.HD),
        # SD tier
        ("Dave SD", Tier.SD),
        ("Dave (SD)", Tier.SD),
        ("Old Film 576p", Tier.SD),
        ("Old Film 576i", Tier.SD),
        ("Kids 480p", Tier.SD),
        # Untagged
        ("BBC Two", Tier.UNTAGGED),
        ("", Tier.UNTAGGED),
        ("HDTV News", Tier.UNTAGGED),  # HD inside a longer word
        ("SHD Shopping", Tier.UNTAGGED),
        ("4KIDS TV", Tier.UNTAGGED),
        ("Channel 7200", Tier.UNTAGGED),
        ("Sky Sports HD2", Tier.UNTAGGED),
        ("HDR Showcase", Tier.UNTAGGED),
        ("SDTV", Tier.UNTAGGED),
        ("Ссылка ТВ", Tier.UNTAGGED),
    ],
)
def test_tier_from_name(name, tier):
    assert tier_from_name(name) == tier


@pytest.mark.parametrize("name", ["Channel UHD", "Channel FHD"])
def test_hd_never_matches_inside_fhd_or_uhd(name):
    assert tier_from_name(name) != Tier.HD


def test_default_hides_above_720():
    assert DEFAULT_HIDDEN_TIERS == {Tier.UHD, Tier.FHD}


def test_parse_tiers_ignores_junk():
    assert parse_tiers(["UHD", "nope", "SD", None]) == {Tier.UHD, Tier.SD}
    assert parse_tiers(None) == set()


@pytest.mark.parametrize(
    "name, tier",
    [
        ("4K| RELAX ᵁᴴᴰ ³⁸⁴⁰ᴾ ☼", Tier.UHD),
        ("US| AT&T ᴿᴬᵂ ⁶⁰ᶠᵖˢ", Tier.UNTAGGED),
        ("US| NEWS ᴴᴰ/ᴿᴬᵂ ⁶⁰ᶠᵖˢ", Tier.HD),
        ("UK: SKY ATLANTIC ᴴᴰ", Tier.HD),
        ("RELAXᵁᴴᴰ", Tier.UHD),  # glued onto the word
        ("CHANNEL ˢᴰ", Tier.SD),
        ("FR: CINE ⁸ᴷ", Tier.UNTAGGED),  # this provider uses "8K" as a brand, not a resolution
        ("End | Shelton vs. Altmaier | 8K EXCLUSIVE | US: SOCCER PPV 3", Tier.UNTAGGED),
        ("ES: LALIGA ʰᵉᵛᶜ", Tier.UNTAGGED),
        ("BE| DAZN PPV ⁽ᴮᴷ⁾", Tier.UNTAGGED),
        ("UK: NATURE ᴴᴰᴿ ³⁸⁴⁰ᴾ", Tier.UHD),  # HDR is not HD, but 3840P is UHD
        ("TV: NEW YORK cɪty ᴿᴬᵂ", Tier.UNTAGGED),
        ("Nature 3840", Tier.UHD),
    ],
)
def test_superscript_tags(name, tier):
    assert tier_from_name(name) == tier


def test_normalise_name():
    from bonsai.quality import normalise_name

    assert normalise_name("4K| RELAX ᵁᴴᴰ ³⁸⁴⁰ᴾ") == "4K| RELAX UHD 3840P"
    assert normalise_name("RELAXᵁᴴᴰ") == "RELAX UHD"
    assert normalise_name("US| AT&T ᴿᴬᵂ ⁶⁰ᶠᵖˢ") == "US| AT&T RAW 60fps"
    assert normalise_name("plain ascii") == "plain ascii"


def test_category_fallback():
    from bonsai.quality import classify

    assert classify("BBC News", Tier.HD) == (Tier.HD, True)
    assert classify("BBC News ˢᴰ", Tier.HD) == (Tier.SD, False)  # own tag wins
    assert classify("BBC News", Tier.UNTAGGED) == (Tier.UNTAGGED, False)


@pytest.mark.parametrize(
    "name, tier",
    [
        ("1+1 International HD (720p)", Tier.HD),
        ("1+1 Marafon (1080p)", Tier.FHD),
        ("Akaal Channel (396p) [Geo-blocked]", Tier.SD),
        ("Some TV (540p)", Tier.SD),
        ("Some TV (576i)", Tier.SD),
        ("Some TV (900p)", Tier.FHD),
        ("Some TV (2160p)", Tier.UHD),
        ("GOLD: EUROSPORT 2 ʜᴅ", Tier.HD),  # small capitals
        ("Channel 100", Tier.UNTAGGED),  # bare number, no p/i
    ],
)
def test_heights_and_small_caps(name, tier):
    assert tier_from_name(name) == tier


def test_small_caps_normalised():
    from bonsai.quality import normalise_name

    assert normalise_name("EXXEN SPORTS 7 ʜᴅ") == "EXXEN SPORTS 7 HD"
    assert normalise_name("TVʜᴅ") == "TV HD"
