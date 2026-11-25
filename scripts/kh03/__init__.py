"""KH-03 Universal Editing Intelligence — Cross-Niche Creative Decision Layer atop KH-02.R2 / SeeCut v3.4 Core.

Five universal editorial skills (SK-01..SK-05) sharing one intelligence layer, one mandatory
user-selection gate, and one existing KH-02.R2 / SeeCut rendering pipeline.
PolyForm Noncommercial License 1.0.0 applies to upstream YeJe-cpu/SeeCut (PROBLEM-01 fail-closed).
"""

from __future__ import annotations

__version__ = "kh03.3.0"
REGISTRY_VERSION = "kh03.skills.v1.0.0"
BASE_QA_SHA = "7e8a93d3e70ad4f4deb16e50c1d502764d0f2bb9"
UPSTREAM_SEECUT_SHA = "b05d7ed27404a064060e0d370402f562b0c0c02e"
VALID_SKILL_IDS: tuple[str, ...] = ("SK-01", "SK-02", "SK-03", "SK-04", "SK-05")
