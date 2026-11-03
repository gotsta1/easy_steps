from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_compose(name):
    return yaml.safe_load((ROOT / name).read_text())


def test_production_and_development_have_separate_database_volumes():
    prod = load_compose("docker-compose.prod.yml")
    dev = load_compose("docker-compose.yml")
    prod_mount = prod["services"]["db"]["volumes"][0]
    dev_mount = dev["services"]["postgres"]["volumes"][0]

    assert prod_mount == "pgdata_prod:/var/lib/postgresql/data"
    assert dev_mount == "pgdata_dev:/var/lib/postgresql/data"
    assert prod["volumes"]["pgdata_prod"] == {
        "external": True,
        "name": "easy_steps_pgdata_prod",
    }


def test_database_is_not_publicly_exposed():
    prod = load_compose("docker-compose.prod.yml")
    dev = load_compose("docker-compose.yml")

    assert not prod["services"]["db"].get("ports")
    assert dev["services"]["postgres"]["ports"] == ["127.0.0.1:5432:5432"]


def test_new_production_database_checksums_and_log_rotation():
    db = load_compose("docker-compose.prod.yml")["services"]["db"]

    assert db["environment"]["POSTGRES_INITDB_ARGS"] == "--data-checksums"
    assert db["logging"] == {
        "driver": "json-file",
        "options": {"max-size": "10m", "max-file": "5", "compress": "true"},
    }
