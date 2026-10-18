from typing import List, TypedDict, Optional, Any, Literal
from pydantic import BaseModel, Field
import utils.llm_request
from utils.llm_request import CLLMRequest
import json
from utils.logs import PrintLog, LogInfo
from langgraph.graph import StateGraph
import time
from utils.prompt_manager import CPromptManager
from datetime import datetime

# ====================== agents/agent_intent_new.py ======================
"""
意图识别 Agent 模块（独立会话理解层，测试版本）
设计变更：
1. 不再依赖内存 m_pending_task，全部信息来自聊天上下文
2. CAgentIntent只做理解，不构造Task对象；analysis_demand为消解指代后的完整自然语言诉求
3. 新增批量测试用例框架，多case一键执行，输出报告
4. 增强：每个case打印输入对话；prompt补充产品背景、三要素约束、示范样例
"""

INTENT_TASK_INFO_READY = "intent_task_ready"
INTENT_TASK_INFO_REFINE = "intent_task_info_refine"
INTENT_TASK_START_CONFIRM = "intent_task_start_confirm"
INTENT_FINANCE_QA = "intent_finance_qa"
INTENT_TASK_CONTINUE_ANSWER = "intent_task_continue_answer"
INTENT_TASK_APP_INFO = "intent_app_info"
INTENT_TASK_CANCEL = "intent_task_cancel"
INTENT_UNKNOWN = "intent_unknown"
INTENT_REJECT = "intent_reject"

class CAgentIntentState(TypedDict):
    """意图识别子图的状态定义"""
    system_prompt: str
    context_text: str
    intent_result: Optional["CIntentResult"]


class CIntentResult(BaseModel):
    """意图识别结构化输出
    注意：本模块不构造任何task实体；analysis_demand仅为文本描述，交由下游解析Agent抽取结构化字段
    """
    intent_type: str = Field(description="意图类型，只能使用预定义英文标签")
    reason: str = Field(description="描述你的思考过程，给当前标签的原因")
    analysis_demand: Optional[str] = Field(
        default=None,
        description="财报分析类意图：消解对话代词/省略，输出一份脱离上下文也可读的完整任务描述；非财报类置为null"
    )
    response: str = Field(default=None, description="回应用户的语句")

    def GetResponse(self):
        return self.response


