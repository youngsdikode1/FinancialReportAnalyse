import json
from typing import List, TypedDict, Optional, Any, Literal
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph
from utils.llm_request import CLLMRequest
from utils.logs import PrintLog,LogInfo


class CStockCheckResult(BaseModel):
    is_valid: bool
    msg: str
    valid_names: List[str] = Field(default_factory=list)
    # 新增：完整公司信息列表，每个元素 {"name","stock_code","fullname","desc"}
    companys: List[dict] = Field(default_factory=list)


class CAgentStockCheck:
    """
    独立标的校验Agent
    职责：校验一批公司名称是否A股/港股上市主体，输出标准化全称 + 模拟公司信息(简称、代码、全称、简介)
    无状态，Run方法全部依靠入参输入
    """
    def __init__(self):
        self.m_graph = self.BuildGraph()

    def BuildGraph(self):
        class CStockCheckState(TypedDict):
            user_query: str
            target_names: List[str]
            check_result: Optional[CStockCheckResult]

        graph_builder = StateGraph(CStockCheckState)
        graph_builder.add_node("StockCheckNode", self._StockCheckNode)
        graph_builder.set_entry_point("StockCheckNode")
        return graph_builder.compile()

    def _StockCheckNode(self, state):
        user_query = state["user_query"]
        target_names = state["target_names"]

        prompt = f"""
你是上市标的校验器。
用户输入：{user_query}
待校验标的名称列表：{target_names}

判断这些企业名称是否为 A 股或港股上市主体。
输出JSON结构要求：
{{
  "is_valid": true/false,
  "msg": "校验说明",
  "valid_names": ["标准化的上市公司全称列表"],
  "companys": [
      {{
          "name":"公司简称",
          "stock_code":"股票代码",
          "fullname":"上市公司完整全称",
          "desc":"简短公司业务简介"
      }}
  ]
}}

规则：
1. 如果标的不是合法上市公司，companys返回空数组。
2. is_valid为true代表全部输入标的校验通过。
3. 只返回JSON，不要额外解释文字、不要markdown代码块。
"""
        llmRequest = CLLMRequest()
        check: CStockCheckResult = llmRequest.InvokeStructured(prompt, CStockCheckResult)
        check_json = check.model_dump_json()
        LogInfo(f"CAgentStockCheck._StockCheckNode check_json:{check_json}")
        return {**state, "check_result": check}

    def Run(self, target_names: List[str], user_query: str) -> CStockCheckResult:
        """
        :param target_names: 需要校验的原始标的名称列表
        :param user_query: 用户原始输入文本
        :return: CStockCheckResult
            is_valid: 是否全部合法上市主体
            valid_names: 全称字符串列表
            companys: 完整公司信息列表 [{"name","stock_code","fullname","desc"}]
        """
        init_state = {
            "user_query": user_query,
            "target_names": target_names,
            "check_result": None
        }
        LogInfo(f"CAgentStockCheck.Run target_names:{target_names}")
        final_state = self.m_graph.invoke(init_state)
        LogInfo(f"CAgentStockCheck.Run user_query:{user_query}")
        return final_state["check_result"]