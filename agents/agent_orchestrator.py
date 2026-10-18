# ====================== agents/agent_orchestrator.py ======================
"""
总编排Agent
流程：
1. DecomposeTaskNode：LLM将顶层CTaskAnalysis拆解为List[CSingleStockRequest]子任务列表
2. CollectSubDataNode：遍历子任务，临时new CAgentSingleStock，执行原始数据采集，收集全部原始结果
3. MergeGenerateReportNode：汇总全部原始采集数据 + 用户总诉求，调用LLM生成最终完整报告
"""
import json
import time
from typing import List, TypedDict, Optional, Any
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph
from agents.agent_single_stock import CAgentSingleStock, CSingleStockRequest, CStockGetData
from companys.company import CCompany
from companys.company_manager import CCompanyManager
from utils.logs import LogInfo, PrintLog
from utils.llm_request import CLLMRequest
from task_analyse import CTaskAnalysis
from utils.config import CConfigManager
from events.event_manager import CEventManager, PostWorkFlowMessage

# LLM任务拆解输出schema
class CDecomposeOutput(BaseModel):
    sub_requests: List[CSingleStockRequest] = Field(description="拆分后的每只股票子采集任务列表")


class COrchestratorState(TypedDict):
    # 直接保存完整task对象引用，不再拆解拷贝各个字段
    task: CTaskAnalysis
    sub_requests: List[CSingleStockRequest]          # LLM拆解得到子任务
    final_report: Optional[str]
    stock_get_data_list : List[CStockGetData]
    # 新增：子任务失败标记，用于条件路由短路整个工作流
    has_sub_task_fail: bool


