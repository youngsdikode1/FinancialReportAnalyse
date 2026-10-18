# ====================== agents/agent_fetch_report.py ======================
"""
财报批量获取 Agent（Fan‑out / Fan‑in 并行版）
职责：
- 接收 (stock_code, year_range)，计算缺失财报列表
- 用 LangGraph Send API 做 fan‑out，并行处理每一份财报
- 用 reducer 做 fan‑in，合并所有并行结果
- 业务代码不出现任何 ThreadPool / submit 等线程 API
- 底层并行由 LangGraph 框架调度，业务层只声明任务
"""
from typing import List, Optional, TypedDict, Annotated
from operator import add
from langgraph.graph import StateGraph
from langgraph.types import Send
from financials import data_sinking
from financials.financial_report import CFinancialReport
from financials.financial_report_manager import CFinancialReportManager
from financials.report_date import CReportDate
from companys.company_manager import CCompanyManager
from utils.logs import LogInfo, LogStack, LogFatal
from utils.llm_request import CLLMRequest
from events.event_manager import PostWorkFlowMessage
from utils.config import CConfigManager



# ====================== 错误码常量定义 ======================
DOWN_ERR_OK = 0                      # 正常无错误
DOWN_ERR_COMPANY_NOT_EXIST = 20010   # 公司不存在
DOWN_ERR_DS_DOWN_FAILED = 20011      # datasinking下载调用失败(网络/429/配额超限等)
# ==========================================================


REQUIREMENT = """
你是财报分析助手，请阅读这份上市公司财报原文，生成一份简洁中文摘要。
【财报研读质量要求】（必须全部遵守）
1. 用markdown分章节输出，优先按财报原生章节组织；
2. 覆盖：公司概况、经营业绩、分产品/分地区业务、成本费用、资产负债、现金流、分红、重大事项、风险提示、审计意见；
3. 所有数字必须来自原文，禁止编造；指标冲突要标⚠️数据冲突；区分不同统计口径；
4. 输出字数尽量控制在5000‑7000token之间，禁止过度简略压缩，在限额内尽可能详实展开，不要省略业务说明、口径备注，不要只罗列数字表格。
"""
FIN_REPORT_SUMMARY_PROMPT = """
{requirement}
=====财报原文开始=====
{content}
=====财报原文结束=====
再强调一次要求:
{requirement}
"""

# ====================== State 定义 ======================
class CMakeReportTaskState(TypedDict):
    """Worker 节点的输入 State，只包含单份财报处理所需字段"""
    stock_code: str
    report_date: CReportDate


class CAgentFetchReportState(TypedDict):
    """主图 State
    fetched_reports 字段必须配 reducer，多个 Worker 并行写回时会自动合并
    err_code / err_desc：全局下载状态，不使用reducer，下载阶段全局标记
    download_fail_report_dates：下载整体失败时，记录原本待下载的缺失报告期，仅SplitTaskNode单线程修改
    err_code = 0 代表正常；非0代表下载前置全局错误
    """
    stock_code: str
    year_range: str
    miss_reports: List[CReportDate]
    fetched_reports: Annotated[List[CFinancialReport], add]

    err_code: int
    err_desc: str
    download_fail_report_dates: List[CReportDate]


