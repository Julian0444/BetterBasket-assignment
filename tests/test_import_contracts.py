import betterbasket_matcher


def test_version_exists():
    assert hasattr(betterbasket_matcher, "__version__")
    assert isinstance(betterbasket_matcher.__version__, str)
    assert betterbasket_matcher.__version__
