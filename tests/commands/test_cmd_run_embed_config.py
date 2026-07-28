import json
import os

import FreeCAD
import pytest

from commands.cmd_run import _embed_run_config
from features.simulation import create_simulation
from palace.embedded_files import resolve


@pytest.fixture
def doc():
    d = FreeCAD.newDocument("TestCmdRunEmbedConfig")
    yield d
    FreeCAD.closeDocument(d.Name)


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def test_single_pass_embeds_config_directly(doc, tmp_path):
    sim = create_simulation(doc)
    config_path = str(tmp_path / "palace_config.json")
    _write_json(config_path, {"Problem": {"Type": "Driven"}})

    _embed_run_config(doc, sim, [(config_path, str(tmp_path), None)])

    resolved = resolve(sim, "ConfigFile")
    assert resolved
    with open(resolved, encoding="utf-8") as fh:
        assert json.load(fh) == {"Problem": {"Type": "Driven"}}


def test_multi_pass_combines_configs_keyed_by_port(doc, tmp_path):
    sim = create_simulation(doc)
    cfg1 = str(tmp_path / "palace_config_port1.json")
    cfg2 = str(tmp_path / "palace_config_port2.json")
    _write_json(cfg1, {"Excitation": 1})
    _write_json(cfg2, {"Excitation": 2})

    _embed_run_config(doc, sim, [
        (cfg1, str(tmp_path / "out1"), 1),
        (cfg2, str(tmp_path / "out2"), 2),
    ])

    resolved = resolve(sim, "ConfigFile")
    with open(resolved, encoding="utf-8") as fh:
        combined = json.load(fh)
    assert combined == {"port_1": {"Excitation": 1}, "port_2": {"Excitation": 2}}


def test_missing_config_file_leaves_property_empty(doc, tmp_path):
    sim = create_simulation(doc)
    missing_path = str(tmp_path / "nonexistent.json")

    _embed_run_config(doc, sim, [(missing_path, str(tmp_path), None)])

    assert resolve(sim, "ConfigFile") == ""


def test_sim_none_does_not_raise(doc, tmp_path):
    config_path = str(tmp_path / "palace_config.json")
    _write_json(config_path, {"Problem": {"Type": "Driven"}})
    _embed_run_config(doc, None, [(config_path, str(tmp_path), None)])  # must not raise


def test_reembedding_overwrites_previous_config(doc, tmp_path):
    sim = create_simulation(doc)
    cfg1 = str(tmp_path / "palace_config.json")
    _write_json(cfg1, {"run": 1})
    _embed_run_config(doc, sim, [(cfg1, str(tmp_path), None)])

    cfg2 = str(tmp_path / "second" / "palace_config.json")
    os.makedirs(os.path.dirname(cfg2))
    _write_json(cfg2, {"run": 2})
    _embed_run_config(doc, sim, [(cfg2, str(tmp_path / "second"), None)])

    resolved = resolve(sim, "ConfigFile")
    with open(resolved, encoding="utf-8") as fh:
        assert json.load(fh) == {"run": 2}
