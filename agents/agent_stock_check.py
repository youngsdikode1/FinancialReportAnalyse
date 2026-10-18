import json
from typing import List, TypedDict, Optional, Any, Literal
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph
from utils.llm_request import CLLMRequest
from utils.logs import PrintLog,LogInfo
from datetime import datetime


class CDemandExtraResult(BaseModel):
    target_stocks: List[str] = Field(default_factory=list, description="提取到的标的名称/代码，必须提取，不能返回空数组")
    year_range: Optional[str] = Field(default=None, description="提取的时间范围，找不到填空字符串")


#分析意图解析agent
class CAgentDemandExtra:

    def __init__(self):
        self.m_graph = self.BuildGraph()

    def BuildGraph(self):
        class CDemandExtraState(TypedDict):
            demand_str: str
            extra_result: Optional[CDemandExtraResult]

        graph_builder = StateGraph(CDemandExtraState)
        graph_builder.add_node("ExtraNode", self._ExtraNode)
        graph_builder.set_entry_point("ExtraNode")
        return graph_builder.compile()

    def _ExtraNode(self, state):
        demand_str = state["demand_str"]


        prompt = f"""
你是股票分析诉求agent，需要在以下分析诉求中提取出两个信息
1.year_range 股票分析涉及的时间范围
2.target_stocks 分析的公司股票名
分析诉求：{demand_str}
- 【重要】year_range 必须输出标准化区间格式：`xxxx.xx月‑xxxx.xx月`，禁止输出类似“2022年和2023年”这种口语形式，识别到多年就展开成起止年月区间；找不到填空字符串""。
- 必须提取：target_stocks（公司名称列表，至少提取 1 个，不允许返回空数组）、year_range（时间，找不到填空字符串""）。
"""
        llmRequest = CLLMRequest()
        extra_obj: CDemandExtraResult = llmRequest.InvokeStructured(prompt, CDemandExtraResult)
        data_json = extra_obj.model_dump_json()
        LogInfo(f"CAgentDemandExtra._StockCheckNode data_json:{data_json}")
        return {**state, "extra_result": extra_obj}

    def Run(self, demand_str: str) -> CDemandExtraResult:

        init_state = {
            "demand_str": demand_str,
        }
        LogInfo(f"CAgentDemandExtra.Run demand:{demand_str}")
        final_state = self.m_graph.invoke(init_state)

        return final_state["extra_result"]


class CStockCheckResult(BaseModel):
    is_valid: bool
    msg: str
    valid_names: List[str] = Field(default_factory=list)
    # 新增：完整公司信息列表，每个元素 {"name","stock_code","fullname","desc"}
    companys: List[dict] = Field(default_factory=list)


class CStockCheckState(TypedDict):
    require_ment: str
    target_names: List[str]
    check_result: Optional[CStockCheckResult]

class CAgentStockCheck:
    """
    校验Agent
    职责：校验一批公司名称是否A股/港股上市主体，输出标准化全称 + 模拟公司信息(简称、代码、全称、简介)
    无状态，Run方法全部依靠入参输入
    """
    def __init__(self):
        self.m_graph = self.BuildGraph()

    def BuildGraph(self):


        graph_builder = StateGraph(CStockCheckState)
        graph_builder.add_node("StockCheckNode", self._StockCheckNode)
        graph_builder.set_entry_point("StockCheckNode")
        return graph_builder.compile()

    def _StockCheckNode(self, state):
        require_ment = state["require_ment"]
        target_names = state["target_names"]

        now_dt = datetime.now()        
        now_str = now_dt.strftime("%Y-%m-%d %A")

        prompt = f"""
你是上市标的校验器。
当前系统时间：{now_str}
分析需求：{require_ment}
待校验标的名称列表：{target_names}

判断这些企业名称是否为 A 股或港股上市主体。
输出JSON结构要求：
{{
  "is_valid": true/false,
  "msg": "校验说明或校验失败原因",
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
- 股票代码只输出6位纯数字字符串，禁止输出 .SH / .SZ / .SS / .BJ 这类交易所后缀；
- 例如：正确："600519"；错误："600519.SH"、"600519.SS"；
- 如果用户输入带交易所后缀，自动剥离后缀，只保留前面6位数字。
4.股票只能是国内A股的公司，不支持分析A股以外公司
5.分析的股票财报时间如果比股票上市时间还早，则视为失败
"""
        llmRequest = CLLMRequest()
        check: CStockCheckResult = llmRequest.InvokeStructured(prompt, CStockCheckResult)
        check_json = check.model_dump_json()
        LogInfo(f"CAgentStockCheck._StockCheckNode check_json:{check_json}")
        return {**state, "check_result": check}

    def Run(self, target_names: List[str], require_ment: str) -> CStockCheckResult:
        """
        :param target_names: 需要校验的原始标的名称列表
        :param require_ment: 用户原始输入文本
        :return: CStockCheckResult
            is_valid: 是否全部合法上市主体
            valid_names: 全称字符串列表
            companys: 完整公司信息列表 [{"name","stock_code","fullname","desc"}]
        """
        init_state = {
            "require_ment": require_ment,
            "target_names": target_names,
            "check_result": None
        }
        LogInfo(f"CAgentStockCheck.Run require_ment:{require_ment} target_names:{target_names}")
        final_state = self.m_graph.invoke(init_state)
        return final_state["check_result"]