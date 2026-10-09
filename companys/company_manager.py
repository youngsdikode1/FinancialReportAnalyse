from typing import Dict, Optional
from companys.company import CCompany
from utils.logs import PrintLog, LogInfo
import time
from utils.config import CConfigManager
from pathlib import Path
import json

class CCompanyManager:
    """
    公司信息管理器【单例类】
    - m_companys: dict，key = 股票代码，value = CCompany对象
    - CreateCompany: 通过字典创建公司，股票代码已存在则不覆盖原有数据
    """
    __instance: Optional["CCompanyManager"] = None

    def __init__(self):
        if CCompanyManager.__instance is not None:
            raise RuntimeError("请使用 CCompanyManager.GetInstance() 获取单例，不要直接new")
        # key:股票代码字符串, value:CCompany实例
        self.m_companys: Dict[str, CCompany] = {}

        self.__LoadCompanyAll()

    @classmethod
    def GetInstance(cls) -> "CCompanyManager":
        """获取单例实例"""
        if cls.__instance is None:
            cls.__instance = cls()
        return cls.__instance

    def CreateCompany(self, comp_dict: dict) -> CCompany:
        """
        comp_dict: {"name":简称,"stock_code":"6位","fullname":全称,"desc":简介}
        """
        stock_code = comp_dict["stock_code"]
        comp = self.GetCompany(stock_code)
        if comp is not None:
            # 已存在，可按需做字段更新
            LogInfo(f"CCompanyManager.CreateCompany {comp.GetNameCode()} already have")
            return comp

        name = comp_dict.get("name", "")
        fullname = comp_dict.get("fullname", "")
        desc = comp_dict.get("desc", "")
        comp = CCompany(name=name, stock_code=stock_code, full_name=fullname, desc=desc)
        self.m_companys[stock_code] = comp

        # ✅新增：创建完成立刻持久化base_info.json
        comp.SaveBase()
        LogInfo(f"CCompanyManager.CreateCompany {comp.GetNameCode()} created")
        return comp


    def ClearAll(self):
        """清空全部公司缓存（测试用）"""
        self.m_companys.clear()

    def __LoadCompany(self, stock_code: str) -> Optional["CCompany"]:
        """静态方法：从base_info.json加载公司基础信息；文件不存在返回None"""

        if stock_code in self.m_companys:
            LogInfo(f"LoadCompany {stock_code} already loaded")
            return 

        cfg = CConfigManager.GetInstance()
        data_dir = Path(cfg.GetDataDir())
        json_path = data_dir / "company" / stock_code / "base_info.json"
        if not json_path.exists():
            return None


        with open(json_path, "r", encoding="utf-8") as f:
            d = json.load(f)
        name = d.get("name", "")
        stock = d.get("stock_code", "")
        full_name = d.get("full_name", "")
        desc = d.get("desc", "")
        company = CCompany(name=name, stock_code=stock, full_name=full_name, desc=desc)

        self.m_companys[stock] = company
        LogInfo(f"LoadCompany {company.GetNameCode()} loaded")        
        return company

    def __LoadCompanyAll(self):
        """私有：遍历data_dir/company全部子文件夹，批量加载所有已持久化的公司"""
        cfg = CConfigManager.GetInstance()
        data_dir = Path(cfg.GetDataDir())
        company_root = data_dir / "company"
        if not company_root.exists():
            LogInfo(f"__LoadCompanyAll dir not exist {company_root}")
            return
        sub_items = list(company_root.iterdir())
        loaded_count = 0
        for item in sub_items:
            # 只处理文件夹，跳过普通文件
            if not item.is_dir():
                continue
            stock_code = item.name
            ret = self.__LoadCompany(stock_code)
            if ret is not None:
                loaded_count += 1
        LogInfo(f"__LoadCompanyAll finish, total loaded:{loaded_count}")

    def GetCompany(self, stock_code):
        code = stock_code.strip()
        company = self.m_companys.get(code)
        if not company:
            return None

        company.Load()
        return company


def TestCreate():
    PrintLog("TestCreate")
    comp_mgr = CCompanyManager.GetInstance()
    c_moutai = comp_mgr.CreateCompany({
        "name": "贵州茅台",
        "stock_code": "600519",
        "fullname": "贵州茅台酒股份有限公司",
        "desc": "贵州茅台酒股份有限公司主营茅台酒及系列酒的生产与销售，主导产品“贵州茅台酒”是大曲酱香型白酒代表，拥有独特酿造工艺，通过直销、i茅台平台以及批发代理渠道面向国内及海外多国市场开展销售。"
    })

    c_wuliangye = comp_mgr.CreateCompany({
        "name": "五粮液",
        "stock_code": "000858",
        "fullname": "宜宾五粮液股份有限公司",
        "desc": "宜宾五粮液股份有限公司主要从事五粮液系列浓香型白酒生产销售，拥有多粮固态发酵酿造核心工艺，产品覆盖高端、中低端白酒矩阵，销售网络覆盖全国并出口海外多个国家地区。"
    })


def TestLoad():
    LogInfo("TestLoad")

    CCompanyManager.GetInstance().GetCompany("600519")

def TestLoadAll():
    LogInfo("TestLoadAll")
    CCompanyManager.GetInstance()


if __name__ == "__main__":
    TestLoad()


    time.sleep(0.3)        