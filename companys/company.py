from typing import Optional
from financials.financial_report_manager import CFinancialReportManager
from utils.config import CConfigManager
from pathlib import Path
import json
from utils.logs import LogInfo, PrintLog
import time

class CCompany:
    """公司信息实体"""
    def __init__(
        self,
        name: str,
        stock_code: str,
        full_name: str,
        desc: str
    ):
        self.m_name: str = name
        self.m_stock_code: str = stock_code
        self.m_full_name: str = full_name
        self.m_desc: str = desc


        self.m_report_manager = None
        self.m_loaded = False

    def Load(self):
        if self.m_loaded:
            return    

        
        self.GetReportManager()
        self.m_loaded = True
        LogInfo(f"CCompany.Load {self.GetNameCode()}")

    def GetReportManager(self):
        if not self.m_report_manager: 
            self.LoadReportManager()

        return self.m_report_manager

    def LoadReportManager(self):
        LogInfo(f"CCompany.LoadReportManager {self.GetNameCode()}")

        self.m_report_manager = CFinancialReportManager(self.m_stock_code, self.m_name)
        self.m_report_manager.LoadExistReports()

    def ToDict(self) -> dict:
        """转为字典，方便日志、传给LLM"""
        return {
            "name": self.m_name,
            "stock_code": self.m_stock_code,
            "fullname": self.m_full_name,
            "desc": self.m_desc
        }

    @staticmethod
    def FromDict(d: dict) -> "CCompany":
        """从字典对象构建CCompany实例"""
        return CCompany(
            name=d["name"],
            stock_code=d["stock_code"],
            full_name=d["fullname"],
            desc=d["desc"]
        )
    def GetName(self):
        return self.m_name


    def GetStockCode(self):
        return self.m_stock_code

    def GetNameCode(self):
        return f"{self.m_name}({self.m_stock_code})"

    def SaveBase(self):
        """持久化基础信息到 base_info.json，只保存4个基础字段"""
        cfg = CConfigManager.GetInstance()
        data_dir = Path(cfg.GetDataDir())
        comp_dir = data_dir / "company" / self.m_stock_code
        comp_dir.mkdir(parents=True, exist_ok=True)

        out_dict = {
            "name": self.m_name,
            "stock_code": self.m_stock_code,
            "full_name": self.m_full_name,
            "desc": self.m_desc
        }
        json_path = comp_dir / "base_info.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(out_dict, f, ensure_ascii=False, indent=2)
        LogInfo(f"CCompany.SaveBase {self.GetNameCode()} {comp_dir}")




