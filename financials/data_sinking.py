import os
import json
import time
import requests
from datetime import datetime
from typing import Dict, Optional, List
from financials.report_date import CReportDate, TYPE_ANNUAL, TYPE_HALF, TYPE_QUARTER
from utils.config import CConfigManager
from utils.logs import LogInfo, LogStack

# ===================== 配置 =====================
# 注意：正式部署请移到 config.json 通过 CConfigManager 读取，不要硬编码提交git
DS_KEY = "N5IbZD4yzyXCNxexbLAJPSoIZ2STDkW4"
MAX_RETRY = 2  # 单period http请求最大重试次数
RETRY_SLEEP_SEC = 1.0


def to_datasink_symbol(stock_code: str) -> str:
    """内部6位纯股票代码转为 datasinking API需要的symbol
    6开头 → .SS 上交所；其他 → .SZ 深交所
    注意：北交所(8开头)未做适配，会错误输出.SZ，demo阶段兼容
    """
    if stock_code.startswith("6"):
        return f"{stock_code}.SS"
    else:
        LogInfo(f"to_datasink_symbol warn: stock_code={stock_code}, treat as SZ,北交所股票可能错误")
        return f"{stock_code}.SZ"


def _gen_report_date_range(startdate: str, enddate: str) -> List[CReportDate]:
    """
    根据 startdate "2020-12-31", enddate "2021-12-31"
    生成区间内所有合法报告：年报、中报、一季报、三季报
    """
    res: List[CReportDate] = []
    fmt = "%Y-%m-%d"
    dt_start = datetime.strptime(startdate, fmt)
    dt_end = datetime.strptime(enddate, fmt)

    y_begin = dt_start.year
    y_end = dt_end.year

    for year in range(y_begin, y_end + 1):
        res.append(CReportDate(TYPE_ANNUAL, year, quarter=0))
        res.append(CReportDate(TYPE_HALF, year, quarter=0))
        res.append(CReportDate(TYPE_QUARTER, year, quarter=1))
        res.append(CReportDate(TYPE_QUARTER, year, quarter=3))
    return res


