from typing import List, TypedDict, Optional, Any, Literal
from pydantic import BaseModel, Field
import utils.llm_request
from utils.llm_request import CLLMRequest
import json
from utils.logs import PrintLog
from langgraph.graph import StateGraph

# ====================== agents/agent_intent.py ======================
"""
意图识别 Agent 模块（独立编排层）

职责:
- 封装 LangGraph 子图，向上层提供 Run()接口
- 不直接依赖 LLM 基础设施，通过外部传入 LLM 请求对象

设计原则:
- 高内聚：State、Schema、业务逻辑封装在同一文件
- 低耦合：LLM 调用由上层传入，避免硬编码依赖
"""

from typing import List, TypedDict, Optional, Any
from pydantic import BaseModel, Field
from utils.logs import PrintLog, LogInfo


INTENT_TASK_CREATE = "intent_task_create"
INTENT_TASK_INFO_REFINE = "intent_task_info_refine"
INTENT_TASK_FOLLOWUP = "intent_task_followup"
INTENT_TASK_START_CONFIRM = "intent_task_start_confirm"
INTENT_FINANCE_QA = "intent_finance_qa"
INTENT_UNKNOWN = "intent_unknown"




class CAgentIntentState(TypedDict):
    """意图识别子图的状态定义"""
    system_prompt: str
    context_text: str
    current_task_status: Optional[str]
    user_query: str
    intent_result: Optional["CIntentResult"]



class CIntentResult(BaseModel):
    """意图识别结构化输出"""
    intent_type: str = Field(description="意图类型，只能使用预定义英文标签")
    reason: str = Field(description="一句话说明判定理由，仅基于本次用户输入")
    target_stocks: List[str] = Field(default_factory=list, description="提取到的标的名称/代码，必须提取，不能返回空数组")
    year_range: Optional[str] = Field(default=None, description="提取的时间范围，找不到填空字符串")
    raw_analysis_demand: Optional[str] = Field(default=None, description="原始分析诉求，找不到填空字符串")


class CAgentIntent:
    """意图识别 Agent（仅做图编排，LLM 调用由上层传入）"""

    def __init__(self, session_id):
        self.m_graph = self.BuildGraph()
        self.m_session_id = session_id

    def BuildGraph(self):
        """构建意图识别子图"""
        from langgraph.graph import StateGraph
        graph_builder = StateGraph(CAgentIntentState)
        graph_builder.add_node("ClassifyIntent", self.ClassifyIntentNode)
        graph_builder.set_entry_point("ClassifyIntent")
        return graph_builder.compile()

    def ClassifyIntentNode(self, state: CAgentIntentState):
        """意图分类节点（接收外部 LLM 请求对象）"""
        sys_prompt = f"""
你是意图分类器，根据【当前用户最新一句话】判定唯一意图标签。
可选标签:
1. {INTENT_TASK_CREATE}：用户要求分析特定公司财报/业绩，必须选这个。
2. {INTENT_TASK_INFO_REFINE}：存在未启动任务，用户补充信息。
3. {INTENT_TASK_FOLLOWUP}：存在已完成任务，用户追问结果。
4. {INTENT_TASK_START_CONFIRM}：当前任务状态为READY就绪，用户回复包含但不限于 ok、确认、好的、开始、执行这类简短确认语句，表示同意启动前面沟通过的新任务，必须选这个。
5. {INTENT_FINANCE_QA}：仅限财务名词、概念解释。
6. {INTENT_UNKNOWN}：其他无关请求。

硬性约束:
- 只要用户意思类似于"分析 XX 公司 XX 年财报/业绩”，必须选 {INTENT_TASK_CREATE}。
- reason 只能基于当前用户最新输入，禁止复用历史对话。
- 【重要】year_range 必须输出标准化区间格式：`xxxx.xx月‑xxxx.xx月`，禁止输出类似“2022年和2023年”这种口语形式，识别到多年就展开成起止年月区间；找不到填空字符串""。
- 必须提取：target_stocks（公司名称列表，至少提取 1 个，不允许返回空数组）、year_range（时间，找不到填空字符串""）、raw_analysis_demand（用户原始诉求，找不到填空字符串"")）。
"""
        prompt = f"""
{sys_prompt}
当前任务状态：{state['current_task_status']}
上下文历史:
{state['context_text']}
用户最新提问：{state['user_query']}
"""

        llmRequest = CLLMRequest()
        intent_res = llmRequest.InvokeStructured(prompt, CIntentResult)
        return {**state, "intent_result": intent_res}

    def Run(self, context_text: str, current_task_status: Optional[str], user_query: str):
        """运行意图识别（通过参数传入 LLM 请求对象）"""
        init_state: CAgentIntentState = {
            "system_prompt": "",
            "context_text": context_text,
            "current_task_status": current_task_status,
            "user_query": user_query,
            "intent_result": None
        }

        LogInfo(f"CAgentIntent.Run user_query:{user_query}")

        final_state = self.m_graph.invoke(init_state)
        return final_state["intent_result"]
