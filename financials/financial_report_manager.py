# financials/financial_report_manager.py
import os
from typing import Dict, Optional, List
from financials.financial_report import CFinancialReport, BASE_COMPANY_DATA_DIR
from financials.report_date import CReportDate, TYPE_ANNUAL, TYPE_HALF, TYPE_QUARTER
from utils.config import CConfigManager
from utils.logs import PrintLog, LogInfo

class CFinancialReportManager:
    """单只股票的财报管理器，按类型分字典存储财报对象"""
    def __init__(self, m_stock_code: str, company_name: str):
        self.m_company_name = company_name
        self.m_stock_code: str = m_stock_code
        # key: CReportDate对象
        self.m_reports_by_year: Dict[CReportDate, CFinancialReport] = dict()
        self.m_reports_by_half_year: Dict[CReportDate, CFinancialReport] = dict()
        self.m_reports_by_quarter: Dict[CReportDate, CFinancialReport] = dict()

    def GetNameCode(self):
        return f"{self.m_company_name}({self.m_stock_code})"

    def GetReport(self, report_date: CReportDate) -> Optional[CFinancialReport]:
        """根据CReportDate获取财报，找不到返回None"""
        if report_date.m_type == TYPE_ANNUAL:
            return self.m_reports_by_year.get(report_date)
        elif report_date.m_type == TYPE_HALF:
            return self.m_reports_by_half_year.get(report_date)
        elif report_date.m_type == TYPE_QUARTER:
            return self.m_reports_by_quarter.get(report_date)
        return None

    def AddReport(self, report: CFinancialReport) -> None:
        """添加财报，按report.m_report_date.m_type存入对应字典，key为CReportDate"""
        rd = report.m_report_date
        if report.m_stock_code != self.m_stock_code:
            raise ValueError(f"财报股票代码 {report.m_stock_code} 和管理器 {self.m_stock_code} 不匹配")

        if rd.m_type == TYPE_ANNUAL:
            self.m_reports_by_year[rd] = report
        elif rd.m_type == TYPE_HALF:
            self.m_reports_by_half_year[rd] = report
        elif rd.m_type == TYPE_QUARTER:
            self.m_reports_by_quarter[rd] = report

    def ClearAll(self):
        """清空内部全部财报字典"""
        self.m_reports_by_year.clear()
        self.m_reports_by_half_year.clear()
        self.m_reports_by_quarter.clear()

    def LoadExistReports(self) -> int:
        """
        扫描本地磁盘 datas/company/{stock_code}/reports 下所有财报目录
        加载前先清空内部字典；遍历子目录，解析CReportDate.GetKey字符串，构造CReportDate，Load，AddReport
        返回成功加载财报的数量
        """
        # 加载前清空所有缓存
        self.ClearAll()

        base_dir = CConfigManager.GetInstance().GetDataDir()

        reports_root = os.path.join(base_dir, "company", self.m_stock_code, "reports")
        if not os.path.isdir(reports_root):
            print(f"LoadExistReports: 目录不存在 {reports_root}")
            return 0

        loaded_count = 0
        # 遍历reports下所有子文件夹，每个子文件夹是一个财报key目录
        for entry in os.scandir(reports_root):
            if not entry.is_dir():
                continue
            key_str = entry.name
            # key格式: type-year-quarter 例 annual-2022-0 , half-2023-0, quarter-2022-1
            parts = key_str.split("-")
            if len(parts) != 3:
                print(f"跳过非法目录名: {reports_root}/{key_str}")
                continue
                
            r_type, r_year_str, r_q_str = parts
            try:
                r_year = int(r_year_str)
                r_quarter = int(r_q_str)
            except ValueError:
                print(f"解析数字失败，跳过目录 {key_str}")
                continue


            report_date = CReportDate(m_type=r_type, year=r_year, quarter=r_quarter)
            rep = CFinancialReport(stock_code=self.m_stock_code, company_name=self.m_company_name, report_date=report_date)
            ok = rep.Load()
            if ok:
                self.AddReport(rep)
                loaded_count += 1
            else:
                print(f"目录 {key_str} Load()失败，跳过")

        LogInfo(f"LoadExistReports {self.GetNameCode()} count:{loaded_count}")        
        return loaded_count

    def __repr__(self):
        return f"<CFinancialReportManager stock={self.m_stock_code}, " \
               f"annual={len(self.m_reports_by_year)}, half={len(self.m_reports_by_half_year)}, qtr={len(self.m_reports_by_quarter)}>"


def TestLoad():
    """测试：加载茅台600519本地全部财报"""
    print("\n==== TestLoad CFinancialReportManager 加载茅台全部本地财报 ====")
    mgr = CFinancialReportManager("600519")
    cnt = mgr.LoadExistReports()
    print(f"成功加载财报数量: {cnt}")
    print(mgr)

    # 尝试取出2022年报打印片段
    rd_2022 = CReportDate(TYPE_ANNUAL, 2022, 0)
    rep_2022 = mgr.GetReport(rd_2022)
    if rep_2022:
        print("\n--- 2022年报 akshare.txt 片段 ---")
        print(rep_2022.GetAkshareText())
        print("\n--- 2022年报 summary.md 片段 ---")
        print(rep_2022.GetSummary())
    else:
        print("未找到2022年报，请先运行financial_report.py的TestSave生成磁盘数据")


if __name__ == "__main__":
    TestLoad()