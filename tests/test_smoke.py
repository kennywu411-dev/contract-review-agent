from contract_review.config import SCHOOLS


def test_schools():
    assert set(SCHOOLS) == {"national", "bnu", "upc", "njust", "lzu", "cumt", "zzu"}
