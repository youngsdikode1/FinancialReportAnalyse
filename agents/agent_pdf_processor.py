from typing import TypedDict, Optional, List
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph
from pathlib import Path
import pdfplumber
from utils.logs import LogInfo, PrintLog
from utils.llm_request import CLLMRequest
from financials.report_date import CReportDate, TYPE_ANNUAL, TYPE_HALF, TYPE_QUARTER
from financials.financial_report import CFinancialReport
from companys.company_manager import CCompanyManager
from companys.company import CCompany
import time
import os
from events.event_manager import PostWorkFlowMessage

# ---------------- 内存临时对象 CPDFPage 只内存，不落地 ----------------
class CPDFPage:
    def __init__(self, page_num: int, content: str):
        self.m_page_num: int = page_num
        self.m_content: str = content

# ===================== 单层扁平schema（来自demo ReportSimpleOut） =====================
class ReportSimpleOut(BaseModel):
    stock_code: str | None = Field(description="股票代码，识别不到返回null")
    company_name: str | None = Field(description="公司完整全称，识别不到返回null")
    company_desc: str | None = Field(description="简短公司业务简介，一百多字，识别不到返回null")
    report_type: str | None = Field(description="只能取：年报|中报|第一季度报告|第三季度报告，识别不到填null")
    report_year: int | None = Field(description="4位数字报告年份，识别不到填null")
    report_quarter: int | None = Field(description="取值 0|1|3；年报填0，一季报1，三季报3，中报填null；识别不到填null")
    report_markdown: str | None = Field(description="完整markdown格式财报研读报告，按章节组织，关键信息带上【来源页码：N】")

# ---------------- Agent State ----------------
class CPdfProcessorState(TypedDict):
    pdf_path: str
    force_overwrite: bool
    dump_batch_summary: bool
    # 解析产物
    page_list: Optional[List[CPDFPage]]
    total_page_count: int
    # 财报元信息，由一次性LLM节点填充
    has_got_meta: bool
    company_short_name: Optional[str]
    stock_code: Optional[str]
    company_full_name: Optional[str]
    company_desc: Optional[str]
    report_type: Optional[str]
    report_year: Optional[int]
    report_quarter: Optional[int]
    report_date: Optional[CReportDate]
    # 目录
    report_dir: Optional[str]
    # 全局输出
    full_report_summary: Optional[str]
    akshare_style_text: Optional[str]


    # 拼接完整raw文本
    raw_full_text: Optional[str]
    # ========= RAG入库状态新增 =========
    rag_ingest_ok: bool
    rag_ingest_err_msg: Optional[str]
    # 返回状态
    success: bool
    is_skip: bool
    fail_msg: Optional[str]