class CAgentIntent:
    def __init__(self, session_id: str):
        self.m_graph = self.BuildGraph()
        self.m_session_id = session_id

    def BuildGraph(self):
        graph_builder = StateGraph(CAgentIntentState)
        graph_builder.add_node("ClassifyIntent", self.ClassifyIntentNode)
        graph_builder.set_entry_point("ClassifyIntent")
        return graph_builder.compile()

    def ClassifyIntentNode(self, state: CAgentIntentState):
        app_back_group = CPromptManager.GetAppBackGroup()
        now_dt = datetime.now()        
        now_str = now_dt.strftime("%Y-%m-%d %A")


        sys_prompt = f"""
当前系统时间：{now_str}
{app_back_group}        
你是财报分析app的意图分类器，面向用户开放问答。
软件执行财报分析任务必须同时具备三大要素：
1.标的公司（股票/企业名称）
2.时间范围（报告年份/周期）
3.分析要求（查询什么指标、做什么分析）,如果用户没有特别指代，比如问某某公司某年表现如何，业绩如何等范围宽泛，可以视为用户需要我们将其总体情况进行总结，摘要
三者全部齐全，才可以进入待确认状态；任意一项缺失，需要引导用户补充信息。

【良好提问示范】
✅ 对比一下2023-2024年茅台和五粮液的营收情况
✅ 分析一下2023-2024年茅台的毛利率变化情况
✅ 分析一下宁德时代2024年海外营收情况

> 重要系统背景：系统不在内存保存任何待确认任务对象，所有诉求全部来自聊天历史；当用户确认执行任务时，会重新读取全部对话推导诉求。
> 规则：取对话时序中【用户最后一轮有效诉求】，忽略已经被用户修改、推翻的旧需求。
> 有时候用户时间上会表达 '最近1年'，'近3年' 等以当前时间为坐标的时间范围，你需要结合当前系统时间来推断，如果系统时间显示今年已经超过了9.1日，则要包含今年

intent_type可选标签说明:

{INTENT_TASK_INFO_READY}：用户最后一句话已经形成完整股票财报分析需求（标的、时间、分析要求三者齐全）且不命中{INTENT_REJECT} 列举的条件
。
用户若是在待确认阶段修改标的/时间/查询条件，修改后要素完整，同样归为此标签。
response复述更新后的分析诉求，询问用户是否确认开始分析，最后告知用户如果确认开始可以输入'确认'或'ok'
analysis_demand填写消解指代、完整独立的分析需求文本。

{INTENT_TASK_INFO_REFINE}：财报分析任务信息不完备，股票标的、分析时间段、分析要求三者至少缺失一项。如果分析要求用户没有特别指代，比如问某某公司某年表现如何，业绩如何等范围宽泛，可以视为用户需要我们将其总体情况进行总结，摘要。
response引导用户补充缺失信息，analysis_demand置为null。

{INTENT_TASK_START_CONFIRM}：对话已经存在完整财报分析诉求，满足三大要素且用户最后一句是确认、ok、好的等明确要求开始执行任务的表述。
response告知将开始执行，请耐心等待，analysis_demand复述完整分析诉求。

{INTENT_TASK_CONTINUE_ANSWER}：用户提问，答案已经存在于聊天历史记录中，可以直接回答，不需要拉取财报。
response直接给出答案，analysis_demand置为null。

{INTENT_TASK_APP_INFO}：用户询问系统使用说明，response回答使用相关说明，analysis_demand置为null。

{INTENT_FINANCE_QA}: 用户询问财务概念、名词知识，不涉及特定公司财报拉取。
response做知识解答，analysis_demand置为null。

{INTENT_TASK_CANCEL}: 用户表达不需要、算了、取消，推翻前面待确认的分析诉求。
response回复已取消，告知可重新发起查询，analysis_demand置为null。


{INTENT_UNKNOWN}：无关请求，无法处理。response提示无法处理，analysis_demand置为null。

硬性约束:
1. intent_type、reason两个字段必填。
2. analysis_demand仅财报相关意图(intent_task_ready / intent_task_start_confirm)填写完整独立文本，其余全部输出null。
3. 代词、省略必须消解；analysis_demand输出的文本，脱离聊天记录也能看懂要做什么分析。
"""
        user_prompt = f"""
{sys_prompt}
用户聊天记录:
{state['context_text']}
"""
        llmRequest = CLLMRequest()
        llmRequest.SWitchToDeepseekModel()
        intent_res = llmRequest.InvokeStructured(user_prompt, CIntentResult)
        return {**state, "intent_result": intent_res}

    def Run(self, context_text: str) -> CIntentResult:
        init_state: CAgentIntentState = {
            "system_prompt": "",
            "context_text": context_text,
            "intent_result": None
        }
        LogInfo(f"CAgentIntent.Run session={self.m_session_id}")
        final_state = self.m_graph.invoke(init_state)
        return final_state["intent_result"]


# ------------------------------ 批量测试用例定义 ------------------------------
class TestCase:
    def __init__(self, name: str, chat_text: str, expect_intent: str):
        self.name = name
        self.chat_text = chat_text
        self.expect_intent = expect_intent


