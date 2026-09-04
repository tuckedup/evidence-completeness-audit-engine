from __future__ import annotations

import os
import random
from typing import Any


def seed_everything(seed: int = 20250314) -> dict[str, Any]:
    """Seed optional ML libraries before an offline extraction run.

    PYTHONHASHSEED must be set before process start for full interpreter-level effect; the
    Docker images set it to zero. This function configures every installed optional backend.
    """
    random.seed(seed)
    configured: dict[str, Any] = {"python_random": seed, "pythonhashseed": os.environ.get("PYTHONHASHSEED")}
    try:
        import numpy as np

        np.random.seed(seed)
        configured["numpy"] = seed
    except ImportError:
        configured["numpy"] = None
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        torch.use_deterministic_algorithms(True)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
        if hasattr(torch.backends.cuda.matmul, "allow_tf32"):
            torch.backends.cuda.matmul.allow_tf32 = False
        configured["torch"] = seed
    except ImportError:
        configured["torch"] = None
    return configured

