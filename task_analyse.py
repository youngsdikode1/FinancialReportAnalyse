import json
from typing import List, TypedDict, Optional, Any, Literal, Set
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph
from utils.logs import PrintLog, LogInfo
from companys.company_manager import CCompanyManager
from companys.company import CCompany


TASK_STATUS_NEW_CREATE = "task_new_create"
TASK_STATUS_NEED_REFINE = "task_need_refine"
TASK_STATUS_READY = "task_ready"
TASK_STATUS_RUNNING = "task_running"
TASK_STATUS_FINISHED = "task_finished"
TASK_STATUS_FAILED = "task_failed"

TASK_TYPE_SINGLE_STOCK: Literal["single_stock"] = "single_stock"
TASK_TYPE_MULTI_STOCK: Literal["multi_stock"] = "multi_stock"
TASK_TYPE_SECTOR: Literal["sector"] = "sector"


class CTaskAnalysis:
    def __init__(self, task_id: str):
        self.m_task_id = task_id
        self.m_status = TASK_STATUS_NEW_CREATE

        self.m_target_stocks: Set[str] = set() #分析的目标的股票代码集合
        self.m_analysis_requirement: str = ""
        self.m_task_type: Optional[Literal["single_stock", "multi_stock", "sector"]] = None
        self.m_year_range: Optional[str] = None
        self.m_full_analysis_result: Optional[str] = None

    def __repr__(self):
        return f"CTaskAnalysis<status:{self.m_status}, companys:{self.GetCompanyNameCodes()}, year_range:{self.m_year_range}, requirement:{self.m_analysis_requirement}>"

    def GetRequirement(self):
        return self.m_analysis_requirement


    def SetStatus(self, status: str):
        self.m_status = status

    def GetStatus(self):
        return self.m_status

    def SetRunning(self):
        self.m_status = TASK_STATUS_RUNNING

    def IsFinish(self):
        return self.m_status == TASK_STATUS_FINISHED
    
    def AddStock(self, _id):
        self.m_target_stocks.add(_id)


    def GetCompanyInfos(self) -> List[CCompany]:
        """
        根据task内部 m_target_stocks 股票代码集合，从全局公司管理器查询
        返回 CCompany 实体对象列表；找不到的股票代码直接跳过
        """
        mgr = CCompanyManager.GetInstance()
        result: List[CCompany] = []


        for stock_code in self.m_target_stocks:
            comp = mgr.GetCompany(stock_code)
            if comp is not None:
                result.append(comp)
       
        return result

    def GetCompanyNameCodes(self) -> List[str]:
        """
        根据task内部 m_target_stocks，返回【公司全称】字符串列表
        """
        info_list = self.GetCompanyInfos()
        name_list: List[str] = []
        for comp in info_list:
            name_list.append(comp.GetNameCode())
        return name_list

    def GetCompanyNameCodesStr(self):
        return ",".join(self.GetCompanyNameCodes())