class CAgentOrchestrator:
    def __init__(self, session_id):
        self.m_graph = self.BuildGraph()
        # 不再持有常驻 m_single_stock_agent，每个子任务运行时临时实例化
        self.m_session_id = session_id

        self.m_task = None

    def SetTask(self, task):
        self.m_task = task

    def BuildGraph(self):
        graph_builder = StateGraph(COrchestratorState)
        graph_builder.add_node("DecomposeTaskNode", self.DecomposeTaskNode)
        graph_builder.add_node("CollectSubDataNode", self.CollectSubDataNode)
        graph_builder.add_node("MergeGenerateReportNode", self.MergeGenerateReportNode)

        graph_builder.set_entry_point("DecomposeTaskNode")
        graph_builder.add_edge("DecomposeTaskNode", "CollectSubDataNode")

        # ========= 旧版langgraph：去掉condition=关键字，位置参数传入 =========
        def _route_after_collect(state: COrchestratorState):
            """CollectSubDataNode执行完成之后条件路由
            返回业务语义标记，由path_map映射到graph节点，实现解耦
            """
            if state.get("has_sub_task_fail", False):
                return "FAIL_EXIT"
            return "NORMAL_CONTINUE"

        graph_builder.add_conditional_edges(
            "CollectSubDataNode",
            _route_after_collect,
            {
                "NORMAL_CONTINUE": "MergeGenerateReportNode",
                "FAIL_EXIT": "__end__"
            }
        )

        graph_builder.add_edge("MergeGenerateReportNode", "__end__")
        return graph_builder.compile()


    def DecomposeTaskNode(self, state: COrchestratorState) -> COrchestratorState:
        """节点1：LLM将顶层总任务拆分为多个CSingleStockRequest子采集任务"""
        task = state["task"]
        companys = task.GetCompanyInfos()
        company_names_code = [c.GetNameCode() for c in companys]
        LogInfo(f"CAgentOrchestrator.DecomposeTaskNode company_names_code:{company_names_code}")
        prompt = f"""
你是任务拆解器，把顶层财报分析任务拆分成每只股票独立的数据采集子任务。
总分析诉求：{task.m_analysis_requirement}
时间区间：{task.m_year_range}
待分析标的列表：{company_names_code}
要求：
1. 为列表里面每一家上市公司生成一个CSingleStockRequest子任务；
2. m_stock_code填股票代码；
3. m_year_range直接沿用输入的时间区间；
4. m_requirement：针对该个股，从总诉求提炼适合这只股票的子分析文字要求；
5. 每个子任务，系统默认会给任务带上相应财报的akshare信息，当期或多期财报的摘要信息
5. m_RAG_list：生成3‑6个检索关键词/检索提示词列表，用于后续知识库RAG检索；
   - 关键词要贴合该股票+当前分析诉求；
   - 可以包含财务指标、业务板块、行业政策、经营风险、管理层关注点；
   - 如果关键词对应查找的信息可能在akshare和财报的摘要信息，则可以不用RAG检索
输出JSON，字段sub_requests为子任务数组。
"""
        llm = CLLMRequest()
        decompose_res: CDecomposeOutput = llm.InvokeStructured(prompt, CDecomposeOutput)
        LogInfo(f"CAgentOrchestrator.DecomposeTaskNode sub_requests:{decompose_res.sub_requests}")
        return {**state, "sub_requests": decompose_res.sub_requests}

    def CollectSubDataNode(self, state: COrchestratorState) -> COrchestratorState:
        """节点2：遍历子任务，临时实例CAgentSingleStock执行原始数据采集，收集全部结果
        任意子任务m_success=False，立刻短路整个工作流，不再执行剩余子任务
        """

        if self.m_task.IsCancel():
            return {**state}


        sub_req_list = state["sub_requests"]
        stock_get_data_list= []
        for req in sub_req_list:
            stock_collector = CAgentSingleStock(self.m_session_id)
            res_obj: CStockGetData = stock_collector.Run(req)
            LogInfo(f"CAgentOrchestrator.CollectSubDataNode success={res_obj.m_success}, reason={res_obj.m_failed_reason}")

            # 发现子任务失败，直接短路，不再循环剩下子任务
            if not res_obj.m_success:
                fail_msg = res_obj.m_failed_reason
                LogInfo(f"CollectSubDataNode检测到子任务失败，触发整体短路：{fail_msg}")
                return {
                    **state,
                    "stock_get_data_list": stock_get_data_list,
                    "final_report": fail_msg,
                    "has_sub_task_fail": True
                }

            stock_get_data_list.append(res_obj)

        # 全部子任务正常执行完成
        new_state = {
            **state,
            "stock_get_data_list": stock_get_data_list,
            "has_sub_task_fail": False
        }
        return new_state

    def MergeGenerateReportNode(self, state: COrchestratorState) -> COrchestratorState:
        LogInfo(f"CAgentOrchestrator.MergeGenerateReportNode")

        if self.m_task.IsCancel():
            return {**state}


        PostWorkFlowMessage(self.m_session_id, f"📊所有信息已汇总,执行分析")
        """节点3：汇总全部原始财报数据，结合总诉求，调用LLM生成最终完整分析报告"""
        task = state["task"]
        #raw_data_text = json.dumps(state["collected_raw_data_list"], ensure_ascii=False, indent=2)
        stock_get_data_list = state["stock_get_data_list"]
        raw_data_text = ""
        for _get_data in stock_get_data_list:
            raw_data_text += _get_data.GetInfoString()
            raw_data_text += "\n"
        prompt = f"""        
你是财报分析专家，基于下面多只股票的原始财报数据，完成用户的分析需求。只在原始材料数据中进行分析，不要使用你的记忆中的数据。
【用户总分析诉求】：{task.m_analysis_requirement}
【全部股票原始财报数据】：
{raw_data_text}
请输出完整可读的markdown分析报告，可以做多公司横向对比。
如果用了相应数据或文字章节回答问题，尽量带上数据对应的财报名，页码或章节(注意不是数据块号)，如果数据中没有页码或章节信息，可以忽略
如果收集的数据不足以回答，则礼貌回答收集到的数据不足以回答问题。
如果给出的数据明显不能回答用户的问题，则礼貌回答原因，不要自行假设数据，不要硬答用户。
如果发现缺少某份财报，可以提示用户在APP的财报上传页面，手动上传财报进行重新分析
"""
        llm = CLLMRequest()
        report = llm.Invoke(prompt)
        PostWorkFlowMessage(self.m_session_id, f"✅分析已完成")
        PostWorkFlowMessage(self.m_session_id, f" ")        
        return {**state, "final_report": report}

    def Run(self, task: CTaskAnalysis):
        LogInfo(f"CAgentOrchestrator.Run task:{task}")
        PostWorkFlowMessage(self.m_session_id, f"我即将开始执行有关于[{task.m_analysis_requirement}]的任务")
        # init_state直接塞入完整task对象，不用拆解拷贝字段
        init_state: COrchestratorState = {
            "task": task,
            "sub_requests": [],
            "final_report": None,
            "stock_get_data_list" : [],
            "has_sub_task_fail": False   # 初始化新增字段
        }
        final_state = self.m_graph.invoke(init_state)
        return final_state["final_report"]


