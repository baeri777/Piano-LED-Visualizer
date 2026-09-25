import copy

import pytest

from pianoled.config import DEFAULTS, validate


@pytest.fixture
def cfg():
    c = validate(copy.deepcopy(DEFAULTS))
    c["strip"]["max_current_ma"] = 0
    c["look"]["startup_animation"] = False
    return c
