# financials/report_date.py
import re
from datetime import date
from typing import Optional, List

TYPE_ANNUAL = "annual"
TYPE_HALF = "half"
TYPE_QUARTER = "quarter"

class CReportDate:
    """
    财报日期对象，区分 年报 / 中报 / 季度报
    m_type: "annual" 年报, "half" 中报, "quarter" 季度报
    m_year: 公历年 int
    m_quarter: 0=非季度; 1=Q1;3=Q3;4=Q4
    m_half: 0=非中报,1=是中报
    支持作为字典key，重写 __hash__、__eq__
    """
    def __init__(self, m_type: str, year, quarter=0, half=0):
        self.m_type: str = m_type
        self.m_year = int(year)
        self.m_quarter = int(quarter)
        self.m_half = int(half)
        if self.m_type == TYPE_HALF:
            self.m_half = 1
        else:
            self.m_half = 0
        if self.m_type == TYPE_ANNUAL:
            self.m_half = 0
            self.m_quarter = 0

    def GetName(self) -> str:
        """返回可读中文财报名称：2024年年报、2023年中报、2024年1季度报"""
        if self.m_type == TYPE_ANNUAL:
            return f"{self.m_year}年年报"
        elif self.m_type == TYPE_HALF:
            return f"{self.m_year}年中报"
        elif self.m_type == TYPE_QUARTER:
            q_map = {1:"1季度",3:"3季度",4:"4季度"}
            q_name = q_map.get(self.m_quarter, f"{self.m_quarter}季度")
            return f"{self.m_year}年{q_name}报"
        else:
            return f"{self.m_year}年未知类型报告"

    def GetKey(self) -> str:
        """生成唯一key字符串，用于hash、文件夹名称"""
        return f"{self.m_type}-{self.m_year}-{self.m_quarter}"

    def __hash__(self):
        return hash(self.GetKey())

    def __eq__(self, other):
        if not isinstance(other, CReportDate):
            return False
        return self.GetKey() == other.GetKey()

    def __repr__(self):
        return f"<CReportDate type={self.m_type}, year={self.m_year}, q={self.m_quarter}, half={self.m_half}>"

    @staticmethod
    def ConvertReportDate(date_range: str) -> List["CReportDate"]:
        """
        将时间范围字符串转为CReportDate列表，只使用年报、中报
        规则：
        1. 2022.1月‑2022.12月 → [2022年报]
        2. 2021.1月‑2022.9月 → [2021年报, 2022年报]
        3. "2021" / "2021年" → [2021年报]
        4. 若区间结束年等于系统当前年，结束月份>=6 → 取当年中报；结束月份<6，不取当年报告
        """
        cur_year = date.today().year
        # 正则提取所有 (年,月) 元组，例如 [(2025,3), (2026,10)]
        pattern = re.compile(r"(\d{4})[.年]?(\d{1,2})?月?")
        matches = pattern.findall(date_range)
        parse_items = []
        for y_str, m_str in matches:
            y = int(y_str)
            m = int(m_str) if m_str else None
            parse_items.append((y, m))
        if len(parse_items) == 0:
            return []

        start_y, start_m = parse_items[0]
        if len(parse_items) >= 2:
            end_y, end_m = parse_items[1]
        else:
            # 只有单个年份，无结束月，当作完整年度
            end_y = start_y
            end_m = 12

        res: List[CReportDate] = []
        # 遍历从 start_y 到 end_y‑1：全部完整过去年份，取年报
        for y in range(start_y, end_y):
            res.append(CReportDate(TYPE_ANNUAL, y, quarter=0, half=0))

        # 处理最后一年 end_y
        if end_y != cur_year:
            # 不是当前系统年：直接取年报
            res.append(CReportDate(TYPE_ANNUAL, end_y, quarter=0, half=0))
        else:
            # 是系统当前年份，看结束月份
            if end_m is not None and end_m >= 6:
                # 跨了半年，取中报
                res.append(CReportDate(TYPE_HALF, end_y, quarter=0, half=1))
            # end_m <6：什么都不加

        return res


def TestConvert():
    print("==== TestConvertReportDate 测试用例 ====")
    print(f"系统实时当前年份:{date.today().year}")
    cases = [
        ("2022.1月-2022.12月", "完整同一年"),
        ("2021.1月-2022.9月", "跨两年，均非当年"),
        ("2021", "仅年份数字"),
        ("2021年", "带年汉字"),
    ]
    for input_str, desc in cases:
        print(f"\n【输入】{input_str} ｜ {desc}")
        lst = CReportDate.ConvertReportDate(input_str)
        for item in lst:
            print(f"    {item}, key={item.GetKey()}")

    # 模拟包含系统本年的两个测试
    print("\n【模拟包含系统本年示例】")
    print("输入：2024.3月‑{}.10月".format(date.today().year))
    lst1 = CReportDate.ConvertReportDate(f"2024.3月‑{date.today().year}.10月")
    for item in lst1:
        print(f"    {item}, key={item.GetKey()}")

    print("\n输入：2024.3月‑{}.4月".format(date.today().year))
    lst2 = CReportDate.ConvertReportDate(f"2024.3月‑{date.today().year}.4月")
    for item in lst2:
        print(f"    {item}, key={item.GetKey()}")


if __name__ == "__main__":
    TestConvert()