class CAgentPDFProcessor:
    def __init__(self):
        self.m_graph = self._build_graph()
        # 新增成员：保存解析得到的财报名
        # 保存解析输出的财报实体对象，对外可访问
        self.m_report: Optional[CFinancialReport] = None
        self.m_session_id = ""

    def _build_graph(self):
        gb = StateGraph(CPdfProcessorState)
        gb.add_node("NodeCheckFile", self._node_check_file)
        gb.add_node("NodeParsePdf", self._node_parse_pdf)
        gb.add_node("NodeOneShotWholeAnalyze", self._node_one_shot_whole_analyze)
        gb.add_node("NodeCheckReportDirExist", self._node_check_report_dir_exist)
        gb.add_node("NodeBuildReportEntity", self._node_build_report_entity)
        gb.add_node("NodeDoRagIngest", self._node_do_rag_ingest)
        gb.add_node("NodeFinish", self._node_finish)

        gb.set_entry_point("NodeCheckFile")

        # 文件校验失败直接结束
        def _route_after_check_file(state: CPdfProcessorState):
            if not state["success"]:
                return "NodeFinish"
            return "NodeParsePdf"
        gb.add_conditional_edges("NodeCheckFile", _route_after_check_file)

        # PDF解析失败直接结束
        def _route_after_parse_pdf(state: CPdfProcessorState):
            if not state["success"]:
                return "NodeFinish"
            return "NodeOneShotWholeAnalyze"
        gb.add_conditional_edges("NodeParsePdf", _route_after_parse_pdf)

        # 一次性全量分析失败直接结束
        def _route_after_one_shot(state: CPdfProcessorState):
            if not state["success"]:
                return "NodeFinish"
            return "NodeCheckReportDirExist"
        gb.add_conditional_edges("NodeOneShotWholeAnalyze", _route_after_one_shot)

        gb.add_edge("NodeCheckReportDirExist", "NodeBuildReportEntity")
        gb.add_edge("NodeBuildReportEntity", "NodeDoRagIngest")
        gb.add_edge("NodeDoRagIngest", "NodeFinish")
        return gb.compile()

    def _node_check_file(self, state: CPdfProcessorState) -> CPdfProcessorState:
        p = Path(state["pdf_path"])
        if not p.exists():
            failed_reason = f"文件不存在:{state['pdf_path']}"
            LogInfo(failed_reason)
            return {**state, "success": False, "fail_msg": failed_reason}
        if p.suffix.lower() != ".pdf":
            failed_reason = "不是pdf后缀文件"
            LogInfo(failed_reason)
            return {**state, "success": False, "fail_msg": failed_reason}
        LogInfo(f"[CAgentPDFProcessor] 文件校验通过 {p}")
        return state

    def _node_parse_pdf(self, state: CPdfProcessorState) -> CPdfProcessorState:
        pdf_p = Path(state["pdf_path"])
        session_id = self.m_session_id
        PostWorkFlowMessage(session_id, f"📄开始解析PDF文件")

        page_list: List[CPDFPage] = []
        page_list.append(CPDFPage(page_num=0, content=""))
        try:
            count = 0
            with pdfplumber.open(pdf_p) as pdf:
                total = len(pdf.pages)
                for idx, page in enumerate(pdf.pages, start=1):
                    txt = page.extract_text() or ""
                    page_list.append(CPDFPage(page_num=idx, content=txt))
                    count += 1
                    if count % 10 == 0:
                        PrintLog(f"[CAgentPDFProcessor] PDF 已处理页数 {count}")
            LogInfo(f"[CAgentPDFProcessor] PDF解析完成，总页数 {total}")
            PostWorkFlowMessage(session_id, f"✅PDF文件解析完成，总页数：{total}")
            return {
                **state,
                "page_list": page_list,
                "total_page_count": total,
            }
        except Exception as e:
            LogInfo(f"[CAgentPDFProcessor] pdf解析异常 {e}")
            PostWorkFlowMessage(session_id, f"❌PDF解析失败：{str(e)}")
            return {**state, "success": False, "fail_msg": f"pdf解析失败:{str(e)}"}

    def FetchWholeReportSummary(self, full_report_text: str) -> ReportSimpleOut:
        LogInfo(f"FetchWholeReportSummary len:{len(full_report_text)}")
        """
        参考demo test_report_langchain_structured实现
        使用单层扁平ReportSimpleOut，无嵌套无数组；完整研读全部放入report_markdown
        兼容旧版CLLMRequest.InvokeStructured仅接收字符串prompt
        """
        user_full_content = f"""
下面是一份完整的A股上市公司财报原始提取文本：
====完整财报全部文本====
{full_report_text}
任务说明：
解析这份财报，提取信息输出结构化对象。
字段说明：
1. stock_code：股票代码；识别不到填null
2. company_name：公司完整全称；识别不到填null
3. company_desc：简短公司业务简介，一百多字；识别不到填null
4. report_type：报告类型，只允许取值【年报|中报|第一季度报告|第三季度报告】，识别不到填null
5. report_year：4位数字报告年份；识别不到填null
6. report_quarter：季度标识，年报填0，一季报填1，三季报填3，中报填null；识别不到填null
7. report_markdown：完整markdown格式财报研读报告，按章节组织，关键信息带上【来源页码：N】。
【财报研读质量要求】（必须全部遵守）
1. 用markdown分章节输出，优先按财报原生章节组织；
2. 覆盖：公司概况、经营业绩、分产品/分地区业务、成本费用、资产负债、现金流、分红、重大事项、风险提示、审计意见；
3. 所有数字必须来自原文，禁止编造；指标冲突要标⚠️数据冲突；区分不同统计口径；
4. 输出字数尽量控制在5000‑7000token之间，禁止过度简略压缩，在限额内尽可能详实展开，不要省略业务说明、口径备注，不要只罗列表格数字。
关键数字标注【来源页码：N】（文本里 `---- grewtrewsfdg--页码号 N 页码号--grewtrewsfdg ----` 是页码分隔标记）；
文档使用固定标记分割PDF物理页码：
`---- grewtrewsfdg--页码号 N 页码号--grewtrewsfdg ----`
N = PDF物理页码。
"""
        sys_content = "你是A股专业财报分析师，根据财报内容提取信息输出结构化对象。"
        full_prompt = f"【System】\n{sys_content}\n\n【User】\n{user_full_content}"
        llm = CLLMRequest()
        llm.SWitchToDeepseekModel()
        result: ReportSimpleOut = llm.InvokeStructured(full_prompt, ReportSimpleOut)
        if result is None:
            raise ValueError("FetchWholeReportSummary: InvokeStructured 返回None，tool‑choice解析失败")
        return result

    def _node_one_shot_whole_analyze(self, state: CPdfProcessorState) -> CPdfProcessorState:
        """
        一次性全量分析节点：
        1. page_list拼接生成带页码标记完整raw_full_text
        2. 调用FetchWholeReportSummary拿到ReportSimpleOut
        3. 将LLM识别的元信息回填state
        4. 生成report_date，校验关键字段完整性
        """
        page_list = state["page_list"]
        # 1 拼接完整带页码标记原始文本
        raw_lines = []
        for idx in range(1, len(page_list)):
            pg = page_list[idx]
            sep = f"---- grewtrewsfdg--页码号 {pg.m_page_num} 页码号--grewtrewsfdg ----"
            raw_lines.append(sep)
            raw_lines.append(pg.m_content)
        raw_full_text = "\n".join(raw_lines)
        state["raw_full_text"] = raw_full_text
        text_len = len(raw_full_text)
        LogInfo(f"_node_one_shot_whole_analyze 全量文本字符长度:{text_len}")
        # 简单超限预警，不阻断流程，仅打印日志，后续业务可在此增加直接失败逻辑
        if text_len > 220000:
            LogInfo(f"WARN: 财报文本过长 {text_len} chars，存在上下文截断风险")
        try:
            out: ReportSimpleOut = self.FetchWholeReportSummary(raw_full_text)
        except Exception as e:
            msg = f"_node_one_shot_whole_analyze 一次性全量财报分析调用异常: {str(e)}"
            LogInfo(msg)
            return {**state, "success": False, "fail_msg": msg}
        # 回填state元信息
        state["stock_code"] = out.stock_code
        state["company_full_name"] = out.company_name
        state["company_desc"] = out.company_desc
        state["report_type"] = out.report_type
        state["report_year"] = out.report_year
        state["report_quarter"] = out.report_quarter
        # 简易生成公司简称，兜底
        state["company_short_name"] = None
        if out.company_name and isinstance(out.company_name, str):
            if "股份有限公司" in out.company_name:
                state["company_short_name"] = out.company_name.replace("股份有限公司", "")
        # 保存完整markdown报告
        state["full_report_summary"] = out.report_markdown
        state["akshare_style_text"] = None
        # 生成 report_date 逻辑
        rt = state["report_type"]
        ry = state["report_year"]
        rd = None
        if rt is not None and ry is not None:
            if rt in ("年报", "年度报告", "第四季度报告", "4季度报告"):
                rd = CReportDate(TYPE_ANNUAL, year=ry, quarter=0)
            elif rt in ("中报", "半年度报告"):
                rd = CReportDate(TYPE_HALF, year=ry, quarter=0)
            elif rt == "第一季度报告":
                rd = CReportDate(TYPE_QUARTER, year=ry, quarter=1)
            elif rt == "第三季度报告":
                rd = CReportDate(TYPE_QUARTER, year=ry, quarter=3)
        state["report_date"] = rd
        # 校验关键字段
        need_fields = [
            state["stock_code"],
            state["company_full_name"],
            state["report_type"],
            state["report_year"],
            state["report_date"]
        ]
        all_ready = all(x is not None for x in need_fields)
        if all_ready:
            state["has_got_meta"] = True
            LogInfo(f"_node_one_shot_whole_analyze 元信息全部集齐 has_got_meta=True rd={state.get('report_date')}")
        else:
            msg = f"LLM未能识别完整财报元信息 stock={state['stock_code']},full={state['company_full_name']},rt={state['report_type']},ry={state['report_year']}"
            LogInfo(msg)
            return {**state, "success": False, "fail_msg": msg}
        return state

    def _node_check_report_dir_exist(self, state: CPdfProcessorState) -> CPdfProcessorState:
        stock_code = state["stock_code"]
        comp_dict = {
            "name": state["company_short_name"],
            "stock_code": stock_code,
            "fullname": state["company_full_name"],
            "desc": state["company_desc"]
        }
        force_overwrite = state["force_overwrite"]
        comp_mgr = CCompanyManager.GetInstance()
        comp_mgr.CreateCompany(comp_dict)
        comp = comp_mgr.GetCompany(stock_code)
        rd: CReportDate = state["report_date"]
        if comp.GetReportManager().GetReport(rd):
            if not force_overwrite:
                LogInfo(f"_node_check_report_dir_exist skip comp:{comp.GetNameCode()} rd:{rd} ")
                return {
                    **state,
                    "success": True,
                    "is_skip": True,
                    "fail_msg": "报告已存在(内存或磁盘)，未开启强制覆盖，跳过处理"
                }
        LogInfo(f"_node_check_report_dir_exist {comp.GetNameCode()} pass")
        return state

    def _node_build_report_entity(self, state: CPdfProcessorState) -> CPdfProcessorState:
        """合并原 _node_build_raw_and_write + _node_build_report_entity，无本地磁盘IO，全部交给rep.Save()"""
        comp_mgr = CCompanyManager.GetInstance()
        comp = comp_mgr.GetCompany(state["stock_code"])
        LogInfo(f"_node_build_report_entity {comp.GetNameCode()} raw_full_text len={len(state['raw_full_text'] or '')}")
        rd: CReportDate = state["report_date"]
        rep = CFinancialReport(stock_code=state["stock_code"], company_name=comp.m_name, report_date=rd)
        rep.m_raw_pdf_text = state["raw_full_text"]
        rep.m_akshare = state["akshare_style_text"]
        rep.m_summary = state["full_report_summary"]
        rep.Save()
        comp.m_report_manager.AddReport(rep)
        self.m_report = rep

        LogInfo(f"[CAgentPDFProcessor] _node_build_report_entity 实体构建并保存完成 {rep.GetReportName()}")
        return state

    def _node_do_rag_ingest(self, state: CPdfProcessorState) -> CPdfProcessorState:
        """
        真实执行RAG向量化入库
        依赖：_node_build_report_entity已经构建好CFinancialReport实体，rep.m_raw_pdf_text已赋值
        注意：RAG入库失败不会让PDF整体处理失败，仅记录状态与日志
        """
        LogInfo("[CAgentPDFProcessor] ===== do RAG INGEST BEGIN =====")
        stock_code = state["stock_code"]
        rd: CReportDate = state["report_date"]
        comp_mgr = CCompanyManager.GetInstance()
        comp = comp_mgr.GetCompany(stock_code)
        rep = comp.GetReportManager().GetReport(rd)

        if rep is None:
            err_msg = "_node_do_rag_ingest：找不到已构建的财报实例，跳过RAG入库"
            LogInfo(err_msg)
            return {
                **state,
                "rag_ingest_ok": False,
                "rag_ingest_err_msg": err_msg
            }
        try:
            PostWorkFlowMessage(self.m_session_id, f"💾PDF文件内容入库中")

            ok = rep.RAGImportRawText()
            LogInfo(f"_node_do_rag_ingest finish ok={ok}, collection={rep.GetCollectionName()}")
            PostWorkFlowMessage(self.m_session_id, f"✅PDF文件内容入库完成")

            return {
                **state,
                "rag_ingest_ok": ok,
                "rag_ingest_err_msg": None
            }


        except Exception as e:
            err_msg = f"_node_do_rag_ingest RAG入库异常:{str(e)}"
            LogInfo(err_msg)
            return {
                **state,
                "rag_ingest_ok": False,
                "rag_ingest_err_msg": err_msg
            }

    def _node_finish(self, state: CPdfProcessorState) -> CPdfProcessorState:
        if state.get("is_skip", False):
            state["success"] = True
        LogInfo(f"[CAgentPDFProcessor] RAG ingest result ok={state.get('rag_ingest_ok')}, err={state.get('rag_ingest_err_msg')}")
        return state

    def Run(self, pdf_file_path: str, force_overwrite: bool = False, dump_batch_summary: bool = False):


        init_state: CPdfProcessorState = {
            "pdf_path": pdf_file_path,      
            "force_overwrite": force_overwrite,
            "dump_batch_summary": dump_batch_summary,
            "page_list": None,
            "total_page_count": 0,
            "has_got_meta": False,
            "company_short_name": None,
            "stock_code": None,
            "company_full_name": None,
            "company_desc": None,
            "report_type": None,
            "report_year": None,
            "report_quarter": None,
            "report_date": None,
            "report_dir": None,
            "full_report_summary": None,
            "akshare_style_text": None,
            "raw_full_text": None,
            "rag_ingest_ok": False,
            "rag_ingest_err_msg": None,
            "success": True,
            "is_skip": False,
            "fail_msg": None
        }
        LogInfo("CAgentPDFProcessor.Run")
        final_state = self.m_graph.invoke(init_state)
        report_name = self.m_report.GetReportName() if self.m_report is not None else ""

        return {
            "success": final_state["success"],
            "is_skip": final_state.get("is_skip", False),
            "fail_msg": final_state.get("fail_msg"),
            "report_dir": final_state.get("report_dir"),
            "rag_ingest_ok": final_state.get("rag_ingest_ok"),
            "rag_ingest_err_msg": final_state.get("rag_ingest_err_msg"),
            "report_name": report_name,

        }





