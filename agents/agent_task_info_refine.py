import json
from typing import List, TypedDict, Optional, Any, Literal
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph
import agents.agent_single_stock
from task_analyse import CTaskAnalysis
from utils.llm_request import CLLMRequest
from utils.logs import PrintLog,LogInfo
from datetime import datetime


class CTaskInfoDetail(BaseModel):
    target_companies: List[str]
    year_range: Optional[str] = None
    analysis_requirement: Optional[str] = None
    task_type: Optional[Literal["single_stock", "multi_stock", "sector"]] = None
    missing_fields: List[str] = Field(default_factory=list)
    ready_to_run: bool = False


class CAgentTaskRefineState(TypedDict):
    system_prompt: str
    current_task: "CTaskAnalysis"
    user_query: str
    stock_check_result: Optional[str]
    success: bool
    fail_reason: Optional[str]
    missing_fields: List[str]
    ready_to_run: bool

class CTaskRefineResult(BaseModel):
    success: bool
    fail_reason: Optional[str] = None
    missing_fields: List[str] = Field(default_factory=list)
    ready_to_run: bool = False


class CAgentTaskInfoRefine:
    def __init__(self):
        self.m_graph = self.BuildGraph()

    def BuildGraph(self):
        graph_builder = StateGraph(CAgentTaskRefineState)
        graph_builder.add_node("RefineTaskInfoDetail", self.RefineTaskInfoDetail)
        graph_builder.set_entry_point("RefineTaskInfoDetail")
        return graph_builder.compile()


    def RefineTaskInfoDetail(self, state: CAgentTaskRefineState) -> CAgentTaskRefineState:
        task: CTaskAnalysis = state["current_task"]
        user_query = state["user_query"]

        # 真实系统当前时间
        reference_now = datetime.now().strftime("%Y年%m月")

        prompt = f"""
你是任务信息提取助手。


用户输入：{user_query}

当前任务信息：
标的：{task.GetCompanyNameCodesStr()}
年份：{task.m_year_range}
诉求：{task.m_analysis_requirement}
任务类型：{task.m_task_type}

时间范围处理强制规则：
1. 如果用户**完全没有给出日期/年份**：自动生成【从当前时间往前推12个月到当前参考月份】的滚动12个月区间，格式示例："2025.09月‑2026.09月"，用于覆盖季度滚动财报；
2. 如果用户只给了单独年份（例如“2022”、“2023年”），必须把它展开成该自然完整年度区间格式："2022.01月‑2022.12月"；
3. year_range字段**永远输出「xxxx.xx月‑xxxx.xx月」区间格式，禁止只输出单独一个年份数字**。
4.【参考当前时间】：{reference_now}


请补全任务信息并输出严格JSON：
{{
    "target_companies": ["公司全称"],
    "year_range": "时间范围",
    "analysis_requirement": "用户分析诉求的标准化描述",
    "task_type": "single_stock / multi_stock / sector",
    "missing_fields": ["缺失字段名，可选year_range、analysis_requirement"],
    "ready_to_run": true/false
}}
只返回JSON，不要任何额外说明。
"""
        LogInfo(f"CAgentTaskInfoRefine.RefineTaskInfoDetail user_query:{user_query}")
        llmRequest = CLLMRequest()
        detail = llmRequest.InvokeStructured(prompt, CTaskInfoDetail)
        detail_json = detail.model_dump_json(indent=2)
        LogInfo(f"CAgentTaskInfoRefine.RefineTaskInfoDetail detail_json:{detail_json}")

        task.m_target_companies = detail.target_companies or task.m_target_companies
        task.m_year_range = detail.year_range or task.m_year_range
        task.m_analysis_requirement = detail.analysis_requirement or task.m_analysis_requirement
        task.m_task_type = detail.task_type or task.m_task_type

        state["missing_fields"] = detail.missing_fields
        state["ready_to_run"] = detail.ready_to_run and len(detail.missing_fields) == 0
        return state


    def Run(self, task: CTaskAnalysis, user_query: str) -> CTaskRefineResult:
        init_state: CAgentTaskRefineState = {
            "system_prompt": "",
            "current_task": task,
            "user_query": user_query,
            "stock_check_result": None,
            "success": True,
            "fail_reason": None,
            "missing_fields": [],
            "ready_to_run": False
        }

        final_state = self.m_graph.invoke(init_state)

        return CTaskRefineResult(
            success=final_state["success"],
            fail_reason=final_state.get("fail_reason"),
            missing_fields=final_state.get("missing_fields", []),
            ready_to_run=final_state.get("ready_to_run", False)
        )