class CDSFile:
    """datasinking单份财报文件实体
    存储两份文件：
        ds_base_info.json：元信息(不含大markdown文本)
        ds_content.md：完整markdown正文
    """
    def __init__(self):
        self.m_stock_code: str = ""
        self.m_stock_name: str = ""
        self.m_report_period: str = ""
        self.m_title: str = ""
        self.m_content: str = ""
        self.m_doc_type: str = ""
        self.m_report_date: Optional[CReportDate] = None

    def Save(self):
        """保存 ds_base_info.json + ds_content.md，不依赖CFinancialReport"""
        if self.m_report_date is None:
            raise ValueError("CDSFile.Save: m_report_date 不能为None")
        cfg = CConfigManager.GetInstance()
        base_reports_dir = cfg.GetReportBaseDir(self.m_stock_code)
        report_dir = os.path.join(base_reports_dir, self.m_report_date.GetKey())
        os.makedirs(report_dir, exist_ok=True)

        json_path = os.path.join(report_dir, "ds_base_info.json")
        md_path = os.path.join(report_dir, "ds_content.md")

        base_info = {
            "m_stock_code": self.m_stock_code,
            "m_stock_name": self.m_stock_name,
            "m_report_period": self.m_report_period,
            "m_title": self.m_title,
            "m_doc_type": self.m_doc_type,
            "report_date_key": self.m_report_date.GetKey()
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(base_info, f, ensure_ascii=False, indent=2)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(self.m_content)
        LogInfo(f"CDSFile.Save ok stock={self.m_stock_code}, dir={report_dir}")

    @classmethod
    def Load(cls, report_dir: str) -> Optional["CDSFile"]:
        """从指定目录本地加载，不访问网络；缺少任一文件返回None"""
        json_path = os.path.join(report_dir, "ds_base_info.json")
        md_path = os.path.join(report_dir, "ds_content.md")
        if not (os.path.exists(json_path) and os.path.exists(md_path)):
            return None
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                base_info = json.load(f)
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            LogStack(e, f"CDSFile.Load read file fail dir={report_dir}")
            return None

        try:
            rd_key = base_info["report_date_key"]
            parts = rd_key.split("-")
            r_type = parts[0]
            r_year = int(parts[1])
            r_q = int(parts[2])
            report_date = CReportDate(m_type=r_type, year=r_year, quarter=r_q)
        except Exception as e:
            LogStack(e, f"CDSFile.Load parse report_date_key fail key={base_info.get('report_date_key')}")
            return None

        obj = cls()
        obj.m_stock_code = base_info["m_stock_code"]
        obj.m_stock_name = base_info["m_stock_name"]
        obj.m_report_period = base_info["m_report_period"]
        obj.m_title = base_info["m_title"]
        obj.m_doc_type = base_info["m_doc_type"]
        obj.m_content = content
        obj.m_report_date = report_date
        return obj


def LoadDSFile(stock_code: str, report_date: CReportDate) -> Optional[CDSFile]:
    """仅本地磁盘读取，不走网络；找不到返回None"""
    cfg = CConfigManager.GetInstance()
    base_reports_dir = cfg.GetReportBaseDir(stock_code)
    report_dir = os.path.join(base_reports_dir, report_date.GetKey())
    return CDSFile.Load(report_dir)


def _c_report_date_to_period_str(rd: CReportDate) -> str:
    """把CReportDate转为API需要report_period字符串："2024-12-31" """
    if rd.m_type == TYPE_ANNUAL:
        return f"{rd.m_year}-12-31"
    elif rd.m_type == TYPE_HALF:
        return f"{rd.m_year}-06-30"
    elif rd.m_type == TYPE_QUARTER and rd.m_quarter == 1:
        return f"{rd.m_year}-03-31"
    elif rd.m_type == TYPE_QUARTER and rd.m_quarter == 3:
        return f"{rd.m_year}-09-30"
    return f"{rd.m_year}-12-31"


def _map_doc_type_to_c_report_date(doc_type: str, report_period: str) -> Optional[CReportDate]:
    """
    datasinking doc_type -> CReportDate
    doc_type: annual / semiannual(半年度) / q1 / q3
    report_period: "2025-12-31"
    """
    try:
        year = int(report_period.split("-")[0])
        if doc_type == "annual":
            return CReportDate(TYPE_ANNUAL, year=year, quarter=0)
        elif doc_type == "half" or doc_type == "semiannual":
            return CReportDate(TYPE_HALF, year=year, quarter=0)
        elif doc_type == "q1":
            return CReportDate(TYPE_QUARTER, year=year, quarter=1)
        elif doc_type == "q3":
            return CReportDate(TYPE_QUARTER, year=year, quarter=3)
        else:
            LogInfo(f"_map_doc_type_to_c_report_date unknown doc_type={doc_type}")
            return None
    except Exception as e:
        LogStack(e, f"_map_doc_type_to_c_report_date parse fail, doc_type={doc_type}, period={report_period}")
        return None


def DownLoadDSFiles(stock_code: str, startdate: str, enddate: str) -> Dict[CReportDate, CDSFile]:
    """
    使用requests直接http调用datasink.ing接口；逐个report_period请求
    :param stock_code: 内部6位股票代码，如"002069"
    :param startdate: "2020-01-01"
    :param enddate: "2024-12-31"
    :return: Dict[CReportDate, CDSFile]
    """
    result_dict: Dict[CReportDate, CDSFile] = dict()
    ds_symbol = to_datasink_symbol(stock_code)
    expect_rds = _gen_report_date_range(startdate, enddate)
    LogInfo(f"DownLoadDSFiles start stock={stock_code}, symbol={ds_symbol}, time_range={startdate} ~ {enddate}")

    for rd in expect_rds:
        period_str = _c_report_date_to_period_str(rd)
        url = (f"https://api.datasink.ing/documents?"
               f"symbol={ds_symbol}&report_period={period_str}&with_content=1&apikey={DS_KEY}")
        resp_json = None
        ok = False
        for retry in range(0, MAX_RETRY + 1):
            try:
                LogInfo(f"DownLoadDSFiles http request stock={stock_code}, {url}")
                resp = requests.get(url, timeout=15)
                resp.raise_for_status()
                resp_json = resp.json()
                ok = True
                break
            except Exception as e:
                LogStack(e, f"DownLoadDSFiles http fail stock={stock_code}, period={period_str}, retry={retry}")
                if retry >= MAX_RETRY:
                    LogInfo(f"DownLoadDSFiles stock={stock_code} 该period达到最大重试，跳过 period={period_str}")
                time.sleep(RETRY_SLEEP_SEC)
        if not ok or resp_json is None:
            continue

        item_list = resp_json.get("items", [])
        total_cnt = resp_json.get("total", 0)
        if total_cnt == 0 or len(item_list) == 0:
            LogInfo(f"DownLoadDSFiles stock={stock_code}, period={period_str} 接口total=0，本周期无财报，跳过")
            continue

        item = item_list[0]
        doc_type = item.get("doc_type", "")
        report_period = item.get("report_period", "")
        parse_rd = _map_doc_type_to_c_report_date(doc_type, report_period)
        if parse_rd is None:
            LogInfo(f"DownLoadDSFiles stock={stock_code} skip parse_rd fail title={item.get('title','')}")
            continue
        try:
            cds = CDSFile()
            cds.m_stock_code = item.get("stock_code", "")
            cds.m_stock_name = item.get("stock_name", "")
            cds.m_report_period = report_period
            cds.m_title = item.get("title", "")
            cds.m_content = item.get("content", "")
            cds.m_doc_type = doc_type
            cds.m_report_date = parse_rd
            cds.Save()
            result_dict[parse_rd] = cds
        except Exception as e:
            LogStack(e, f"DownLoadDSFiles stock={stock_code} single report save skip title={item.get('title','')}")
            continue
    LogInfo(f"DownLoadDSFiles stock={stock_code} finish, total ok count={len(result_dict)}")
    return result_dict


def FetchDSFiles(stock_code: str, startdate: str, enddate: str) -> Dict[CReportDate, CDSFile]:
    """
    上层主要入口
    1. 生成区间全部预期CReportDate列表
    2. 优先 LoadDSFile 读取本地缓存
    3. 如果存在本地缺失报告，取缺失报告的最小/最大年份，缩小下载窗口调用DownLoadDSFiles
    4. 合并【本地已存在 + 刚下载】返回结果字典
    :param stock_code: 内部6位股票代码
    :param startdate: "2020-12-31"
    :param enddate: "2021-12-31"
    :return: Dict[CReportDate, CDSFile]
    """
    result: Dict[CReportDate, CDSFile] = dict()
    expect_rds = _gen_report_date_range(startdate, enddate)

    missing_rds: List[CReportDate] = []
    # 第一步：全部优先读本地磁盘
    for rd in expect_rds:
        cds = LoadDSFile(stock_code, rd)
        if cds is not None:
            result[rd] = cds
        else:
            missing_rds.append(rd)

    LogInfo(f"FetchDSFiles stock={stock_code}, local hit:{len(result)}, missing count:{len(missing_rds)}")
    # 没有缺失，直接返回本地结果，不走网络
    if len(missing_rds) == 0:
        return result

    # 从缺失报告集合提取最小、最大年份，缩小下载时间窗口
    missing_years = [rd.m_year for rd in missing_rds]
    min_year = min(missing_years)
    max_year = max(missing_years)
    dl_start = f"{min_year}-01-01"
    dl_end = f"{max_year}-12-31"
    LogInfo(f"FetchDSFiles stock={stock_code}, 缺失报告年份范围 [{min_year} ~ {max_year}]，实际下载窗口 {dl_start} ~ {dl_end}")

    downloaded_dict = DownLoadDSFiles(stock_code, dl_start, dl_end)
    # 下载回来的数据合并进结果字典，下载到的会覆盖同key
    for rd, cds_file in downloaded_dict.items():
        result[rd] = cds_file

    return result


if __name__ == "__main__":

    LogInfo("===== data_sinking.py 测试用例开始 =====")
    test_stock = "002069"
    test_stock = "600519"
    s_date = "2020-12-31"
    e_date = "2024-12-31"
    cds_dict = FetchDSFiles(test_stock, s_date, e_date)
    LogInfo(f"测试 FetchDSFiles stock={test_stock} 返回报告数量: {len(cds_dict)}")
    for rd, cds_file in cds_dict.items():
        LogInfo(f"  key={rd.GetKey()}, title={cds_file.m_title}, content_len={len(cds_file.m_content)}")
        # ============上层业务示例，演示如何给到CFinancialReport复用============
        # from financials.financial_report import CFinancialReport
        # rep = CFinancialReport(
        #     stock_code=cds_file.m_stock_code,
        #     company_name=cds_file.m_stock_name,
        #     report_date=cds_file.m_report_date
        # )
        # rep.m_raw_pdf_text = cds_file.m_content
        # rep.Save()
        # ok = rep.RAGImportRawText()
        # LogInfo(f"demo import rag ok={ok}")
    LogInfo("===== data_sinking.py 测试用例结束 =====")