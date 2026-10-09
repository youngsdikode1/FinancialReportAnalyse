# ====================== agents/agent_single_stock.py ======================
"""
单股数据采集Agent
当前版本：优先读取CCompany内存中已经从本地磁盘加载完毕财报对象
输出返回 CStockGetData Pydantic结构化对象
"""
from typing import List, TypedDict, Optional
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph

from companys.company_manager import CCompanyManager
from companys.company import CCompany
from financials.report_date import CReportDate
from financials.fetch_report import CDSFetchReports
from utils.logs import LogInfo
from events.event import CSubScribeObj, CEventMessage, CEventType
from events.event_manager import CEventManager

class CSingleStockRequest(BaseModel):
    """单只股票数据收集子任务请求实体"""
    m_stock_code: str
    m_requirement: str       # 分配给该股票的子分析诉求，由顶层Orchestrator的LLM拆解生成
    m_year_range: str        # 时间跨度，格式：2022.01月‑2022.12月
    m_RAG_list: List[str]   # RAG 提示词列表


class CStockGetData(BaseModel):
    """单只股票采集完成后的返回结果对象"""
    m_success: bool
    m_failed_reason: str = Field(default="")
    #: 基础财报信息：akshare财报文本、summary摘要合并列表
    m_base_info: str = Field(default="")
    #: RAG相关信息：{"query_list":[检索词],"chunks":[检索返回片段]}
    m_RAG_info: str = Field(default="")
    m_company_name: str = Field(default="")

    def GetInfoString(self):
        ret_string = ""
        ret_string += f"\n-------------------{self.m_company_name}信息---------------------\n"
        ret_string += self.m_base_info + "\n" + self.m_RAG_info

        ret_string += f"\n-------------------{self.m_company_name}信息---------------------\n"
        return ret_string

class CSingleStockState(TypedDict):
    """单股采集子图的状态定义"""
    stock_code: str
    year_range: str
    sub_requirement: str
    rag_key_list: List[str]
    raw_result: Optional[CStockGetData]


class CAgentSingleStock:
    def __init__(self, session_id):
        self.m_graph = self.BuildGraph()
        self.m_session_id = session_id


    def BuildGraph(self):
        graph_builder = StateGraph(CSingleStockState)
        graph_builder.add_node("LoadLocalReportNode", self.LoadLocalReportNode)
        graph_builder.set_entry_point("LoadLocalReportNode")
        return graph_builder.compile()

    def LoadLocalReportNode(self, state: CSingleStockState) -> CSingleStockState:
        stock_code = state["stock_code"]
        year_range = state["year_range"]
        rag_key_list = state["rag_key_list"]

        comp_mgr = CCompanyManager.GetInstance()
        company: Optional[CCompany] = comp_mgr.GetCompany(stock_code)
        if company is None:
            LogInfo(f"CAgentSingleStock.LoadLocalReportNode 找不到公司对象 {stock_code}")
            res = CStockGetData(
                m_success=False,
                m_failed_reason=f"找不到公司实例 stock_code={stock_code}",
                m_base_info="",
                m_RAG_info=""
            )


            return {**state, "raw_result": res}


        LogInfo(f"CAgentSingleStock.LoadLocalReportNode {self.m_session_id} stock={stock_code} name:{company.GetName()} year_range:{year_range}")


        event_msg = CEventMessage(
            msg_type = CEventType.TYPE_WORK_FLOW_STEP,
            session_id = self.m_session_id,
            content = f"🚧开始获取{company.GetNameCode()}相关财报信息",
        )


        evt_mgr = CEventManager.GetInstance()
        evt_mgr.Publish(event_msg)


        obj = CDSFetchReports()
        obj.m_session_id = self.m_session_id
        obj.m_stock_code = stock_code
        obj.m_year_range = year_range
        obj.Start()

        rd_list = CReportDate.ConvertReportDate(year_range)
        report_mgr = company.m_report_manager

        base_info_string = ""
        rag_info_string = ""
        for rd in rd_list:
            rep = report_mgr.GetReport(rd)
            if rep is not None:
                base_info_string += rep.GetBaseInfo()    
                rag_info_string += rep.RAGRetrieves(rag_key_list).GetLLMContent()
            

        res = CStockGetData(
            m_success=True,
            m_failed_reason="",
            m_base_info=base_info_string,
            m_RAG_info = rag_info_string,
            m_company_name=company.GetName()
        )
 
        return {**state, "raw_result": res}

    def Run(self, req: CSingleStockRequest) -> CStockGetData:
        init_state: CSingleStockState = {
            "stock_code": req.m_stock_code,
            "year_range": req.m_year_range,
            "sub_requirement": req.m_requirement,
            "rag_key_list": req.m_RAG_list,
            "raw_result": None
        }
        final_state = self.m_graph.invoke(init_state)
        raw_result: CStockGetData = final_state["raw_result"]
        LogInfo(f"CAgentSingleStock.Run finish success={raw_result.m_success}, reason={raw_result.m_failed_reason}")
        return raw_result