# ------------------- 简单本地测试demo ----------------
def TestRun():
    PrintLog("TestRun")
    proc = CAgentPDFProcessor()
    file_path = "/home/young/finacial_datas/downloads/maotai-2024.pdf"
    file_path = "/home/young/finacial_datas/downloads/maotai-2023.pdf"    
    file_path = "/home/young/finacial_datas/downloads/wu-liang-ye-2024.pdf"    
    file_path = "/home/young/finacial_datas/downloads/wu-liang-ye-2023.pdf"
    
        
    #file_path = "/home/young/finacial_datas/downloads/zhang-zi-dao-2024.pdf"
    res = proc.Run(file_path, force_overwrite=True, dump_batch_summary=True)
    LogInfo(f"TestRun result: {res}")

def TestFullReportSummary():
    PrintLog(f"TestFullReportSummary")
    txt_path = "./test_summary.txt"
    if not os.path.exists(txt_path):
        PrintLog(f"错误：文件不存在 {txt_path}")
        return
    with open(txt_path, "r", encoding="utf-8") as f:
        test_summary = f.read()
    proc = CAgentPDFProcessor()
    out: ReportSimpleOut = proc.FetchWholeReportSummary(test_summary)
    PrintLog(f"stock_code={out.stock_code}")
    PrintLog(f"company_name={out.company_name}")
    PrintLog(f"report_type={out.report_type}, report_year={out.report_year}")
    PrintLog(f"report_markdown:\n{(out.report_markdown[:2000])}")
    with open("./report_markdown.md", "w", encoding="utf-8") as f:
        f.write(out.report_markdown)

