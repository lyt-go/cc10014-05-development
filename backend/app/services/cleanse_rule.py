"""清洗策略业务规则：配置清洗规则、按执行顺序整组清洗并生成清洗规则执行报告。

链路约定：
- 规则顺序、DWD刷新标记与清洗规则执行报告在同一个事务里落库，任一步失败整体回滚；
- 执行报告按规则口径判定，记录每条规则的命中数与替换值；
- 同字段多版本冲突先判定优先规则，同时保留“按版本顺序”和“按命中范围”两种历史视图；
- 报告超时即按阈值重试，重试原因与超时阈值写入报告。
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any, Callable

from app.config import settings
from app.store import store

MODULE = "cleanse_rule"
LIST_FIELDS = ["策略编号", "目标字段", "空值处理", "异常阈值", "正则表达式", "替换值", "策略版本", "执行顺序"]
REQUIRED_FIELDS = ["策略编号", "目标字段"]
STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "停用规则": "已停用"}
NEGATIVE_ACTIONS = ["停用规则"]
# 已停用的策略不再参与整组清洗；草稿和待校验策略仍可试跑，避免DWD构建拿不到清洗口径
EXECUTABLE_EXCLUDED_STATUS = "已停用"

_NUMBER = r"-?\d+(?:\.\d+)?"
_COMPARE_PATTERN = re.compile(r"(<=|>=|<|>)\s*(" + _NUMBER + r")")
_ENUM_PATTERN = re.compile(r"\^?\(([^)]+)\)\$?")


class CleanseReportTimeout(RuntimeError):
    """整组清洗超过报告超时阈值；由执行入口按重试阈值重新拉起。"""


class CleanseRuleService:
    # ------------------------------------------------------------------
    # 基础查询与登记
    # ------------------------------------------------------------------

    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("策略编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        errors = self.validate_values(values)
        if errors:
            return None, errors
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        if values.get("目标数据集"):
            entry["目标数据集"] = values["目标数据集"]
        rows.append(entry)
        return entry, []

    def validate_values(self, values: dict[str, Any]) -> list[str]:
        """按规则口径校验阈值范围、条件与标准，违规值不进库。

        - 必填字段缺失；
        - 正则表达式必须可编译；
        - 异常阈值里的比较条件不能把替换值/填充值判成违规；
        - 枚举型标准（如 ^(普通|VIP|SVIP)$）要求替换值落在枚举内；
        - 执行顺序如填写数字则不允许为负数。
        """
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        errors = [f"缺少必填字段：{field}" for field in missing]
        if errors:
            return errors

        regex_str = str(values.get("正则表达式") or "").strip()
        pattern: re.Pattern[str] | None = None
        if regex_str:
            try:
                pattern = re.compile(regex_str)
            except re.error as exc:
                errors.append(f"正则表达式无法编译：{exc}")

        threshold = str(values.get("异常阈值") or "").strip()
        replacement = str(values.get("替换值") or "").strip()
        fill_value = self._extract_fill_value(values.get("空值处理", ""), replacement)
        invalid_checks = self._build_invalid_checks(threshold)

        for label, candidate in (("替换值", replacement), ("空值填充值", fill_value)):
            if not candidate:
                continue
            for description, is_invalid in invalid_checks:
                try:
                    if is_invalid(candidate):
                        errors.append(
                            f"{label}「{candidate}」落在异常阈值「{threshold}」的违规范围（{description}）")
                        break
                except (TypeError, ValueError):
                    continue

        if pattern is not None and "非枚举" in threshold and replacement and not pattern.search(replacement):
            errors.append(f"替换值「{replacement}」不符合枚举标准「{regex_str}」")

        order_text = str(values.get("执行顺序") or "").strip()
        if order_text:
            try:
                if int(order_text) < 0:
                    errors.append("执行顺序不允许为负数")
            except ValueError:
                pass
        return errors

    def _build_invalid_checks(self, threshold: str) -> list[tuple[str, Callable[[str], bool]]]:
        """把异常阈值表达式翻译成“该值是否违规”的判定函数。"""
        checks: list[tuple[str, Callable[[str], bool]]] = []
        for op, bound_text in _COMPARE_PATTERN.findall(threshold):
            bound = float(bound_text)

            def make_check(operator: str, limit: float) -> Callable[[str], bool]:
                def _check(value: str) -> bool:
                    number = float(value)
                    return {
                        "<": number < limit,
                        "<=": number <= limit,
                        ">": number > limit,
                        ">=": number >= limit,
                    }[operator]
                return _check

            checks.append((f"{op}{bound_text}", make_check(op, bound)))
        return checks

    @staticmethod
    def _extract_fill_value(null_handle: str, replacement: str) -> str:
        text = str(null_handle or "")
        if "填充为" in text:
            return text.split("填充为", 1)[1].strip()
        if "填充" in text:
            return text.split("填充", 1)[1].strip() or replacement
        if "保留" in text and "空" in text:
            return ""
        return replacement

    # ------------------------------------------------------------------
    # 状态流转与版本
    # ------------------------------------------------------------------

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"清洗策略 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于清洗策略可执行范围"
        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        # 版本化：变更时旧版本保留
        store.save_version(MODULE, entry_id, entry)
        # 当策略生效/变更时，标记关联DWD表需要刷新
        if action in ("标记生效", "提交校验"):
            self._mark_dwd_refresh(entry)
        return entry, f"清洗策略已{action}"

    def update_cleanse_version(self, entry_id: int, new_values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """更新清洗策略并升级版本号，旧版本保留在版本历史中。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"清洗策略 {entry_id} 不存在"
        merged = {**entry, **new_values}
        errors = self.validate_values(merged)
        if errors:
            return None, "；".join(errors)
        old_version = entry.get("策略版本", "v1")
        # 升级版本号
        try:
            vnum = int(old_version.replace("v", ""))
            entry["策略版本"] = f"v{vnum + 1}"
        except Exception:
            entry["策略版本"] = "v2"
        for k in ["目标字段", "空值处理", "异常阈值", "正则表达式", "替换值", "执行顺序"]:
            if k in new_values:
                entry[k] = new_values[k]
        entry["pending"] = True
        store.save_version(MODULE, entry_id, entry)
        self._mark_dwd_refresh(entry)
        return entry, f"清洗策略已升级至 {entry['策略版本']}"

    def _mark_dwd_refresh(self, entry: dict[str, Any]) -> None:
        """把策略变更同步成DWD刷新标记；刷新原因逐字段带上替换值。"""
        dataset = entry.get("目标数据集", "")
        strategy_no = entry.get("策略编号", "")
        target_field = entry.get("目标字段", "")
        version = entry.get("策略版本", "v1")
        order = entry.get("执行顺序", "")
        replacement = entry.get("替换值", "")
        reason = (
            f"{target_field}←{strategy_no} {version}（执行顺序{order}，替换值 {replacement}）")
        for row in self._dwd_rows_for_dataset(dataset, strategy_no):
            row["待刷新"] = True
            row["刷新原因"] = f"清洗策略 {strategy_no} 变更：{reason}"
            row["刷新字段"] = target_field
            store.save_version("dwd_layer", row["id"], row)

    @staticmethod
    def _dwd_rows_for_dataset(dataset: str, strategy_no: str = "") -> list[dict[str, Any]]:
        matched: list[dict[str, Any]] = []
        for row in store.rows("dwd_layer"):
            if strategy_no and row.get("清洗策略") == strategy_no:
                matched.append(row)
                continue
            parent = row.get("父层来源", "")
            ods = store.find_by_field("ods_layer", "表编号", parent) or store.find_by_field(
                "ods_layer", "表名称", parent)
            if ods and dataset and ods.get("表名称") == dataset:
                matched.append(row)
        return matched

    # ------------------------------------------------------------------
    # 单条规则清洗（保持既有读取兼容）
    # ------------------------------------------------------------------

    def apply_cleanse(self, entry_id: int, data_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """对输入数据行应用单条清洗规则，返回清洗后的行。"""
        entry = self.get_entry(entry_id)
        if not entry:
            return data_rows
        cleaned, _ = self._apply_rule(entry, data_rows)
        return cleaned

    def _apply_rule(
        self, entry: dict[str, Any], data_rows: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """执行一条规则并统计命中情况，供执行报告记录命中数与替换值。"""
        target_field = entry.get("目标字段", "")
        regex_str = entry.get("正则表达式", "")
        replace_val = entry.get("替换值", "")
        null_handle = entry.get("空值处理", "")
        pattern = re.compile(regex_str) if regex_str else None
        fill_value = self._extract_fill_value(null_handle, replace_val)
        apply_fill = "填充" in str(null_handle)
        null_hits = 0
        regex_hits = 0
        hit_rows: list[dict[str, Any]] = []
        result: list[dict[str, Any]] = []
        for index, row in enumerate(data_rows):
            new_row = dict(row)
            val = new_row.get(target_field)
            replaced = False
            if val is None or val == "":
                if apply_fill:
                    new_row[target_field] = fill_value
                    null_hits += 1
                    replaced = True
            elif pattern and not pattern.search(str(val)):
                new_row[target_field] = replace_val
                regex_hits += 1
                replaced = True
            if replaced:
                hit_rows.append({
                    "行号": index + 1,
                    "清洗前": val,
                    "清洗后": new_row.get(target_field),
                })
            result.append(new_row)
        detail = {
            "策略编号": entry.get("策略编号", ""),
            "策略版本": entry.get("策略版本", "v1"),
            "目标字段": target_field,
            "执行顺序": entry.get("执行顺序", ""),
            # 规则与阈值跟报告走，便于从取数链路直接定位清洗策略结果
            "异常阈值": entry.get("异常阈值", ""),
            "空值处理": entry.get("空值处理", ""),
            "正则表达式": entry.get("正则表达式", ""),
            "命中数": null_hits + regex_hits,
            "空值命中数": null_hits,
            "正则命中数": regex_hits,
            "替换值": replace_val,
            "空值填充值": fill_value if apply_fill else "",
            "命中明细": hit_rows[:20],
        }
        return result, detail

    def get_rules_for_dataset(self, dataset: str) -> list[dict[str, Any]]:
        return store.find_all_by_field(MODULE, "目标数据集", dataset)

    # ------------------------------------------------------------------
    # 整组清洗：按执行顺序执行并生成清洗规则执行报告
    # ------------------------------------------------------------------

    @staticmethod
    def _version_key(value: Any) -> tuple[int, str]:
        text = str(value or "v1")
        try:
            return int(text.replace("v", "")), text
        except ValueError:
            return 9999, text

    @classmethod
    def _order_key(cls, entry: dict[str, Any]) -> tuple[float, int, str, int]:
        try:
            order = float(entry.get("执行顺序") or 9999)
        except (TypeError, ValueError):
            order = 9999.0
        version_num, version_text = cls._version_key(entry.get("策略版本", "v1"))
        return order, version_num, version_text, int(entry.get("id", 0))

    def execution_rules(self, dataset: str, rule_codes: list[str] | None = None) -> list[dict[str, Any]]:
        """待执行规则：排除已停用，按执行顺序、版本、登记顺序排定。

        rule_codes 用于明细表显式关联、但没写目标数据集的清洗策略。
        """
        codes = {code for code in (rule_codes or []) if code}
        rows = [
            row for row in store.rows(MODULE)
            if row.get("status") != EXECUTABLE_EXCLUDED_STATUS
            and (
                (dataset and row.get("目标数据集") == dataset)
                or (codes and row.get("策略编号") in codes)
            )
        ]
        return sorted(rows, key=self._order_key)

    @classmethod
    def _detect_conflicts(cls, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """同字段多版本冲突判定：执行顺序靠前的优先，同顺序则版本高的优先。"""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for rule in rules:
            grouped.setdefault(str(rule.get("目标字段", "")), []).append(rule)
        conflicts: list[dict[str, Any]] = []
        for field, group in grouped.items():
            if not field or len(group) < 2:
                continue
            versions = {str(item.get("策略版本", "v1")) for item in group}
            if len(versions) < 2:
                # 同字段同版本不是版本冲突，但执行顺序仍会互相覆盖，判定优先级时一并记录
                pass
            priority = sorted(group, key=cls._order_key)[0]
            conflicts.append({
                "目标字段": field,
                "冲突规则": [
                    {
                        "策略编号": item.get("策略编号", ""),
                        "策略版本": item.get("策略版本", "v1"),
                        "执行顺序": item.get("执行顺序", ""),
                        "替换值": item.get("替换值", ""),
                    }
                    for item in sorted(group, key=cls._order_key)
                ],
                "优先策略": priority.get("策略编号", ""),
                "优先版本": priority.get("策略版本", "v1"),
                "判定依据": (
                    f"执行顺序最小（{priority.get('执行顺序', '')}）的清洗策略先处理；"
                    "顺序相同再按版本号从高到低判定优先级"),
                "历史片段": "版本顺序视图与命中范围视图分别保留冲突前的历史替换值",
            })
        return conflicts

    def _version_view(self, rule_details: list[dict[str, Any]], rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按版本顺序列出替换值：对应规则版本演进的历史片段。"""
        version_of = {
            str(rule.get("策略编号", "")): str(rule.get("策略版本", "v1")) for rule in rules
        }
        view: list[dict[str, Any]] = []
        for detail in sorted(
            rule_details,
            key=lambda item: (
                self._version_key(version_of.get(str(item["策略编号"]), "v1")),
                str(item["策略编号"]),
            ),
        ):
            view.append({
                "历史片段": f"版本 {detail['策略版本']}",
                "策略编号": detail["策略编号"],
                "策略版本": detail["策略版本"],
                "目标字段": detail["目标字段"],
                "执行顺序": detail["执行顺序"],
                "命中数": detail["命中数"],
                "替换值": detail["替换值"],
            })
        return view

    @staticmethod
    def _hit_range_view(rule_details: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按命中范围列出替换值：对应命中量区间划分的历史片段。"""
        bands = [
            ("未命中", 0, 0),
            ("少量命中", 1, 2),
            ("批量命中", 3, 10),
            ("全量命中", 11, None),
        ]
        view: list[dict[str, Any]] = []
        for label, lower, upper in bands:
            members = [
                detail for detail in rule_details
                if detail["命中数"] >= lower and (upper is None or detail["命中数"] <= upper)
            ]
            if not members:
                continue
            view.append({
                "历史片段": label,
                "命中范围": f"{lower}+" if upper is None else f"{lower}-{upper}",
                "规则数": len(members),
                "替换值": [
                    {
                        "策略编号": item["策略编号"],
                        "策略版本": item["策略版本"],
                        "目标字段": item["目标字段"],
                        "命中数": item["命中数"],
                        "替换值": item["替换值"],
                    }
                    for item in members
                ],
            })
        return view

    def execute_group(
        self,
        dataset: str,
        data_rows: list[dict[str, Any]] | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        dwd_entry_id: int | None = None,
        rule_codes: list[str] | None = None,
    ) -> dict[str, Any]:
        """按执行顺序整组清洗并落库执行报告。

        超时按阈值重试；规则顺序、DWD刷新标记与报告在同一事务内提交。
        """
        timeout = settings.cleanse_report_timeout_seconds if timeout_seconds is None else timeout_seconds
        retries = settings.cleanse_report_max_retries if max_retries is None else max_retries
        attempts = 0
        retry_reasons: list[str] = []
        while True:
            attempts += 1
            try:
                return self._execute_once(
                    dataset=dataset,
                    data_rows=data_rows,
                    timeout_seconds=timeout,
                    attempts=attempts,
                    retry_reasons=retry_reasons,
                    dwd_entry_id=dwd_entry_id,
                    rule_codes=rule_codes,
                )
            except CleanseReportTimeout as exc:
                retry_reasons.append(str(exc))
                if attempts > retries:
                    return {
                        "ok": False,
                        "message": (
                            f"清洗规则执行报告生成超时：已重试 {retries} 次仍未完成（阈值 {timeout} 秒）"),
                        "报告编号": "",
                        "目标数据集": dataset,
                        "重试次数": len(retry_reasons),
                        "重试原因": retry_reasons,
                        "超时阈值": timeout,
                    }
                continue

    def _execute_once(
        self,
        *,
        dataset: str,
        data_rows: list[dict[str, Any]] | None,
        timeout_seconds: float,
        attempts: int,
        retry_reasons: list[str],
        dwd_entry_id: int | None,
        rule_codes: list[str] | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
        rules = self.execution_rules(dataset, rule_codes)
        source_rows = [dict(row) for row in (data_rows if data_rows is not None else store.table_rows(dataset))]
        conflicts = self._detect_conflicts(rules)
        rule_details: list[dict[str, Any]] = []
        cleaned_rows = source_rows
        for rule in rules:
            # 超时判定放在每条规则边界，保证报告要么整份生成、要么整体重试
            if time.monotonic() > deadline:
                raise CleanseReportTimeout(
                    f"第 {attempts} 次执行在第 {len(rule_details) + 1} 条规则前超过 {timeout_seconds} 秒阈值")
            cleaned_rows, detail = self._apply_rule(rule, cleaned_rows)
            detail["冲突"] = any(
                conflict["目标字段"] == detail["目标字段"] for conflict in conflicts)
            rule_details.append(detail)
            if time.monotonic() > deadline:
                raise CleanseReportTimeout(
                    f"第 {attempts} 次执行在规则 {detail['策略编号']} 后超过 {timeout_seconds} 秒阈值")

        report_no = store.next_report_no()
        dwd_rows = self._target_dwd_rows(dataset, dwd_entry_id)
        refresh_notes = self._refresh_notes(rule_details, dwd_rows)
        report = {
            "报告编号": report_no,
            "目标数据集": dataset,
            "明细表编号": ", ".join(row.get("表编号", "") for row in dwd_rows),
            "执行状态": "已完成" if rules else "无可用规则",
            "生成时间": datetime.now().isoformat(),
            "规则总数": len(rules),
            "待清洗行数": len(source_rows),
            "清洗后行数": len(cleaned_rows),
            "超时阈值": timeout_seconds,
            "重试次数": len(retry_reasons),
            "重试原因": retry_reasons,
            "重试说明": (
                f"报告生成超过 {timeout_seconds} 秒阈值即重试，最多重试 "
                f"{settings.cleanse_report_max_retries} 次；重试原因逐次记录在报告中"),
            "规则明细": rule_details,
            "冲突判定": conflicts,
            "版本顺序替换视图": self._version_view(rule_details, rules),
            "命中范围替换视图": self._hit_range_view(rule_details),
            "DWD刷新": refresh_notes,
            # 与DWD刷新原因同文案：便于逐字段核对报告的替换值和明细层刷新口径
            "清洗口径摘要": refresh_notes[0]["刷新原因"] if refresh_notes else "",
            "清洗结果预览": cleaned_rows[:20],
        }
        # 规则顺序 + DWD刷新标记 + 报告：同一事务提交，避免报告与明细口径不一致
        with store.transaction():
            for note in refresh_notes:
                dwd_row = store.find("dwd_layer", int(note["id"])) if note.get("id") else None
                if dwd_row is not None:
                    dwd_row["待刷新"] = True
                    dwd_row["刷新原因"] = note["刷新原因"]
                    dwd_row["刷新字段"] = "、".join(note["字段替换值"].keys())
                    dwd_row["最近报告"] = report_no
                    store.save_version("dwd_layer", dwd_row["id"], dwd_row)
                store.add_dwd_refresh_log({**note, "报告编号": report_no})
            for rule in rules:
                store.save_version(MODULE, int(rule["id"]), rule)
            store.save_cleanse_report(report, cleaned_rows)
        return {
            "ok": True,
            "message": f"清洗规则执行报告 {report_no} 已生成，共执行 {len(rules)} 条规则",
            "报告": report,
            "报告编号": report_no,
            "清洗后数据": cleaned_rows,
        }

    def _target_dwd_rows(self, dataset: str, dwd_entry_id: int | None) -> list[dict[str, Any]]:
        if dwd_entry_id is not None:
            entry = store.find("dwd_layer", dwd_entry_id)
            return [entry] if entry else []
        return self._dwd_rows_for_dataset(dataset)

    def _refresh_notes(
        self, rule_details: list[dict[str, Any]], dwd_rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """按字段汇总替换值，让DWD刷新原因与报告里的替换值逐字段对应。"""
        field_map: dict[str, dict[str, Any]] = {}
        for detail in rule_details:
            field_map[detail["目标字段"]] = {
                "策略编号": detail["策略编号"],
                "策略版本": detail["策略版本"],
                "执行顺序": detail["执行顺序"],
                "替换值": detail["替换值"],
                "命中数": detail["命中数"],
            }
        reason_parts = [
            f"{field}←{item['策略编号']} {item['策略版本']}（执行顺序{item['执行顺序']}，替换值 {item['替换值']}，命中{item['命中数']}）"
            for field, item in field_map.items()
        ]
        reason = "清洗规则执行变更：" + "；".join(reason_parts) if reason_parts else "无清洗规则命中"
        notes: list[dict[str, Any]] = []
        for row in dwd_rows:
            notes.append({
                "id": row.get("id"),
                "表编号": row.get("表编号", ""),
                "待刷新": True,
                "刷新原因": reason,
                "字段替换值": field_map,
            })
        if not dwd_rows:
            notes.append({
                "id": None,
                "表编号": "",
                "待刷新": True,
                "刷新原因": reason,
                "字段替换值": field_map,
            })
        return notes

    # ------------------------------------------------------------------
    # 报告查询
    # ------------------------------------------------------------------

    def list_reports(
        self,
        *,
        dataset: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        reports = store.list_cleanse_reports()
        if dataset:
            reports = [item for item in reports if dataset.casefold() in str(item.get("目标数据集", "")).casefold()]
        if keyword:
            reports = [item for item in reports if keyword.casefold() in str(item.get("报告编号", "")).casefold()]
        total = len(reports)
        start = (page - 1) * size
        return reports[start:start + size], total

    def get_report(self, report_no: str) -> dict[str, Any] | None:
        return store.get_cleanse_report(report_no)

    def get_report_rows(self, report_no: str) -> list[dict[str, Any]]:
        return store.get_cleanse_report_rows(report_no)
