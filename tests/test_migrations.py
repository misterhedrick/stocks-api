from types import SimpleNamespace

from app.db import migrations


def test_can_skip_noop_bridge_migration_for_known_0011_to_0012_state(monkeypatch):
    monkeypatch.setattr(
        migrations,
        "_get_current_database_revision",
        lambda: migrations.NOOP_BRIDGE_CURRENT_REVISION,
    )
    monkeypatch.setattr(
        migrations.ScriptDirectory,
        "from_config",
        lambda _: SimpleNamespace(get_heads=lambda: [migrations.NOOP_BRIDGE_HEAD_REVISION]),
    )

    assert migrations._can_skip_noop_bridge_migration(SimpleNamespace())


def test_cannot_skip_noop_bridge_migration_when_other_heads_exist(monkeypatch):
    monkeypatch.setattr(
        migrations,
        "_get_current_database_revision",
        lambda: migrations.NOOP_BRIDGE_CURRENT_REVISION,
    )
    monkeypatch.setattr(
        migrations.ScriptDirectory,
        "from_config",
        lambda _: SimpleNamespace(
            get_heads=lambda: [migrations.NOOP_BRIDGE_HEAD_REVISION, "0013_real_migration"]
        ),
    )

    assert not migrations._can_skip_noop_bridge_migration(SimpleNamespace())


def test_cannot_skip_noop_bridge_migration_from_unexpected_revision(monkeypatch):
    monkeypatch.setattr(migrations, "_get_current_database_revision", lambda: "0010_previous")
    monkeypatch.setattr(
        migrations.ScriptDirectory,
        "from_config",
        lambda _: SimpleNamespace(get_heads=lambda: [migrations.NOOP_BRIDGE_HEAD_REVISION]),
    )

    assert not migrations._can_skip_noop_bridge_migration(SimpleNamespace())
