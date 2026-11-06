def test_settings_round_trip(tmp_path):
    from clipboard_sidekick.config import Settings, save_settings, load_settings
    p = tmp_path / "settings.json"
    s = Settings(hotkey="ctrl+alt+space", backend="ollama")
    save_settings(s, p)
    loaded = load_settings(p)
    assert loaded.hotkey == "ctrl+alt+space"
    assert loaded.backend == "ollama"
    assert loaded.prefetch_on is True


def test_load_missing_file_returns_defaults(tmp_path):
    from clipboard_sidekick.config import Settings, load_settings
    p = tmp_path / "does_not_exist.json"
    loaded = load_settings(p)
    assert loaded == Settings()