def TestSingle():
    PrintLog("TestSingle")
    task = CTaskAnalysis("100")
    task.SetRunning()
    task.AddStock("600519")
    task.m_year_range = "2024.01月‑2024.12月"
    question1 = "分析贵州茅台2024年的财务报表"
    question2 = "贵州茅台 2024 年报，本期费用化研发投入、资本化研发投入分别是多少元？"
    question3 = "贵州茅台2024 年末公司回购专用证券账户里面持有多少股股份？本次回购总支付金额多少（不含交易费用）？"
    question4 = "贵州茅台2024 年非经常性损益合计净额是多少？请列出主要构成项目。"
    question5 = "天健会计师事务所对 贵州茅台 2024 年报识别出哪两项关键审计事项？审计程序做了哪些工作？"
    question6 = "贵州茅台2024 年报披露，公司 2025 年经营计划营业总收入目标增长多少？固定资产投资计划金额是多少？"
    question7 = "贵州茅台2024 年国内、国外经销商期末数量，本期新增、减少分别是多少家？"
    question8 = "贵州茅台2024 期末外币余额是多少?"
    task.m_analysis_requirement = question8
    agent = CAgentOrchestrator("abcd")
    report = agent.Run(task)
    task.m_full_analysis_result = report
    PrintLog(f"report:{report}")


def TestSameCompany2Year():
    PrintLog("TestSameCompany2Year")
    task = CTaskAnalysis("100")
    task.SetRunning()
    task.AddStock("600519")
    task.m_year_range = "2023.01月‑2024.12月"
    question1 = "分析贵州茅台2023,2024年的财务报表"
    question2 = "贵州茅台 2023 2024 期末外币余额变化情况?"
    question3 = "贵州茅台 2023 2024 防治污染设施的建设和运行情况怎样?"
    task.m_analysis_requirement = question3
    agent = CAgentOrchestrator("abcd")
    report = agent.Run(task)
    task.m_full_analysis_result = report
    PrintLog(f"report:{report}")


def Test2Company1Year():
    PrintLog("Test2Company1Year")
    task = CTaskAnalysis("100")
    task.SetRunning()
    task.AddStock("600519")
    task.AddStock("000858")
    task.m_year_range = "2023.01月‑2023.12月"
    question1 = "比较一下贵州茅台和五粮液2023的业绩"
    question2 = "比较一下贵州茅台和五粮液2023的情况，说一下哪家更赚钱。"
    question3 = "2023 年报风险提示章节，茅台和五粮液各自列出的主要风险，原文表述有哪些不一样。"
    task.m_analysis_requirement = question3
    agent = CAgentOrchestrator("abcd")
    report = agent.Run(task)
    task.m_full_analysis_result = report
    PrintLog(f"report:{report}")


def Main():
    CConfigManager.GetInstance()
    comp_dict = {'name': '贵州茅台', 'stock_code': '600519', 'fullname': '贵州茅台酒股份有限公司', 'desc': '中国白酒行业龙头企业，主要从事茅台酒及系列酒的研发、生产和销售'}
    company_mgr = CCompanyManager.GetInstance()
    company = company_mgr.CreateCompany(comp_dict)
    comp_wuliangye = {
        'name': '五粮液',
        'stock_code': '000858',
        'fullname': '宜宾五粮液股份有限公司',
        'desc': '国内浓香型白酒龙头企业，主要从事五粮液系列白酒产品的生产、研发与销售，拥有多个梯度白酒产品矩阵，覆盖高端、次高端及大众消费市场'
    }
    company = company_mgr.CreateCompany(comp_wuliangye)
    # 獐子岛
    comp_zhangzidao = {
        'name': '獐子岛',
        'stock_code': '002069',
        'fullname': '獐子岛集团股份有限公司',
        'desc': '海洋水产企业，主要从事海参、鲍鱼、虾夷扇贝等海珍品育苗、养殖、加工与销售，开展水产品国内外贸易业务'
    }
    company = company_mgr.CreateCompany(comp_zhangzidao)
    #TestSingle()
    #TestSameCompany2Year()
    Test2Company1Year()
    time.sleep(0.3)


if __name__ == "__main__":
    Main()