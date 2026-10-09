"""意图评测默认离线；真实模型与报告由显式参数开启。"""


def pytest_addoption(parser):
    group = parser.getgroup("intent evaluation")
    group.addoption(
        "--intent-live", action="store_true", help="调用配置的真实模型评测意图"
    )
    group.addoption("--intent-report", help="评测报告 JSON 路径（同时生成 Markdown）")