def BatchRunTestCases():
    """批量执行全部测试用例，打印测试报告，不做硬断言，人工核验结果"""
    test_cases: List[TestCase] = [
        TestCase(
            name="case01_修改待确认任务_更换标的",
            chat_text="""-------- 用户聊天记录分割线 --------
user: 茅台2024年营收多少？
assistant: 分析诉求：查询贵州茅台2024年全年营业收入
确认开始执行吗？回复“确认执行”即可启动。
user: 换成五粮液吧
-------- 用户聊天记录分割线 --------""",
            expect_intent=INTENT_TASK_INFO_READY
        ),
        TestCase(
            name="case02_修改待确认任务_更换标的+年份",
            chat_text="""-------- 用户聊天记录分割线 --------
user: 茅台2024年营收多少？
assistant: 分析诉求：查询贵州茅台2024年全年营业收入
确认开始执行吗？回复“确认执行”即可启动。
user: 换成五粮液，看2023年
-------- 用户聊天记录分割线 --------""",
            expect_intent=INTENT_TASK_INFO_READY
        ),
        TestCase(
            name="case03_pending阶段_用户取消任务",
            chat_text="""-------- 用户聊天记录分割线 --------
user: 茅台2024年营收多少？
assistant: 分析诉求：查询贵州茅台2024年全年营业收入
确认开始执行吗？回复“确认执行”即可启动。
user: 不需要了
-------- 用户聊天记录分割线 --------""",
            expect_intent=INTENT_TASK_CANCEL
        ),
        TestCase(
            name="case04_pending阶段_用户确认执行",
            chat_text="""-------- 用户聊天记录分割线 --------
user: 茅台2024年营收多少？
assistant: 分析诉求：查询贵州茅台2024年全年营业收入
确认开始执行吗？回复“确认执行”即可启动。
user: 确认执行
-------- 用户聊天记录分割线 --------""",
            expect_intent=INTENT_TASK_START_CONFIRM
        ),
        TestCase(
            name="case05_信息不全_只给股票不给年份",
            chat_text="""-------- 用户聊天记录分割线 --------
user: 分析五粮液的营收
-------- 用户聊天记录分割线 --------""",
            expect_intent=INTENT_TASK_INFO_REFINE
        ),
        TestCase(
            name="case06_对话历史已有答案_直接追问",
            chat_text="""-------- 用户聊天记录分割线 --------
user: 贵州茅台2024年表现怎样，简单100字以内说明一下。
assistant: # 贵州茅台2024年表现简析
2024年贵州茅台营收约945亿元，同比增长约16%，毛利率约85%。
确认开始执行吗？回复“确认执行”即可启动。
user: 那营收是多少呢？
-------- 用户聊天记录分割线 --------""",
            expect_intent=INTENT_TASK_CONTINUE_ANSWER
        ),
        TestCase(
            name="case07_对话只有摘要_追问摘要不存在字段_需要重新拉财报",
            chat_text="""-------- 用户聊天记录分割线 --------
user: 贵州茅台2024年表现怎样，简单100字以内说明一下。
assistant: # 贵州茅台2024年表现简析
2024年贵州茅台营收约945亿元，同比增长约16%，毛利率约85%。
确认开始执行吗？回复“确认执行”即可启动。
user: 那高管的薪酬是多少？
-------- 用户聊天记录分割线 --------""",
            expect_intent=INTENT_TASK_INFO_READY
        ),
        TestCase(
            name="case08_纯财务概念问答",
            chat_text="""-------- 用户聊天记录分割线 --------
user: 什么是扣非净利润
-------- 用户聊天记录分割线 --------""",
            expect_intent=INTENT_FINANCE_QA
        ),
        TestCase(
            name="case09_先取消再输入确认_应当不再触发任务",
            chat_text="""-------- 用户聊天记录分割线 --------
user: 茅台2024营收多少？
assistant: 分析诉求：查询贵州茅台2024年全年营业收入
确认开始执行吗？回复“确认执行”即可启动。
user: 不需要了
assistant: 已为您取消本次分析需求，您可以重新发起财报查询。
user: 确认执行
-------- 用户聊天记录分割线 --------""",
            expect_intent=INTENT_UNKNOWN
        ),
        TestCase(
            name="case10_全新完整财报查询",
            chat_text="""-------- 用户聊天记录分割线 --------
user: 分析一下五粮液2024年的净利润与毛利率
-------- 用户聊天记录分割线 --------""",
            expect_intent=INTENT_TASK_INFO_READY
        ),
    ]
    total = len(test_cases)
    PrintLog(f"\n========== 开始批量意图测试，共 {total} 个用例 ==========\n")
    ok_cnt = 0
    for idx, tc in enumerate(test_cases):
        PrintLog(f"==== Case {idx+1}/{total} | {tc.name} | 预期intent={tc.expect_intent} ====")
        PrintLog(f"【输入完整对话历史】:\n{tc.chat_text}\n")
        try:
            agent = CAgentIntent(session_id=f"test_{idx}")
            res = agent.Run(tc.chat_text)
            PrintLog(f"【输出intent_type】: {res.intent_type}")
            PrintLog(f"【reason】: {res.reason}")
            PrintLog(f"【analysis_demand】: {res.analysis_demand}")
            PrintLog(f"【response】: {res.response}")

            if res.intent_type == tc.expect_intent:
                PrintLog(f">>> ✅ 标签匹配预期")
                ok_cnt +=1
            else:
                PrintLog(f">>> ⚠️ 标签不匹配！预期:{tc.expect_intent} 实际:{res.intent_type}")
        except Exception as e:
            PrintLog(f">>> ❌ 调用异常：{str(e)}")
        PrintLog("\n")
        time.sleep(0.25)

    PrintLog(f"========== 批量测试结束：匹配预期 {ok_cnt}/{total} ==========\n")


CHAT_HISTORY1 = """
-------- 用户聊天记录分割线 --------
user: 茅台2024年营收多少？
assistant: 分析诉求：查询贵州茅台2024年全年营业收入
确认开始执行吗？回复“确认执行”即可启动。
user: 换成五粮液吧
-------- 用户聊天记录分割线 --------
"""
def TestSingle():
    PrintLog("TestSingle")
    proc = CAgentIntent("abc")
    res = proc.Run(CHAT_HISTORY1)
    LogInfo(f"TestRun result: {res}")


if __name__ == "__main__":
    # 执行全部批量用例
    BatchRunTestCases()