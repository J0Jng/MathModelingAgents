def test_questionary_installed():
    import questionary  # noqa: F401
    assert hasattr(questionary, "select")


def test_rich_installed():
    import rich  # noqa: F401
    assert hasattr(rich, "console")