# ====================== CAgentFetchReport ======================
class CAgentFetchReport:
    """
    财报批量获取 Agent
    - 内部构建一个 LangGraph 子图，用 Send 做 fan‑out，用 reducer 做 fan‑in
    - 业务代码不出现 ThreadPool / submit
    """
    # 并行上限：控制MakeReport后处理(LLM/RAG)并发，下载逻辑在split节点单线程
    MAX_CONCURRENCY = 1

    def __init__(self, session_id: str):
        self.m_session_id: str = session_id
        self.m_stock_code: str = ""
        self.m_year_range: str = ""
        self.m_report_manager: Optional[CFinancialReportManager] = None

        # 对外输出载体：用于将LangGraph子图state内部错误信息带出invoke作用域，供调用方读取
        self.m_task_err_code: int = 0
        self.m_task_err_desc: str = ""
        # 下载整体失败(DOWN_ERR_DS_DOWN_FAILED)时：保存原本待下载的报告期列表
        self.m_err_report_dates: List[CReportDate] = []

        self.m_graph = self._build_graph()

    # ---------------- Graph 构建 ----------------
    def _build_graph(self):
        gb = StateGraph(CAgentFetchReportState)
        gb.add_node("SplitTaskNode", self._split_task_node)
        gb.add_node("MakeReportNode", self._make_report_node)
        gb.set_entry_point("SplitTaskNode")
        # 从 SplitTaskNode 出发，fan‑out 到多个 MakeReportNode
        gb.add_conditional_edges(
            "SplitTaskNode",
            self._fan_out_to_make_report,
            ["MakeReportNode"]
        )
        # MakeReportNode 执行完成后直接结束，结果通过 reducer 合并进 state
        gb.add_edge("MakeReportNode", "__end__")
        return gb.compile()

    # ---------------- 节点实现 ----------------
    def _split_task_node(self, state: CAgentFetchReportState):
        """入口节点：计算缺失报告列表，执行下载；下载失败直接短路
        注意：本节点不并行，只负责准备任务列表 + http下载
        """
        LogInfo(f"CAgentFetchReport.SplitTaskNode session={self.m_session_id} "
                f"stock={state['stock_code']} range={state['year_range']}")
        company = CCompanyManager.GetInstance().GetCompany(state["stock_code"])
        if not company:
            err_msg = f"找不到公司对象 {state['stock_code']}"
            LogInfo(f"CAgentFetchReport.SplitTaskNode {err_msg}")
            return {
                **state,
                "miss_reports": [],
                "err_code": DOWN_ERR_COMPANY_NOT_EXIST,
                "err_desc": err_msg,
                "download_fail_report_dates": []
            }

        self.m_report_manager = company.GetReportManager()
        miss_reports = self._cal_miss_reports(state["stock_code"], state["year_range"])
        LogInfo(f"CAgentFetchReport.SplitTaskNode miss count={len(miss_reports)}")

        if len(miss_reports) > 0:
            miss_desc = f"🔧{company.GetNameCode()}在系统本地缺少财报:"
            for rd in miss_reports:
                miss_desc += f"{rd.GetName()} "
            miss_desc += ",我将开始下载"
            PostWorkFlowMessage(self.m_session_id, miss_desc)
            # 预下载：优先本地，缺失触发 HTTP
            miss_years = [rd.m_year for rd in miss_reports]
            dl_start = f"{min(miss_years)}-01-01"
            dl_end = f"{max(miss_years)}-12-31"

            dl_result = data_sinking.FetchDSFiles(state["stock_code"], dl_start, dl_end)
            # ========== 下载全局失败，立刻短路 ==========
            if not dl_result.m_download_success:
                err_msg = dl_result.m_err_desc
                LogFatal(f"CAgentFetchReport.SplitTaskNode 下载失败：{err_msg}")
                PostWorkFlowMessage(self.m_session_id, f"❌{company.GetNameCode()}财报下载失败：{err_msg}")
                return {
                    **state,
                    "miss_reports": [],
                    "err_code": DOWN_ERR_DS_DOWN_FAILED,
                    "err_desc": err_msg,
                    "download_fail_report_dates": miss_reports
                }

        return {
            "miss_reports": miss_reports,
            "err_code": state["err_code"],
            "err_desc": state["err_desc"],
            "download_fail_report_dates": []
        }

    def _fan_out_to_make_report(self, state: CAgentFetchReportState):
        """Fan‑out 分支函数：为每一份待处理财报生成一个 Send
        LangGraph 会根据返回的 Send 列表，并行调度 MakeReportNode
        如果 err_code !=0，返回空列表，不会启动worker
        """
        if state["err_code"] != 0:
            LogInfo(f"CAgentFetchReport._fan_out_to_make_report err_code={state['err_code']},跳过fan‑out")
            return []

        sends = [
            Send("MakeReportNode", {
                "stock_code": state["stock_code"],
                "report_date": rd,
            })
            for rd in state["miss_reports"]
        ]
        LogInfo(f"CAgentFetchReport._fan_out_to_make_report fanout count={len(sends)}")
        return sends

    def _make_report_node(self, state: CMakeReportTaskState):
        """Worker 节点：处理单份财报
        - 内部保持串行：读本地文件 → MakeReport → Save
        - 捕获异常，返回空列表而不是抛异常，避免拖垮整个 fan‑in
        - 返回值通过 reducer 合并进主图 state.fetched_reports
        """
        stock_code = state["stock_code"]
        report_date: CReportDate = state["report_date"]
        try:
            rep = self._make_one_report(stock_code, report_date)
        except Exception as e:
            LogStack(e, f"CAgentFetchReport.MakeReportNode fail "
                        f"stock={stock_code} rd={report_date.GetKey()}")
            rep = None
        if rep is None:
            # 单份失败不抛异常，返回空列表，不影响其他分支
            return {"fetched_reports": []}
        return {"fetched_reports": [rep]}


    def _cal_miss_reports(self, stock_code: str, year_range: str) -> List[CReportDate]:
        """计算本地缺失的财报列表，按时间升序"""
        expect_rds = CReportDate.ConvertReportDate(year_range)
        miss_list: List[CReportDate] = []
        for rd in expect_rds:
            if not self.m_report_manager.GetReport(rd):
                miss_list.append(rd)
        miss_list.sort(key=lambda x: (x.m_year, x.m_type, x.m_quarter))
        return miss_list

    def _make_one_report(
        self,
        stock_code: str,
        report_date: CReportDate
    ) -> Optional[CFinancialReport]:
        """单份财报处理：
        1. LoadDSFile 拿本地 markdown
        2. 组装 prompt，调用 LLM 生成摘要（失败时摘要置空，原文仍入库）
        3. 构建 CFinancialReport，AddReport + Save
        4. RAG 入库
        """

        company = CCompanyManager.GetInstance().GetCompany(stock_code)

        PostWorkFlowMessage(self.m_session_id, f"🌐{company.GetName()}{report_date.GetName()} 下载中...")
        LogInfo(f"CAgentFetchReport._make_one_report stock={stock_code} "
                f"rd={report_date.GetKey()} session={self.m_session_id}")
        cds_file = data_sinking.LoadDSFile(stock_code, report_date)
        if cds_file is None:
            LogFatal(f"CAgentFetchReport._make_one_report LoadDSFile None, skip {company.GetName()}{report_date.GetName()}")
            PostWorkFlowMessage(self.m_session_id, f"❌⏳{company.GetName()}{report_date.GetName()} 下载失败")


            return None

        PostWorkFlowMessage(self.m_session_id, f"⏳{company.GetName()}{report_date.GetName()} 已下载(1/3)")

        rep = CFinancialReport(
            stock_code=cds_file.m_stock_code,
            company_name=cds_file.m_stock_name,
            report_date=report_date
        )
        rep.m_raw_ds_text = cds_file.m_content
        rep.m_ds_title = cds_file.m_title
        PostWorkFlowMessage(self.m_session_id, f"⏳{rep.GetName()} 开始提取摘要,大约需要10秒")
        # LLM 摘要：失败时只记录日志，不影响原文入库
        try:
            prompt = FIN_REPORT_SUMMARY_PROMPT.format(content=rep.m_raw_ds_text, requirement=REQUIREMENT)
            llm = CLLMRequest()
            llm.SWitchToHeavyModel()
            output_text = llm.Invoke(prompt)
            rep.m_summary = output_text.strip()
        except Exception as e:
            LogStack(e, f"CAgentFetchReport LLM summary fail rd={report_date.GetKey()}")
            rep.m_summary = ""
        # 写回 manager 与磁盘
        # 注意：_make_report_node 被多线程并行调度，这里 AddReport 存在竞争风险。
        # 但 Python dict 单键赋值是原子操作，且每个 rd 唯一，不会写冲突。

        company.GetReportManager().AddReport(rep)
        PostWorkFlowMessage(self.m_session_id, f"⏳{rep.GetName()},完成摘要(2/3)")

        rep.Save()
        insert_need_time = CConfigManager.GetInstance().GetValue('rag_insert_need_time', 90)

        PostWorkFlowMessage(self.m_session_id, f"💾{rep.GetName()}数据入库中,大约需要{insert_need_time}秒")
        rep.RAGImportRawText()
        PostWorkFlowMessage(self.m_session_id, f"💡{rep.GetName()}入库完成(3/3)")
        return rep

    # ---------------- 对外入口 ----------------
    def Start(self) -> List[CReportDate]:

        LogInfo(f"CAgentFetchReport.Start session={self.m_session_id} "
                f"stock={self.m_stock_code} range={self.m_year_range}")
        company = CCompanyManager.GetInstance().GetCompany(self.m_stock_code)
        if not company:
            LogInfo(f"CAgentFetchReport.Start error: company not found {self.m_stock_code}")
            self.m_task_err_code = DOWN_ERR_COMPANY_NOT_EXIST
            self.m_task_err_desc = f"找不到公司实例 {self.m_stock_code}"
            self.m_err_report_dates = []
            return []

        # state初始化
        init_state: CAgentFetchReportState = {
            "stock_code": self.m_stock_code,
            "year_range": self.m_year_range,
            "miss_reports": [],
            "fetched_reports": [],
            "err_code": DOWN_ERR_OK,
            "err_desc": "",
            "download_fail_report_dates": []
        }
        config = {"max_concurrency": self.MAX_CONCURRENCY}
        try:
            final_state = self.m_graph.invoke(init_state, config=config)
        except Exception as e:
            LogStack(e, f"CAgentFetchReport.Start graph invoke fail "
                        f"stock={self.m_stock_code} range={self.m_year_range}")
            # 方案B：顶层invoke异常仅打日志，不分配专门err_code
            self.m_task_err_code = DOWN_ERR_OK
            self.m_task_err_desc = f"子图执行异常：{str(e)}"
            self.m_err_report_dates = []
            return []

        # 将子图state状态拷贝到实例对外成员
        self.m_task_err_code = final_state.get("err_code", DOWN_ERR_OK)
        self.m_task_err_desc = final_state.get("err_desc", "")
        self.m_err_report_dates = final_state.get("download_fail_report_dates", [])

        fetched_reports: List[CFinancialReport] = final_state.get("fetched_reports", [])
        handled_list = [rep.m_report_date for rep in fetched_reports]
        LogInfo(f"CAgentFetchReport.Start finish handled={len(handled_list)} "
                f"miss total={len(final_state.get('miss_reports', []))}, err_code={self.m_task_err_code}, fail_date_cnt={len(self.m_err_report_dates)}")
        return handled_list