def TestSaveReport():
    PrintLog(f"TestSaveReport")
    company = CCompanyManager.GetInstance().GetCompany("600519")
    """
    测试用例：伪造茅台2024年年报 CPdfProcessorState，模拟PDF+LLM分析完成后的内存状态
    不读取PDF、不调用LLM，直接执行graph后续节点链路，验证对象保存落盘逻辑
    """
    PrintLog("==== TestSaveReport 伪造茅台2024年年报state测试 ====")
    from financials.report_date import CReportDate, TYPE_ANNUAL
    fake_state: CPdfProcessorState = {
        "pdf_path": "/fake/path/maotai‑2024.pdf",
        "force_overwrite": True,
        "dump_batch_summary": False,
        "page_list": [],
        "total_page_count": 143,
        "has_got_meta": True,
        "company_short_name": "贵州茅台",
        "stock_code": "600519",
        "company_full_name": "贵州茅台酒股份有限公司",
        "company_desc": "贵州茅台酒股份有限公司主营茅台酒及系列酒的生产与销售，主导产品“贵州茅台酒”是大曲酱香型白酒代表。",
        "report_type": "年报",
        "report_year": 2024,
        "report_quarter": 0,
        "report_date": CReportDate(TYPE_ANNUAL, year=2024, quarter=0),
        "report_dir": None,
        "full_report_summary": """# 贵州茅台酒股份有限公司2024年年度报告研读
## 一、公司概况
- **公司全称**：贵州茅台酒股份有限公司；股票代码：600519。
## 二、经营业绩
2024年实现营业总收入1741.44亿元，归母净利润862.28亿元。
""",
        "akshare_style_text": None,
        "raw_full_text": "---- grewtrewsfdg--页码号 1 页码号--grewtrewsfdg ----\n伪造模拟的茅台PDF原始文本内容\n",
        "rag_ingest_ok": False,
        "rag_ingest_err_msg": None,
        "success": True,
        "is_skip": False,
        "fail_msg": None
    }
    proc = CAgentPDFProcessor()
    graph = proc.m_graph
    state = fake_state
    state = proc._node_build_report_entity(state)
    state = proc._node_do_rag_ingest(state)
    state = proc._node_finish(state)
    PrintLog(f"TestSaveReport 执行结束 success={state['success']}, is_skip={state['is_skip']}")
    PrintLog(f"report_dir = {state.get('report_dir')}")
    if not state["success"]:
        PrintLog(f"fail_msg = {state.get('fail_msg')}")

if __name__ == "__main__":
    TestRun()
    time.sleep(0.3)