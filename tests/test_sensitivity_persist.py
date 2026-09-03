"""tests for mathmodelingagents.agents._persist_sensitivity_decision."""

import json

from mathmodelingagents.agents import _persist_sensitivity_decision


class TestPersistSensitivityDecision:
    def test_writes_json_to_output_dir(self, tmp_path):
        config = {"output_dir": str(tmp_path)}
        _persist_sensitivity_decision(config, True, "含优化参数，需扰动检验")
        path = tmp_path / "sensitivity_decision.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["enabled"] is True
        assert data["reason"] == "含优化参数，需扰动检验"
        assert data["mode"] == "auto"
        assert "generated_at" in data

    def test_overwrites_existing_json(self, tmp_path):
        config = {"output_dir": str(tmp_path)}
        _persist_sensitivity_decision(config, True, "第一次")
        _persist_sensitivity_decision(config, False, "第二次覆盖")
        data = json.loads((tmp_path / "sensitivity_decision.json").read_text(encoding="utf-8"))
        assert data["enabled"] is False
        assert data["reason"] == "第二次覆盖"

    def test_missing_output_dir_warns_without_crash(self, caplog):
        _persist_sensitivity_decision({}, True, "理由")  # 不应 raise
        _persist_sensitivity_decision({"output_dir": ""}, False, "理由")  # 不应 raise

    def test_write_failure_warns_without_crash(self, tmp_path):
        # output_dir 指向一个文件路径 → open 必然失败
        blocker = tmp_path / "not_a_dir"
        blocker.write_text("x", encoding="utf-8")
        _persist_sensitivity_decision({"output_dir": str(blocker)}, True, "理由")  # 不应 raise
