"""测试：清洗策略整组执行、清洗规则执行报告与事务一致性。

验证：
- 按执行顺序整组清洗，报告保留每条规则的命中数与替换值
- 同字段版本冲突判定优先策略，并保留版本顺序/命中范围两种历史视图
- 规则顺序、DWD刷新标记与报告在同一事务落库，失败整体回滚
- 报告超时按阈值重试并说明原因
- 阈值范围、条件、标准不合规时拒绝保存违规值
- 修改策略顺序后，报告替换值与DWD刷新原因逐字段对应
"""
from __future__ import annotations

import pytest

from app.seed import SEED_ROWS
from app.store import store
from app.services.cleanse_rule import CleanseRuleService
from app.services.dwd_layer import DwdLayerService


def reset_store():
    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    store._table_data.clear()
    store._versions.clear()
    store._lineage_edges.clear()
    store._quality_results.clear()
    store._snapshot_data.clear()
    store._archive_records.clear()
    store._backfill_partitions.clear()
    store._cleanse_reports.clear()
    store._cleanse_rows.clear()
    store._dwd_refresh_log.clear()
    store._report_seq = 0


class TestCleanseGroupReport:
    def setup_method(self):
        reset_store()

    def test_report_keeps_hit_count_and_replacement_per_rule(self):
        """报告要逐条规则保留命中数与替换值。"""
        cs = CleanseRuleService()
        cs.create_entry({
            "策略编号": "CLN-ORD-002", "目标字段": "order_status",
            "空值处理": "填充待支付", "异常阈值": "非枚举值",
            "正则表达式": r"^(已完成|待支付)$", "替换值": "待支付",
            "策略版本": "v1", "执行顺序": "2", "目标数据集": "ods_orders",
        })
        result = cs.execute_group("ods_orders", data_rows=[
            {"order_id": "O1", "order_amount": "100.50", "order_status": "已完成"},
            {"order_id": "O2", "order_amount": "-10.00", "order_status": ""},
            {"order_id": "O3", "order_amount": "", "order_status": "已取消"},
        ])
        assert result["ok"] is True
        report = result["报告"]
        details = {item["策略编号"]: item for item in report["规则明细"]}
        assert details["CLN-ORD-001"]["命中数"] == 2
        assert details["CLN-ORD-001"]["替换值"] == "0.00"
        assert details["CLN-ORD-002"]["命中数"] == 2
        assert details["CLN-ORD-002"]["替换值"] == "待支付"
        # 执行顺序：order_amount 的规则在前
        assert [item["策略编号"] for item in report["规则明细"]] == ["CLN-ORD-001", "CLN-ORD-002"]
        # 报告落库可按编号复读
        stored = cs.get_report(report["报告编号"])
        assert stored is not None
        assert [item["命中数"] for item in stored["规则明细"]] == [2, 2]

    def test_conflict_priority_and_two_history_views(self):
        """同字段不同版本冲突先判定优先策略，两种历史视图分别成立。"""
        cs = CleanseRuleService()
        cs.create_entry({
            "策略编号": "CLN-ORD-003", "目标字段": "order_amount",
            "空值处理": "填充9.99", "异常阈值": "<0或>999999",
            "正则表达式": r"^\d+\.\d{2}$", "替换值": "9.99",
            "策略版本": "v2", "执行顺序": "3", "目标数据集": "ods_orders",
        })
        result = cs.execute_group("ods_orders", data_rows=[
            {"order_id": "O1", "order_amount": "100.50"},
            {"order_id": "O2", "order_amount": "-1.00"},
            {"order_id": "O3", "order_amount": ""},
        ])
        report = result["报告"]
        assert len(report["冲突判定"]) == 1
        conflict = report["冲突判定"][0]
        assert conflict["目标字段"] == "order_amount"
        assert conflict["优先策略"] == "CLN-ORD-001"
        assert conflict["优先版本"] == "v1"
        # 版本顺序视图：按版本列出替换值
        version_view = report["版本顺序替换视图"]
        assert [item["策略版本"] for item in version_view] == ["v1", "v2"]
        assert [item["替换值"] for item in version_view] == ["0.00", "9.99"]
        # 命中范围视图：按命中区间列出替换值
        hit_view = {item["历史片段"]: item for item in report["命中范围替换视图"]}
        assert hit_view["少量命中"]["替换值"][0]["策略编号"] == "CLN-ORD-001"
        assert hit_view["少量命中"]["替换值"][0]["替换值"] == "0.00"
        assert hit_view["未命中"]["替换值"][0]["策略编号"] == "CLN-ORD-003"
        # 既有单条清洗读取仍然可用
        legacy = CleanseRuleService().apply_cleanse(1, [{"order_id": "O1", "order_amount": "-1.00"}])
        assert legacy[0]["order_amount"] == "0.00"

    def test_rules_order_refresh_and_report_commit_together(self):
        """规则顺序、DWD刷新标记与报告同事务落库。"""
        cs = CleanseRuleService()
        cs.create_entry({
            "策略编号": "CLN-ORD-004", "目标字段": "order_amount",
            "空值处理": "填充0.00", "异常阈值": "<0或>999999",
            "正则表达式": r"^\d+\.\d{2}$", "替换值": "1.00",
            "策略版本": "v3", "执行顺序": "5", "目标数据集": "ods_orders",
        })
        result = cs.execute_group("ods_orders", data_rows=[{"order_id": "O1", "order_amount": "-1.00"}])
        assert result["ok"] is True
        report_no = result["报告编号"]
        assert store.get_cleanse_report(report_no) is not None
        dwd = store.find("dwd_layer", 1)
        assert dwd["待刷新"] is True
        assert dwd["最近报告"] == report_no
        assert store.get_dwd_refresh_log(report_no)[0]["表编号"] == "DWD_ORD_001"
        assert store.get_versions("cleanse_rule", 4), "规则顺序应随事务保存版本"

    def test_transaction_rolls_back_report_and_refresh(self):
        """报告写库失败时，规则顺序与DWD刷新标记一起回滚，不留旧口径。"""
        cs = CleanseRuleService()
        dwd_before = dict(store.find("dwd_layer", 1))
        versions_before = len(store.get_versions("cleanse_rule", 1))

        original_save = store.save_cleanse_report

        def boom(report, rows):
            raise RuntimeError("报告落库失败")

        store.save_cleanse_report = boom  # type: ignore[method-assign]
        try:
            with pytest.raises(RuntimeError):
                cs.execute_group("ods_orders", data_rows=[{"order_id": "O1", "order_amount": "-1"}])
        finally:
            store.save_cleanse_report = original_save  # type: ignore[method-assign]

        dwd_after = store.find("dwd_layer", 1)
        assert dwd_after.get("待刷新") == dwd_before.get("待刷新")
        assert dwd_after.get("刷新原因") == dwd_before.get("刷新原因")
        assert len(store.get_versions("cleanse_rule", 1)) == versions_before
        assert store.get_dwd_refresh_log() == []
        assert cs.list_reports()[1] == 0

    def test_report_retries_on_timeout_and_explains_reason(self):
        """报告超时按阈值重试，失败时说明原因与阈值。"""
        cs = CleanseRuleService()
        result = cs.execute_group(
            "ods_orders",
            data_rows=[{"order_id": "O1", "order_amount": "-1.00"}],
            timeout_seconds=0.0,
            max_retries=2,
        )
        assert result["ok"] is False
        assert "超时" in result["message"]
        assert result["超时阈值"] == 0.0
        assert result["重试次数"] == 3
        assert len(result["重试原因"]) == 3
        assert all("阈值" in reason for reason in result["重试原因"])
        assert cs.list_reports()[1] == 0, "超时不应写入半份报告"

    def test_retry_records_reason_when_first_attempt_times_out(self):
        """首次执行超时、重试成功时，报告要保留重试次数与原因。"""
        cs = CleanseRuleService()
        calls = {"count": 0}
        original = cs._apply_rule

        def slow_once(entry, rows):
            calls["count"] += 1
            if calls["count"] == 1:
                import time
                time.sleep(0.02)
            return original(entry, rows)

        cs._apply_rule = slow_once  # type: ignore[method-assign]
        result = cs.execute_group(
            "ods_orders",
            data_rows=[{"order_id": "O1", "order_amount": "-1.00"}],
            timeout_seconds=0.005,
            max_retries=1,
        )
        assert result["ok"] is True
        assert result["报告"]["重试次数"] == 1
        assert "超过" in result["报告"]["重试原因"][0]

    def test_reject_invalid_threshold_values(self):
        """阈值范围、条件和标准不允许保存违规值。"""
        cs = CleanseRuleService()
        entry, errors = cs.create_entry({
            "策略编号": "CLN-BAD-001", "目标字段": "order_amount",
            "空值处理": "填充-1", "异常阈值": "<0或>999999",
            "正则表达式": r"^\d+\.\d{2}$", "替换值": "-1",
            "策略版本": "v1", "执行顺序": "1", "目标数据集": "ods_orders",
        })
        assert entry is None
        assert any("替换值" in message for message in errors)
        assert any("空值填充值" in message for message in errors)
        assert not any(row.get("策略编号") == "CLN-BAD-001" for row in store.rows("cleanse_rule"))

    def test_reject_uncompilable_regex_and_enum_mismatch(self):
        cs = CleanseRuleService()
        entry, errors = cs.create_entry({
            "策略编号": "CLN-BAD-002", "目标字段": "user_level",
            "空值处理": "填充普通", "异常阈值": "非枚举值",
            "正则表达式": r"^(普通|VIP$", "替换值": "普通",
            "策略版本": "v1", "执行顺序": "1", "目标数据集": "ods_users",
        })
        assert entry is None
        assert any("正则表达式" in message for message in errors)

        entry, errors = cs.create_entry({
            "策略编号": "CLN-BAD-003", "目标字段": "user_level",
            "空值处理": "填充游客", "异常阈值": "非枚举值",
            "正则表达式": r"^(普通|VIP|SVIP)$", "替换值": "游客",
            "策略版本": "v1", "执行顺序": "1", "目标数据集": "ods_users",
        })
        assert entry is None
        assert any("不符合枚举标准" in message for message in errors)

    def test_update_order_keeps_report_values_aligned_with_refresh_reason(self):
        """修改策略顺序后，报告替换值与DWD刷新原因逐字段对应。"""
        cs = CleanseRuleService()
        cs.create_entry({
            "策略编号": "CLN-ORD-005", "目标字段": "order_status",
            "空值处理": "填充待支付", "异常阈值": "非枚举值",
            "正则表达式": r"^(已完成|待支付)$", "替换值": "待支付",
            "策略版本": "v1", "执行顺序": "2", "目标数据集": "ods_orders",
        })
        updated, message = cs.update_cleanse_version(1, {"执行顺序": "0"})
        assert updated is not None, message
        assert updated["执行顺序"] == "0"

        result = cs.execute_group("ods_orders", data_rows=[
            {"order_id": "O1", "order_amount": "-1.00", "order_status": "已取消"},
        ])
        report = result["报告"]
        assert [item["策略编号"] for item in report["规则明细"]] == ["CLN-ORD-001", "CLN-ORD-005"]
        dwd = store.find("dwd_layer", 1)
        assert dwd["刷新原因"] == report["清洗口径摘要"]
        for field, item in report["DWD刷新"][0]["字段替换值"].items():
            assert f"{field}←{item['策略编号']}" in dwd["刷新原因"]
            detail = next(d for d in report["规则明细"] if d["策略编号"] == item["策略编号"])
            assert detail["替换值"] == item["替换值"]
            assert str(detail["执行顺序"]) == str(item["执行顺序"])

    def test_dwd_build_uses_whole_chain_and_records_report(self):
        """明细层构建按执行顺序整组清洗，构建后清除待刷新并保留报告编号。"""
        store.set_table_rows("ods_orders", [
            {"order_id": "O001", "order_amount": "100.50", "order_status": "已完成"},
            {"order_id": "O002", "order_amount": "-10.00", "order_status": "已完成"},
            {"order_id": "O003", "order_amount": "", "order_status": "已完成"},
        ])
        dwd = DwdLayerService()
        result = dwd.build_dwd_from_ods(1)
        assert result["ok"] is True, result.get("message")
        assert result["报告编号"]
        rows = store.table_rows("dwd_order_detail")
        by_id = {row["order_id"]: row["order_amount"] for row in rows}
        assert by_id["O001"] == "100.50"
        assert by_id["O002"] == "0.00"
        assert by_id["O003"] == "0.00"
        entry = store.find("dwd_layer", 1)
        assert entry["待刷新"] is False
        assert entry["最近报告"] == result["报告编号"]
        assert store.get_cleanse_report(result["报告编号"])["规则明细"][0]["命中数"] == 2
