"""清洗策略接口：维护清洗策略，覆盖提交校验、标记生效、停用策略等动作。

同时提供整组清洗入口与清洗规则执行报告查询：规则顺序、DWD刷新标记与报告
在服务层同一事务里落库。
"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services.cleanse_rule import CleanseRuleService

router = APIRouter(prefix="/api/cleanse_rule", tags=["清洗策略"])

service = CleanseRuleService()

LIST_FIELDS = ["策略编号", "目标字段", "空值处理", "异常阈值", "正则表达式", "替换值", "策略版本", "执行顺序"]
STATUSES = ["草稿", "待校验", "已生效", "已停用"]
REPORT_LIST_FIELDS = ["报告编号", "目标数据集", "明细表编号", "执行状态", "生成时间",
                      "规则总数", "待清洗行数", "重试次数"]


def query_filters(request: Request) -> dict[str, str]:
    """只接受当前模块声明过的字段，忽略分页等保留参数。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in LIST_FIELDS and value.strip()
    }


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按策略编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已生效、已停用"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """按页面字段、策略编号关键字与状态过滤清洗策略列表。"""
    items, total = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    """下载当前筛选条件下的 UTF-8 CSV；必须声明在 /{entry_id} 之前。"""
    items, _ = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=1, size=1000000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *LIST_FIELDS, "status"])
    for item in items:
        row = []
        for value in [item.get("id"), *(item.get(field, "") for field in LIST_FIELDS), item.get("status", "")]:
            text = "" if value is None else str(value)
            row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
        writer.writerow(row)
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="cleanse_rule.csv"'})


@router.get("/reports", response_model=PageResult[dict])
def list_reports(
    dataset: str | None = Query(default=None, description="按目标数据集检索"),
    keyword: str | None = Query(default=None, description="按报告编号检索"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """清洗规则执行报告列表；必须声明在 /{entry_id} 之前。"""
    items, total = service.list_reports(dataset=dataset, keyword=keyword, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/reports/export")
def export_reports(
    dataset: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
) -> StreamingResponse:
    """导出报告清单 CSV；同样是静态路径，先于 /reports/{report_no} 声明。"""
    items, _ = service.list_reports(dataset=dataset, keyword=keyword, page=1, size=1000000)
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(REPORT_LIST_FIELDS)
    for item in items:
        row = []
        for field in REPORT_LIST_FIELDS:
            value = item.get(field, "")
            text = "" if value is None else str(value)
            row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
        writer.writerow(row)
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="cleanse_rule_report.csv"'})


@router.get("/reports/{report_no}", response_model=dict)
def get_report(report_no: str) -> dict:
    """读取清洗规则执行报告详情，含每条规则的命中数、替换值、冲突判定与两种历史视图。"""
    report = service.get_report(report_no)
    if report is None:
        raise HTTPException(status_code=404, detail=f"清洗规则执行报告 {report_no} 不存在")
    return report


@router.get("/reports/{report_no}/rows", response_model=dict)
def get_report_rows(report_no: str) -> dict:
    """读取该报告对应的清洗结果行，便于和明细层列表逐字段核对。"""
    report = service.get_report(report_no)
    if report is None:
        raise HTTPException(status_code=404, detail=f"清洗规则执行报告 {report_no} 不存在")
    return {"items": service.get_report_rows(report_no)}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条清洗策略明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"清洗策略 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: dict[str, object]) -> ActionResult:
    """登记一条清洗策略；阈值范围、条件或标准不合规时说明原因，不保存违规值。"""
    entry, errors = service.create_entry(payload)
    if errors:
        return ActionResult(ok=False, message="；".join(errors))
    return ActionResult(ok=True, message="清洗策略已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """对单条清洗策略执行提交校验、标记生效、停用策略；不允许的动作会被拦下并说明原因。"""
    entry, message = service.run_action(entry_id, payload.action.strip())
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/execute", response_model=ActionResult)
def execute_group(payload: dict[str, Any]) -> ActionResult:
    """按执行顺序整组清洗待清洗原始数据行，并生成清洗规则执行报告。

    payload：目标数据集、可选数据行、可选超时阈值/重试次数、可选明细表 id。
    规则顺序、DWD刷新标记与报告同事务落库；超时按阈值重试，超限后说明原因。
    """
    dataset = str(payload.get("目标数据集") or "").strip()
    if not dataset:
        return ActionResult(ok=False, message="执行整组清洗需要指定目标数据集")
    rows = payload.get("数据行")
    if rows is not None and not isinstance(rows, list):
        return ActionResult(ok=False, message="数据行必须是数组，每行一个字段键值对")
    timeout = payload.get("超时阈值")
    retries = payload.get("重试次数")
    try:
        timeout_value = float(timeout) if timeout is not None and str(timeout).strip() != "" else None
        retry_value = int(retries) if retries is not None and str(retries).strip() != "" else None
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="超时阈值必须是秒数，重试次数必须是整数")
    if timeout_value is not None and timeout_value <= 0:
        return ActionResult(ok=False, message="超时阈值必须大于 0 秒")
    if retry_value is not None and retry_value < 0:
        return ActionResult(ok=False, message="重试次数不允许为负数")
    dwd_entry_id = payload.get("明细表id")
    result = service.execute_group(
        dataset,
        data_rows=rows,
        timeout_seconds=timeout_value,
        max_retries=retry_value,
        dwd_entry_id=int(dwd_entry_id) if dwd_entry_id not in (None, "") else None,
    )
    if not result.get("ok"):
        return ActionResult(
            ok=False,
            message=result.get("message", "清洗规则执行报告生成失败"),
            entry={key: value for key, value in result.items() if key != "清洗后数据"},
        )
    return ActionResult(ok=True, message=result["message"], entry=result.get("报告"))


@router.post("/{entry_id}/execute", response_model=ActionResult)
def execute_for_entry(entry_id: int, payload: dict[str, Any] | None = None) -> ActionResult:
    """从单条清洗策略出发整组执行：按策略的目标数据集串起同数据集的全部规则。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        return ActionResult(ok=False, message=f"清洗策略 {entry_id} 不存在或已归档")
    if entry.get("status") == "已停用":
        return ActionResult(ok=False, message=f"清洗策略 {entry.get('策略编号', '')} 已停用，不参与整组清洗")
    payload = payload or {}
    rows = payload.get("数据行")
    if rows is not None and not isinstance(rows, list):
        return ActionResult(ok=False, message="数据行必须是数组，每行一个字段键值对")
    result = service.execute_group(
        str(entry.get("目标数据集") or ""),
        data_rows=rows,
        dwd_entry_id=int(payload["明细表id"]) if payload.get("明细表id") not in (None, "") else None,
        rule_codes=[str(entry.get("策略编号", ""))],
    )
    if not result.get("ok"):
        return ActionResult(ok=False, message=result.get("message", "清洗规则执行报告生成失败"))
    return ActionResult(ok=True, message=result["message"], entry=result.get("报告"))
