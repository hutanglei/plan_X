"""获取本机系统真实日期时间的脚本。

用于校准"当前时间"基准，避免依赖可能滞后/错误的环境注入日期。
优先使用本机系统时钟，输出多种常用格式供调用方选取。
"""
from datetime import datetime, timezone


def get_current_datetime():
    now = datetime.now().astimezone()
    utc_now = datetime.now(timezone.utc)
    return {
        "date": now.strftime("%Y-%m-%d"),
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "weekday": now.strftime("%A"),
        "weekday_cn": "星期" + "一二三四五六日"[now.weekday()],
        "iso": now.isoformat(),
        "timezone": now.strftime("%z"),
        "utc": utc_now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "compact": now.strftime("%Y%m%d"),
        "year": now.year,
        "month": now.month,
        "day": now.day,
    }


def main():
    info = get_current_datetime()
    print("=" * 40)
    print("本机系统当前日期时间（真实基准）")
    print("=" * 40)
    print(f"日期        : {info['date']} ({info['weekday_cn']} / {info['weekday']})")
    print(f"日期时间    : {info['datetime']}")
    print(f"紧凑日期    : {info['compact']}")
    print(f"时区        : {info['timezone']}")
    print(f"ISO8601     : {info['iso']}")
    print(f"UTC         : {info['utc']}")
    print("=" * 40)


if __name__ == "__main__":
    main()
