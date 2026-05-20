"""Unit tests for orchestra.domain.review — ReviewResult / ReviewIssue / ReviewGate."""

from __future__ import annotations

import pytest

from orchestra.domain.review import (
    ReviewArea,
    ReviewGate,
    ReviewIssue,
    ReviewOwner,
    ReviewResult,
    ReviewSeverity,
    ReviewVerdict,
)


class TestReviewIssue:
    def test_minimal_construction(self) -> None:
        """构造最小字段 ReviewIssue。"""
        issue = ReviewIssue(
            id="vis-001",
            severity=ReviewSeverity.P1,
            owner=ReviewOwner.artist,
            area=ReviewArea.visual,
            problem="敌我阵营不可读",
        )
        assert issue.id == "vis-001"
        assert issue.severity == ReviewSeverity.P1
        assert issue.owner == ReviewOwner.artist
        assert issue.area == ReviewArea.visual
        assert issue.problem == "敌我阵营不可读"
        assert issue.suggestion is None
        assert issue.acceptance is None

    def test_maximal_construction(self) -> None:
        """构造完整字段 ReviewIssue（含 suggestion + acceptance）。"""
        issue = ReviewIssue(
            id="ui-002",
            severity=ReviewSeverity.P2,
            owner=ReviewOwner.designer,
            area=ReviewArea.ui,
            problem="按钮位置不符合设计规范",
            suggestion="将主按钮移至右下角，遵循 F 型阅读模式",
            acceptance=["3 秒内找到主操作按钮", "触控区域 ≥44pt"],
        )
        assert issue.suggestion is not None
        assert len(issue.acceptance) == 2

    def test_serialization_roundtrip(self) -> None:
        """序列化 → 反序列化往返一致。"""
        issue = ReviewIssue(
            id="perf-003",
            severity=ReviewSeverity.P0,
            owner=ReviewOwner.developer,
            area=ReviewArea.performance,
            problem="首帧加载 >5s",
            suggestion="使用异步资源加载 + 预处理纹理图集",
            acceptance=["首帧加载 <2s"],
        )
        data = issue.model_dump()
        restored = ReviewIssue.model_validate(data)
        assert restored.id == issue.id
        assert restored.severity == issue.severity
        assert restored.acceptance == issue.acceptance

    def test_invalid_severity_raises(self) -> None:
        """非法 severity 值应拒绝。"""
        with pytest.raises(ValueError):
            ReviewIssue(
                id="e-001",
                severity="CRITICAL",  # type: ignore[arg-type]
                owner=ReviewOwner.developer,
                area=ReviewArea.technical,
                problem="bad",
            )

    def test_invalid_owner_raises(self) -> None:
        """非法 owner 值应拒绝。"""
        with pytest.raises(ValueError):
            ReviewIssue(
                id="e-002",
                severity=ReviewSeverity.P1,
                owner="manager",  # type: ignore[arg-type]
                area=ReviewArea.technical,
                problem="bad",
            )


class TestReviewResult:
    def test_pass_verdict(self) -> None:
        """pass verdict 构造。"""
        result = ReviewResult(
            verdict=ReviewVerdict.PASS,
            confidence=0.98,
            summary="所有检查项通过",
        )
        assert result.verdict == ReviewVerdict.PASS
        assert result.confidence == 0.98
        assert result.issues == []

    def test_fail_with_issues(self) -> None:
        """fail verdict 带 issues。"""
        issues = [
            ReviewIssue(
                id="g-001",
                severity=ReviewSeverity.P0,
                owner=ReviewOwner.developer,
                area=ReviewArea.gameplay,
                problem="核心循环无法触发",
            ),
            ReviewIssue(
                id="v-001",
                severity=ReviewSeverity.P1,
                owner=ReviewOwner.artist,
                area=ReviewArea.visual,
                problem="主角精灵缺失",
            ),
        ]
        result = ReviewResult(
            verdict=ReviewVerdict.FAIL,
            confidence=0.3,
            summary="存在阻断性问题和严重问题",
            issues=issues,
        )
        assert result.verdict == ReviewVerdict.FAIL
        assert len(result.issues) == 2
        assert result.issues[0].id == "g-001"

    def test_serialization_roundtrip(self) -> None:
        """ReviewResult 序列化往返。"""
        result = ReviewResult(
            verdict=ReviewVerdict.FAIL,
            confidence=0.5,
            summary="需要修复",
            issues=[
                ReviewIssue(
                    id="x-001",
                    severity=ReviewSeverity.P2,
                    owner=ReviewOwner.tester,
                    area=ReviewArea.docs,
                    problem="缺失 API 文档",
                ),
            ],
        )
        data = result.model_dump()
        restored = ReviewResult.model_validate(data)
        assert restored.verdict == ReviewVerdict.FAIL
        assert restored.confidence == 0.5
        assert len(restored.issues) == 1

    def test_confidence_bounds(self) -> None:
        """confidence 必须在 [0, 1]。"""
        with pytest.raises(ValueError):
            ReviewResult(verdict=ReviewVerdict.PASS, confidence=1.5)

        with pytest.raises(ValueError):
            ReviewResult(verdict=ReviewVerdict.PASS, confidence=-0.1)

    def test_default_issues_is_empty(self) -> None:
        """issues 默认为空列表。"""
        result = ReviewResult(verdict=ReviewVerdict.PASS)
        assert result.issues == []


class TestReviewGate:
    def test_minimal_construction(self) -> None:
        """最小 ReviewGate 构造。"""
        gate = ReviewGate()
        assert gate.maxIterations == 5
        assert gate.onMaxReached == "fail"
        assert gate.routing == {}
        assert gate.retest == []

    def test_full_config(self) -> None:
        """完整 ReviewGate 配置。"""
        gate = ReviewGate(
            agent="blueberry",
            agentSelector={"role": "chat", "capabilities": ["analyze"]},
            input={"code": "$.code", "test": "$.test"},
            prompt="Review the following changes...",
            outputSchema={"$ref": "config/review-schema.yaml#/ReviewResult"},
            maxIterations=3,
            onMaxReached="continue",
            routing={"developer": "walnut", "designer": "cherry"},
            retest=["test", "ui-verify"],
        )
        assert gate.agent == "blueberry"
        assert gate.maxIterations == 3
        assert gate.onMaxReached == "continue"
        assert gate.routing == {"developer": "walnut", "designer": "cherry"}
        assert gate.retest == ["test", "ui-verify"]

    def test_serialization_roundtrip(self) -> None:
        """ReviewGate 序列化往返。"""
        gate = ReviewGate(
            agent="blueberry",
            maxIterations=5,
            routing={"developer": "walnut"},
            retest=["test"],
        )
        data = gate.model_dump()
        restored = ReviewGate.model_validate(data)
        assert restored.agent == "blueberry"
        assert restored.routing == {"developer": "walnut"}


class TestEnums:
    def test_review_severity_values(self) -> None:
        assert ReviewSeverity.P0.value == "P0"
        assert ReviewSeverity.P1.value == "P1"
        assert ReviewSeverity.P2.value == "P2"
        assert ReviewSeverity.P3.value == "P3"

    def test_review_verdict_values(self) -> None:
        assert ReviewVerdict.PASS.value == "pass"
        assert ReviewVerdict.FAIL.value == "fail"

    def test_review_owner_all_values(self) -> None:
        owners = {o.value for o in ReviewOwner}
        assert owners == {"developer", "designer", "tester", "artist"}

    def test_review_area_all_values(self) -> None:
        areas = {a.value for a in ReviewArea}
        assert areas == {"gameplay", "visual", "ui", "technical", "performance", "audio", "docs"}
