import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from memory_answer_eval import correct


def test_refusal_is_not_a_correct_fact_even_when_it_repeats_keywords():
    assert not correct("资料不足，无法确定备份与升级的顺序。", "先备份数据库再升级")
    assert not correct("资料不足，newhost标为旧地址，无法确定当前地址。", "newhost")
    assert correct("先备份数据库，再执行升级。", "先备份数据库再升级")
    assert correct("资料不足，无法确定配置。", "资料不足")
