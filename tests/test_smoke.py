from contract_review.config import TENANTS


def test_schools():
    assert set(TENANTS) == {"common", "huaxin", "yunqi", "ruifeng"}
